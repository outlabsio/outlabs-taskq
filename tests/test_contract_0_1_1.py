"""ADR-012 / SQL contract 0.1.1 executable boundary vectors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import Migration, _migrate_impl, discover_migrations, migrate

pytestmark = pytest.mark.taskq_sql

RoleConnect = Callable[[str], Awaitable[asyncpg.Connection]]
ZERO_UUID = UUID(int=0)


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _make_queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'adr012')", name)


async def _enqueue_claim(
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    queue: str,
    *,
    worker: str = "adr012-worker",
) -> tuple[UUID, UUID]:
    enqueued = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'adr012.echo', '{}'::jsonb)", queue
    )
    assert enqueued is not None
    batch = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1, $2)", queue, worker)
    assert batch is not None and batch["state"] == "claimed"
    job = batch["jobs"][0]
    return job["job_id"], job["attempt_id"]


async def _assert_tq422(conn: asyncpg.Connection, query: str, *args: object) -> None:
    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await conn.fetchrow(query, *args)
    assert excinfo.value.sqlstate == "TQ422"


@pytest.mark.parametrize("mode", ["fresh", "upgrade"])
async def test_contract_chain_installs_fresh_and_upgrades_from_0001(
    taskq_dsn: str, mode: str
) -> None:
    """Both supported paths end at the same full 0.2.2 ledger and activation posture."""
    database = f"taskq_adr012_{mode}_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        assert [migration.id for migration in migrations] == [
            "0001_initial",
            "0002_contract_0_1_1",
            "0003_contract_0_1_2",
            "0004_read_models",
            "0005_read_model_conformance",
            "0006_activate_ready_read_model",
            "0007_admission_reservations",
            "0008_followups",
            "0009_workflows",
            "0010_schedules",
        ]
        async with engine.connect() as conn:
            if mode == "upgrade":
                first: Migration = migrations[0]
                applied = await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, [first]))
                assert applied == ["0001_initial"]
                before = await conn.exec_driver_sql(
                    "SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'"
                )
                assert before.scalar_one() == "0.1"
                await conn.commit()
                second: Migration = migrations[1]
                applied = await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, [second]))
                assert applied == ["0002_contract_0_1_1"]
                at_0_1_1 = await conn.exec_driver_sql(
                    "SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'"
                )
                assert at_0_1_1.scalar_one() == "0.1.1"
                await conn.commit()
            await migrate(conn)
            version = await conn.exec_driver_sql(
                "SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'"
            )
            assert version.scalar_one() == "0.2.2"
            ledger = await conn.exec_driver_sql(
                "SELECT id FROM taskq.schema_migrations ORDER BY id"
            )
            assert list(ledger.scalars()) == [
                "0001_initial",
                "0002_contract_0_1_1",
                "0003_contract_0_1_2",
                "0004_read_models",
                "0005_read_model_conformance",
                "0006_activate_ready_read_model",
                "0007_admission_reservations",
                "0008_followups",
                "0009_workflows",
                "0010_schedules",
            ]
            count = await conn.exec_driver_sql(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='taskq' AND p.prokind='f'"
            )
            assert count.scalar_one() == 62
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()


async def test_ready_view_transitions_from_tq501_to_page_at_0006(taskq_dsn: str) -> None:
    """The immutable upgrade, not manual DML, is the only ready activation vehicle."""
    database = f"taskq_adr019_activation_{uuid4().hex}"
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
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:5])
            )
            assert applied[-1] == "0005_read_model_conformance"
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.ensure_queue('adr019_activation', '{}'::jsonb, 'adr019')"
            )
            await conn.commit()
            with pytest.raises(DBAPIError) as inactive:
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.list_jobs('adr019_activation', 'ready')"
                )
            assert getattr(inactive.value, "orig", inactive.value).sqlstate == "TQ501"
            await conn.rollback()

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[5:6])
            )
            assert applied == ["0006_activate_ready_read_model"]
            page = await conn.exec_driver_sql(
                "SELECT * FROM taskq.list_jobs('adr019_activation', 'ready')"
            )
            assert page.first() is not None
            await conn.commit()
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()


async def test_admission_capability_transitions_only_at_0007(taskq_dsn: str) -> None:
    """The bridge accepts 0.1.5, but migration 0007 alone activates admission."""
    database = f"taskq_adr023_activation_{uuid4().hex}"
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
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:6])
            )
            assert applied[-1] == "0006_activate_ready_read_model"
            before = await conn.exec_driver_sql(
                "SELECT value FROM taskq.meta WHERE key='capabilities'"
            )
            assert before.scalar_one() == {"active": ["read_model_list_ready"]}
            absent = await conn.exec_driver_sql(
                "SELECT to_regprocedure("
                "'taskq.reserve_admission(text,text,text,uuid,integer,integer)')"
            )
            assert absent.scalar_one() is None
            await conn.commit()

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[6:7])
            )
            assert applied == ["0007_admission_reservations"]
            version = await conn.exec_driver_sql(
                "SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'"
            )
            assert version.scalar_one() == "0.1.5"
            after = await conn.exec_driver_sql(
                "SELECT value FROM taskq.meta WHERE key='capabilities'"
            )
            assert after.scalar_one() == {
                "active": ["admission_reservations", "read_model_list_ready"]
            }
            present = await conn.exec_driver_sql(
                "SELECT to_regprocedure("
                "'taskq.reserve_admission(text,text,text,uuid,integer,integer)')"
            )
            assert present.scalar_one() is not None
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()


async def test_explicit_null_bounded_arguments_raise_tq422(
    pg: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _make_queue(operator, "adr012_nulls")
    await _assert_tq422(
        runner,
        "SELECT * FROM taskq.claim_jobs($1, $2, p_batch => NULL)",
        "adr012_nulls",
        "adr012-worker",
    )
    await _assert_tq422(
        runner,
        "SELECT * FROM taskq.release_job($1, $2, $3, p_delay_seconds => NULL)",
        ZERO_UUID,
        ZERO_UUID,
        "adr012-worker",
    )
    await _assert_tq422(
        operator,
        "SELECT * FROM taskq.redrive_failed($1, NULL, $2)",
        "adr012_nulls",
        "adr012",
    )
    await _assert_tq422(
        operator,
        "SELECT taskq.purge_queued($1, NULL, $2)",
        "adr012_nulls",
        "adr012",
    )
    await _assert_tq422(housekeeper, "SELECT taskq.tick(NULL)")

    # Omission still invokes the declared defaults.
    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1, $2)", "adr012_nulls", "adr012-worker"
    )
    assert batch is not None and batch["state"] == "empty"


async def test_truncate_utf8_returns_longest_valid_prefix(pg: asyncpg.Connection) -> None:
    cases = [
        ("x" * 2048, 2048, 2048),
        ("x" * 2049, 2048, 2048),
        ("é" * 2000, 2048, 2048),
        ("🙂" * 1000, 500, 500),
        (None, 2048, None),
    ]
    for value, limit, expected_bytes in cases:
        row = await pg.fetchrow(
            "SELECT taskq.truncate_utf8($1, $2) AS value, "
            "octet_length(taskq.truncate_utf8($1, $2)) AS bytes",
            value,
            limit,
        )
        assert row is not None
        assert row["bytes"] == expected_bytes
        if value is not None:
            assert value.startswith(row["value"])


async def test_fail_snooze_and_cancel_persist_byte_bounded_diagnostics(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    reason = "é" * 6000

    await _make_queue(operator, "adr012_fail")
    job_id, attempt_id = await _enqueue_claim(producer, runner, "adr012_fail")
    failed = await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1, $2, $3, $4, false)",
        job_id,
        attempt_id,
        "adr012-worker",
        reason,
    )
    assert failed is not None and failed["job_status"] == "failed"
    fail_row = await pg.fetchrow(
        "SELECT octet_length(j.error) AS job_error, octet_length(a.error) AS attempt_error "
        "FROM taskq.jobs j JOIN taskq.job_attempts a ON a.id=$2 WHERE j.id=$1",
        job_id,
        attempt_id,
    )
    assert dict(fail_row) == {"job_error": 2048, "attempt_error": 2048}

    await _make_queue(operator, "adr012_snooze")
    job_id, attempt_id = await _enqueue_claim(producer, runner, "adr012_snooze")
    snoozed = await runner.fetchrow(
        "SELECT * FROM taskq.snooze_job($1, $2, $3, 0, $4)",
        job_id,
        attempt_id,
        "adr012-worker",
        reason,
    )
    assert snoozed is not None and snoozed["job_status"] == "queued"
    assert (
        await pg.fetchval(
            "SELECT octet_length(error) FROM taskq.job_attempts WHERE id=$1", attempt_id
        )
        == 2048
    )

    await _make_queue(operator, "adr012_cancel")
    enqueued = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'adr012.echo', '{}'::jsonb)", "adr012_cancel"
    )
    assert enqueued is not None
    cancelled = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_job($1, 'adr012', $2)", enqueued["job_id"], reason
    )
    assert cancelled is not None and cancelled["job_status"] == "cancelled"
    cancel_row = await pg.fetchrow(
        "SELECT octet_length(error) AS error, octet_length(cancel_reason) AS reason "
        "FROM taskq.jobs WHERE id=$1",
        enqueued["job_id"],
    )
    assert dict(cancel_row) == {"error": 2048, "reason": 2048}

    for checked_job_id in (job_id, enqueued["job_id"]):
        event_bytes = await pg.fetchval(
            "SELECT max(octet_length(message)) FROM taskq.job_events "
            "WHERE job_id=$1 AND message IS NOT NULL",
            checked_job_id,
        )
        assert event_bytes is not None and event_bytes <= 500


async def test_application_roles_cannot_execute_internal_truncation_helper(
    role_conn: RoleConnect,
) -> None:
    for role in (
        "taskq_producer",
        "taskq_runner",
        "taskq_observer",
        "taskq_operator",
        "taskq_housekeeper",
    ):
        conn = await role_conn(role)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("SELECT taskq.truncate_utf8('secret', 3)")
