"""R3-F04 remaining T8 installer, concurrency, CLI, and compatibility vectors."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.cli import main
from taskq.sql import TASKQ_ROLES, migrate, verify

pytestmark = pytest.mark.taskq_sql


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _sync_sqlalchemy_dsn(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(
        ("postgresql+psycopg", parts.netloc, f"/{database}", parts.query, parts.fragment)
    )


async def test_clean_concurrent_installers_serialize_to_one_chain(taskq_dsn: str) -> None:
    database = f"taskq_r3_concurrent_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    dsn = _database_dsn(taskq_dsn, database, sqlalchemy=True)
    engines = [create_async_engine(dsn), create_async_engine(dsn)]

    async def install(index: int) -> list[str]:
        async with engines[index].connect() as conn:
            return await migrate(conn)

    try:
        checkpoints = await asyncio.gather(install(0), install(1), return_exceptions=True)
        assert all(isinstance(result, Exception) for result in checkpoints)
        assert all(
            getattr(getattr(result, "orig", result), "sqlstate", None) == "TQ422"
            for result in checkpoints
        )
        binder = await asyncpg.connect(_database_dsn(taskq_dsn, database))
        try:
            identity = await binder.fetchrow("SELECT * FROM taskq.get_target_identity()")
            assert identity is not None and identity["environment"] == "unbound"
            await binder.fetchrow(
                "SELECT * FROM taskq.bind_target_identity($1,$2,$3,$4,$5,$6)",
                identity["installation_id"],
                "test",
                "installer-matrix",
                identity["binding_version"],
                False,
                None,
            )
        finally:
            await binder.close()

        results = await asyncio.gather(install(0), install(1))
        assert sorted(results, key=len) == [
            [],
            [
                "0020_standalone_scheduler",
                "0021_cli_read_model",
                "0022_queue_counters",
                "0023_activate_queue_counters",
            ],
        ]
        async with engines[0].connect() as conn:
            report = await verify(conn)
        assert report.ok
    finally:
        for engine in engines:
            await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}"')
        await admin.close()


async def test_managed_owner_bootstraps_and_retains_owner_membership(
    taskq_dsn: str,
) -> None:
    """Managed CREATEROLE owners can install, bind, and upgrade without superuser."""

    role = f"taskq_managed_owner_{uuid4().hex}"
    database = f"taskq_managed_install_{uuid4().hex}"
    password = uuid4().hex
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    server_version = int(await admin.fetchval("SHOW server_version_num"))

    engine = None
    database_created = False
    role_created = False
    try:
        await admin.execute(
            f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' CREATEROLE CREATEDB BYPASSRLS"
        )
        role_created = True
        existing_roles = await admin.fetch(
            "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname = ANY($1::text[])",
            list(TASKQ_ROLES),
        )
        for existing_role in existing_roles:
            grant_options = (
                "WITH ADMIN TRUE, SET FALSE, INHERIT FALSE"
                if server_version >= 180000
                else "WITH ADMIN OPTION"
            )
            await admin.execute(f'GRANT "{existing_role["rolname"]}" TO "{role}" {grant_options}')
        await admin.execute(f'CREATE DATABASE "{database}" OWNER "{role}"')
        database_created = True

        parts = urlsplit(taskq_dsn)
        netloc = f"{quote(role)}:{quote(password)}@{parts.hostname}"
        if parts.port is not None:
            netloc += f":{parts.port}"
        dsn = urlunsplit(
            ("postgresql+asyncpg", netloc, f"/{database}", parts.query, parts.fragment)
        )
        engine = create_async_engine(dsn)
        async with engine.connect() as conn:
            with pytest.raises(Exception) as checkpoint:
                await migrate(conn)
            assert (
                getattr(getattr(checkpoint.value, "orig", checkpoint.value), "sqlstate", None)
                == "TQ422"
            )
            assert (
                await conn.exec_driver_sql(
                    "SELECT pg_catalog.pg_has_role(current_user, 'taskq_owner', 'SET')"
                )
            ).scalar_one() is True
            identity = (
                (await conn.exec_driver_sql("SELECT * FROM taskq.get_target_identity()"))
                .mappings()
                .one()
            )
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.bind_target_identity($1,$2,$3,$4,$5,$6)",
                (
                    identity["installation_id"],
                    "staging",
                    "managed-installer-matrix",
                    identity["binding_version"],
                    False,
                    None,
                ),
            )
            await conn.commit()
            assert await migrate(conn) == [
                "0020_standalone_scheduler",
                "0021_cli_read_model",
                "0022_queue_counters",
                "0023_activate_queue_counters",
            ]
            report = await verify(conn)
            assert report.ok
    finally:
        if engine is not None:
            await engine.dispose()
        if database_created:
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        if role_created:
            await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


def test_cli_migrate_and_verify_success(taskq_dsn: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["db", "plan", "--dsn", taskq_dsn, "-o", "json"]) == 0
    plan = json.loads(capsys.readouterr().out)["data"]
    assert plan["changes"] is False
    assert (
        main(
            [
                "db",
                "migrate",
                "--dsn",
                taskq_dsn,
                "--actor",
                "installer-matrix",
                "--expected-environment",
                "test",
                "--plan-digest",
                plan["plan_digest"],
                "--yes",
                "-o",
                "json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data"]["up_to_date"] is True
    assert main(["db", "verify", "--dsn", taskq_dsn, "-o", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["ok"] is True


async def test_sync_psycopg_cli_preserves_literal_percent_migration_sql(
    taskq_dsn: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = f"taskq_sync_psycopg_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    dsn = _sync_sqlalchemy_dsn(taskq_dsn, database)
    try:
        assert await asyncio.to_thread(main, ["db", "plan", "--dsn", dsn, "-o", "json"]) == 0
        first_plan = json.loads(capsys.readouterr().out)["data"]
        assert (
            await asyncio.to_thread(
                main,
                [
                    "db",
                    "migrate",
                    "--dsn",
                    dsn,
                    "--actor",
                    "installer-matrix",
                    "--expected-environment",
                    "test",
                    "--plan-digest",
                    first_plan["plan_digest"],
                    "--yes",
                    "-o",
                    "json",
                ],
            )
            == 3
        )
        assert capsys.readouterr().out == ""
        binder = await asyncpg.connect(_database_dsn(taskq_dsn, database))
        try:
            identity = await binder.fetchrow("SELECT * FROM taskq.get_target_identity()")
            assert identity is not None
            await binder.fetchrow(
                "SELECT * FROM taskq.bind_target_identity($1,$2,$3,$4,$5,$6)",
                identity["installation_id"],
                "test",
                "installer-matrix",
                identity["binding_version"],
                False,
                None,
            )
        finally:
            await binder.close()
        assert await asyncio.to_thread(main, ["db", "plan", "--dsn", dsn, "-o", "json"]) == 0
        second_plan = json.loads(capsys.readouterr().out)["data"]
        assert [item["id"] for item in second_plan["pending"]] == [
            "0020_standalone_scheduler",
            "0021_cli_read_model",
            "0022_queue_counters",
            "0023_activate_queue_counters",
        ]
        assert (
            await asyncio.to_thread(
                main,
                [
                    "db",
                    "migrate",
                    "--dsn",
                    dsn,
                    "--actor",
                    "installer-matrix",
                    "--expected-environment",
                    "test",
                    "--plan-digest",
                    second_plan["plan_digest"],
                    "--yes",
                    "-o",
                    "json",
                ],
            )
            == 0
        )
        migrated = json.loads(capsys.readouterr().out)["data"]
        assert migrated["applied"] == [
            "0020_standalone_scheduler",
            "0021_cli_read_model",
            "0022_queue_counters",
            "0023_activate_queue_counters",
        ]
        assert await asyncio.to_thread(main, ["db", "verify", "--dsn", dsn, "-o", "json"]) == 0
        assert json.loads(capsys.readouterr().out)["data"]["ok"] is True
    finally:
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()


async def test_cli_verify_failure_has_exit_one_and_named_check(
    taskq_dsn: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute("ALTER ROLE taskq_housekeeper LOGIN")
        assert (
            await asyncio.to_thread(main, ["db", "verify", "--dsn", taskq_dsn, "-o", "json"]) == 1
        )
        output = json.loads(capsys.readouterr().out)
        assert output["data"]["ok"] is False
        assert any(
            check["name"] == "role_manifest" and check["ok"] is False
            for check in output["data"]["checks"]
        )
    finally:
        await admin.execute("ALTER ROLE taskq_housekeeper NOLOGIN")
        await admin.close()
