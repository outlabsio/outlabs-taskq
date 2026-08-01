"""R3-F03 reserved-role preflight against a genuinely fresh database."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import migrate

pytestmark = pytest.mark.taskq_sql


@dataclass(frozen=True, slots=True)
class UnsafeRole:
    name: str
    mutation: str
    restoration: str
    detail: str
    setup: tuple[str, ...] = ()
    teardown: tuple[str, ...] = ()


CASES = (
    UnsafeRole(
        "login",
        "ALTER ROLE taskq_housekeeper LOGIN",
        "ALTER ROLE taskq_housekeeper NOLOGIN",
        "prohibited LOGIN",
    ),
    UnsafeRole(
        "superuser",
        "ALTER ROLE taskq_owner SUPERUSER",
        "ALTER ROLE taskq_owner NOSUPERUSER",
        "prohibited SUPERUSER",
    ),
    UnsafeRole(
        "create_role",
        "ALTER ROLE taskq_operator CREATEROLE",
        "ALTER ROLE taskq_operator NOCREATEROLE",
        "prohibited CREATEROLE",
    ),
    UnsafeRole(
        "create_database",
        "ALTER ROLE taskq_producer CREATEDB",
        "ALTER ROLE taskq_producer NOCREATEDB",
        "prohibited CREATEDB",
    ),
    UnsafeRole(
        "replication",
        "ALTER ROLE taskq_runner REPLICATION",
        "ALTER ROLE taskq_runner NOREPLICATION",
        "prohibited REPLICATION",
    ),
    UnsafeRole(
        "bypass_rls",
        "ALTER ROLE taskq_observer BYPASSRLS",
        "ALTER ROLE taskq_observer NOBYPASSRLS",
        "prohibited BYPASSRLS",
    ),
    UnsafeRole(
        "membership",
        "GRANT taskq_r3_f03_parent TO taskq_observer",
        "REVOKE taskq_r3_f03_parent FROM taskq_observer",
        "member of prohibited role 'taskq_r3_f03_parent'",
        setup=("CREATE ROLE taskq_r3_f03_parent NOLOGIN",),
        teardown=("DROP ROLE taskq_r3_f03_parent",),
    ),
)


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_unsafe_preexisting_role_is_rejected_before_fresh_install(
    case: UnsafeRole,
    taskq_dsn: str,
) -> None:
    database = f"taskq_r3_f03_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    engine = None
    mutated = False
    setup_count = 0
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        for statement in case.setup:
            await admin.execute(statement)
            setup_count += 1
        await admin.execute(case.mutation)
        mutated = True

        engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
        async with engine.connect() as conn:
            pid = (await conn.exec_driver_sql("SELECT pg_catalog.pg_backend_pid()")).scalar_one()
            await conn.commit()
            with pytest.raises(RuntimeError, match=case.detail):
                await migrate(conn)
            assert (
                await conn.exec_driver_sql("SELECT pg_catalog.to_regnamespace('taskq')")
            ).scalar_one() is None
            locks = (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM pg_catalog.pg_locks "
                    "WHERE pid=$1 AND locktype='advisory' AND granted",
                    (pid,),
                )
            ).scalar_one()
            assert locks == 0
            await conn.rollback()
    finally:
        if engine is not None:
            await engine.dispose()
        if mutated:
            await admin.execute(case.restoration)
        for statement in reversed(case.teardown[:setup_count]):
            await admin.execute(statement)
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()
