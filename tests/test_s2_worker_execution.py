"""S2-04A execution primitives and deterministic worker harness."""

from __future__ import annotations

import asyncio
from threading import Thread
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from taskq import (
    Cancel,
    CancellationReason,
    CancellationToken,
    Complete,
    JobContext,
    NonRetryable,
    Retry,
    Snooze,
    Task,
    TaskCancelled,
    TaskqConfigError,
)
from taskq.errors import TaskqUnavailableError
from taskq.protocol import JobStatus, SettleAlreadySettledResult
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


@pytest.mark.parametrize(
    "result",
    [
        Complete(result={"doubled": 2}),
        Snooze(delay_seconds=0),
        Cancel(reason="done"),
        Retry(after_seconds=2),
        NonRetryable(error="bad"),
    ],
)
def test_handler_result_models_are_closed_and_frozen(result: BaseModel) -> None:
    with pytest.raises(ValidationError):
        type(result)(**{**result.model_dump(), "invented": True})
    with pytest.raises(ValidationError):
        result.__setattr__(next(iter(type(result).model_fields)), None)


def test_handler_result_boundaries_are_local_and_exact() -> None:
    Complete(
        followups=tuple({"step": f"step-{index}", "job_type": "tests.child"} for index in range(20))
    )
    with pytest.raises(ValidationError):
        Complete(
            followups=tuple(
                {"step": f"step-{index}", "job_type": "tests.child"} for index in range(21)
            )
        )
    for model, field in (
        (Snooze, {"delay_seconds": -1}),
        (Snooze, {"delay_seconds": 2_592_001}),
        (Retry, {"after_seconds": -1}),
        (Cancel, {"reason": ""}),
        (NonRetryable, {"error": ""}),
    ):
        with pytest.raises(ValidationError):
            model(**field)


def test_cancellation_token_is_thread_safe_and_reason_only_escalates() -> None:
    token = CancellationToken()
    threads = [
        Thread(target=token.cancel, args=(reason,))
        for reason in (
            CancellationReason.SHUTDOWN,
            CancellationReason.OPERATOR,
            CancellationReason.LEASE_LOST,
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert token.is_cancelled
    assert token.reason is CancellationReason.LEASE_LOST
    assert token.cancel(CancellationReason.SHUTDOWN) is False


def _context() -> JobContext:
    return JobContext(
        job_id=uuid4(),
        queue="worker",
        job_type="math.double",
        payload=Input(value=2),
        headers={"trace": "private"},
        progress={"cursor": 0},
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
    )


async def test_context_checkpoint_generation_never_discards_a_newer_value() -> None:
    context = _context()
    await context.checkpoint({"cursor": 1})
    first = context._pending_checkpoint()
    assert first is not None
    context.checkpoint_nowait({"cursor": 2})
    context._ack_checkpoint(first[0])
    pending = context._pending_checkpoint()
    assert pending is not None and pending[1] == {"cursor": 2}
    context._ack_checkpoint(pending[0])
    assert context._pending_checkpoint() is None
    returned = context.progress
    assert returned == {"cursor": 2}
    returned["cursor"] = 99
    assert context.progress == {"cursor": 2}


async def test_context_effect_reporter_is_bounded_and_fence_free() -> None:
    seen: list[dict[str, Any]] = []

    async def reporter(request: dict[str, Any]) -> dict[str, Any]:
        seen.append(request)
        return {"state": "applied"}

    context = JobContext(
        job_id=uuid4(),
        queue="worker",
        job_type="math.double",
        payload=Input(value=2),
        headers=None,
        progress=None,
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        effect_reporter=reporter,
    )
    assert await context.report_effect({"operation": "apply"}) == {"state": "applied"}
    assert seen == [{"operation": "apply"}]
    assert not hasattr(context, "attempt_id")
    with pytest.raises(TaskqConfigError, match="8KB"):
        await context.report_effect({"payload": "x" * 8193})


async def test_context_effect_reporter_is_absent_and_cancellation_aware() -> None:
    context = _context()
    with pytest.raises(TaskqConfigError, match="no trusted effect reporter"):
        await context.report_effect({"operation": "apply"})
    context.cancellation.cancel(CancellationReason.OPERATOR)
    with pytest.raises(TaskCancelled):
        await context.report_effect({"operation": "apply"})


def test_context_checkpoint_and_repr_are_safe() -> None:
    context = _context()
    assert "private" not in repr(context)
    with pytest.raises(TaskqConfigError, match="2KB"):
        context.checkpoint_nowait({"cursor": "x" * 2049})
    with pytest.raises(TaskqConfigError, match="serializable"):
        context.checkpoint_nowait({"bad": object()})
    context.cancellation.cancel(CancellationReason.OPERATOR)
    with pytest.raises(TaskCancelled) as excinfo:
        context.raise_if_cancelled()
    assert excinfo.value.reason is CancellationReason.OPERATOR


async def async_context_handler(ctx: JobContext, payload: Input) -> Output | Snooze:
    ctx.raise_if_cancelled()
    return Output(doubled=payload.value * 2)


def sync_context_handler(ctx: JobContext, payload: Input) -> Output | Retry:
    ctx.raise_if_cancelled()
    return Output(doubled=payload.value * 2)


async def bad_context_handler(ctx: str, payload: Input) -> Output:
    return Output(doubled=payload.value * 2)


def test_registry_accepts_closed_sync_async_context_signatures() -> None:
    async_task = Task(
        name="math.async_context",
        queue="worker",
        input_model=Input,
        output_model=Output,
        handler=async_context_handler,
    )
    sync_task = Task(
        name="math.sync_context",
        queue="worker",
        input_model=Input,
        output_model=Output,
        handler=sync_context_handler,
    )
    assert async_task.handler_is_async is True
    assert sync_task.handler_is_async is False
    with pytest.raises(TaskqConfigError, match="JobContext"):
        Task(
            name="math.bad_context",
            queue="worker",
            input_model=Input,
            output_model=Output,
            handler=bad_context_handler,
        )


async def test_manual_clock_orders_sleepers_without_wall_time() -> None:
    clock = ManualClock()
    wake_order: list[str] = []

    async def sleeper(name: str, delay: float) -> None:
        await clock.sleep(delay)
        wake_order.append(name)

    tasks = [
        asyncio.create_task(sleeper("later", 2)),
        asyncio.create_task(sleeper("first", 1)),
    ]
    await asyncio.sleep(0)
    assert clock.sleeping == 2
    clock.advance(1)
    await asyncio.sleep(0)
    assert wake_order == ["first"]
    clock.advance(1)
    await asyncio.gather(*tasks)
    assert wake_order == ["first", "later"]


async def test_scripted_transport_models_applied_but_lost_response() -> None:
    transport = ScriptedTransport()
    replay = SettleAlreadySettledResult(job_status=JobStatus.SUCCEEDED, scheduled_at=None)
    transport.drop_response_after_apply("complete", replay=replay)
    args = (uuid4(), uuid4(), "worker")
    with pytest.raises(TaskqUnavailableError) as excinfo:
        await transport.complete(*args)
    assert getattr(excinfo.value, "retryable", False) is True
    assert await transport.complete(*args) == replay
    assert transport.semantic_applications["complete"] == 1
    assert "attempt_id" not in repr(transport)
