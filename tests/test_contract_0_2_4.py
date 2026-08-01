"""SQL contract 0.2.4 — bounded worker-presence definition and activation."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import _migrate_impl, discover_migrations

pytestmark = pytest.mark.taskq_sql


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _assert_tq422(conn: asyncpg.Connection, *args: object) -> None:
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await conn.fetchrow(
            "SELECT * FROM taskq.worker_heartbeat($1,$2,NULL,NULL,$3,NULL)",
            *args,
        )
    assert exc_info.value.sqlstate == "TQ422"


async def test_inactive_presence_and_exact_heartbeat_bounds(
    pg: asyncpg.Connection,
    runner: asyncpg.Connection,
    observer: asyncpg.Connection,
) -> None:
    await _assert_tq422(runner, "too-many", [f"queue_{index}" for index in range(33)], "v")
    await _assert_tq422(runner, "duplicate", ["one", "one"], "v")
    await _assert_tq422(runner, "bad-queue", ["Not_Canonical"], "v")
    await _assert_tq422(runner, "long-version", ["one"], "v" * 201)
    assert await pg.fetchval("SELECT count(*) FROM taskq.workers") == 0

    row = await runner.fetchrow(
        "SELECT * FROM taskq.worker_heartbeat("
        "'bounded-worker',ARRAY['one','two'],NULL,NULL,$1,NULL)",
        "v" * 200,
    )
    assert row is not None and row["shutdown_requested"] is False
    stored = await pg.fetchrow(
        "SELECT worker_id,queues,version FROM taskq.workers WHERE worker_id='bounded-worker'"
    )
    assert stored is not None
    assert stored["queues"] == ["one", "two"]
    assert stored["version"] == "v" * 200


async def test_active_presence_page_is_redacted_bounded_and_counts_running_jobs(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    observer: asyncpg.Connection,
) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue('presence_queue','{}'::jsonb,'presence-test')"
    )
    for worker_id in ("worker-a", "worker-b", "worker-c"):
        await runner.fetchrow(
            "SELECT * FROM taskq.worker_heartbeat("
            "$1,ARRAY['presence_queue'],'private-host',123,'v1',"
            '\'{"private":"value"}\'::jsonb)',
            worker_id,
        )
    job = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue('presence_queue','tests.presence','{}'::jsonb)"
    )
    assert job is not None
    claim = await runner.fetchrow("SELECT * FROM taskq.claim_jobs('presence_queue','worker-c')")
    assert claim is not None and claim["state"] == "claimed"
    await operator.fetchval("SELECT taskq.request_worker_shutdown('worker-c',NULL,'presence-test')")

    first = await observer.fetchrow("SELECT * FROM taskq.list_worker_presence(2)")
    assert first is not None
    assert first["as_of"].tzinfo is not None
    assert len(first["items"]) == 2
    assert first["next_last_seen_at"] is not None
    assert first["next_worker_id"] is not None
    by_id = {item["worker_id"]: item for item in first["items"]}
    worker_c = by_id["worker-c"]
    assert worker_c["declared_queues"] == ["presence_queue"]
    assert worker_c["version"] == "v1"
    assert worker_c["online"] is True
    assert worker_c["running_jobs"] == 1
    assert worker_c["shutdown_requested"] is True
    assert set(worker_c.keys()) == {
        "worker_id",
        "declared_queues",
        "version",
        "started_at",
        "last_seen_at",
        "online",
        "running_jobs",
        "shutdown_requested",
    }

    second = await observer.fetchrow(
        "SELECT * FROM taskq.list_worker_presence(2,$1,$2)",
        first["next_last_seen_at"],
        first["next_worker_id"],
    )
    assert second is not None
    assert [item["worker_id"] for item in second["items"]] == ["worker-a"]
    assert second["next_last_seen_at"] is None
    assert second["next_worker_id"] is None
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE id=$1", job["job_id"]) == 1


async def test_live_cursor_omits_a_worker_that_heartbeats_ahead(
    runner: asyncpg.Connection,
    observer: asyncpg.Connection,
) -> None:
    for worker_id in ("live-a", "live-b", "live-c"):
        await runner.fetchrow(
            "SELECT * FROM taskq.worker_heartbeat($1,ARRAY['live'])",
            worker_id,
        )
    first = await observer.fetchrow("SELECT * FROM taskq.list_worker_presence(1)")
    assert first is not None
    assert [item["worker_id"] for item in first["items"]] == ["live-c"]

    await runner.fetchrow("SELECT * FROM taskq.worker_heartbeat('live-a',ARRAY['live'])")
    second = await observer.fetchrow(
        "SELECT * FROM taskq.list_worker_presence(100,$1,$2)",
        first["next_last_seen_at"],
        first["next_worker_id"],
    )
    assert second is not None
    ids = [item["worker_id"] for item in second["items"]]
    assert ids == ["live-b"]
    assert "live-a" not in ids


async def test_0014_refuses_to_rewrite_an_invalid_existing_worker(taskq_dsn: str) -> None:
    database = f"taskq_presence_invalid_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        assert migrations[13].id == "0014_worker_presence_projection"
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:13])
            )
            assert applied[-1] == "0013_workflow_page_composite_repair"
            await conn.exec_driver_sql(
                "INSERT INTO taskq.workers(worker_id,queues) "
                "VALUES ('invalid-existing',ARRAY['Not_Canonical'])"
            )
            await conn.commit()

            with pytest.raises(DBAPIError) as invalid:
                await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, migrations[13:14]))
            assert "existing worker presence violates bounded label domain" in str(invalid.value)
            await conn.rollback()
            version = await conn.exec_driver_sql(
                "SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'"
            )
            assert version.scalar_one() == "0.2.3"
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()


async def test_0014_to_0015_transition_is_typed_and_metadata_only(taskq_dsn: str) -> None:
    database = f"taskq_presence_transition_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        assert migrations[14].id == "0015_activate_worker_presence"
        async with engine.connect() as conn:
            await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, migrations[:14]))
            with pytest.raises(DBAPIError) as inactive:
                await conn.exec_driver_sql("SELECT * FROM taskq.list_worker_presence()")
            assert getattr(inactive.value, "orig", inactive.value).sqlstate == "TQ501"
            await conn.rollback()

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[14:15])
            )
            assert applied == ["0015_activate_worker_presence"]
            page = await conn.exec_driver_sql("SELECT * FROM taskq.list_worker_presence()")
            row = page.one()
            assert row._mapping["items"] == []
            capabilities = await conn.exec_driver_sql(
                "SELECT value FROM taskq.meta WHERE key='capabilities'"
            )
            assert capabilities.scalar_one()["active"][-1] == "worker_presence"
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()
