"""Shared fixtures for the taskq harness (Test & Benchmark Harness doc §1.1).

Environment
-----------
``TASKQ_TEST_DSN``
    Postgres DSN of a **scratch database** the suite may freely mutate.
    Absent → every ``taskq_sql``-marked suite skips; the T1 unit layer always
    runs. The connecting user must be a **superuser (or the database owner
    with membership in every taskq role)**: the fixtures ``SET ROLE`` into
    the capability roles (ADR-010/011), ``TRUNCATE`` taskq tables between
    tests, and the corruption tests ``ALTER`` catalog objects — none of
    which a plain application login may do.

Lifecycle (harness §1.1): the installer runs ONCE per session into schema
``taskq`` (``migrate`` is separately proven idempotent by T2); per-test
isolation is truncation of the taskq state tables, never a re-install.
``taskq.schema_migrations`` and ``taskq.meta`` survive truncation — they are
install-state, not test state.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import asyncpg
import pytest

from taskq.sql import TASKQ_ROLES, discover_migrations, migrate

DSN_ENV = "TASKQ_TEST_DSN"
_TRUNCATE_KEEP = frozenset({"schema_migrations", "meta"})

RoleConnect = Callable[[str], Awaitable[asyncpg.Connection]]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "taskq_sql: SQL-contract test needing real Postgres at $TASKQ_TEST_DSN "
        "(superuser/owner scratch database)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(DSN_ENV):
        return
    skip = pytest.mark.skip(
        reason=f"{DSN_ENV} not set — Postgres suites skip, unit layer runs (harness §1.1)"
    )
    for item in items:
        if item.get_closest_marker("taskq_sql"):
            item.add_marker(skip)


def _plain_dsn(dsn: str) -> str:
    """DSN form asyncpg accepts (any SQLAlchemy ``+driver`` suffix stripped)."""
    scheme, sep, rest = dsn.partition("://")
    return scheme.split("+", 1)[0] + sep + rest


def _asyncpg_engine_dsn(dsn: str) -> str:
    """SQLAlchemy async-engine form of the test DSN."""
    _, sep, rest = _plain_dsn(dsn).partition("://")
    return "postgresql+asyncpg" + sep + rest


@pytest.fixture(scope="session")
def taskq_dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        pytest.skip(f"{DSN_ENV} not set")
    return dsn


@pytest.fixture(scope="session")
def sqlalchemy_dsn(taskq_dsn: str) -> str:
    return _asyncpg_engine_dsn(taskq_dsn)


async def _migrate_once(dsn: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_asyncpg_engine_dsn(dsn))
    try:
        async with engine.connect() as conn:
            await migrate(conn)
    finally:
        await engine.dispose()


async def _install_stateful_time_travel(dsn: str) -> None:
    """Install the Harness §1.1 scratch-only lease clock helper.

    This schema is test infrastructure, never package migration content. The
    helpers remain owner-executed. The lease helper grants only the
    housekeeper role used by T4; admission clock control stays owner-only for
    the durable-admission expiry and retention oracles.
    """
    conn = await asyncpg.connect(_plain_dsn(dsn))
    try:
        await conn.execute(
            """
            CREATE SCHEMA IF NOT EXISTS taskq_test AUTHORIZATION taskq_owner;
            ALTER SCHEMA taskq_test OWNER TO taskq_owner;
            REVOKE ALL ON SCHEMA taskq_test FROM PUBLIC;
            GRANT USAGE ON SCHEMA taskq_test TO taskq_housekeeper;

            CREATE OR REPLACE FUNCTION taskq_test.rewind_lease(
                p_job_id uuid,
                p_by interval
            ) RETURNS boolean
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp
            AS $function$
            BEGIN
                IF p_by IS NULL OR p_by <= interval '0 seconds' THEN
                    RAISE EXCEPTION 'rewind interval must be positive';
                END IF;
                UPDATE taskq.jobs
                   SET lease_expires_at = lease_expires_at - p_by
                 WHERE id = p_job_id AND status = 'running';
                RETURN FOUND;
            END
            $function$;
            ALTER FUNCTION taskq_test.rewind_lease(uuid, interval) OWNER TO taskq_owner;
            REVOKE EXECUTE ON FUNCTION taskq_test.rewind_lease(uuid, interval) FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION taskq_test.rewind_lease(uuid, interval)
                TO taskq_housekeeper;

            CREATE OR REPLACE FUNCTION taskq_test.rewind_admission(
                p_queue text,
                p_idempotency_key text,
                p_by interval
            ) RETURNS boolean
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp
            AS $function$
            BEGIN
                IF p_by IS NULL OR p_by <= interval '0 seconds' THEN
                    RAISE EXCEPTION 'rewind interval must be positive';
                END IF;
                UPDATE taskq.admissions
                   SET reservation_expires_at = reservation_expires_at - p_by,
                       receipt_expires_at = CASE
                           WHEN receipt_expires_at IS NULL THEN NULL
                           ELSE receipt_expires_at - p_by
                       END,
                       updated_at = updated_at - p_by,
                       admitted_at = CASE
                           WHEN admitted_at IS NULL THEN NULL
                           ELSE admitted_at - p_by
                       END,
                       cancelled_at = CASE
                           WHEN cancelled_at IS NULL THEN NULL
                           ELSE cancelled_at - p_by
                       END
                 WHERE queue = p_queue AND idempotency_key = p_idempotency_key;
                RETURN FOUND;
            END
            $function$;
            ALTER FUNCTION taskq_test.rewind_admission(text, text, interval)
                OWNER TO taskq_owner;
            REVOKE EXECUTE ON FUNCTION taskq_test.rewind_admission(text, text, interval)
                FROM PUBLIC;
            """
        )
    finally:
        await conn.close()


async def _drop_stateful_time_travel(dsn: str) -> None:
    conn = await asyncpg.connect(_plain_dsn(dsn))
    try:
        await conn.execute("DROP SCHEMA IF EXISTS taskq_test CASCADE")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated(taskq_dsn: str) -> None:
    """Session-scoped one-time install into the scratch database.

    Synchronous on purpose: it runs in its own short-lived event loop so no
    connection object ever crosses into the per-test loops pytest-asyncio
    creates for the async fixtures below.
    """
    if not discover_migrations():
        pytest.skip(
            "no .sql files packaged under taskq/sql/migrations yet — "
            "T2 needs migration 0001 (authored from the 0.1 Function Manifest)"
        )
    asyncio.run(_migrate_once(taskq_dsn))


@pytest.fixture(scope="session")
def stateful_time_travel(taskq_dsn: str, migrated: None) -> Iterator[None]:
    """Scratch-only owner helper used by T4; removed after the test session."""
    asyncio.run(_install_stateful_time_travel(taskq_dsn))
    try:
        yield
    finally:
        asyncio.run(_drop_stateful_time_travel(taskq_dsn))


async def _truncate_taskq(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'taskq'")
    names = [r["tablename"] for r in rows if r["tablename"] not in _TRUNCATE_KEEP]
    if names:
        targets = ", ".join(f'taskq."{name}"' for name in names)
        for attempt in range(5):
            try:
                await conn.execute(f"TRUNCATE {targets} CASCADE")
                break
            except asyncpg.DeadlockDetectedError:
                if attempt == 4:
                    raise
                await asyncio.sleep(0.05 * (2**attempt))
    await conn.execute(
        """
        INSERT INTO taskq.control_state (key) VALUES
            ('tick'), ('janitor_daily'), ('stats_snapshot')
        ON CONFLICT (key) DO NOTHING
        """
    )
    await conn.execute(
        """
        INSERT INTO taskq.schedules (
            name, target, recurrence, catchup_policy, max_catchup, state,
            initialized, next_fire_at, version, created_by, updated_by
        ) VALUES (
            'taskq-janitor-daily',
            '{"kind":"maintenance","maintenance":"janitor"}'::jsonb,
            '{"kind":"cron","expression":"0 3 * * *","timezone":"UTC"}'::jsonb,
            'fire_once', 1, 'active', false, now(), 1,
            'test-reset', 'test-reset'
        )
        ON CONFLICT (name) DO NOTHING
        """
    )


@pytest.fixture
async def pg(taskq_dsn: str, migrated: None) -> AsyncIterator[asyncpg.Connection]:
    """Superuser assertion/fixture connection; truncates taskq state before the test."""
    conn = await asyncpg.connect(_plain_dsn(taskq_dsn))
    try:
        await _truncate_taskq(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def role_conn(
    taskq_dsn: str, migrated: None, pg: asyncpg.Connection
) -> AsyncIterator[RoleConnect]:
    """Factory: a connection running as one capability role via ``SET ROLE``.

    Harness §1.1 rule 3 / ADR-011: suites dispatch every call through the
    exact capability role it is granted to, so grant regressions fail loudly.
    """
    # Depend explicitly on ``pg``: pytest-asyncio may otherwise initialize
    # independent async fixtures in either order, allowing its per-test
    # truncate to erase a queue after a capability connection has provisioned it.
    assert not pg.is_closed()
    opened: list[asyncpg.Connection] = []

    async def connect(role: str) -> asyncpg.Connection:
        assert role in TASKQ_ROLES, f"unknown taskq capability role: {role!r}"
        conn = await asyncpg.connect(_plain_dsn(taskq_dsn))
        opened.append(conn)
        await conn.execute(f"SET ROLE {role}")  # fixed vocabulary above, never user input
        return conn

    yield connect
    for conn in opened:
        await conn.close()


@pytest.fixture
async def producer(role_conn: RoleConnect) -> asyncpg.Connection:
    return await role_conn("taskq_producer")


@pytest.fixture
async def runner(role_conn: RoleConnect) -> asyncpg.Connection:
    return await role_conn("taskq_runner")


@pytest.fixture
async def observer(role_conn: RoleConnect) -> asyncpg.Connection:
    return await role_conn("taskq_observer")


@pytest.fixture
async def operator(role_conn: RoleConnect) -> asyncpg.Connection:
    return await role_conn("taskq_operator")


@pytest.fixture
async def housekeeper(role_conn: RoleConnect) -> asyncpg.Connection:
    return await role_conn("taskq_housekeeper")
