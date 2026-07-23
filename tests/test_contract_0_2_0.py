"""SQL contract 0.2.0 — lossless, atomic native follow-ups."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import _migrate_impl, discover_migrations, verify

from conftest import RoleConnect

pytestmark = pytest.mark.taskq_sql


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _queue(
    operator: asyncpg.Connection,
    name: str,
    profile: dict[str, object] | None = None,
) -> None:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,$2::jsonb,'followup-test')",
        name,
        json.dumps(profile or {}),
    )
    assert row is not None


async def _running_parent(
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    queue: str,
    key: str,
) -> asyncpg.Record:
    enqueued = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,'test.parent','{}'::jsonb,p_idempotency_key => $2)",
        queue,
        key,
    )
    assert enqueued is not None
    batch = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1,'followup-worker')", queue)
    assert batch is not None and batch["state"] == "claimed"
    assert len(batch["jobs"]) == 1
    return batch["jobs"][0]


async def _complete(
    runner: asyncpg.Connection,
    parent: asyncpg.Record,
    followups: object,
) -> asyncpg.Record:
    row = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'followup-worker',p_followups => $3::jsonb)",
        parent["job_id"],
        parent["attempt_id"],
        json.dumps(followups, separators=(",", ":")),
    )
    assert row is not None
    return row


async def test_validate_all_before_parent_mutation_and_accept_twenty(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "followup_validate"
    await _queue(operator, queue)
    parent = await _running_parent(producer, runner, queue, "parent")
    invalid = [
        {},
        ["not-an-object"],
        [{"step": "one", "job_type": "test.child", "extra": True}],
        [{"step": "one"}],
        [{"step": "one", "job_type": "test.child", "queue": "missing_queue"}],
        [{"step": "one", "job_type": "test.child", "payload": []}],
        [{"step": "one", "job_type": "test.child", "priority": "1"}],
        [
            {"step": "same", "job_type": "test.child"},
            {"step": "same", "job_type": "test.child"},
        ],
        [{"step": f"s{i}", "job_type": "test.child"} for i in range(21)],
    ]
    for followups in invalid:
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await _complete(runner, parent, followups)
        assert excinfo.value.sqlstate == "TQ422"
        assert (
            await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", parent["job_id"])
            == "running"
        )
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent["job_id"]
            )
            == 0
        )

    result = await _complete(
        runner,
        parent,
        [{"step": f"s{i}", "job_type": "test.child"} for i in range(20)],
    )
    assert result["result"] == "ok"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent["job_id"]
        )
        == 20
    )


async def test_cross_queue_followup_bypasses_only_depth_admission(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "followup_parent")
    await _queue(operator, "followup_child", {"max_depth": 1})
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue('followup_child','test.blocker','{}'::jsonb,"
        "p_idempotency_key => 'full')"
    )
    parent = await _running_parent(producer, runner, "followup_parent", "parent")

    result = await _complete(
        runner,
        parent,
        [
            {
                "step": "cross",
                "job_type": "test.child",
                "queue": "followup_child",
                "payload": {"value": 1},
                "headers": {"trace": "bounded"},
            }
        ],
    )
    assert result["result"] == "ok"
    child = await pg.fetchrow(
        "SELECT queue,parent_job_id,payload,headers FROM taskq.jobs WHERE parent_job_id=$1",
        parent["job_id"],
    )
    assert child is not None
    assert dict(child) == {
        "queue": "followup_child",
        "parent_job_id": parent["job_id"],
        "payload": '{"value": 1}',
        "headers": '{"trace": "bounded"}',
    }


async def test_inconsistent_derived_key_rolls_back_parent_settlement(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "followup_collision"
    await _queue(operator, queue)
    parent = await _running_parent(producer, runner, queue, "parent")
    derived = f"chain:{parent['job_id']}:child"
    occupied = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,'test.wrong','{\"wrong\":true}'::jsonb,"
        "p_idempotency_key => $2)",
        queue,
        derived,
    )
    assert occupied is not None

    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await _complete(
            runner,
            parent,
            [
                {"step": "first", "job_type": "test.first"},
                {"step": "child", "job_type": "test.right", "payload": {"right": True}},
            ],
        )
    assert excinfo.value.sqlstate == "TQ500"
    assert (
        await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", parent["job_id"])
        == "running"
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent["job_id"]
        )
        == 0
    )
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE id=$1", occupied["job_id"]) == 1


async def test_settlement_replay_never_duplicates_followups(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "followup_replay"
    await _queue(operator, queue)
    parent = await _running_parent(producer, runner, queue, "parent")
    spec = [{"step": "child", "job_type": "test.child"}]
    first = await _complete(runner, parent, spec)
    replay = await _complete(runner, parent, spec)
    assert first["result"] == "ok"
    assert replay["result"] == "already_settled"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent["job_id"]
        )
        == 1
    )


async def test_concurrent_settlement_serializes_to_one_child(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    role_conn: RoleConnect,
) -> None:
    queue = "followup_race"
    await _queue(operator, queue)
    first_runner = await role_conn("taskq_runner")
    second_runner = await role_conn("taskq_runner")
    parent = await _running_parent(producer, first_runner, queue, "parent")
    spec = json.dumps([{"step": "child", "job_type": "test.child"}])
    query = "SELECT * FROM taskq.complete_job($1,$2,'followup-worker',p_followups => $3::jsonb)"

    transaction = first_runner.transaction()
    await transaction.start()
    first = await first_runner.fetchrow(query, parent["job_id"], parent["attempt_id"], spec)
    assert first is not None and first["result"] == "ok"
    second_task = asyncio.create_task(
        second_runner.fetchrow(query, parent["job_id"], parent["attempt_id"], spec)
    )
    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline:
        waiting = await pg.fetchrow(
            "SELECT wait_event_type,wait_event FROM pg_catalog.pg_stat_activity WHERE pid=$1",
            second_runner.get_server_pid(),
        )
        if waiting is not None and waiting["wait_event_type"] == "Lock":
            break
        await asyncio.sleep(0.005)
    else:
        second_task.cancel()
        await transaction.rollback()
        raise AssertionError("concurrent settlement never blocked on the parent tuple")

    await transaction.commit()
    second = await asyncio.wait_for(second_task, timeout=3.0)
    assert second is not None and second["result"] == "already_settled"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent["job_id"]
        )
        == 1
    )


async def test_private_helper_has_no_application_execute_grant(pg: asyncpg.Connection) -> None:
    assert (
        await pg.fetchval(
            "SELECT proacl::text FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='taskq' AND p.proname='_enqueue_followup'"
        )
        == "{taskq_owner=X/taskq_owner}"
    )


async def test_followups_transition_only_at_0008(taskq_dsn: str) -> None:
    database = f"taskq_followup_transition_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:7])
            )
            assert applied[-1] == "0007_admission_reservations"
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.ensure_queue('followup_transition','{}'::jsonb,'test')"
            )
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.enqueue('followup_transition','test.parent','{}'::jsonb)"
            )
            claimed = (
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.claim_jobs('followup_transition','worker')"
                )
            ).first()
            assert claimed is not None
            job = (
                await conn.exec_driver_sql(
                    "SELECT id,current_attempt_id FROM taskq.jobs WHERE queue='followup_transition'"
                )
            ).one()
            await conn.commit()
            with pytest.raises(DBAPIError) as inactive:
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.complete_job($1,$2,'worker',"
                    'p_followups => \'[{"step":"child",'
                    '"job_type":"test.child"}]\'::jsonb)',
                    (job.id, job.current_attempt_id),
                )
            assert getattr(inactive.value, "orig", inactive.value).sqlstate == "TQ501"
            await conn.rollback()

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[7:8])
            )
            assert applied == ["0008_followups"]
            settled = (
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.complete_job($1,$2,'worker',"
                    'p_followups => \'[{"step":"child",'
                    '"job_type":"test.child"}]\'::jsonb)',
                    (job.id, job.current_attempt_id),
                )
            ).first()
            assert settled is not None and settled.result == "ok"
            report = await verify(conn)
            assert report.ok
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()
