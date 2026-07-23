from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from taskq import Complete, FollowupTarget, JobContext, Retry, Task, TaskQ, TaskRegistry
from taskq.testing import FakeTaskQClient, drain, inline_mode


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_repeated_inline_cancellation_restores_transport_and_tasks() -> None:
    for index in range(20):
        started = asyncio.Event()

        async def waiting(context: JobContext, payload: Input) -> Output:
            started.set()
            await asyncio.Event().wait()
            return Output(value=payload.value)

        task = Task(
            name="audit.waiting",
            queue="audit",
            input_model=Input,
            output_model=Output,
            handler=waiting,
        )
        original = FakeTaskQClient()
        tq = TaskQ(original, registry=TaskRegistry((task,)))
        before = {
            running for running in asyncio.all_tasks() if running is not asyncio.current_task()
        }

        async def invoke() -> None:
            async with inline_mode(tq):
                await tq.enqueue(task, {"value": index})

        running = asyncio.create_task(invoke(), name=f"consumer-inline-{index}")
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        await asyncio.sleep(0)
        after = {
            running for running in asyncio.all_tasks() if running is not asyncio.current_task()
        }
        assert tq.transport is original
        assert after == before


@pytest.mark.asyncio
async def test_repeated_followup_and_drain_caps_release_overflow_without_leaks() -> None:
    async def child(payload: Input) -> Output:
        return Output(value=payload.value)

    async def parent(payload: Input) -> Complete:
        return Complete(
            result={"value": payload.value},
            followups=(
                {
                    "step": "child",
                    "job_type": "audit.child",
                    "payload": {"value": payload.value},
                },
            ),
        )

    async def retry(payload: Input) -> Retry:
        return Retry(after_seconds=0, error=f"again-{payload.value}")

    child_task = Task(
        name="audit.child",
        queue="audit",
        input_model=Input,
        output_model=Output,
        handler=child,
    )
    parent_task = Task(
        name="audit.parent",
        queue="audit",
        input_model=Input,
        output_model=Output,
        followup_targets=(FollowupTarget(queue="audit", job_type="audit.child"),),
        handler=parent,
    )
    retry_task = Task(
        name="audit.retry",
        queue="audit",
        input_model=Input,
        output_model=Output,
        retry=100,
        handler=retry,
    )
    registry = TaskRegistry((child_task, parent_task, retry_task))

    for index in range(20):
        original = FakeTaskQClient(queues=("audit",))
        tq = TaskQ(original, registry=registry)
        async with inline_mode(tq, follow=True, max_jobs=2) as recorder:
            await tq.enqueue(parent_task, {"value": index})
            assert len(recorder.settlements) == 2
        assert tq.transport is original

        await tq.enqueue(retry_task, {"value": index}, max_attempts=100)
        with pytest.raises(AssertionError, match="runaway work"):
            await drain(tq, queue="audit", max_jobs=1)
        assert original.settlements[-1].command == "release"
        await asyncio.sleep(0)
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and task.get_name().startswith("taskq-")
        ]
