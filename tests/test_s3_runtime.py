"""S3-03 runtime lifecycle, budget, housekeeper, and process-exit vectors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import httpx
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from taskq import TaskQ
from taskq.errors import (
    TaskqCapabilityError,
    TaskqConfigError,
    TaskqUnavailableError,
    TaskqVersionError,
)
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
from taskq.protocol import ContractMeta, ScheduleClaim, ScheduleClaimResult


class _Transport:
    def __init__(
        self,
        *,
        version: str = "0.1.2",
        capabilities: dict[str, Any] | None = None,
        ticks: list[object] | None = None,
    ) -> None:
        self.version = version
        self.capabilities = dict(capabilities or {})
        self.ticks = list(ticks or [{}])
        self.tick_calls = 0
        self.close_calls = 0
        self.schedule_batches: list[ScheduleClaimResult] = []
        self.schedule_fires: list[tuple[Any, ...]] = []
        self.schedule_errors: list[tuple[Any, ...]] = []

    async def get_contract_meta(self) -> ContractMeta:
        return ContractMeta(contract_version=self.version, capabilities=self.capabilities)

    async def tick(self, reap_limit: int = 100) -> dict[str, Any]:
        del reap_limit
        self.tick_calls += 1
        result = self.ticks.pop(0) if self.ticks else {}
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    async def aclose(self) -> None:
        self.close_calls += 1

    async def claim_schedules(
        self, worker_id: str, *, limit: int = 10, lease_seconds: int = 60
    ) -> ScheduleClaimResult:
        del worker_id, limit, lease_seconds
        if self.schedule_batches:
            return self.schedule_batches.pop(0)
        return ScheduleClaimResult(state="empty")

    async def fire_schedule(self, *args: Any) -> None:
        self.schedule_fires.append(args)

    async def schedule_error(self, *args: Any, **kwargs: Any) -> None:
        self.schedule_errors.append((*args, kwargs))


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
        admission_enabled=bool(options and options.admission_enabled),
        workflow_producer=transport if options and options.workflow_enabled else None,  # type: ignore[arg-type]
        workflow_authorization=transport if options and options.workflow_enabled else None,  # type: ignore[arg-type]
        workflow_enabled=bool(options and options.workflow_enabled),
        schedule_enabled=bool(options and options.schedule_enabled),
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


@pytest.mark.parametrize(
    "version",
    ["0.1.2", "0.1.3", "0.1.4", "0.1.5", "0.2.0", "0.2.1", "0.2.2", "0.2.3"],
)
async def test_runtime_bridge_accepts_closed_contract_set_and_keeps_prebridge_rejection(
    version: str,
) -> None:
    bridge = _runtime(_Transport(version=version))
    await bridge.start()
    assert bridge.state is TaskqRuntimeState.RUNNING
    await bridge.stop()

    # These preserved historical sets are deliberate negative proofs: each
    # preceding bridge still fails closed on the next metadata revision.
    with pytest.raises(TaskqVersionError) as exc_info:
        _require_supported_sql_contract(
            "0.2.1",
            supported_versions=frozenset({"0.1.2", "0.1.3", "0.1.4", "0.1.5", "0.2.0"}),
        )
    assert exc_info.value.details == {"contract_version": "0.2.1"}
    with pytest.raises(TaskqVersionError) as exc_info:
        _require_supported_sql_contract(
            "0.2.2",
            supported_versions=frozenset({"0.1.2", "0.1.3", "0.1.4", "0.1.5", "0.2.0", "0.2.1"}),
        )
    assert exc_info.value.details == {"contract_version": "0.2.2"}
    with pytest.raises(TaskqVersionError) as exc_info:
        _require_supported_sql_contract(
            "0.2.3",
            supported_versions=frozenset(
                {"0.1.2", "0.1.3", "0.1.4", "0.1.5", "0.2.0", "0.2.1", "0.2.2"}
            ),
        )
    assert exc_info.value.details == {"contract_version": "0.2.3"}


async def test_admission_runtime_refuses_wrong_metadata_and_accepts_exact_capability() -> None:
    options = TaskqRuntimeOptions(
        housekeeper_enabled=False,
        long_poll_listener_enabled=False,
        admission_enabled=True,
    )
    wrong_version = _runtime(_Transport(version="0.1.4"), options=options)
    with pytest.raises(TaskqVersionError):
        await wrong_version.start()

    missing = _runtime(_Transport(version="0.1.5"), options=options)
    with pytest.raises(TaskqCapabilityError):
        await missing.start()

    active = _runtime(
        _Transport(
            version="0.1.5",
            capabilities={"active": ["admission_reservations", "read_model_list_ready"]},
        ),
        options=options,
    )
    await active.start()
    assert active.state is TaskqRuntimeState.RUNNING
    await active.stop()

    followup_contract = _runtime(
        _Transport(
            version="0.2.0",
            capabilities={
                "active": [
                    "admission_reservations",
                    "followups",
                    "read_model_list_ready",
                ]
            },
        ),
        options=options,
    )
    await followup_contract.start()
    assert followup_contract.state is TaskqRuntimeState.RUNNING
    await followup_contract.stop()

    workflow_contract = _runtime(
        _Transport(
            version="0.2.1",
            capabilities={
                "active": [
                    "admission_reservations",
                    "dependencies_workflows",
                    "followups",
                    "read_model_list_ready",
                ]
            },
        ),
        options=options,
    )
    await workflow_contract.start()
    assert workflow_contract.state is TaskqRuntimeState.RUNNING
    await workflow_contract.stop()

    schedule_contract = _runtime(
        _Transport(
            version="0.2.2",
            capabilities={
                "active": [
                    "admission_reservations",
                    "dependencies_workflows",
                    "followups",
                    "read_model_list_ready",
                    "schedules",
                ]
            },
        ),
        options=options,
    )
    await schedule_contract.start()
    assert schedule_contract.state is TaskqRuntimeState.RUNNING
    await schedule_contract.stop()


async def test_workflow_runtime_requires_exact_contract_and_capability() -> None:
    options = TaskqRuntimeOptions(
        housekeeper_enabled=False,
        long_poll_listener_enabled=False,
        workflow_enabled=True,
    )
    wrong_version = _runtime(_Transport(version="0.2.0"), options=options)
    with pytest.raises(TaskqVersionError):
        await wrong_version.start()

    missing = _runtime(_Transport(version="0.2.1"), options=options)
    with pytest.raises(TaskqCapabilityError):
        await missing.start()

    active = _runtime(
        _Transport(
            version="0.2.1",
            capabilities={
                "active": [
                    "admission_reservations",
                    "dependencies_workflows",
                    "followups",
                    "read_model_list_ready",
                ]
            },
        ),
        options=options,
    )
    await active.start()
    assert active.state is TaskqRuntimeState.RUNNING
    await active.stop()

    additive = _runtime(
        _Transport(
            version="0.2.2",
            capabilities={
                "active": [
                    "admission_reservations",
                    "dependencies_workflows",
                    "followups",
                    "read_model_list_ready",
                    "schedules",
                ]
            },
        ),
        options=options,
    )
    await additive.start()
    assert additive.state is TaskqRuntimeState.RUNNING
    await additive.stop()


def test_schedule_surface_is_explicitly_disabled_by_default() -> None:
    assert TaskqRuntimeOptions().schedule_enabled is False
    assert "schedule_operator" not in TaskqFacadeTransports.__dataclass_fields__


async def test_schedule_runtime_requires_exact_gate_and_settles_each_claim() -> None:
    options = TaskqRuntimeOptions(
        housekeeper_enabled=True,
        housekeeper_interval=60,
        long_poll_listener_enabled=False,
        schedule_enabled=True,
    )
    wrong = _runtime(_Transport(version="0.2.1"), options=options)
    with pytest.raises(TaskqVersionError):
        await wrong.start()

    missing = _runtime(_Transport(version="0.2.2"), options=options)
    with pytest.raises(TaskqCapabilityError):
        await missing.start()

    now = datetime(2026, 1, 1, tzinfo=UTC)
    transport = _Transport(
        version="0.2.2",
        capabilities={"active": ["schedules"]},
    )
    good = ScheduleClaim(
        schedule_id=uuid4(),
        name="tests.good",
        definition_version=1,
        as_of=now,
        target={"kind": "job", "queue": "tests", "job_type": "tests.good"},
        recurrence={"kind": "interval", "interval_seconds": 60},
        catchup_policy="fire_all",
        max_catchup=1,
        initialized=False,
        next_fire_at=now,
        token=uuid4(),
        lease_seconds=60,
    )
    invalid = good.model_copy(
        update={
            "schedule_id": uuid4(),
            "name": "tests.invalid",
            "recurrence": {"kind": "unknown"},
            "token": uuid4(),
        }
    )
    transport.schedule_batches.append(
        ScheduleClaimResult(state="claimed", schedules=(good, invalid))
    )
    runtime = _runtime(transport, options=options)
    await runtime.start()
    assert len(transport.schedule_fires) == 1
    assert transport.schedule_fires[0][0:3] == (
        good.schedule_id,
        good.token,
        good.definition_version,
    )
    assert len(transport.schedule_errors) == 1
    assert transport.schedule_errors[0][0:3] == (
        invalid.schedule_id,
        invalid.token,
        invalid.definition_version,
    )
    assert transport.schedule_errors[0][3] == "calendar:TaskqValidationError"
    await runtime.stop()


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
