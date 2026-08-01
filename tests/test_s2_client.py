"""S2-03 typed facade compilation and transaction-ownership contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from taskq import (
    EnqueueCreatedResult,
    EnqueueResult,
    RetryStrategy,
    Task,
    TaskQ,
    TaskRegistry,
    TaskqConfigError,
    TaskqNotFoundError,
    UnknownTaskError,
)
from taskq.protocol import EnqueueCommand, EnqueueManyItem
from taskq.sql.transport import SqlTaskqTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _task(**kwargs: Any) -> Task[Input, Output]:
    return Task(
        name="math.double",
        queue="s2_client",
        input_model=Input,
        output_model=Output,
        aliases=("math.double_v1",),
        **kwargs,
    )


class FakeTransport:
    def __init__(self) -> None:
        self.commands: list[EnqueueCommand] = []
        self.bulk: list[tuple[str, tuple[EnqueueManyItem, ...]]] = []
        self.closed = False

    async def enqueue(self, command: EnqueueCommand) -> EnqueueResult:
        self.commands.append(command)
        return EnqueueCreatedResult(
            job_id=uuid4(),
            created=True,
            queue=command.queue,
            job_type=command.job_type,
            idempotency_key=command.idempotency_key,
            scheduled_at=command.scheduled_at,
        )

    async def enqueue_many(
        self, queue: str, items: Sequence[EnqueueManyItem]
    ) -> list[EnqueueResult]:
        self.bulk.append((queue, tuple(items)))
        return [
            EnqueueCreatedResult(
                job_id=uuid4(),
                created=True,
                queue=queue,
                job_type=item.job_type,
                idempotency_key=item.idempotency_key,
                scheduled_at=item.scheduled_at,
            )
            for item in items
        ]

    async def aclose(self) -> None:
        self.closed = True


async def test_typed_enqueue_compiles_canonical_metadata_once() -> None:
    task = _task(
        retry=RetryStrategy(max_attempts=7, mode="fixed", base_seconds=10, cap_seconds=20),
        priority=50,
        lease_seconds=60,
    )
    transport = FakeTransport()
    app = TaskQ(transport, registry=TaskRegistry([task]))  # type: ignore[arg-type]
    result = await app.enqueue(
        task,
        {"value": 4},
        idempotency_key="request-1",
        priority=9,
        headers={"trace": "safe"},
    )
    assert isinstance(result, EnqueueCreatedResult)
    assert len(transport.commands) == 1
    command = transport.commands[0]
    assert command.job_type == "math.double"
    assert command.queue == "s2_client"
    assert command.payload == {"value": 4}
    assert command.priority == 9
    assert command.lease_seconds == 60
    assert command.max_attempts == 7
    assert command.backoff_mode == "fixed"
    assert command.backoff_base == 10 and command.backoff_cap == 20


@pytest.mark.parametrize(
    ("retry", "expected"),
    [(False, 1), (True, None), (5, 5)],
)
async def test_retry_shortcuts_compile_to_row_stamps(
    retry: bool | int, expected: int | None
) -> None:
    task = _task(retry=retry)
    transport = FakeTransport()
    app = TaskQ(transport, registry=TaskRegistry([task]))  # type: ignore[arg-type]
    await app.enqueue(task, Input(value=1))
    assert transport.commands[0].max_attempts == expected


async def test_unknown_task_payload_and_bulk_key_failures_do_not_delegate() -> None:
    task = _task()
    transport = FakeTransport()
    app = TaskQ(transport, registry=TaskRegistry([task]))  # type: ignore[arg-type]
    with pytest.raises(UnknownTaskError):
        await app.enqueue(_task(), {"value": 1})
    with pytest.raises(ValidationError):
        await app.enqueue(task, {"value": "bad"})
    with pytest.raises(TaskqConfigError, match="payload count"):
        await app.enqueue_many(task, [{"value": 1}, {"value": 2}], idempotency_keys=["one"])
    assert transport.commands == [] and transport.bulk == []


async def test_bulk_is_ordered_canonical_and_delegated_once() -> None:
    task = _task(retry=False)
    transport = FakeTransport()
    app = TaskQ(transport, registry=TaskRegistry([task]))  # type: ignore[arg-type]
    results = await app.enqueue_many(
        task,
        [{"value": 1}, Input(value=2)],
        idempotency_keys=["one", "two"],
    )
    assert len(results) == 2 and len(transport.bulk) == 1
    queue, items = transport.bulk[0]
    assert queue == "s2_client"
    assert [item.job_type for item in items] == ["math.double", "math.double"]
    assert [item.payload for item in items] == [{"value": 1}, {"value": 2}]
    assert [item.idempotency_key for item in items] == ["one", "two"]
    assert [item.max_attempts for item in items] == [1, 1]


async def test_raw_enqueue_is_explicitly_opted_out_of_registry_validation() -> None:
    transport = FakeTransport()
    guarded = TaskQ(transport)  # type: ignore[arg-type]
    with pytest.raises(TaskqConfigError, match="validate_job_types=False"):
        await guarded.enqueue_raw(queue="raw", job_type="raw.job", payload={})
    open_app = TaskQ(transport, validate_job_types=False)  # type: ignore[arg-type]
    result = await open_app.enqueue_raw(queue="raw", job_type="raw.job", payload={"x": 1})
    assert result.job_type == "raw.job" and len(transport.commands) == 1


async def test_non_sql_transport_rejects_session_instead_of_ignoring_it() -> None:
    task = _task()
    transport = FakeTransport()
    app = TaskQ(transport, registry=TaskRegistry([task]))  # type: ignore[arg-type]
    session = AsyncSession()
    try:
        with pytest.raises(TaskqConfigError, match="SqlTaskqTransport"):
            await app.enqueue(task, {"value": 1}, session=session)
    finally:
        await session.close()
    assert transport.commands == []


async def test_construction_enqueue_and_close_create_no_background_tasks() -> None:
    before = asyncio.all_tasks()
    task = _task()
    transport = FakeTransport()
    app = TaskQ(transport, registry=TaskRegistry([task]))  # type: ignore[arg-type]
    await app.enqueue(task, {"value": 1})
    await app.aclose()
    assert asyncio.all_tasks() == before
    assert transport.closed


async def _counts(engine: Any) -> tuple[int, int, int]:
    async with engine.connect() as conn:
        domain = int(await conn.scalar(text("SELECT count(*) FROM public.taskq_s2_domain")) or 0)
        jobs = int(
            await conn.scalar(text("SELECT count(*) FROM taskq.jobs WHERE queue='s2_client'")) or 0
        )
        events = int(
            await conn.scalar(
                text(
                    "SELECT count(*) FROM taskq.job_events e JOIN taskq.jobs j ON j.id=e.job_id "
                    "WHERE j.queue='s2_client'"
                )
            )
            or 0
        )
    return domain, jobs, events


@pytest.mark.taskq_sql
async def test_session_commit_rollback_and_single_connection(
    pg: asyncpg.Connection, sqlalchemy_dsn: str
) -> None:
    await pg.execute("DROP TABLE IF EXISTS public.taskq_s2_domain")
    await pg.execute("CREATE TABLE public.taskq_s2_domain (value int PRIMARY KEY)")
    await pg.fetchrow("SELECT * FROM taskq.ensure_queue('s2_client', '{}'::jsonb, 'test')")
    engine = create_async_engine(sqlalchemy_dsn, pool_size=1, max_overflow=0, pool_timeout=0.25)
    task = _task()
    app = TaskQ(SqlTaskqTransport(engine), registry=TaskRegistry([task]))
    try:
        async with AsyncSession(engine) as session:
            async with session.begin():
                await session.execute(text("INSERT INTO public.taskq_s2_domain(value) VALUES (1)"))
                await app.enqueue(task, {"value": 1}, idempotency_key="commit", session=session)
        assert await _counts(engine) == (1, 1, 1)

        async with AsyncSession(engine) as session:
            await session.begin()
            await session.execute(text("INSERT INTO public.taskq_s2_domain(value) VALUES (2)"))
            await app.enqueue(task, {"value": 2}, idempotency_key="rollback", session=session)
            await session.rollback()
        assert await _counts(engine) == (1, 1, 1)

        async with AsyncSession(engine) as session:
            await app.enqueue(task, {"value": 3}, idempotency_key="autobegin", session=session)
            assert session.in_transaction()
            await session.rollback()
        assert await _counts(engine) == (1, 1, 1)

        missing = Task(
            name="math.missing",
            queue="s2_missing",
            input_model=Input,
            output_model=Output,
        )
        failed_app = TaskQ(SqlTaskqTransport(engine), registry=TaskRegistry([task, missing]))
        async with AsyncSession(engine) as session:
            await session.begin()
            with pytest.raises(TaskqNotFoundError):
                await failed_app.enqueue(missing, {"value": 4}, session=session)
            assert session.in_transaction()
            with pytest.raises(DBAPIError):
                await session.execute(text("SELECT 1"))
            await session.rollback()
    finally:
        await engine.dispose()
        await pg.execute("DROP TABLE IF EXISTS public.taskq_s2_domain")


@pytest.mark.taskq_sql
async def test_savepoint_connection_and_bulk_transaction_semantics(
    pg: asyncpg.Connection, sqlalchemy_dsn: str
) -> None:
    await pg.execute("DROP TABLE IF EXISTS public.taskq_s2_domain")
    await pg.execute("CREATE TABLE public.taskq_s2_domain (value int PRIMARY KEY)")
    await pg.fetchrow("SELECT * FROM taskq.ensure_queue('s2_client', '{}'::jsonb, 'test')")
    engine = create_async_engine(sqlalchemy_dsn)
    task = _task()
    app = TaskQ(SqlTaskqTransport(engine), registry=TaskRegistry([task]))
    try:
        async with AsyncSession(engine) as session:
            async with session.begin():
                await session.execute(text("INSERT INTO public.taskq_s2_domain(value) VALUES (10)"))
                nested = await session.begin_nested()
                await session.execute(text("INSERT INTO public.taskq_s2_domain(value) VALUES (11)"))
                await app.enqueue(task, {"value": 11}, idempotency_key="savepoint", session=session)
                await nested.rollback()
        assert await _counts(engine) == (1, 0, 0)

        async with engine.connect() as connection:
            transaction = await connection.begin()
            await app.enqueue(
                task, {"value": 20}, idempotency_key="connection", connection=connection
            )
            await transaction.rollback()
        assert await _counts(engine) == (1, 0, 0)

        async with AsyncSession(engine) as session:
            async with session.begin():
                results = await app.enqueue_many(
                    task,
                    [{"value": 30}, {"value": 31}],
                    idempotency_keys=["bulk-1", "bulk-2"],
                    session=session,
                )
                assert len(results) == 2
        assert await _counts(engine) == (1, 2, 2)
    finally:
        await engine.dispose()
        await pg.execute("DROP TABLE IF EXISTS public.taskq_s2_domain")
