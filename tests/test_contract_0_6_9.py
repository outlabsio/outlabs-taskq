"""Terminal host reconciliation is installation-bound and serializes with redrive."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import lock_terminal_effect_job, verify
from taskq.sql.manifest import FUNCTIONS

FENCE = "SELECT * FROM taskq.lock_terminal_effect_job($1,$2,$3,$4,$5,false)"
IDENTITY = "taskq.lock_terminal_effect_job(uuid,text,text,text,uuid,boolean)"


def test_terminal_fence_is_sql_only_producer_capability():
    assert FUNCTIONS[IDENTITY].grants == frozenset({"taskq_producer"})
    assert FUNCTIONS[IDENTITY].volatility == "v"


async def failed_job(pg, operator, producer, runner):
    queue = "terminal_fence"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'test')", queue)
    result = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,'test.import',$2::jsonb)",
        queue,
        json.dumps({"import_id": str(uuid4()), "generation": 1}),
    )
    claim = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1,'terminal-worker')", queue)
    job = claim["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1,$2,'terminal-worker','sanitized',false)",
        job["job_id"],
        job["attempt_id"],
    )
    installation = await pg.fetchval("SELECT installation_id FROM taskq.get_target_identity()")
    return result["job_id"], queue, installation


@pytest.mark.taskq_sql
async def test_exact_terminal_fence_and_every_other_role_denied(
    pg, operator, producer, runner, role_conn
):
    job_id, queue, installation = await failed_job(pg, operator, producer, runner)
    row = await producer.fetchrow(FENCE, job_id, queue, "test.import", "test", installation)
    assert row["status"] == "failed" and row["finished_at"] is not None
    assert set(row.keys()) == {"status", "outcome", "finished_at", "payload", "workflow_id"}
    assert row["workflow_id"] is None
    assert json.loads(row["payload"])["generation"] == 1
    for job, q, task in [
        (uuid4(), queue, "test.import"),
        (job_id, "other", "test.import"),
        (job_id, queue, "other.task"),
    ]:
        assert await producer.fetchrow(FENCE, job, q, task, "test", installation) is None
    for environment, target in [("production", installation), ("test", uuid4()), ("test", None)]:
        with pytest.raises(asyncpg.PostgresError) as error:
            await producer.fetchrow(FENCE, job_id, queue, "test.import", environment, target)
        assert error.value.sqlstate == "TQ422"
    for role in ("taskq_runner", "taskq_observer", "taskq_operator", "taskq_housekeeper"):
        connection = await role_conn(role)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.fetchrow(FENCE, job_id, queue, "test.import", "test", installation)
    assert not await pg.fetchval(
        "SELECT has_table_privilege('taskq_producer','taskq.jobs','SELECT,UPDATE')"
    )


@pytest.mark.taskq_sql
async def test_terminal_lock_blocks_redrive_and_redrive_winner_returns_no_terminal_row(
    pg, operator, producer, runner
):
    job_id, queue, installation = await failed_job(pg, operator, producer, runner)
    tx = producer.transaction()
    await tx.start()
    try:
        assert await producer.fetchrow(FENCE, job_id, queue, "test.import", "test", installation)
        attempt = operator.transaction()
        await attempt.start()
        try:
            await operator.execute("SET LOCAL lock_timeout = '100ms'")
            with pytest.raises(asyncpg.LockNotAvailableError):
                await operator.fetchval("SELECT taskq.redrive_job($1,'test')", job_id)
        finally:
            await attempt.rollback()
    finally:
        await tx.commit()
    # Redrive wins the next lock. Fence must wait and re-evaluate status after commit.
    redrive = operator.transaction()
    await redrive.start()
    waiter = None
    try:
        assert await operator.fetchval("SELECT taskq.redrive_job($1,'test')", job_id)
        waiter = asyncio.create_task(
            producer.fetchrow(FENCE, job_id, queue, "test.import", "test", installation)
        )
        await asyncio.sleep(0.1)
        assert not waiter.done()
        await redrive.commit()
        assert await asyncio.wait_for(waiter, timeout=5) is None
    finally:
        if waiter is not None and not waiter.done():
            waiter.cancel()


@pytest.mark.taskq_sql
async def test_borrowed_adapter_and_catalog_verification(
    pg, operator, producer, runner, sqlalchemy_dsn
):
    job_id, queue, installation = await failed_job(pg, operator, producer, runner)
    engine = create_async_engine(sqlalchemy_dsn)
    try:
        async with engine.connect() as connection:
            with pytest.raises(ValueError, match="caller-owned transaction"):
                await lock_terminal_effect_job(
                    connection,
                    job_id=job_id,
                    queue=queue,
                    job_type="test.import",
                    expected_environment="test",
                    expected_installation_id=installation,
                )
            async with connection.begin():
                await connection.exec_driver_sql("SET LOCAL ROLE taskq_producer")
                result = await lock_terminal_effect_job(
                    connection,
                    job_id=job_id,
                    queue=queue,
                    job_type="test.import",
                    expected_environment="test",
                    expected_installation_id=installation,
                )
                assert result is not None and result.status == "failed"
                assert result.payload["generation"] == 1
                assert connection.in_transaction()
        async with engine.connect() as connection:
            report = await verify(connection)
            assert report.ok, report.failures
    finally:
        await engine.dispose()
