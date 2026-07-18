"""S2-04B monotonic heartbeat and fenced per-job supervision."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

from pydantic import BaseModel

from taskq import (
    CancellationReason,
    JobContext,
    JobRunOutcome,
    JobRunState,
    Task,
    TaskRegistry,
    WorkerOptions,
    WorkerSupervisor,
)
from taskq.errors import TaskqUnavailableError, TaskqValidationError
from taskq.protocol import ClaimedJob, HeartbeatResult
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _claim(*, lease_seconds: int = 15, job_type: str = "math.wait") -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue="worker",
        job_type=job_type,
        priority=100,
        payload={"value": 2},
        headers={},
        progress=None,
        attempt_id=uuid4(),
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        lease_expires_at=datetime.now(UTC) - timedelta(days=1),
        lease_seconds=lease_seconds,
    )


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 30) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


def _waiting_handler(release: asyncio.Event) -> Callable[[JobContext, Input], object]:
    async def handler(ctx: JobContext, payload: Input) -> Output:
        ctx.raise_if_cancelled()
        await release.wait()
        return Output(doubled=payload.value * 2)

    return handler


def _supervisor(
    handler: object,
    transport: ScriptedTransport,
    clock: ManualClock,
    *,
    grace: float = 30,
) -> WorkerSupervisor:
    task = Task(
        name="math.wait",
        queue="worker",
        input_model=Input,
        output_model=Output,
        handler=handler,  # type: ignore[arg-type]
    )
    return WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        TaskRegistry([task]),
        "worker-1",
        options=WorkerOptions(cancel_grace_seconds=grace),
        clock=clock,
    )


async def test_heartbeat_uses_returned_duration_not_absolute_expiry() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    release = asyncio.Event()
    claim = _claim(lease_seconds=15)
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(claim))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(4.999)
    await asyncio.sleep(0)
    assert transport.calls == []
    clock.advance(0.001)
    await _spin_until(lambda: len(transport.calls) == 1)
    assert transport.calls[0].command == "heartbeat"
    assert transport.calls[0].arguments["lease_seconds"] == 15
    release.set()
    report = await running
    assert report.state is JobRunState.SETTLED
    assert [call.command for call in transport.calls] == ["heartbeat", "complete"]
    await supervisor.aclose()


async def test_checkpoint_is_flushed_once_and_acknowledged_after_success() -> None:
    async def checkpointing(ctx: JobContext, payload: Input) -> Output:
        await ctx.checkpoint({"cursor": payload.value})
        await release.wait()
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    release = asyncio.Event()
    claim = _claim()
    supervisor = _supervisor(checkpointing, transport, clock)
    running = asyncio.create_task(supervisor.run_job(claim))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _spin_until(lambda: len(transport.calls) == 1)
    assert transport.calls[0].arguments["progress"] == {"cursor": 2}
    release.set()
    await running
    await supervisor.aclose()


async def test_two_heartbeat_failures_recover_and_retain_handler() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat",
        TaskqUnavailableError(),
        TaskqUnavailableError(),
        HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None),
    )
    release = asyncio.Event()
    claim = _claim()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(claim))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _spin_until(lambda: len(transport.calls) == 1 and clock.sleeping == 1)
    clock.advance(0.25)
    await _spin_until(lambda: len(transport.calls) == 2 and clock.sleeping == 1)
    clock.advance(0.5)
    await _spin_until(lambda: len(transport.calls) == 3)
    assert not running.done()
    release.set()
    assert (await running).state is JobRunState.SETTLED
    await supervisor.aclose()


async def test_third_heartbeat_failure_cancels_async_and_suppresses_settle() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat",
        TaskqUnavailableError(),
        TaskqUnavailableError(),
        TaskqUnavailableError(),
    )
    release = asyncio.Event()
    claim = _claim()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(claim))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _spin_until(lambda: len(transport.calls) == 1 and clock.sleeping == 1)
    clock.advance(0.25)
    await _spin_until(lambda: len(transport.calls) == 2 and clock.sleeping == 1)
    clock.advance(0.5)
    report = await running
    assert report.state is JobRunState.OWNERSHIP_LOST
    assert report.cancellation_reason is CancellationReason.LEASE_LOST
    assert [call.command for call in transport.calls] == ["heartbeat"] * 3
    await supervisor.aclose()


async def test_typed_heartbeat_loss_cancels_without_settlement() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat", HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
    )
    release = asyncio.Event()
    claim = _claim()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(claim))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    report = await running
    assert report.outcome is JobRunOutcome.OWNERSHIP_LOST
    assert [call.command for call in transport.calls] == ["heartbeat"]
    await supervisor.aclose()


async def test_non_retryable_heartbeat_error_is_runtime_failure() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", TaskqValidationError())
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim()))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    report = await running
    assert report.state is JobRunState.RUNTIME_FAILED
    assert report.outcome is JobRunOutcome.RUNTIME_ERROR
    assert [call.command for call in transport.calls] == ["heartbeat"]
    await supervisor.aclose()


async def test_operator_cancel_hard_cancels_after_grace_and_acks_fenced() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat", HeartbeatResult(ok=True, cancel_requested=True, lease_expires_at=None)
    )
    release = asyncio.Event()
    claim = _claim()
    supervisor = _supervisor(_waiting_handler(release), transport, clock, grace=2)
    running = asyncio.create_task(supervisor.run_job(claim))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _spin_until(lambda: len(transport.calls) == 1 and clock.sleeping == 2)
    clock.advance(2)
    report = await running
    assert report.cancellation_reason is CancellationReason.OPERATOR
    assert report.settlement_command == "cancel_running"
    assert [call.command for call in transport.calls] == ["heartbeat", "cancel_running"]
    await supervisor.aclose()


async def test_missing_handler_releases_without_starting_heartbeat() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    supervisor = WorkerSupervisor(
        transport,
        TaskRegistry(),
        "worker-1",
        clock=clock,  # type: ignore[arg-type]
    )
    report = await supervisor.run_job(_claim(job_type="missing"))
    assert report.outcome is JobRunOutcome.NO_HANDLER
    assert [call.command for call in transport.calls] == ["release"]
    assert clock.sleeping == 0
    await supervisor.aclose()


async def test_sync_handler_observes_lease_loss_and_is_never_settled() -> None:
    started = Event()
    release = Event()
    observed = Event()

    def sync_handler(ctx: JobContext, payload: Input) -> Output:
        started.set()
        release.wait()
        if ctx.should_cancel():
            observed.set()
            ctx.raise_if_cancelled()
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat", HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
    )
    supervisor = _supervisor(sync_handler, transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim()))
    assert await asyncio.to_thread(started.wait, 1)
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _spin_until(lambda: len(transport.calls) == 1)
    assert supervisor.requires_process_exit
    release.set()
    report = await running
    assert observed.is_set()
    assert report.state is JobRunState.ABANDONED_SYNC
    assert report.requires_process_exit
    assert not supervisor.requires_process_exit
    assert [call.command for call in transport.calls] == ["heartbeat"]
    await supervisor.aclose()


async def test_construction_and_close_create_no_background_tasks() -> None:
    before = asyncio.all_tasks()
    supervisor = WorkerSupervisor(
        ScriptedTransport(),
        TaskRegistry(),
        "worker-1",  # type: ignore[arg-type]
    )
    assert asyncio.all_tasks() == before
    await supervisor.aclose()
    assert asyncio.all_tasks() == before
