"""S2-04C verb-aware settlement replay and response-loss convergence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from taskq import (
    Cancel,
    Complete,
    JobRunOutcome,
    JobRunState,
    NonRetryable,
    Retry,
    Snooze,
    Task,
    TaskRegistry,
    WorkerOptions,
    WorkerSupervisor,
)
from taskq.errors import (
    TaskqCapabilityError,
    TaskqConflictError,
    TaskqUnavailableError,
    TaskqValidationError,
)
from taskq.protocol import SETTLE_RESULT_ADAPTER, ClaimedJob, JobStatus, SettleOutcome
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


async def complete_handler(payload: Input) -> Output:
    return Output(doubled=payload.value * 2)


async def retry_handler(payload: Input) -> Retry:
    return Retry(error=f"retry {payload.value}")


async def snooze_handler(payload: Input) -> Snooze:
    return Snooze(delay_seconds=payload.value)


async def cancel_handler(payload: Input) -> Cancel:
    return Cancel(reason=f"cancel {payload.value}")


async def followup_handler(payload: Input) -> Complete:
    return Complete(
        result={"doubled": payload.value * 2},
        followups=({"job_type": "later.task", "payload": {}},),
    )


def _claim(job_type: str = "math.work") -> ClaimedJob:
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
        lease_expires_at=datetime.now(UTC),
        lease_seconds=15,
    )


def _supervisor(
    transport: ScriptedTransport,
    clock: ManualClock,
    handler: object | None,
    *,
    attempts: int = 5,
) -> WorkerSupervisor:
    registry = TaskRegistry()
    if handler is not None:
        registry.register(
            Task(
                name="math.work",
                queue="worker",
                input_model=Input,
                output_model=Output,
                handler=handler,  # type: ignore[arg-type]
            )
        )
    return WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        registry,
        "worker-1",
        options=WorkerOptions(settle_max_attempts=attempts),
        clock=clock,
    )


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 30) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


@pytest.mark.parametrize(
    ("command", "handler", "status"),
    [
        ("complete", complete_handler, JobStatus.SUCCEEDED),
        ("fail", retry_handler, JobStatus.QUEUED),
        ("snooze", snooze_handler, JobStatus.QUEUED),
        ("cancel_running", cancel_handler, JobStatus.CANCELLED),
        ("release", None, JobStatus.QUEUED),
    ],
)
async def test_lost_response_retries_identical_verb_and_runs_handler_once(
    command: str,
    handler: object | None,
    status: JobStatus,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.drop_response_after_apply(
        command,
        replay=SETTLE_RESULT_ADAPTER.validate_python(
            {
                "result": SettleOutcome.ALREADY_SETTLED,
                "job_status": status,
                "scheduled_at": None,
            }
        ),
    )
    calls = 0
    selected_handler = handler
    if handler is complete_handler:

        async def counted(payload: Input) -> Output:
            nonlocal calls
            calls += 1
            return await complete_handler(payload)

        selected_handler = counted

    supervisor = _supervisor(transport, clock, selected_handler)
    running = asyncio.create_task(supervisor.run_job(_claim("math.work" if handler else "missing")))
    await _spin_until(lambda: len(transport.calls) == 1 and clock.sleeping >= 1)
    clock.advance(0.25)
    report = await running
    assert report.state is JobRunState.SETTLED
    assert [call.command for call in transport.calls] == [command, command]
    assert transport.semantic_applications[command] == 1
    if handler is complete_handler:
        assert calls == 1
    await supervisor.aclose()


@pytest.mark.parametrize(
    ("outcome", "expected_state", "expected_outcome", "fatal"),
    [
        (SettleOutcome.LOST, JobRunState.OWNERSHIP_LOST, JobRunOutcome.OWNERSHIP_LOST, False),
        (
            SettleOutcome.SETTLE_CONFLICT,
            JobRunState.RUNTIME_FAILED,
            JobRunOutcome.SETTLE_CONFLICT,
            True,
        ),
        (SettleOutcome.DEAD, JobRunState.RUNTIME_FAILED, JobRunOutcome.RUNTIME_ERROR, True),
    ],
)
async def test_complete_outcomes_are_command_specific_and_never_switch_verbs(
    outcome: SettleOutcome,
    expected_state: JobRunState,
    expected_outcome: JobRunOutcome,
    fatal: bool,
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        SETTLE_RESULT_ADAPTER.validate_python(
            {"result": outcome, "job_status": JobStatus.FAILED, "scheduled_at": None}
        ),
    )
    supervisor = _supervisor(transport, clock, complete_handler)
    report = await supervisor.run_job(_claim())
    assert report.state is expected_state
    assert report.outcome is expected_outcome
    assert report.fatal is fatal
    assert [call.command for call in transport.calls] == ["complete"]
    await supervisor.aclose()


async def test_retry_exhaustion_is_settlement_unknown_and_stops_heartbeat() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "complete",
        TaskqUnavailableError(),
        TaskqUnavailableError(),
        TaskqUnavailableError(),
    )
    supervisor = _supervisor(transport, clock, complete_handler, attempts=3)
    running = asyncio.create_task(supervisor.run_job(_claim()))
    await _spin_until(lambda: len(transport.calls) == 1 and clock.sleeping >= 1)
    clock.advance(0.25)
    await _spin_until(lambda: len(transport.calls) == 2)
    clock.advance(0.5)
    report = await running
    assert report.outcome is JobRunOutcome.SETTLEMENT_UNKNOWN
    assert report.fatal
    assert [call.command for call in transport.calls] == ["complete"] * 3
    assert clock.sleeping == 0
    await supervisor.aclose()


async def test_nonretryable_settlement_error_is_fatal_and_not_retried() -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("complete", TaskqConflictError())
    supervisor = _supervisor(transport, clock, complete_handler)
    report = await supervisor.run_job(_claim())
    assert report.outcome is JobRunOutcome.RUNTIME_ERROR
    assert report.fatal
    assert [call.command for call in transport.calls] == ["complete"]
    await supervisor.aclose()


@pytest.mark.parametrize(
    ("error", "fatal"),
    [(TaskqValidationError(), False), (TaskqCapabilityError(), True)],
)
async def test_followup_rejection_terminal_fails_parent_before_optional_soft_stop(
    error: Exception, fatal: bool
) -> None:
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script("complete", error)
    supervisor = _supervisor(transport, clock, followup_handler)
    report = await supervisor.run_job(_claim())
    assert report.outcome is JobRunOutcome.FOLLOWUP_REJECTED
    assert report.fatal is fatal
    assert report.state is (JobRunState.RUNTIME_FAILED if fatal else JobRunState.SETTLED)
    assert [call.command for call in transport.calls] == ["complete", "fail"]
    await supervisor.aclose()


async def test_explicit_nonretryable_handler_uses_fail_once() -> None:
    async def nonretryable(payload: Input) -> NonRetryable:
        return NonRetryable(error=f"bad {payload.value}")

    transport = ScriptedTransport()
    supervisor = _supervisor(transport, ManualClock(), nonretryable)
    report = await supervisor.run_job(_claim())
    assert report.state is JobRunState.SETTLED
    assert [call.command for call in transport.calls] == ["fail"]
    assert transport.calls[0].arguments["retryable"] is False
    await supervisor.aclose()
