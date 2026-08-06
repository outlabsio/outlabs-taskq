"""SQL contract 0.2.6 trusted host-effect fence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import discover_migrations
from taskq.sql.effects import lock_active_effect_attempt
from taskq.sql.manifest import CONTRACT_VERSION, FUNCTIONS

RoleConnect = Callable[[str], Awaitable[asyncpg.Connection]]

EXPECTED_0017_CHECKSUM = "3cc926fa699e58985c98c7b552d29295ff8417116f208e8ca417db6033a7a7f2"
FENCE_SQL = "SELECT * FROM taskq.lock_active_effect_attempt($1,$2,$3,$4,$5)"


def test_0018_follows_byte_immutable_0017() -> None:
    migrations = discover_migrations()
    by_name = {migration.filename: migration for migration in migrations}
    assert "0018_trusted_effect_fence.sql" in by_name
    assert (
        hashlib.sha256(by_name["0017_activate_workflow_continuations.sql"].sql.encode()).hexdigest()
        == EXPECTED_0017_CHECKSUM
    )
    assert CONTRACT_VERSION == "0.4.0"


def test_0_2_6_machine_surface_has_exact_effect_fence() -> None:
    identity = "taskq.lock_active_effect_attempt(uuid,uuid,text,text,text)"
    assert FUNCTIONS[identity].grants == frozenset({"taskq_producer"})
    assert FUNCTIONS[identity].result == (
        "TABLE(payload jsonb, workflow_id uuid, workflow_counts jsonb)"
    )


async def _enqueue_claim(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    *,
    queue: str,
    worker: str,
) -> tuple[UUID, UUID]:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'effect-fence')",
        queue,
    )
    enqueued = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,'test.effect',$2::jsonb)",
        queue,
        json.dumps({"subject_id": "subject-1"}),
    )
    assert enqueued is not None
    claimed = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2)",
        queue,
        worker,
    )
    assert claimed is not None and claimed["state"] == "claimed"
    job = claimed["jobs"][0]
    return job["job_id"], job["attempt_id"]


@pytest.mark.taskq_sql
async def test_effect_fence_returns_only_the_exact_live_attempt(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "effect_exact"
    worker = "effect-worker"
    job_id, attempt_id = await _enqueue_claim(
        operator,
        producer,
        runner,
        queue=queue,
        worker=worker,
    )

    row = await producer.fetchrow(
        FENCE_SQL,
        job_id,
        attempt_id,
        worker,
        queue,
        "test.effect",
    )
    assert row is not None
    assert json.loads(row["payload"]) == {"subject_id": "subject-1"}
    assert row["workflow_id"] is None
    assert row["workflow_counts"] is None

    mismatches = (
        (UUID(int=1), worker, queue, "test.effect"),
        (attempt_id, "other-worker", queue, "test.effect"),
        (attempt_id, worker, "other_queue", "test.effect"),
        (attempt_id, worker, queue, "test.other"),
    )
    for candidate_attempt, candidate_worker, candidate_queue, candidate_type in mismatches:
        assert (
            await producer.fetchrow(
                FENCE_SQL,
                job_id,
                candidate_attempt,
                candidate_worker,
                candidate_queue,
                candidate_type,
            )
            is None
        )

    await pg.execute(
        "UPDATE taskq.jobs SET cancel_requested_at=clock_timestamp() WHERE id=$1",
        job_id,
    )
    assert (
        await producer.fetchrow(
            FENCE_SQL,
            job_id,
            attempt_id,
            worker,
            queue,
            "test.effect",
        )
        is None
    )
    await pg.execute(
        "UPDATE taskq.jobs SET cancel_requested_at=NULL, "
        "lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
        job_id,
    )
    assert (
        await producer.fetchrow(
            FENCE_SQL,
            job_id,
            attempt_id,
            worker,
            queue,
            "test.effect",
        )
        is None
    )


@pytest.mark.taskq_sql
async def test_effect_fence_projects_exact_workflow_counts(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "effect_workflow"
    worker = "effect-workflow-worker"
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'effect-fence')",
        queue,
    )
    workflow = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,ARRAY[$2],'effect-fence')",
        "effect-workflow",
        queue,
    )
    assert workflow is not None
    member = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue("
        "$1,'test.effect',$2::jsonb,p_workflow_id=>$3,p_step_key=>'effect')",
        queue,
        json.dumps({"subject_id": "subject-1"}),
        workflow["workflow_id"],
    )
    assert member is not None
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'effect-fence')",
        workflow["workflow_id"],
    )
    claimed = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2)",
        queue,
        worker,
    )
    assert claimed is not None and claimed["state"] == "claimed"
    job = claimed["jobs"][0]

    row = await producer.fetchrow(
        FENCE_SQL,
        job["job_id"],
        job["attempt_id"],
        worker,
        queue,
        "test.effect",
    )
    assert row is not None
    assert row["workflow_id"] == workflow["workflow_id"]
    assert json.loads(row["workflow_counts"]) == {
        "blocked": 0,
        "queued": 0,
        "running": 1,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }


@pytest.mark.taskq_sql
async def test_effect_fence_is_producer_only(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    role_conn: RoleConnect,
) -> None:
    queue = "effect_acl"
    worker = "effect-acl-worker"
    job_id, attempt_id = await _enqueue_claim(
        operator,
        producer,
        runner,
        queue=queue,
        worker=worker,
    )
    for role in ("taskq_runner", "taskq_observer", "taskq_operator", "taskq_housekeeper"):
        connection = await role_conn(role)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.fetchrow(
                FENCE_SQL,
                job_id,
                attempt_id,
                worker,
                queue,
                "test.effect",
            )


@pytest.mark.taskq_sql
async def test_effect_fence_serializes_with_settlement(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "effect_race"
    worker = "effect-race-worker"
    job_id, attempt_id = await _enqueue_claim(
        operator,
        producer,
        runner,
        queue=queue,
        worker=worker,
    )

    effect_tx = producer.transaction()
    await effect_tx.start()
    try:
        assert (
            await producer.fetchrow(
                FENCE_SQL,
                job_id,
                attempt_id,
                worker,
                queue,
                "test.effect",
            )
            is not None
        )
        settle_tx = runner.transaction()
        await settle_tx.start()
        try:
            await runner.execute("SET LOCAL lock_timeout = '100ms'")
            with pytest.raises(asyncpg.LockNotAvailableError):
                await runner.fetchrow(
                    "SELECT * FROM taskq.complete_job($1,$2,$3,'{}'::jsonb)",
                    job_id,
                    attempt_id,
                    worker,
                )
        finally:
            await settle_tx.rollback()
    finally:
        await effect_tx.commit()

    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,'{}'::jsonb)",
        job_id,
        attempt_id,
        worker,
    )
    assert settled is not None and settled["result"] == "ok"


@pytest.mark.taskq_sql
async def test_borrowed_connection_adapter_retains_caller_transaction(
    sqlalchemy_dsn: str,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "effect_adapter"
    worker = "effect-adapter-worker"
    job_id, attempt_id = await _enqueue_claim(
        operator,
        producer,
        runner,
        queue=queue,
        worker=worker,
    )
    engine = create_async_engine(sqlalchemy_dsn)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.exec_driver_sql("SET LOCAL ROLE taskq_producer")
                active = await lock_active_effect_attempt(
                    connection,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    worker_id=worker,
                    queue=queue,
                    job_type="test.effect",
                )
                assert active is not None
                assert active.payload == {"subject_id": "subject-1"}
                assert active.workflow_id is None
                assert active.workflow_counts is None
    finally:
        await engine.dispose()
