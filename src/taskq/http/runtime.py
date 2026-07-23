"""Composable FastAPI lifecycle for housekeeper and opt-in embedded execution."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Protocol

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from taskq.client import TaskQ
from taskq.errors import TaskqCapabilityError, TaskqConfigError, TaskqError, TaskqVersionError
from taskq.http.facade import TaskqFacadeTransports
from taskq.http.hub import ClaimWaitHub
from taskq.registry import TaskRegistry
from taskq.sql.notifications import PostgresNotificationSource
from taskq.sql.transport import SqlTaskqTransport
from taskq.transport import HousekeeperTransport
from taskq.worker import WorkerOptions, WorkerService, WorkerServiceOptions

logger = logging.getLogger("taskq.runtime")


# ADR-020: this bridge is deliberately a closed compatibility set, not a range.
# Set membership alone exposes no newly added capability surface; it only lets
# an already-deployed runtime survive additive metadata revisions while each
# later transport/facade surface remains separately gated.
SUPPORTED_SQL_CONTRACT_VERSIONS = frozenset({"0.1.2", "0.1.3", "0.1.4", "0.1.5", "0.2.0"})
ADMISSION_SQL_CONTRACT_VERSIONS = frozenset({"0.1.5", "0.2.0"})


def _require_supported_sql_contract(
    contract_version: str, *, supported_versions: frozenset[str] = SUPPORTED_SQL_CONTRACT_VERSIONS
) -> None:
    if contract_version not in supported_versions:
        raise TaskqVersionError(details={"contract_version": contract_version})


class EmbeddedWorkerOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    queues: tuple[str, ...]
    acknowledge_process_multiplication: bool
    concurrency: int = Field(default=1, ge=1, le=1000)
    sync_workers: int | None = Field(default=None, ge=1, le=1000)
    batch: int = Field(default=1, ge=1, le=50)
    listen: bool = True
    poll_interval: float = Field(default=5.0, ge=0.1, le=3600)
    presence_interval: float = Field(default=60.0, ge=5, le=3600)

    @model_validator(mode="after")
    def _validate_embedded(self) -> EmbeddedWorkerOptions:
        WorkerServiceOptions(
            queues=self.queues,
            batch=self.batch,
            listen=self.listen,
            poll_interval=self.poll_interval,
            presence_interval=self.presence_interval,
        )
        if not self.acknowledge_process_multiplication:
            raise ValueError("embedded worker requires process-multiplication acknowledgement")
        if self.sync_workers is not None and self.sync_workers > self.concurrency:
            raise ValueError("sync_workers cannot exceed concurrency")
        if self.batch > self.concurrency:
            raise ValueError("batch cannot exceed concurrency")
        return self


class TaskqRuntimeOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    housekeeper_enabled: bool = True
    long_poll_listener_enabled: bool = True
    embedded_worker: EmbeddedWorkerOptions | None = None
    admission_enabled: bool = False
    request_pool_max: int = Field(default=10, ge=1, le=1000)
    operator_pool_max: int = Field(default=0, ge=0, le=1000)
    housekeeper_pool_max: int = Field(default=1, ge=1, le=1000)
    embedded_worker_pool_max: int = Field(default=2, ge=1, le=1000)
    expected_asgi_processes: int | None = Field(default=None, ge=1, le=10000)
    database_connection_ceiling: int | None = Field(default=None, ge=1)
    database_connection_reserve: int = Field(default=0, ge=0)
    housekeeper_interval: float = Field(default=5.0, ge=0.1, le=3600)
    housekeeper_jitter: float = Field(default=0.1, ge=0, le=0.5)
    housekeeper_backoff_cap: float = Field(default=30.0, ge=0.1, le=3600)
    long_poll_listener_backoff: float = Field(default=0.25, ge=0.01, le=30)
    soft_stop_timeout: float | None = Field(default=None, ge=0)
    asgi_graceful_timeout: float | None = Field(default=None, ge=0)
    expected_environment: str = Field(default="development", min_length=1)
    allow_production: bool = False

    @property
    def process_pool_capacity(self) -> int:
        return (
            self.request_pool_max
            + self.operator_pool_max
            + (self.housekeeper_pool_max if self.housekeeper_enabled else 0)
            + (self.embedded_worker_pool_max if self.embedded_worker is not None else 0)
        )

    @property
    def process_listener_capacity(self) -> int:
        return int(self.long_poll_listener_enabled) + int(
            self.embedded_worker is not None and self.embedded_worker.listen
        )

    @model_validator(mode="after")
    def _validate_runtime(self) -> TaskqRuntimeOptions:
        if self.expected_environment == "production" and not self.allow_production:
            raise ValueError("production requires allow_production=True")
        ceiling = self.database_connection_ceiling
        if ceiling is not None and self.database_connection_reserve >= ceiling:
            raise ValueError("database connection reserve must be below the ceiling")
        if self.expected_asgi_processes is not None and ceiling is not None:
            total = self.expected_asgi_processes * (
                self.process_pool_capacity + self.process_listener_capacity
            )
            if total > ceiling - self.database_connection_reserve:
                raise ValueError("estimated taskq connections exceed the database ceiling")
        return self


class TaskqRuntimeState(StrEnum):
    CONSTRUCTED = "constructed"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TaskqRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: TaskqRuntimeState
    ready: bool
    started_monotonic: float | None
    last_housekeeper_success_age: float | None
    housekeeper_failures: int
    listener_healthy: bool
    embedded_worker_ready: bool | None
    requires_process_exit: bool
    expected_asgi_processes: int | None
    total_handler_capacity: int | None
    total_pool_capacity: int | None
    total_listener_connections: int | None
    budget_known: bool


class RuntimeClock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, delay: float) -> None: ...


class _RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


class _RuntimeNotificationListener:
    def __init__(
        self, source: PostgresNotificationSource, hub: ClaimWaitHub, *, backoff: float
    ) -> None:
        self.source = source
        self.hub = hub
        self.backoff = backoff
        self.healthy = True
        self._queues: set[str] = set()
        self._lock = asyncio.Lock()
        self._monitor: asyncio.Task[None] | None = None
        self._closed = False

    def _nudge(self) -> None:
        asyncio.create_task(self.hub.notify(), name="taskq-runtime-long-poll-nudge")

    async def ensure_queue(self, queue: str) -> None:
        async with self._lock:
            if self._closed:
                raise TaskqConfigError("runtime notification listener is closed")
            if queue in self._queues:
                return
            self._queues.add(queue)
            channel = f"taskq_{queue}"
            if self._monitor is None:
                try:
                    await self.source.connect([channel], self._nudge)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.healthy = False
                    self._monitor = asyncio.create_task(
                        self._reconnect_loop(), name="taskq-runtime-listener-reconnect"
                    )
                else:
                    self.healthy = True
                    self._monitor = asyncio.create_task(
                        self._monitor_loop(), name="taskq-runtime-listener"
                    )
            else:
                await self.source.add_channels([channel])

    async def _monitor_loop(self) -> None:
        try:
            await self.source.wait_disconnected()
            self.healthy = False
            await self._reconnect_loop()
        except asyncio.CancelledError:
            raise

    async def _reconnect_loop(self) -> None:
        while not self._closed:
            try:
                await self.source.connect(
                    [f"taskq_{queue}" for queue in sorted(self._queues)], self._nudge
                )
                self.healthy = True
                await self.hub.notify()
                await self.source.wait_disconnected()
                self.healthy = False
            except asyncio.CancelledError:
                raise
            except Exception:
                self.healthy = False
            await asyncio.sleep(self.backoff)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            monitor, self._monitor = self._monitor, None
        await self.source.aclose()
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


class TaskqRuntime:
    """Idempotent owner of taskq lifecycle resources; construction performs no I/O."""

    def __init__(
        self,
        taskq: TaskQ,
        facade_transports: TaskqFacadeTransports,
        *,
        options: TaskqRuntimeOptions | None = None,
        housekeeper_transport: HousekeeperTransport | None = None,
        embedded_service: WorkerService | None = None,
        notification_listener: _RuntimeNotificationListener | None = None,
        owned_resources: Sequence[object] = (),
        clock: RuntimeClock | None = None,
        process_exit: Callable[[int], Any] = os._exit,
    ) -> None:
        self.taskq = taskq
        self.facade_transports = facade_transports
        self.options = options or TaskqRuntimeOptions(
            housekeeper_enabled=False, long_poll_listener_enabled=False
        )
        if self.options.housekeeper_enabled != (housekeeper_transport is not None):
            raise TaskqConfigError("housekeeper option and transport must be configured together")
        if (self.options.embedded_worker is None) != (embedded_service is None):
            raise TaskqConfigError("embedded worker option and service must be configured together")
        self.housekeeper_transport = housekeeper_transport
        self.embedded_service = embedded_service
        self.notification_listener = notification_listener
        self._owned_resources = tuple(owned_resources)
        self.clock = clock or _RealClock()
        self.process_exit = process_exit
        self._state = TaskqRuntimeState.CONSTRUCTED
        self._started_monotonic: float | None = None
        self._last_housekeeper_success: float | None = None
        self._housekeeper_failures = 0
        self._housekeeper_healthy = not self.options.housekeeper_enabled
        self._stop_requested = asyncio.Event()
        self._housekeeper_task: asyncio.Task[None] | None = None
        self._process_exit_task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._resources_closed = False
        self._process_exit_acted = False

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        registry: TaskRegistry | None = None,
        options: TaskqRuntimeOptions | None = None,
        process_exit: Callable[[int], Any] = os._exit,
    ) -> TaskqRuntime:
        resolved = options or TaskqRuntimeOptions()
        registry = registry or TaskRegistry()
        ordinary = SqlTaskqTransport.from_dsn(
            dsn, pool_size=resolved.request_pool_max, max_overflow=0
        )
        hub = ClaimWaitHub()
        runtime_listener: _RuntimeNotificationListener | None = None
        owned: list[object] = [ordinary]
        if resolved.long_poll_listener_enabled:
            runtime_listener = _RuntimeNotificationListener(
                PostgresNotificationSource(dsn), hub, backoff=resolved.long_poll_listener_backoff
            )
            hub.install_queue_registrar(runtime_listener.ensure_queue)
        facade = TaskqFacadeTransports(
            producer=ordinary,
            runner=ordinary,
            observer=ordinary,
            authorization=ordinary,
            claim_wait_hub=hub,
            admission_enabled=resolved.admission_enabled,
        )
        housekeeper: SqlTaskqTransport | None = None
        if resolved.housekeeper_enabled:
            housekeeper = SqlTaskqTransport.from_dsn(
                dsn, pool_size=resolved.housekeeper_pool_max, max_overflow=0
            )
            owned.append(housekeeper)
        service: WorkerService | None = None
        if resolved.embedded_worker is not None:
            embedded = resolved.embedded_worker
            worker_transport = SqlTaskqTransport.from_dsn(
                dsn, pool_size=resolved.embedded_worker_pool_max, max_overflow=0
            )
            worker_notifications = PostgresNotificationSource(dsn) if embedded.listen else None
            service = WorkerService(
                worker_transport,
                registry,
                f"api:{socket.gethostname()}:{os.getpid()}",
                options=WorkerServiceOptions(
                    queues=embedded.queues,
                    batch=embedded.batch,
                    poll_interval=embedded.poll_interval,
                    listen=embedded.listen,
                    presence_interval=embedded.presence_interval,
                ),
                supervisor_options=WorkerOptions(
                    concurrency=embedded.concurrency,
                    sync_workers=embedded.sync_workers,
                    soft_stop_timeout=resolved.soft_stop_timeout,
                ),
                notifications=worker_notifications,
            )
            owned.append(worker_transport)
        return cls(
            TaskQ(ordinary, registry=registry),
            facade,
            options=resolved,
            housekeeper_transport=housekeeper,
            embedded_service=service,
            notification_listener=runtime_listener,
            owned_resources=owned,
            process_exit=process_exit,
        )

    @property
    def state(self) -> TaskqRuntimeState:
        return self._state

    @property
    def ready(self) -> bool:
        return self.snapshot().ready

    def snapshot(self) -> TaskqRuntimeSnapshot:
        processes = self.options.expected_asgi_processes
        worker_ready = None if self.embedded_service is None else self.embedded_service.ready
        listener_healthy = (
            True if self.notification_listener is None else self.notification_listener.healthy
        )
        requires_exit = bool(
            self.embedded_service is not None and self.embedded_service.requires_process_exit
        )
        ready = (
            self._state is TaskqRuntimeState.RUNNING
            and self._housekeeper_healthy
            and listener_healthy
            and (worker_ready is None or worker_ready)
            and not requires_exit
        )
        now = self.clock.monotonic()
        embedded = self.options.embedded_worker
        return TaskqRuntimeSnapshot(
            state=self._state,
            ready=ready,
            started_monotonic=self._started_monotonic,
            last_housekeeper_success_age=(
                None
                if self._last_housekeeper_success is None
                else max(0.0, now - self._last_housekeeper_success)
            ),
            housekeeper_failures=self._housekeeper_failures,
            listener_healthy=listener_healthy,
            embedded_worker_ready=worker_ready,
            requires_process_exit=requires_exit,
            expected_asgi_processes=processes,
            total_handler_capacity=(
                None if processes is None or embedded is None else processes * embedded.concurrency
            ),
            total_pool_capacity=(
                None if processes is None else processes * self.options.process_pool_capacity
            ),
            total_listener_connections=(
                None if processes is None else processes * self.options.process_listener_capacity
            ),
            budget_known=(
                processes is not None and self.options.database_connection_ceiling is not None
            ),
        )

    async def start(self) -> None:
        if self._state in {TaskqRuntimeState.RUNNING, TaskqRuntimeState.DEGRADED}:
            return
        if self._state is not TaskqRuntimeState.CONSTRUCTED:
            raise TaskqConfigError("stopped taskq runtime cannot be restarted")
        self._state = TaskqRuntimeState.STARTING
        self._started_monotonic = self.clock.monotonic()
        try:
            meta = await self.facade_transports.observer.get_contract_meta()
            _require_supported_sql_contract(meta.contract_version)
            if self.facade_transports.admission_enabled:
                if meta.contract_version not in ADMISSION_SQL_CONTRACT_VERSIONS:
                    raise TaskqVersionError(details={"contract_version": meta.contract_version})
                active = meta.capabilities.get("active")
                if not isinstance(active, list) or "admission_reservations" not in active:
                    raise TaskqCapabilityError(details={"capability": "admission_reservations"})
            self._log_budget()
            if self.housekeeper_transport is not None:
                await self._tick(startup=True)
                self._housekeeper_task = asyncio.create_task(
                    self._housekeeper_loop(), name="taskq-runtime-housekeeper"
                )
            if self.embedded_service is not None:
                await self.embedded_service.start()
                self._process_exit_task = asyncio.create_task(
                    self._monitor_process_exit(), name="taskq-runtime-process-exit"
                )
            self._state = (
                TaskqRuntimeState.RUNNING
                if self._housekeeper_healthy
                else TaskqRuntimeState.DEGRADED
            )
            logger.info("runtime.started")
        except BaseException:
            self._state = TaskqRuntimeState.FAILED
            self._stop_requested.set()
            await self.facade_transports.claim_wait_hub.shutdown()
            if self.embedded_service is not None:
                await self.embedded_service.stop(cancel=True)
            await self._cleanup_started()
            raise

    def _log_budget(self) -> None:
        snapshot = self.snapshot()
        logger.info(
            "runtime.budget",
            extra={
                "expected_asgi_processes": snapshot.expected_asgi_processes,
                "total_handler_capacity": snapshot.total_handler_capacity,
                "total_pool_capacity": snapshot.total_pool_capacity,
                "total_listener_connections": snapshot.total_listener_connections,
            },
        )
        if not snapshot.budget_known:
            logger.warning("runtime.budget_unknown")
        if (
            self.options.asgi_graceful_timeout is not None
            and self.options.soft_stop_timeout is not None
            and self.options.asgi_graceful_timeout <= self.options.soft_stop_timeout
        ):
            logger.warning("runtime.asgi_grace_too_short")

    async def _tick(self, *, startup: bool = False) -> None:
        assert self.housekeeper_transport is not None
        try:
            await self.housekeeper_transport.tick()
        except asyncio.CancelledError:
            raise
        except TaskqError as exc:
            self._housekeeper_failures += 1
            self._housekeeper_healthy = False
            if isinstance(exc, TaskqVersionError) or not exc.retryable:
                self._state = TaskqRuntimeState.FAILED
                raise
            if not startup:
                self._state = TaskqRuntimeState.DEGRADED
            logger.warning("housekeeper.failed", extra={"error_type": type(exc).__name__})
        except Exception as exc:
            self._housekeeper_failures += 1
            self._housekeeper_healthy = False
            if not startup:
                self._state = TaskqRuntimeState.DEGRADED
            logger.warning("housekeeper.failed", extra={"error_type": type(exc).__name__})
        else:
            recovered = not self._housekeeper_healthy
            self._housekeeper_healthy = True
            self._last_housekeeper_success = self.clock.monotonic()
            if recovered and self._state is TaskqRuntimeState.DEGRADED:
                self._state = TaskqRuntimeState.RUNNING
                logger.info("housekeeper.recovered")

    async def _housekeeper_loop(self) -> None:
        backoff = self.options.housekeeper_interval
        while not self._stop_requested.is_set():
            jitter = 1 + random.uniform(
                -self.options.housekeeper_jitter, self.options.housekeeper_jitter
            )
            await self._sleep_or_stop(backoff * jitter)
            if self._stop_requested.is_set():
                return
            try:
                await self._tick()
            except TaskqError:
                self._stop_requested.set()
                await self.facade_transports.claim_wait_hub.shutdown()
                if self.embedded_service is not None:
                    await self.embedded_service.stop(cancel=True)
                await self._cleanup_started()
                return
            backoff = (
                self.options.housekeeper_interval
                if self._housekeeper_healthy
                else min(backoff * 2, self.options.housekeeper_backoff_cap)
            )

    async def _sleep_or_stop(self, delay: float) -> None:
        sleeping = asyncio.create_task(self.clock.sleep(delay))
        stopping = asyncio.create_task(self._stop_requested.wait())
        await asyncio.wait((sleeping, stopping), return_when=asyncio.FIRST_COMPLETED)
        sleeping.cancel()
        stopping.cancel()
        await asyncio.gather(sleeping, stopping, return_exceptions=True)

    async def _monitor_process_exit(self) -> None:
        assert self.embedded_service is not None
        while self._state is not TaskqRuntimeState.STOPPED and not self._process_exit_acted:
            if await self._act_on_process_exit_requirement():
                return
            await asyncio.sleep(0.01)

    async def _act_on_process_exit_requirement(self) -> bool:
        if self._process_exit_acted:
            return True
        if self.embedded_service is None or not self.embedded_service.requires_process_exit:
            return False
        self._process_exit_acted = True
        self._state = TaskqRuntimeState.FAILED
        self._stop_requested.set()
        await self.facade_transports.claim_wait_hub.shutdown()
        await self.embedded_service._prepare_process_exit()
        await self._close_resources()
        logger.critical("runtime.process_exit_required")
        logging.shutdown()
        self.process_exit(3)
        return True

    async def stop(self, *, cancel: bool = False) -> None:
        if self._state is TaskqRuntimeState.CONSTRUCTED:
            await self.facade_transports.claim_wait_hub.shutdown()
            await self._close_resources()
            self._state = TaskqRuntimeState.STOPPED
            return
        if self._state is TaskqRuntimeState.STOPPED:
            return
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(
                self._stop(cancel=cancel), name="taskq-runtime-stop"
            )
        elif cancel and self.embedded_service is not None:
            await self.embedded_service.stop(cancel=True)
        externally_cancelled = False
        while not self._stop_task.done():
            try:
                await asyncio.shield(self._stop_task)
            except asyncio.CancelledError:
                externally_cancelled = True
        self._stop_task.result()
        if externally_cancelled:
            raise asyncio.CancelledError

    async def _stop(self, *, cancel: bool) -> None:
        self._state = TaskqRuntimeState.STOPPING
        self._stop_requested.set()
        await self.facade_transports.claim_wait_hub.shutdown()
        if self.embedded_service is not None:
            await self.embedded_service.stop(cancel=cancel)
            if await self._act_on_process_exit_requirement():
                return
        await self._cleanup_started()
        self._state = TaskqRuntimeState.STOPPED
        logger.info("runtime.stopped")

    async def _cleanup_started(self) -> None:
        current = asyncio.current_task()
        tasks = tuple(
            task
            for task in (self._housekeeper_task, self._process_exit_task)
            if task is not None and task is not current
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._close_resources()

    async def _close_resources(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        if self.notification_listener is not None:
            await self.notification_listener.aclose()
        for resource in reversed(self._owned_resources):
            await _close(resource)

    async def __aenter__(self) -> TaskqRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.stop(cancel=exc_type is not None)


async def _close(resource: object) -> None:
    close = getattr(resource, "aclose", None)
    if close is not None:
        await close()


_MISSING = object()


@asynccontextmanager
async def _runtime_app_context(runtime: TaskqRuntime, app: FastAPI) -> AsyncIterator[None]:
    previous = getattr(app.state, "taskq", _MISSING)
    await runtime.start()
    app.state.taskq = runtime.taskq
    try:
        yield
    finally:
        try:
            await runtime.stop()
        finally:
            if previous is _MISSING:
                delattr(app.state, "taskq")
            else:
                app.state.taskq = previous


def taskq_lifespan(runtime: TaskqRuntime) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with _runtime_app_context(runtime, app):
            yield

    return lifespan


def compose_lifespans(
    host_lifespan: Callable[[FastAPI], Any] | None, runtime: TaskqRuntime
) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if host_lifespan is None:
            async with _runtime_app_context(runtime, app):
                yield
            return
        async with host_lifespan(app):
            async with _runtime_app_context(runtime, app):
                yield

    return lifespan


def get_taskq_client(request: Request) -> TaskQ:
    taskq = getattr(request.app.state, "taskq", None)
    if not isinstance(taskq, TaskQ):
        raise TaskqConfigError("taskq runtime is not active")
    return taskq


__all__ = [
    "EmbeddedWorkerOptions",
    "TaskqRuntime",
    "TaskqRuntimeOptions",
    "TaskqRuntimeSnapshot",
    "TaskqRuntimeState",
    "compose_lifespans",
    "get_taskq_client",
    "taskq_lifespan",
]
