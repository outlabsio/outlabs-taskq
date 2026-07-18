"""ADR-013 / SQL contract 0.1.2 effective-lease projection vectors."""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.errors import TaskqInternalError
from taskq.sql import Migration, _migrate_impl, discover_migrations, verify
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql

EXPECTED_CLAIMED_JOB_SHAPE = (
    ("job_id", "uuid"),
    ("queue", "text"),
    ("job_type", "text"),
    ("priority", "smallint"),
    ("payload", "jsonb"),
    ("headers", "jsonb"),
    ("progress", "jsonb"),
    ("attempt_id", "uuid"),
    ("attempt_number", "integer"),
    ("failure_count", "smallint"),
    ("max_attempts", "smallint"),
    ("lease_expires_at", "timestamp with time zone"),
    ("workflow_id", "uuid"),
    ("step_key", "text"),
    ("lease_seconds", "integer"),
)


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def test_claimed_job_append_only_catalog_and_verify(
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    rows = await pg.fetch(
        """
        SELECT a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type
          FROM pg_catalog.pg_type t
          JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
          JOIN pg_catalog.pg_class c ON c.oid = t.typrelid
          JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
         WHERE n.nspname = 'taskq' AND t.typname = 'claimed_job'
           AND a.attnum > 0 AND NOT a.attisdropped
         ORDER BY a.attnum
        """
    )
    assert tuple((row["attname"], row["data_type"]) for row in rows) == (EXPECTED_CLAIMED_JOB_SHAPE)
    engine = create_async_engine(sqlalchemy_dsn)
    try:
        async with engine.connect() as connection:
            report = await verify(connection)
        assert report.ok
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("queue_default", "stamped", "claim_override", "expected", "stored"),
    [
        (111, None, None, 111, 111),
        (111, 222, None, 222, 222),
        (111, 222, 333, 333, 222),
    ],
)
async def test_claim_returns_exact_effective_lease_duration(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    queue_default: int,
    stamped: int | None,
    claim_override: int | None,
    expected: int,
    stored: int,
) -> None:
    queue = f"adr013_{queue_default}_{stamped or 0}_{claim_override or 0}"
    profile = json.dumps({"default_lease_seconds": queue_default})
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 'adr013')", queue, profile
    )
    enqueued = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'adr013.echo', '{}'::jsonb, p_lease_seconds => $2)",
        queue,
        stamped,
    )
    assert enqueued is not None
    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1, 'adr013-worker', p_lease_seconds => $2)",
        queue,
        claim_override,
    )
    assert batch is not None and batch["state"] == "claimed"
    job = batch["jobs"][0]
    assert job["lease_seconds"] == expected

    durable = await pg.fetchrow(
        """
        SELECT j.lease_seconds AS stored_lease,
               a.lease_seconds AS attempt_lease,
               extract(epoch FROM (j.lease_expires_at - now())) AS remaining
          FROM taskq.jobs j
          JOIN taskq.job_attempts a ON a.id = j.current_attempt_id
         WHERE j.id = $1
        """,
        enqueued["job_id"],
    )
    assert durable is not None
    assert durable["stored_lease"] == stored
    assert durable["attempt_lease"] == expected
    assert expected - 5 <= float(durable["remaining"]) <= expected


async def test_pre_0_1_2_catalog_fails_claim_decode_loudly(taskq_dsn: str) -> None:
    database = f"taskq_pre_012_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        first_two: list[Migration] = migrations[:2]
        async with engine.connect() as connection:
            applied = await connection.run_sync(
                lambda sync_connection: _migrate_impl(sync_connection, first_two)
            )
            assert applied == ["0001_initial", "0002_contract_0_1_1"]
            await connection.commit()
        raw = await asyncpg.connect(_database_dsn(taskq_dsn, database))
        try:
            await raw.fetchrow("SELECT * FROM taskq.ensure_queue('pre_012', '{}'::jsonb, 'audit')")
            await raw.fetchrow("SELECT * FROM taskq.enqueue('pre_012', 'audit.echo', '{}'::jsonb)")
        finally:
            await raw.close()
        transport = SqlTaskqTransport(engine)
        with pytest.raises(TaskqInternalError):
            await transport.claim("pre_012", "audit-worker")
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}"')
        await admin.close()


@pytest.mark.parametrize(
    ("argument", "value"),
    [("p_batch", 0), ("p_batch", 51), ("p_lease_seconds", 14), ("p_lease_seconds", 86401)],
)
async def test_claim_sql_bounds_raise_tq422(
    operator: asyncpg.Connection,
    runner: asyncpg.Connection,
    argument: str,
    value: int,
) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue('claim_bounds', '{}'::jsonb, 'audit')"
    )
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await runner.fetchrow(
            f"SELECT * FROM taskq.claim_jobs('claim_bounds', 'audit-worker', {argument} => $1)",
            value,
        )
    assert exc_info.value.sqlstate == "TQ422"
