"""S3-03 runtime lifecycle, budget, housekeeper, and process-exit vectors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import httpx
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from taskq import TaskQ
from taskq.errors import TaskqConfigError, TaskqUnavailableError, TaskqVersionError
from taskq.http import (
    ClaimWaitHub,
    EmbeddedWorkerOptions,
    TaskqFacadeTransports,
    TaskqRuntime,
    TaskqRuntimeOptions,
    TaskqRuntimeState,
    compose_lifespans,
    get_taskq_client,
    taskq_lifespan,
)
from taskq.http.runtime import _require_supported_sql_contract
from taskq.protocol import ContractMeta


class _Transport:
    def __init__(self, *, version: str = "0.1.2", ticks: list[object] | None = None) -> None:
        self.version = version
        self.ticks = list(ticks or [{}])
        self.tick_calls = 0
        self.close_calls = 0

    async def get_contract_meta(self) -> ContractMeta:
        return ContractMeta(contract_version=self.version, capabilities={})

    async def tick(self, reap_limit: int = 100) -> dict[str, Any]:
        del reap_limit
        self.tick_calls += 1
        result = self.ticks.pop(0) if self.ticks else {}
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    async def aclose(self) -> None:
        self.close_calls += 1


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleepers: list[asyncio.Future[None]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay
        future = asyncio.get_running_loop().create_future()
        self.sleepers.append(future)
        await future

    def advance(self) -> None:
        for future in self.sleepers:
            if not future.done():
                future.set_result(None)
        self.sleepers.clear()


class _Service:
    def __init__(self) -> None:
        self.ready = False
        self.requires_process_exit = False
        self.started = 0
        self.stopped = 0
        self.prepared = 0

    async def start(self) -> None:
        self.started += 1
        self.ready = True

    async def stop(self, *, cancel: bool = False) -> None:
        del cancel
        self.stopped += 1
        self.ready = False

    async def _prepare_process_exit(self) -> None:
        self.prepared += 1


class _SlowResource(_Transport):
    def __init__(self) -> None:
        super().__init__()
        self.closing = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.closing.set()
        await self.release.wait()
        await super().aclose()


def _runtime(
    transport: _Transport,
    *,
    options: TaskqRuntimeOptions | None = None,
    service: _Service | None = None,
    clock: _Clock | None = None,
    process_exit: Any = None,
    owned: tuple[object, ...] = (),
) -> TaskqRuntime:
    hub = ClaimWaitHub()
    resources = TaskqFacadeTransports(
        producer=transport,  # type: ignore[arg-type]
        runner=transport,  # type: ignore[arg-type]
        observer=transport,  # type: ignore[arg-type]
        authorization=transport,  # type: ignore[arg-type]
        claim_wait_hub=hub,
    )
    kwargs: dict[str, Any] = {}
    if process_exit is not None:
        kwargs["process_exit"] = process_exit
    return TaskqRuntime(
        TaskQ(transport),  # type: ignore[arg-type]
        resources,
        options=options
        or TaskqRuntimeOptions(housekeeper_enabled=False, long_poll_listener_enabled=False),
        housekeeper_transport=(
            transport if options is not None and options.housekeeper_enabled else None
        ),
        embedded_service=service,  # type: ignore[arg-type]
        owned_resources=owned,
        clock=clock,
        **kwargs,
    )


def test_embedded_acknowledgement_and_budget_ceiling_are_mandatory() -> None:
    with pytest.raises(ValidationError, match="process-multiplication acknowledgement"):
        EmbeddedWorkerOptions(queues=("alpha",), acknowledge_process_multiplication=False)
    embedded = EmbeddedWorkerOptions(
        queues=("alpha",), acknowledge_process_multiplication=True, concurrency=2
    )
    options = TaskqRuntimeOptions(
        embedded_worker=embedded,
        expected_asgi_processes=4,
        database_connection_ceiling=100,
        database_connection_reserve=10,
    )
    assert options.process_pool_capacity == 13
    assert options.process_listener_capacity == 2
    with pytest.raises(ValidationError, match="exceed the database ceiling"):
        TaskqRuntimeOptions(
            embedded_worker=embedded,
            expected_asgi_processes=4,
            database_connection_ceiling=60,
            database_connection_reserve=1,
        )


async def test_composed_lifespan_orders_host_and_runtime_and_restores_state() -> None:
    events: list[str] = []
    transport = _Transport()
    runtime = _runtime(transport)
    app = FastAPI()
    previous = object()
    app.state.taskq = previous

    @asynccontextmanager
    async def host(_app: FastAPI) -> AsyncIterator[None]:
        events.append("host-start")
        yield
        events.append("host-stop")

    lifespan = compose_lifespans(host, runtime)
    async with lifespan(app):
        events.append("serve")
        assert app.state.taskq is runtime.taskq
        assert runtime.ready
        await runtime.start()
    assert events == ["host-start", "serve", "host-stop"]
    assert app.state.taskq is previous
    assert runtime.state is TaskqRuntimeState.STOPPED


async def test_taskq_lifespan_removes_new_state_and_startup_failure_unwinds() -> None:
    app = FastAPI()
    runtime = _runtime(_Transport())
    async with taskq_lifespan(runtime)(app):
        assert app.state.taskq is runtime.taskq
    assert not hasattr(app.state, "taskq")

    failing = _runtime(_Transport(version="9.9"))
    with pytest.raises(Exception):
        async with taskq_lifespan(failing)(FastAPI()):
            raise AssertionError("unreachable")
    assert failing.state is TaskqRuntimeState.FAILED


@pytest.mark.parametrize("version", ["0.1.2", "0.1.3", "0.1.4"])
async def test_runtime_bridge_accepts_both_contract_revisions_and_keeps_prebridge_rejection(
    version: str,
) -> None:
    bridge = _runtime(_Transport(version=version))
    await bridge.start()
    assert bridge.state is TaskqRuntimeState.RUNNING
    await bridge.stop()

    # This preserved historical set is the deliberate negative proof: a
    # pre-bridge exact-0.1.2 runtime must still fail closed on newer metadata.
    with pytest.raises(TaskqVersionError) as exc_info:
        _require_supported_sql_contract("0.1.4", supported_versions=frozenset({"0.1.2"}))
    assert exc_info.value.details == {"contract_version": "0.1.4"}


async def test_both_lifespan_startup_failure_directions_unwind_exactly_once() -> None:
    events: list[str] = []
    runtime = _runtime(_Transport())

    @asynccontextmanager
    async def failing_host(_app: FastAPI) -> AsyncIterator[None]:
        events.append("host-start")
        raise RuntimeError("host failed")
        yield

    with pytest.raises(RuntimeError, match="host failed"):
        async with compose_lifespans(failing_host, runtime)(FastAPI()):
            raise AssertionError("unreachable")
    assert runtime.state is TaskqRuntimeState.CONSTRUCTED
    assert events == ["host-start"]

    events.clear()
    runtime = _runtime(_Transport(version="9.9"))

    @asynccontextmanager
    async def host(_app: FastAPI) -> AsyncIterator[None]:
        events.append("host-start")
        try:
            yield
        finally:
            events.append("host-stop")

    with pytest.raises(Exception):
        async with compose_lifespans(host, runtime)(FastAPI()):
            raise AssertionError("unreachable")
    assert events == ["host-start", "host-stop"]
    assert runtime.state is TaskqRuntimeState.FAILED


async def test_housekeeper_transient_failure_degrades_then_recovers() -> None:
    transport = _Transport(ticks=[TaskqUnavailableError(), {}])
    clock = _Clock()
    options = TaskqRuntimeOptions(
        housekeeper_enabled=True,
        long_poll_listener_enabled=False,
        housekeeper_interval=5,
        housekeeper_jitter=0,
    )
    runtime = _runtime(transport, options=options, clock=clock)
    await runtime.start()
    assert runtime.state is TaskqRuntimeState.DEGRADED
    for _ in range(10):
        if clock.sleepers:
            break
        await asyncio.sleep(0)
    clock.advance()
    for _ in range(20):
        if transport.tick_calls == 2:
            break
        await asyncio.sleep(0)
    assert runtime.state is TaskqRuntimeState.RUNNING
    assert runtime.snapshot().housekeeper_failures == 1
    assert runtime.snapshot().last_housekeeper_success_age == 0
    await runtime.stop()


async def test_budget_unknown_and_inverted_asgi_grace_warn_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime(
        _Transport(),
        options=TaskqRuntimeOptions(
            housekeeper_enabled=False,
            long_poll_listener_enabled=False,
            soft_stop_timeout=10,
            asgi_graceful_timeout=10,
        ),
    )
    with caplog.at_level(logging.WARNING, logger="taskq.runtime"):
        await runtime.start()
    assert {record.message for record in caplog.records} >= {
        "runtime.budget_unknown",
        "runtime.asgi_grace_too_short",
    }
    assert not runtime.snapshot().budget_known
    await runtime.stop()


async def test_public_dependency_uses_app_state_and_normal_fastapi_override() -> None:
    runtime = _runtime(_Transport())
    app = FastAPI()

    @app.get("/client")
    async def client_name(client: Any = Depends(get_taskq_client)) -> dict[str, bool]:
        return {"runtime": client is runtime.taskq}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        missing = await client.get("/client")
        assert missing.status_code == 500
        app.dependency_overrides[get_taskq_client] = lambda: runtime.taskq
        overridden = await client.get("/client")
        assert overridden.json() == {"runtime": True}


async def test_concurrent_stop_is_shared_and_external_cancellation_cleans_up() -> None:
    transport = _Transport()
    runtime = _runtime(transport, owned=(transport,))
    await runtime.start()
    first = asyncio.create_task(runtime.stop())
    second = asyncio.create_task(runtime.stop())
    await asyncio.gather(first, second)
    assert runtime.state is TaskqRuntimeState.STOPPED
    assert transport.close_calls == 1

    transport = _SlowResource()
    runtime = _runtime(transport, owned=(transport,))
    await runtime.start()
    stopping = asyncio.create_task(runtime.stop())
    await transport.closing.wait()
    stopping.cancel()
    transport.release.set()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert runtime.state is TaskqRuntimeState.STOPPED
    assert transport.close_calls == 1


async def test_runtime_is_asgi_process_exit_actor_for_unsafe_sync() -> None:
    transport = _Transport()
    service = _Service()
    exits: list[int] = []
    options = TaskqRuntimeOptions(
        housekeeper_enabled=False,
        long_poll_listener_enabled=False,
        embedded_worker=EmbeddedWorkerOptions(
            queues=("alpha",), acknowledge_process_multiplication=True
        ),
    )
    runtime = _runtime(
        transport,
        options=options,
        service=service,
        process_exit=exits.append,
        owned=(transport,),
    )
    app = FastAPI(lifespan=taskq_lifespan(runtime))
    async with app.router.lifespan_context(app):
        service.requires_process_exit = True
        for _ in range(100):
            if exits:
                break
            await asyncio.sleep(0.01)
        assert exits == [3]
        assert runtime.state is TaskqRuntimeState.FAILED
        assert service.prepared == 1
        assert transport.close_calls == 1


def test_runtime_configuration_mismatch_is_rejected() -> None:
    transport = _Transport()
    configured = _runtime(transport)
    with pytest.raises(TaskqConfigError, match="housekeeper option"):
        TaskqRuntime(
            configured.taskq,
            configured.facade_transports,
            options=TaskqRuntimeOptions(housekeeper_enabled=True, long_poll_listener_enabled=False),
        )
