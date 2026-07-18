from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from taskq import Complete, Retry, Snooze, Task, TaskQ, TaskRegistry
from taskq.errors import TaskqConfigError
from taskq.testing import FakeTaskQClient, drain, inline_mode, require_enqueued, work
from taskq.sql.transport import SqlTaskqTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


async def complete_handler(payload: Input) -> Output:
    return Output(doubled=payload.value * 2)


async def snooze_handler(payload: Input) -> Snooze:
    return Snooze(delay_seconds=5, progress={"value": payload.value})


def sync_handler(payload: Input) -> Complete:
    return Complete(result={"doubled": payload.value * 2})


async def retry_handler(payload: Input) -> Retry:
    return Retry(after_seconds=0, error=f"again-{payload.value}")


COMPLETE_TASK = Task(
    name="tests.complete",
    queue="testing",
    input_model=Input,
    output_model=Output,
    handler=complete_handler,
)
SNOOZE_TASK = Task(
    name="tests.snooze",
    queue="testing",
    input_model=Input,
    output_model=Output,
    handler=snooze_handler,
)
SYNC_TASK = Task(
    name="tests.sync",
    queue="testing",
    input_model=Input,
    output_model=Output,
    handler=sync_handler,
)
RETRY_TASK = Task(
    name="tests.retry",
    queue="testing",
    input_model=Input,
    output_model=Output,
    retry=3,
    handler=retry_handler,
)


@pytest.mark.asyncio
async def test_work_uses_production_normalization_for_async_sync_and_intents() -> None:
    completed = await work(task=COMPLETE_TASK, payload={"value": 4})
    snoozed = await work(task=SNOOZE_TASK, payload=Input(value=5), progress={"start": True})
    sync = await work(task=SYNC_TASK, payload={"value": 6}, unique_mode="isolated")

    assert completed == Complete(result={"doubled": 8})
    assert snoozed == Snooze(delay_seconds=5, progress={"value": 5})
    assert sync == Complete(result={"doubled": 12})

    no_handler = Task(
        name="tests.none",
        queue="testing",
        input_model=Input,
        output_model=Output,
    )
    with pytest.raises(TaskqConfigError, match="requires a task with a handler"):
        await work(task=no_handler, payload={"value": 1})
    with pytest.raises(TaskqConfigError, match="unique_mode"):
        await work(task=COMPLETE_TASK, payload={"value": 1}, unique_mode="invented")


@pytest.mark.asyncio
async def test_require_enqueued_fake_matches_one_created_record() -> None:
    registry = TaskRegistry((COMPLETE_TASK,))
    fake = FakeTaskQClient()
    tq = TaskQ(fake, registry=registry)
    result = await tq.enqueue(
        COMPLETE_TASK,
        {"value": 7},
        idempotency_key="one",
        headers={"trace": {"id": "safe"}},
    )
    await tq.enqueue(COMPLETE_TASK, {"value": 7}, idempotency_key="one")

    job = await require_enqueued(
        tq,
        job_type="tests.complete",
        where={"payload.value": 7, "headers.trace.id": "safe"},
        unique_skipped=False,
        enqueue_result=result,
    )
    assert job.job_id == result.job_id

    with pytest.raises(AssertionError, match="exactly one"):
        await require_enqueued(fake, job_type="tests.missing")
    with pytest.raises(AssertionError, match="status created"):
        await require_enqueued(
            fake,
            job_type="tests.complete",
            unique_skipped=False,
            enqueue_result=await tq.enqueue(COMPLETE_TASK, {"value": 7}, idempotency_key="one"),
        )


@pytest.mark.asyncio
async def test_inline_executes_created_once_and_restores_transport() -> None:
    registry = TaskRegistry((COMPLETE_TASK,))
    original = FakeTaskQClient()
    tq = TaskQ(original, registry=registry)

    async with inline_mode(tq) as recorder:
        first = await tq.enqueue(COMPLETE_TASK, {"value": 3}, idempotency_key="same")
        second = await tq.enqueue(COMPLETE_TASK, {"value": 3}, idempotency_key="same")
        assert first.created and second.created
        assert recorder.settled("tests.complete")[0].is_complete
        assert recorder.settled("tests.complete")[0].intent == Complete(result={"doubled": 6})
        assert len(recorder.settled("tests.complete")) == 2
    assert tq.transport is original


@pytest.mark.asyncio
async def test_inline_followups_are_recorded_or_boundedly_executed() -> None:
    async def parent(payload: Input) -> Complete:
        return Complete(
            result={"doubled": payload.value * 2},
            followups=({"job_type": "tests.complete", "payload": {"value": 9}},),
        )

    parent_task = Task(
        name="tests.parent",
        queue="testing",
        input_model=Input,
        output_model=Output,
        handler=parent,
    )
    tq = TaskQ(FakeTaskQClient(), registry=TaskRegistry((parent_task, COMPLETE_TASK)))
    async with inline_mode(tq, follow=False) as recorded:
        await tq.enqueue(parent_task, {"value": 1})
        assert len(recorded.settled("tests.parent")) == 1
        assert not recorded.enqueued("tests.complete")

    async with inline_mode(tq, follow=True) as executed:
        await tq.enqueue(parent_task, {"value": 1})
        assert len(executed.settled("tests.complete")) == 1

    with pytest.raises(AssertionError, match="runaway followup"):
        async with inline_mode(tq, follow=True, max_jobs=1):
            await tq.enqueue(parent_task, {"value": 1})
    assert isinstance(tq.transport, FakeTaskQClient)

    for invalid in (None, True, 0, 10_001):
        with pytest.raises(TaskqConfigError, match="max_jobs"):
            async with inline_mode(tq, max_jobs=invalid):
                pass


