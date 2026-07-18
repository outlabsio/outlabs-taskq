"""S2-05B capacity-safe claim admission, presence, and shutdown."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from taskq import (
    Task,
    TaskRegistry,
    WorkerOptions,
    WorkerService,
    WorkerServiceOptions,
    WorkerServiceState,
)
from taskq.errors import TaskqConflictError, TaskqUnavailableError
from taskq.protocol import ClaimResult, ClaimState
from tests.test_s2_worker_service_poll import Input, Output, _claim, _registry, _spin_until
from tests.worker_support import ManualClock, ScriptedTransport


async def test_first_presence_precedes_first_claim_and_carries_safe_metadata() -> None:
    transport = ScriptedTransport()
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    await service.start()
    await _spin_until(lambda: any(call.command == "claim" for call in transport.calls))
    assert [call.command for call in transport.calls[:2]] == ["worker_heartbeat", "claim"]
    arguments = transport.calls[0].arguments
    assert arguments["queues"] == ("alpha",)
    assert arguments["meta"] == {
        "concurrency": 1,
        "sync_workers": 1,
        "batch": 1,
        "listen": False,
    }
    assert "attempt_id" not in repr(transport.calls[0])
    await service.aclose()


async def test_initial_remote_shutdown_never_claims() -> None:
    transport = ScriptedTransport()
    transport.script("worker_heartbeat", True)
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-drained",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    await service.start()
    await _spin_until(lambda: service.stopped)
    assert [call.command for call in transport.calls] == ["worker_heartbeat"]


async def test_presence_failure_degrades_then_success_recovers() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("worker_heartbeat", TaskqUnavailableError(), False)
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(
            queues=("alpha",), listen=False, poll_interval=30, presence_interval=5
        ),
        clock=clock,
    )
    await service.start()
    assert service.state is WorkerServiceState.DEGRADED
    await _spin_until(lambda: clock.sleeping >= 2)
    clock.advance(5)
    await _spin_until(
        lambda: [call.command for call in transport.calls].count("worker_heartbeat") == 2
    )
    assert service.ready
    assert service.snapshot().presence_failures == 1
    await service.aclose()


async def test_remote_shutdown_stops_before_another_claim_boundary() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("worker_heartbeat", False, True)
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(
            queues=("alpha",), listen=False, poll_interval=30, presence_interval=5
        ),
        clock=clock,
    )
    await service.start()
    await _spin_until(lambda: clock.sleeping >= 2)
    clock.advance(5)
    await _spin_until(lambda: service.stopped)
    shutdown_index = next(
        index
        for index, call in enumerate(transport.calls)
        if call.command == "worker_heartbeat" and index > 0
    )
    assert not any(call.command == "claim" for call in transport.calls[shutdown_index + 1 :])


class ClaimBarrierTransport(ScriptedTransport):
    def __init__(self, result: ClaimResult) -> None:
        super().__init__()
        self.result = result
        self.entered = asyncio.Event()
        self.response_allowed = asyncio.Event()

    async def claim(self, *args: Any, **kwargs: Any) -> ClaimResult:
        self.entered.set()
        await self.response_allowed.wait()
        await super().claim(*args, **kwargs)
        return self.result


async def test_stop_waits_for_claim_response_then_supervises_returned_job() -> None:
    transport = ClaimBarrierTransport(
        ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),))
    )
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    await service.start()
    await transport.entered.wait()
    stopping = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    transport.response_allowed.set()
    await stopping
    assert [call.command for call in transport.calls].count("claim") == 1
    assert any(call.command == "complete" for call in transport.calls)
    assert service.snapshot().claimed_jobs == 1


async def test_hard_stop_cannot_close_intake_before_claim_admission() -> None:
    transport = ClaimBarrierTransport(
        ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),))
    )
    blocker = asyncio.Event()

    async def never_finishes(payload: Input) -> Output:
        await blocker.wait()
        return Output(doubled=payload.value * 2)

    registry = TaskRegistry(
        (
            Task(
                name="alpha.work",
                queue="alpha",
                input_model=Input,
                output_model=Output,
                handler=never_finishes,
            ),
        )
    )
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        registry,
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        supervisor_options=WorkerOptions(concurrency=1),
        clock=ManualClock(),
    )
    await service.start()
    await transport.entered.wait()
    graceful = asyncio.create_task(service.stop())
    hard = asyncio.create_task(service.stop(cancel=True))
    await asyncio.sleep(0)
    assert not graceful.done() and not hard.done()
    transport.response_allowed.set()
    await asyncio.gather(graceful, hard)
    assert service.snapshot().claimed_jobs == 1
    assert not service.snapshot().fatal
    assert any(call.command == "release" for call in transport.calls)
    assert service.snapshot().active_slots == 0
    assert service.stopped


async def test_fatal_job_report_auto_stops_service() -> None:
    transport = ScriptedTransport()
    transport.script("claim", ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),)))
    transport.script("complete", TaskqConflictError())
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    await service.start()
    await _spin_until(lambda: service.stopped)
    assert service.snapshot().fatal
    assert not service.ready


async def test_external_run_cancellation_cleans_up_and_reraises() -> None:
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    running = asyncio.create_task(service.run())
    await _spin_until(lambda: service.state is WorkerServiceState.RUNNING)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert running.cancelled()
    assert service.stopped
