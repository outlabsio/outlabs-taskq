"""SQL contract 0.2.3 — finite operator/workflow projections."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import _migrate_impl, discover_migrations, verify

pytestmark = pytest.mark.taskq_sql


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _workflow_member(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    queue: str,
) -> tuple[asyncpg.Record, asyncpg.Record]:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'projection-test')",
        queue,
    )
    workflow = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,ARRAY[$2],'planner')",
        f"projection-{uuid4()}",
        queue,
    )
    assert workflow is not None
    member = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue("
        "$1,'test.projection','{}'::jsonb,"
        "p_workflow_id=>$2,p_step_key=>'one')",
        queue,
        workflow["workflow_id"],
    )
    assert member is not None
    return workflow, member


async def test_workflow_count_transitions_and_inactive_page(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    observer: asyncpg.Connection,
) -> None:
    workflow, member = await _workflow_member(operator, producer, "projection_counts")
    workflow_id = workflow["workflow_id"]

    counts = await pg.fetchrow(
        "SELECT * FROM taskq.workflow_member_counts WHERE workflow_id=$1",
        workflow_id,
    )
    assert counts is not None
    assert tuple(counts) == (workflow_id, 0, 1, 0, 0, 0, 0)

    await producer.fetchrow("SELECT * FROM taskq.seal_workflow($1,'planner')", workflow_id)
    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs('projection_counts','projection-worker')"
    )
    assert batch is not None and batch["state"] == "claimed"
    claimed = batch["jobs"][0]
    counts = await pg.fetchrow(
        "SELECT * FROM taskq.workflow_member_counts WHERE workflow_id=$1",
        workflow_id,
    )
    assert counts is not None
    assert tuple(counts)[1:] == (0, 0, 1, 0, 0, 0)

    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'projection-worker')",
        member["job_id"],
        claimed["attempt_id"],
    )
    counts = await pg.fetchrow(
        "SELECT * FROM taskq.workflow_member_counts WHERE workflow_id=$1",
        workflow_id,
    )
    assert counts is not None
    assert tuple(counts)[1:] == (0, 0, 0, 1, 0, 0)

    with pytest.raises(asyncpg.PostgresError) as inactive:
        await observer.fetchrow("SELECT * FROM taskq.get_workflow_page($1)", workflow_id)
    assert inactive.value.sqlstate == "TQ501"
    assert inactive.value.detail == "reason=read_model_view_inactive view=workflow"

    with pytest.raises(asyncpg.PostgresError) as missing:
        await observer.fetchrow("SELECT * FROM taskq.get_workflow_page($1)", uuid4())
    assert missing.value.sqlstate == "TQ001"


async def test_workflow_counter_lifecycle_and_missing_invariant_fail_closed(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue('projection_invariant','{}'::jsonb,'projection-test')"
    )
    workflow = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow("
        "'projection-invariant','dag','{}'::jsonb,"
        "ARRAY['projection_invariant'],'planner')"
    )
    assert workflow is not None
    workflow_id = workflow["workflow_id"]
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 1
    )

    await pg.execute(
        "DELETE FROM taskq.workflow_member_counts WHERE workflow_id=$1",
        workflow_id,
    )
    with pytest.raises(asyncpg.PostgresError) as missing:
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue("
            "'projection_invariant','test.projection','{}'::jsonb,"
            "p_workflow_id=>$1,p_step_key=>'one')",
            workflow_id,
        )
    assert missing.value.sqlstate == "TQ500"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1",
            workflow_id,
        )
        == 0
    )


async def test_0011_backfills_counts_and_full_verify(taskq_dsn: str) -> None:
    database = f"taskq_projection_transition_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    dsn = _database_dsn(taskq_dsn, database)
    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        assert migrations[-1].id == "0011_finite_projections"
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:10])
            )
            assert applied[-1] == "0010_schedules"

        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute("SET ROLE taskq_operator")
            await connection.fetchrow(
                "SELECT * FROM taskq.ensure_queue('projection_backfill','{}'::jsonb,'test')"
            )
            await connection.execute("RESET ROLE")
            await connection.execute("SET ROLE taskq_producer")
            workflow = await connection.fetchrow(
                "SELECT * FROM taskq.create_workflow("
                "'projection-backfill','dag','{}'::jsonb,"
                "ARRAY['projection_backfill'],'planner')"
            )
            assert workflow is not None
            await connection.fetchrow(
                "SELECT * FROM taskq.enqueue("
                "'projection_backfill','test.projection','{}'::jsonb,"
                "p_workflow_id=>$1,p_step_key=>'one')",
                workflow["workflow_id"],
            )
            await connection.execute("RESET ROLE")
        finally:
            await connection.close()

        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[10:11])
            )
            assert applied == ["0011_finite_projections"]
            count = (
                await conn.exec_driver_sql(
                    "SELECT queued FROM taskq.workflow_member_counts WHERE workflow_id=$1",
                    (workflow["workflow_id"],),
                )
            ).scalar_one()
            assert count == 1
            report = await verify(conn)
            assert report.ok, report
            meta = (await conn.exec_driver_sql("SELECT * FROM taskq.get_contract_meta()")).one()
            assert meta.contract_version == "0.2.3"
            assert meta.capabilities["active"] == [
                "admission_reservations",
                "dependencies_workflows",
                "followups",
                "read_model_list_ready",
                "schedules",
            ]
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()