@pytest.mark.asyncio
async def test_inline_cancellation_restores_and_leaks_no_task() -> None:
    started = asyncio.Event()

    async def waits(payload: Input) -> Output:
        started.set()
        await asyncio.Event().wait()
        return Output(doubled=payload.value * 2)

    waiting = Task(
        name="tests.waiting",
        queue="testing",
        input_model=Input,
        output_model=Output,
        handler=waits,
    )
    original = FakeTaskQClient()
    tq = TaskQ(original, registry=TaskRegistry((waiting,)))

    async def body() -> None:
        async with inline_mode(tq):
            await tq.enqueue(waiting, {"value": 1})

    running = asyncio.create_task(body())
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert tq.transport is original
    await asyncio.sleep(0)
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("taskq-")]


@pytest.mark.asyncio
async def test_drain_fake_counts_results_and_fails_loudly_at_cap() -> None:
    registry = TaskRegistry((COMPLETE_TASK, SNOOZE_TASK, RETRY_TASK))
    fake = FakeTaskQClient(queues=("testing",))
    tq = TaskQ(fake, registry=registry)
    await tq.enqueue(COMPLETE_TASK, {"value": 1})
    await tq.enqueue(SNOOZE_TASK, {"value": 2})
    report = await drain(tq, queue="testing", max_jobs=10)
    assert report.claimed == 2
    assert report.completed == 1
    assert report.snoozed == 1

    await tq.enqueue(RETRY_TASK, {"value": 3})
    retried = await drain(tq, queue="testing", max_jobs=10)
    assert retried.claimed == 3
    assert retried.retried == 2
    assert retried.failed == 1

    runaway = FakeTaskQClient(queues=("testing",))
    runaway_tq = TaskQ(runaway, registry=registry)
    await runaway_tq.enqueue(RETRY_TASK, {"value": 4}, max_attempts=100)
    with pytest.raises(AssertionError, match="runaway work"):
        await drain(runaway_tq, queue="testing", max_jobs=1)

    for invalid in (None, False, 0, 10_001):
        with pytest.raises(TaskqConfigError, match="max_jobs"):
            await drain(tq, queue="testing", max_jobs=invalid)


@pytest.mark.taskq_sql
async def test_sql_require_and_work_share_caller_transaction(pg, sqlalchemy_dsn: str) -> None:
    await pg.fetchrow("SELECT * FROM taskq.ensure_queue('testing', '{}'::jsonb, 'test')")
    engine = create_async_engine(sqlalchemy_dsn, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            result = await work(
                connection,
                task=COMPLETE_TASK,
                payload={"value": 8},
                unique_mode="isolated",
            )
            assert result == Complete(result={"doubled": 16})
            job = await require_enqueued(
                connection,
                job_type="tests.complete",
                where={"payload.value": 8, "status": "succeeded"},
            )
            assert job.status.value == "succeeded"
            assert connection.in_transaction()
            await transaction.rollback()

        async with engine.connect() as connection:
            count = await connection.scalar(
                text("SELECT count(*) FROM taskq.jobs WHERE job_type='tests.complete'")
            )
            assert count == 0
    finally:
        await engine.dispose()


@pytest.mark.taskq_sql
async def test_sql_require_missing_ambiguous_and_matcher_injection(pg, sqlalchemy_dsn: str) -> None:
    await pg.fetchrow("SELECT * FROM taskq.ensure_queue('testing', '{}'::jsonb, 'test')")
    engine = create_async_engine(sqlalchemy_dsn)
    tq = TaskQ(SqlTaskqTransport(engine), registry=TaskRegistry((COMPLETE_TASK,)))
    try:
        async with engine.begin() as connection:
            with pytest.raises(AssertionError, match="found 0"):
                await require_enqueued(connection, job_type="tests.complete")
            await tq.enqueue(COMPLETE_TASK, {"value": 1}, connection=connection)
            await tq.enqueue(COMPLETE_TASK, {"value": 2}, connection=connection)
            with pytest.raises(AssertionError, match="found 2"):
                await require_enqueued(connection, job_type="tests.complete")
            selected = await require_enqueued(
                connection,
                job_type="tests.complete",
                where={"payload.value": 2},
            )
            assert selected.payload == {"value": 2}
            with pytest.raises(TaskqConfigError, match="safe dotted"):
                await require_enqueued(
                    connection,
                    job_type="tests.complete",
                    where={"payload.value') OR true --": 2},
                )
    finally:
        await engine.dispose()


@pytest.mark.taskq_sql
async def test_sql_drain_uses_exact_transaction_and_rolls_back(pg, sqlalchemy_dsn: str) -> None:
    await pg.fetchrow("SELECT * FROM taskq.ensure_queue('testing', '{}'::jsonb, 'test')")
    engine = create_async_engine(sqlalchemy_dsn, pool_size=1, max_overflow=0)
    tq = TaskQ(SqlTaskqTransport(engine), registry=TaskRegistry((COMPLETE_TASK,)))
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await tq.enqueue(COMPLETE_TASK, {"value": 1}, connection=connection)
            await tq.enqueue(COMPLETE_TASK, {"value": 2}, connection=connection)
            report = await drain(
                tq,
                queue="testing",
                max_jobs=10,
                connection=connection,
            )
            assert report.claimed == report.completed == 2
            assert connection.in_transaction()
            statuses = (
                await connection.execute(
                    text(
                        "SELECT status FROM taskq.jobs WHERE job_type='tests.complete' "
                        "ORDER BY payload->>'value'"
                    )
                )
            ).scalars()
            assert list(statuses) == ["succeeded", "succeeded"]
            await transaction.rollback()

        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM taskq.jobs WHERE job_type='tests.complete'")
                )
                == 0
            )
    finally:
        await engine.dispose()
