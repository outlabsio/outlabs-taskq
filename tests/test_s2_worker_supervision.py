"""S2-04B monotonic heartbeat and fenced per-job supervision."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from typing import Any
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from pydantic import BaseModel

from taskq import (
    CancellationReason,
    JobContext,
    JobRunReport,
    JobRunOutcome,
    JobRunState,
    Task,
    TaskRegistry,
    WorkerOptions,
    WorkerSupervisor,
)
from taskq.errors import (
    TaskqBackpressureError,
    TaskqInternalError,
    TaskqProtocolError,
    TaskqUnavailableError,
    TaskqValidationError,
)
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


async def _spin_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        # Yield real scheduler time as well as an event-loop turn: several
        # lifecycle predicates are cleared by the managed thread callback.
        await asyncio.sleep(0.001)
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


async def test_lease_loss_marks_managed_blocking_section_for_process_exit() -> None:
    started = Event()
    release = Event()

    async def handler(ctx: JobContext, payload: Input) -> Output:
        def blocking_work() -> int:
            started.set()
            release.wait()
            return payload.value * 2

        return Output(doubled=await ctx.run_sync(blocking_work))

    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat",
        HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None),
    )
    supervisor = _supervisor(handler, transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim()))
    assert await asyncio.to_thread(started.wait, 1)
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    report = await running

    assert report.state is JobRunState.ABANDONED_SYNC
    assert report.outcome is JobRunOutcome.OWNERSHIP_LOST
    assert report.requires_process_exit is True
    assert supervisor.requires_process_exit is True
    release.set()
    await _spin_until(lambda: not supervisor.requires_process_exit)
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


async def _advance_until_done(
    clock: ManualClock, running: asyncio.Task[JobRunReport], *, limit: float, step: float = 0.25
) -> float:
    while not running.done() and clock.monotonic() < limit:
        clock.advance(step)
        for _ in range(5):
            await asyncio.sleep(0)
    return clock.monotonic()


def _ownership_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and record.message.startswith("job.ownership_lost")
    ]


async def test_retryable_heartbeat_outage_within_lease_budget_recovers() -> None:
    # Regression (2026-11-02): three fast retryable failures used to drop a
    # 3600 s lease after <1 s, orphaning the job and its concurrency slot.
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat",
        *(TaskqUnavailableError() for _ in range(20)),
        HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None),
    )
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(30)
    await _advance_until_done(clock, running, limit=600, step=1)
    assert len(transport.calls) > 21
    assert not running.done()
    release.set()
    assert (await running).state is JobRunState.SETTLED
    await supervisor.aclose()


async def test_retryable_heartbeat_failures_past_budget_drop_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", *(TaskqUnavailableError() for _ in range(100)))
    release = asyncio.Event()
    claim = _claim(lease_seconds=15)
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(claim))
        await _spin_until(lambda: clock.sleeping == 1)
        dropped_at = await _advance_until_done(clock, running, limit=30)
        report = await running
    # budget = 15 - min(60, 15 / 4) = 11.25 s after the claim baseline.
    assert 11.25 <= dropped_at < 15
    assert report.state is JobRunState.OWNERSHIP_LOST
    assert report.cancellation_reason is CancellationReason.LEASE_LOST
    assert {call.command for call in transport.calls} == {"heartbeat"}
    [warning] = _ownership_warnings(caplog)
    assert f"job_id={claim.job_id}" in warning.message
    assert f"attempt_id={claim.attempt_id}" in warning.message
    assert "reason=heartbeat_unavailable" in warning.message
    assert warning.__dict__["attempt_id"] == str(claim.attempt_id)
    await supervisor.aclose()


async def test_hung_first_heartbeat_is_bounded_by_lease_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    never = asyncio.Event()

    async def hung_heartbeat(*args: object, **kwargs: object) -> HeartbeatResult:
        await never.wait()
        raise AssertionError("unreachable")

    transport.heartbeat = hung_heartbeat  # type: ignore[method-assign]
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
        await _spin_until(lambda: clock.sleeping == 1)
        dropped_at = await _advance_until_done(clock, running, limit=30)
        try:
            assert running.done(), "in-flight heartbeat was not bounded by the lease budget"
        finally:
            release.set()  # an unbounded worker must fail this test, not hang it
        report = await asyncio.wait_for(running, timeout=1)
    assert 11.25 <= dropped_at < 15
    assert report.state is JobRunState.OWNERSHIP_LOST
    [warning] = _ownership_warnings(caplog)
    assert "reason=heartbeat_timeout failures=0" in warning.message
    await supervisor.aclose()


async def test_claim_reference_point_starts_the_lease_budget() -> None:
    clock = ManualClock()
    clock.advance(100)
    transport = ScriptedTransport()
    transport.script("heartbeat", *(TaskqUnavailableError() for _ in range(100)))
    supervisor = _supervisor(_waiting_handler(asyncio.Event()), transport, clock)
    # Claim sent 4 s before the job started: the 11.25 s budget (15 - 15/4)
    # ends at t=107.25, so the first heartbeat is pulled in to half the
    # remaining budget (t=103.625) and the drop lands at t=107.25, not 111.25.
    running = asyncio.create_task(
        supervisor.run_job(_claim(lease_seconds=15), claimed_at=clock.monotonic() - 4)
    )
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(3.625)
    await _spin_until(lambda: len(transport.calls) == 1)
    dropped_at = await _advance_until_done(clock, running, limit=130)
    report = await asyncio.wait_for(running, timeout=1)
    assert 107.25 <= dropped_at < 108
    assert report.state is JobRunState.OWNERSHIP_LOST
    await supervisor.aclose()


async def test_mid_outage_success_resets_budget_and_logs_once_per_outage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat",
        TaskqUnavailableError(),
        TaskqUnavailableError(),
        HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None),
        *(TaskqUnavailableError() for _ in range(100)),
    )
    supervisor = _supervisor(_waiting_handler(asyncio.Event()), transport, clock)
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
        await _spin_until(lambda: clock.sleeping == 1)
        dropped_at = await _advance_until_done(clock, running, limit=40)
        report = await asyncio.wait_for(running, timeout=1)
    # Success sent at t=5.75 moves the 11.25 s budget to t=17.
    assert 17 <= dropped_at < 5.75 + 15
    assert report.state is JobRunState.OWNERSHIP_LOST
    messages = [record.message.split(" ")[0] for record in caplog.records]
    assert messages == [
        "job.heartbeat_failing",
        "job.heartbeat_recovered",
        "job.heartbeat_failing",
        "job.ownership_lost",
    ]
    assert "failures=2" in caplog.records[1].message
    assert caplog.records[0].levelno == logging.WARNING
    assert caplog.records[1].levelno == logging.INFO
    await supervisor.aclose()


async def test_settle_outage_while_owned_retries_until_the_api_returns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("complete", *(TaskqUnavailableError() for _ in range(12)))
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    claim = _claim(lease_seconds=3600)
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(claim))
        await _advance_until_done(clock, running, limit=600)
        report = await asyncio.wait_for(running, timeout=1)
    assert report.state is JobRunState.SETTLED
    assert not report.fatal
    assert [call.command for call in transport.calls].count("complete") == 13
    events = [r.message.split(" ")[0] for r in caplog.records if r.message.startswith("job.settle")]
    assert events == ["job.settle_failing", "job.settle_recovered"]
    recovered = next(r for r in caplog.records if r.message.startswith("job.settle_recovered"))
    assert f"attempt_id={claim.attempt_id}" in recovered.message
    await supervisor.aclose()


async def test_settle_outage_past_lease_budget_is_non_fatal_ownership_loss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", *(TaskqUnavailableError() for _ in range(500)))
    transport.script("complete", *(TaskqUnavailableError() for _ in range(500)))
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        await _spin_until(lambda: len(transport.calls) == 1)
        release.set()
        dropped_at = await _advance_until_done(clock, running, limit=30)
        report = await asyncio.wait_for(running, timeout=1)
    assert 11.25 <= dropped_at < 15
    assert report.state is JobRunState.OWNERSHIP_LOST
    assert not report.fatal
    assert "complete" in [call.command for call in transport.calls]
    assert len(_ownership_warnings(caplog)) == 1
    await supervisor.aclose()


async def test_settle_outage_while_draining_keeps_the_attempt_cap() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("complete", *(TaskqUnavailableError() for _ in range(500)))
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
    await _spin_until(lambda: clock.sleeping == 1)
    stopping = asyncio.create_task(supervisor.stop())
    release.set()
    await _advance_until_done(clock, running, limit=600)
    report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    assert report.fatal
    assert [call.command for call in transport.calls].count("complete") == 5
    await stopping
    await supervisor.aclose()


@pytest.mark.parametrize("lease", [15, 30, 60, 90])
async def test_long_poll_claim_with_healthy_heartbeats_never_drops(lease: int) -> None:
    # Regression: an HTTP claim long-polls up to 25 s before the server commits,
    # so the send-time budget may be spent before the job starts. A healthy job
    # must confirm with a heartbeat instead of dropping on the clock alone.
    clock = ManualClock()
    clock.advance(1000)
    transport = ScriptedTransport()
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(
        supervisor.run_job(_claim(lease_seconds=lease), claimed_at=clock.monotonic() - 25)
    )
    await _spin_until(lambda: clock.sleeping == 1)
    await _advance_until_done(clock, running, limit=1000 + 3 * lease)
    assert not running.done()
    release.set()
    assert (await asyncio.wait_for(running, timeout=1)).state is JobRunState.SETTLED
    await supervisor.aclose()


async def test_spent_claim_budget_drops_when_the_confirming_heartbeat_fails() -> None:
    clock = ManualClock()
    clock.advance(1000)
    transport = ScriptedTransport()
    transport.script("heartbeat", *(TaskqUnavailableError() for _ in range(10)))
    supervisor = _supervisor(_waiting_handler(asyncio.Event()), transport, clock)
    running = asyncio.create_task(
        supervisor.run_job(_claim(lease_seconds=15), claimed_at=clock.monotonic() - 25)
    )
    await _advance_until_done(clock, running, limit=1010)
    report = await asyncio.wait_for(running, timeout=1)
    assert report.state is JobRunState.OWNERSHIP_LOST
    assert [call.command for call in transport.calls] == ["heartbeat"]


async def test_transport_swallowing_the_budget_cancel_still_drops() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    calls = 0

    async def swallowing_heartbeat(*args: object, **kwargs: object) -> HeartbeatResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TaskqUnavailableError()
        if calls > 2:
            await asyncio.Event().wait()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if clock.monotonic() >= 30:  # teardown cancel: let it through
                raise
            pass  # misbehaving transport: swallow the budget cancel, report success
        return HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None)

    transport.heartbeat = swallowing_heartbeat  # type: ignore[method-assign]
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
    await _spin_until(lambda: clock.sleeping == 1)
    dropped_at = await _advance_until_done(clock, running, limit=60)
    try:
        assert running.done(), "swallowed budget cancel left the handler running"
    finally:
        release.set()
    report = await asyncio.wait_for(running, timeout=1)
    assert 11.25 <= dropped_at < 15
    assert report.state is JobRunState.OWNERSHIP_LOST
    await supervisor.aclose()


async def test_raw_timeout_and_connection_errors_are_retryable_heartbeat_failures() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat",
        TimeoutError(),
        ConnectionError(),
        HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None),
    )
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _advance_until_done(clock, running, limit=8)
    assert not running.done()
    assert len(transport.calls) == 3
    release.set()
    assert (await asyncio.wait_for(running, timeout=1)).state is JobRunState.SETTLED
    await supervisor.aclose()


async def test_crashed_heartbeat_loop_drops_ownership(caplog: pytest.LogCaptureFixture) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", object())  # no .ok: the loop itself raises
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim()))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        await _spin_until(running.done)
        release.set()
        report = await asyncio.wait_for(running, timeout=1)
    assert report.state is JobRunState.RUNTIME_FAILED
    assert report.fatal
    [warning] = _ownership_warnings(caplog)
    assert "reason=heartbeat_crashed AttributeError" in warning.message
    await supervisor.aclose()


async def test_settle_only_outage_with_healthy_heartbeats_is_bounded_by_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("complete", *(TaskqUnavailableError() for _ in range(10_000)))
    release = asyncio.Event()
    release.set()

    class _UpperRng(random.Random):
        def uniform(self, a: float, b: float) -> float:
            return b

    supervisor = WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        TaskRegistry(
            [
                Task(
                    name="math.wait",
                    queue="worker",
                    input_model=Input,
                    output_model=Output,
                    handler=_waiting_handler(release),
                )
            ]  # type: ignore[arg-type]
        ),
        "worker-1",
        clock=clock,
        rng=_UpperRng(),
    )
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=60)))
        ended_at = await _advance_until_done(clock, running, limit=600)
        report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    assert report.fatal
    # Delays 0.25, 0.5, 1, 2 (settle cap), then 4, 8, 16, 20 (heartbeat-interval
    # cap): attempt 9 at t=51.75 is past the 45 s budget. A 5 s cap would need 13+.
    assert [call.command for call in transport.calls].count("complete") == 9
    assert 51.75 <= ended_at < 60
    events = [r.message.split(" ")[0] for r in caplog.records if r.message.startswith("job.settle")]
    assert events == ["job.settle_failing", "job.settle_exhausted"]
    await supervisor.aclose()


def _slow_ok_heartbeat(clock: ManualClock, rtt: float) -> Callable[..., object]:
    async def heartbeat(*args: object, **kwargs: object) -> HeartbeatResult:
        await clock.sleep(rtt)
        return HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None)

    return heartbeat


@pytest.mark.parametrize(
    ("lease", "age"), [(15, 10.0), (15, 10.5), (15, 11.0), (30, 21.0), (30, 21.5), (30, 22.0)]
)
async def test_near_spent_claim_budget_gives_first_heartbeat_an_interval(
    lease: int, age: float
) -> None:
    clock = ManualClock()
    clock.advance(1000)
    transport = ScriptedTransport()
    transport.heartbeat = _slow_ok_heartbeat(clock, 0.75)  # type: ignore[method-assign]
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(
        supervisor.run_job(_claim(lease_seconds=lease), claimed_at=clock.monotonic() - age)
    )
    await _advance_until_done(clock, running, limit=1000 + 3 * lease)
    assert not running.done()
    release.set()
    assert (await asyncio.wait_for(running, timeout=1)).state is JobRunState.SETTLED
    await supervisor.aclose()


async def test_hung_confirming_heartbeat_on_spent_claim_drops_after_one_interval() -> None:
    clock = ManualClock()
    clock.advance(1000)
    transport = ScriptedTransport()
    never = asyncio.Event()

    async def hung_heartbeat(*args: object, **kwargs: object) -> HeartbeatResult:
        await never.wait()
        raise AssertionError("unreachable")

    transport.heartbeat = hung_heartbeat  # type: ignore[method-assign]
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(
        supervisor.run_job(_claim(lease_seconds=15), claimed_at=clock.monotonic() - 11)
    )
    dropped_at = await _advance_until_done(clock, running, limit=1030)
    try:
        assert running.done(), "confirming heartbeat was not bounded"
    finally:
        release.set()
    report = await asyncio.wait_for(running, timeout=1)
    # first beat after half the 0.25 s left, then bounded by the 5 s interval.
    assert 1005 <= dropped_at < 1005.75
    assert report.state is JobRunState.OWNERSHIP_LOST
    await supervisor.aclose()


@pytest.mark.parametrize(("lease", "rtt"), [(15, 5.0), (60, 20.0)])
async def test_steady_slow_heartbeats_are_paced_from_the_send(lease: int, rtt: float) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.heartbeat = _slow_ok_heartbeat(clock, rtt)  # type: ignore[method-assign]
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=lease)))
    await _advance_until_done(clock, running, limit=6 * lease)
    assert not running.done()
    release.set()
    assert (await asyncio.wait_for(running, timeout=1)).state is JobRunState.SETTLED
    await supervisor.aclose()


async def test_lost_heartbeat_during_settle_is_not_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat", HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
    )
    complete = transport.complete

    async def slow_complete(*args: Any, **kwargs: Any) -> object:
        await clock.sleep(10)  # committed server-side while the heartbeat races it
        return await complete(*args, **kwargs)

    transport.complete = slow_complete  # type: ignore[method-assign]
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
        await _advance_until_done(clock, running, limit=30)
        report = await asyncio.wait_for(running, timeout=1)
    assert report.state is JobRunState.SETTLED
    lost = [r for r in caplog.records if r.message.startswith("job.ownership_lost")]
    assert [r.levelno for r in lost] == [logging.INFO]
    await supervisor.aclose()


async def test_stop_during_post_cap_settle_backoff_wakes_and_logs_exhausted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("complete", *(TaskqUnavailableError() for _ in range(10_000)))
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
        await _advance_until_done(clock, running, limit=60)
        assert any(r.message.startswith("job.settle_failing") for r in caplog.records)
        stopping = asyncio.create_task(supervisor.stop())
        await _spin_until(running.done)  # no clock advance: the stop wakes the backoff
        report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    events = [r.message.split(" ")[0] for r in caplog.records if r.message.startswith("job.settle")]
    assert events == ["job.settle_failing", "job.settle_exhausted"]
    await stopping
    await supervisor.aclose()


async def test_backpressure_horizon_past_cap_logs_settle_exhausted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        *(TaskqUnavailableError() for _ in range(6)),
        *(TaskqBackpressureError(retry_after_seconds=10) for _ in range(100)),
    )
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
        await _advance_until_done(clock, running, limit=600)
        report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    events = [r.message.split(" ")[0] for r in caplog.records if r.message.startswith("job.settle")]
    assert events == ["job.settle_failing", "job.settle_exhausted"]
    await supervisor.aclose()


async def test_stray_cancelled_error_from_transport_drops_ownership(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", asyncio.CancelledError())
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=15)))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        await _advance_until_done(clock, running, limit=30)
        try:
            assert running.done(), "stray CancelledError left the handler running"
        finally:
            release.set()  # an unbounded worker must fail this test, not hang it
        report = await asyncio.wait_for(running, timeout=1)
    assert report.state is JobRunState.OWNERSHIP_LOST
    [warning] = _ownership_warnings(caplog)
    assert "reason=heartbeat_cancelled" in warning.message
    await supervisor.aclose()


async def test_mixed_503_and_500_settle_outage_while_owned_settles() -> None:
    # Server DB stalls (pool timeout, statement_timeout) surface as TQ500.
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        *(TaskqInternalError() if n % 2 else TaskqUnavailableError() for n in range(12)),
    )
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
    await _advance_until_done(clock, running, limit=600)
    report = await asyncio.wait_for(running, timeout=1)
    assert report.state is JobRunState.SETTLED
    assert not report.fatal
    assert [call.command for call in transport.calls].count("complete") == 13
    await supervisor.aclose()


async def test_client_protocol_error_on_owned_settle_keeps_the_attempt_cap() -> None:
    # A proxy 413 or a malformed envelope fails the same way on every resend.
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        *(
            TaskqProtocolError(details={"reason": "missing_or_invalid_protocol_header"})
            for _ in range(500)
        ),
    )
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
    await _advance_until_done(clock, running, limit=600)
    report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    assert report.fatal
    assert [call.command for call in transport.calls].count("complete") == 5
    await supervisor.aclose()


async def test_client_protocol_error_during_post_cap_settle_outage_ends_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        *(TaskqUnavailableError() for _ in range(7)),
        *(TaskqProtocolError(details={"reason": "invalid_json_envelope"}) for _ in range(500)),
    )
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
        await _advance_until_done(clock, running, limit=600)
        report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    assert [call.command for call in transport.calls].count("complete") == 8
    events = [r.message.split(" ")[0] for r in caplog.records if r.message.startswith("job.settle")]
    assert events == ["job.settle_failing", "job.settle_exhausted"]
    await supervisor.aclose()


async def test_backpressure_after_stop_during_post_cap_settle_keeps_its_backoff() -> None:
    # The stop event stays set once stopping; waking on it again would spin on TQ429.
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        *(TaskqUnavailableError() for _ in range(6)),
        *(TaskqBackpressureError(retry_after_seconds=10) for _ in range(1000)),
    )
    release = asyncio.Event()
    release.set()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)

    def completes() -> int:
        return [call.command for call in transport.calls].count("complete")

    running = asyncio.create_task(supervisor.run_job(_claim(lease_seconds=3600)))
    while completes() < 6 and clock.monotonic() < 600:
        clock.advance(0.25)
        for _ in range(5):
            await asyncio.sleep(0)
    for _ in range(20):
        await asyncio.sleep(0)
    assert completes() == 6  # past the cap, in a post-cap backoff
    stopping = asyncio.create_task(supervisor.stop())
    for _ in range(50):
        await asyncio.sleep(0)
    assert completes() == 7  # the stop woke the backoff once; TQ429 then backs off
    await _advance_until_done(clock, running, limit=clock.monotonic() + 600)
    report = await asyncio.wait_for(running, timeout=1)
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    assert completes() < 30
    await stopping
    await supervisor.aclose()


async def test_typed_heartbeat_loss_cancels_without_settlement(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat", HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
    )
    release = asyncio.Event()
    claim = _claim()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(claim))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        report = await running
    assert report.outcome is JobRunOutcome.OWNERSHIP_LOST
    assert [call.command for call in transport.calls] == ["heartbeat"]
    [warning] = _ownership_warnings(caplog)
    assert f"job_id={claim.job_id}" in warning.message
    assert "reason=heartbeat_lost" in warning.message
    await supervisor.aclose()


async def test_non_retryable_heartbeat_error_is_runtime_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", TaskqValidationError())
    release = asyncio.Event()
    claim = _claim(lease_seconds=3600)
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(claim))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(30)
        report = await running
    [warning] = _ownership_warnings(caplog)
    assert f"attempt_id={claim.attempt_id}" in warning.message
    assert "reason=heartbeat_error code=" in warning.message
    assert report.state is JobRunState.RUNTIME_FAILED
    assert report.outcome is JobRunOutcome.RUNTIME_ERROR
    assert [call.command for call in transport.calls] == ["heartbeat"]
    await supervisor.aclose()


async def test_unclassified_heartbeat_exception_fails_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("heartbeat", RuntimeError("broken transport adapter"))
    release = asyncio.Event()
    supervisor = _supervisor(_waiting_handler(release), transport, clock)
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        running = asyncio.create_task(supervisor.run_job(_claim()))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        report = await running
    [warning] = _ownership_warnings(caplog)
    assert "reason=heartbeat_exception RuntimeError" in warning.message
    assert report.state is JobRunState.RUNTIME_FAILED
    assert report.outcome is JobRunOutcome.RUNTIME_ERROR
    assert report.fatal
    assert [call.command for call in transport.calls] == ["heartbeat"]
    await supervisor.aclose()


async def test_supervisor_applies_explicit_effect_request_and_response_bounds() -> None:
    observed: list[int] = []

    class _Reporter:
        async def report_effect(
            self, _attempt: object, request: dict[str, object]
        ) -> dict[str, object]:
            observed.append(len(str(request["payload"])))
            return {"payload": "y" * 24_000}

    async def effect_handler(ctx: JobContext, payload: Input) -> Output:
        response = await ctx.report_effect({"payload": "x" * 16_000})
        assert len(str(response["payload"])) == 24_000
        return Output(doubled=payload.value * 2)

    transport = ScriptedTransport()
    task = Task(
        name="math.wait",
        queue="worker",
        input_model=Input,
        output_model=Output,
        handler=effect_handler,
    )
    supervisor = WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        TaskRegistry([task]),
        "worker-1",
        options=WorkerOptions(
            effect_request_max_bytes=32 * 1024,
            effect_response_max_bytes=32 * 1024,
        ),
        effect_reporter=_Reporter(),  # type: ignore[arg-type]
    )

    report = await supervisor.run_job(_claim())

    assert report.state is JobRunState.SETTLED
    assert observed == [16_000]
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
