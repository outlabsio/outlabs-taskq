"""ADR-022 trusted worker effect reporter boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from taskq import (
    JobContext,
    JobRunState,
    Task,
    TaskRegistry,
    TaskqUnavailableError,
    WorkerEffectAttempt,
    WorkerOptions,
    WorkerSupervisor,
)
from taskq.protocol import ClaimedJob, HeartbeatResult
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _claim() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue="worker",
        job_type="math.effect",
        priority=100,
        payload={"value": 2},
        headers={},
        progress=None,
        attempt_id=uuid4(),
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=15),
        lease_seconds=15,
    )


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 40) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


class RecordingReporter:
    def __init__(self, *, lose_first_response: bool = False) -> None:
        self.lose_first_response = lose_first_response
        self.calls: list[tuple[WorkerEffectAttempt, dict[str, Any]]] = []

    async def report_effect(
        self, attempt: WorkerEffectAttempt, request: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((attempt, request))
        if self.lose_first_response and len(self.calls) == 1:
            raise TaskqUnavailableError()
        return {"effect": "applied", "call_count": len(self.calls)}


def _supervisor(
    handler: object,
    transport: ScriptedTransport,
    clock: ManualClock,
    reporter: RecordingReporter,
) -> WorkerSupervisor:
    task = Task(
        name="math.effect",
        queue="worker",
        input_model=Input,
        output_model=Output,
        handler=handler,  # type: ignore[arg-type]
    )
    return WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        TaskRegistry([task]),
        "worker-effect-1",
        options=WorkerOptions(settle_backoff_base=0.25, settle_backoff_cap=1),
        clock=clock,
        effect_reporter=reporter,
    )


async def test_reporter_receives_active_attempt_but_handler_does_not() -> None:
    observed_context: list[str] = []

    async def handler(ctx: JobContext, payload: Input) -> Output:
        observed_context.append(repr(ctx))
        result = await ctx.report_effect({"operation": "contact_apply", "value": payload.value})
        assert result == {"effect": "applied", "call_count": 1}
        assert not hasattr(ctx, "attempt_id")
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    reporter = RecordingReporter()
    claim = _claim()
    supervisor = _supervisor(handler, transport, clock, reporter)

    report = await supervisor.run_job(claim)

    assert report.state is JobRunState.SETTLED
    assert observed_context and str(claim.attempt_id) not in observed_context[0]
    assert len(reporter.calls) == 1
    active, request = reporter.calls[0]
    assert active.job_id == claim.job_id
    assert active.attempt_id == claim.attempt_id
    assert active.worker_id == "worker-effect-1"
    assert active.queue == claim.queue
    assert active.job_type == claim.job_type
    assert request == {"operation": "contact_apply", "value": 2}
    assert [call.command for call in transport.calls] == ["complete"]
    await supervisor.aclose()


async def test_reporter_replays_the_same_request_after_response_loss() -> None:
    async def handler(ctx: JobContext, payload: Input) -> Output:
        result = await ctx.report_effect({"operation": "contact_apply", "value": payload.value})
        assert result == {"effect": "applied", "call_count": 2}
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    reporter = RecordingReporter(lose_first_response=True)
    claim = _claim()
    supervisor = _supervisor(handler, transport, clock, reporter)
    running = asyncio.create_task(supervisor.run_job(claim))

    await _spin_until(lambda: len(reporter.calls) == 1)
    for _ in range(25):
        clock.advance(0.25)
        await asyncio.sleep(0)
        if len(reporter.calls) == 2:
            break
    assert len(reporter.calls) == 2
    report = await running

    assert report.state is JobRunState.SETTLED
    assert reporter.calls[0] == reporter.calls[1]
    assert transport.calls[-1].command == "complete"
    await supervisor.aclose()


async def test_lost_ownership_prevents_a_later_effect_report() -> None:
    entered = asyncio.Event()
    allow_report = asyncio.Event()

    async def handler(ctx: JobContext, payload: Input) -> Output:
        entered.set()
        await allow_report.wait()
        await ctx.report_effect({"operation": "contact_apply", "value": payload.value})
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "heartbeat", HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
    )
    reporter = RecordingReporter()
    claim = _claim()
    supervisor = _supervisor(handler, transport, clock, reporter)
    running = asyncio.create_task(supervisor.run_job(claim))

    await entered.wait()
    await _spin_until(lambda: clock.sleeping == 1)
    clock.advance(5)
    await _spin_until(lambda: transport.calls and transport.calls[0].command == "heartbeat")
    allow_report.set()
    report = await running

    assert report.state is JobRunState.OWNERSHIP_LOST
    assert reporter.calls == []
    await supervisor.aclose()
