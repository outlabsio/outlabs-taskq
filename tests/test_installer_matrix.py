"""R3-F04 remaining T8 installer, concurrency, CLI, and compatibility vectors."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.cli import main
from taskq.sql import migrate, verify

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
            ["0020_standalone_scheduler"],
        ]
        async with engines[0].connect() as conn:
            report = await verify(conn)
        assert report.ok
    finally:
        for engine in engines:
            await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}"')
        await admin.close()


def test_cli_migrate_and_verify_success(taskq_dsn: str, capsys: pytest.CaptureFixture[str]) -> None:
    main(["migrate", taskq_dsn])
    capsys.readouterr()
    main(["migrate", taskq_dsn])
    assert "schema is up to date" in capsys.readouterr().out
    main(["verify", taskq_dsn])
    output = capsys.readouterr().out
    assert "[ok] function_catalog" in output
    assert output.endswith("verify: ok\n")


async def test_sync_psycopg_cli_preserves_literal_percent_migration_sql(
    taskq_dsn: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = f"taskq_sync_psycopg_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    dsn = _sync_sqlalchemy_dsn(taskq_dsn, database)
    try:
        with pytest.raises(Exception) as checkpoint:
            await asyncio.to_thread(main, ["migrate", dsn])
        assert (
            getattr(getattr(checkpoint.value, "orig", checkpoint.value), "sqlstate", None)
            == "TQ422"
        )
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
        await asyncio.to_thread(main, ["migrate", dsn])
        output = capsys.readouterr().out
        assert "0020_standalone_scheduler" in output
        await asyncio.to_thread(main, ["verify", dsn])
        assert capsys.readouterr().out.endswith("verify: ok\n")
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
        with pytest.raises(SystemExit) as excinfo:
            await asyncio.to_thread(main, ["verify", taskq_dsn])
        assert excinfo.value.code == 1
        output = capsys.readouterr().out
        assert "[FAIL] role_manifest" in output
        assert "rolcanlogin" in output
    finally:
        await admin.execute("ALTER ROLE taskq_housekeeper NOLOGIN")
        await admin.close()
