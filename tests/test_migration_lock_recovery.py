"""R3-F02 migration advisory-lock ownership and failure recovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

import taskq.sql as sql_module
from taskq.sql import MIGRATE_LOCK_KEY, Migration, migrate, migrate_sync

pytestmark = pytest.mark.taskq_sql

Invoke = Callable[[AsyncConnection], Awaitable[list[str]]]


async def _invoke_async(conn: AsyncConnection) -> list[str]:
    return await migrate(conn)


async def _invoke_sync_adapter(conn: AsyncConnection) -> list[str]:
    return await conn.run_sync(migrate_sync)


ADAPTERS: tuple[tuple[str, Invoke], ...] = (
    ("async", _invoke_async),
    ("sync", _invoke_sync_adapter),
)


def _migration(migration_id: str, sql: str) -> Migration:
    return Migration(
        id=migration_id,
        filename=f"{migration_id}.sql",
        checksum="f" * 64,
        sql=sql,
    )


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda item: item[0])
@pytest.mark.parametrize("caller_owned", [False, True], ids=["runner_txn", "caller_txn"])
async def test_failed_migration_releases_lock_and_second_connection_recovers(
    adapter: tuple[str, Invoke],
    caller_owned: bool,
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, invoke = adapter
    failing = _migration(
        "9998_r3_f02_failure_probe",
        "CREATE TABLE taskq.r3_f02_probe (id integer); SELECT 1 / 0;",
    )
    recovered = _migration(
        failing.id,
        "CREATE TABLE taskq.r3_f02_probe (id integer);",
    )
    monkeypatch.setattr(sql_module, "discover_migrations", lambda: [failing])

    engine = create_async_engine(sqlalchemy_dsn)
    try:
        async with engine.connect() as failed_conn:
            failed_pid = (
                await failed_conn.exec_driver_sql("SELECT pg_catalog.pg_backend_pid()")
            ).scalar_one()
            await failed_conn.commit()

            caller_txn = await failed_conn.begin() if caller_owned else None
            with pytest.raises(DBAPIError):
                await invoke(failed_conn)

            if caller_txn is not None:
                assert failed_conn.in_transaction()
                await caller_txn.rollback()
            else:
                assert not failed_conn.in_transaction()

            assert (
                await pg.fetchval(
                    "SELECT count(*) FROM pg_catalog.pg_locks "
                    "WHERE pid=$1 AND locktype='advisory' AND granted",
                    failed_pid,
                )
                == 0
            )
            assert await pg.fetchval("SELECT to_regclass('taskq.r3_f02_probe')") is None
            assert not await pg.fetchval(
                "SELECT EXISTS (SELECT 1 FROM taskq.schema_migrations WHERE id=$1)",
                failing.id,
            )

            async with engine.connect() as recovery_conn:
                acquired = (
                    await recovery_conn.exec_driver_sql(
                        "SELECT pg_catalog.pg_try_advisory_lock($1)",
                        (MIGRATE_LOCK_KEY,),
                    )
                ).scalar_one()
                assert acquired is True
                released = (
                    await recovery_conn.exec_driver_sql(
                        "SELECT pg_catalog.pg_advisory_unlock($1)",
                        (MIGRATE_LOCK_KEY,),
                    )
                ).scalar_one()
                assert released is True
                await recovery_conn.commit()

                monkeypatch.setattr(sql_module, "discover_migrations", lambda: [recovered])
                recovery_txn = await recovery_conn.begin()
                try:
                    assert await invoke(recovery_conn) == [recovered.id]
                    assert (
                        await recovery_conn.exec_driver_sql(
                            "SELECT count(*) FROM taskq.schema_migrations WHERE id=$1",
                            (recovered.id,),
                        )
                    ).scalar_one() == 1
                finally:
                    await recovery_txn.rollback()

            assert await pg.fetchval("SELECT to_regclass('taskq.r3_f02_probe')") is None
    finally:
        await engine.dispose()
