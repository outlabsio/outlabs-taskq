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
        results = await asyncio.gather(install(0), install(1))
        assert sorted(results, key=len) == [
            [],
            [
                "0001_initial",
                "0002_contract_0_1_1",
                "0003_contract_0_1_2",
                "0004_read_models",
                "0005_read_model_conformance",
                "0006_activate_ready_read_model",
                "0007_admission_reservations",
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


def test_cli_migrate_and_verify_success(taskq_dsn: str, capsys: pytest.CaptureFixture[str]) -> None:
    main(["migrate", taskq_dsn])
    assert "schema is up to date" in capsys.readouterr().out
    main(["verify", taskq_dsn])
    output = capsys.readouterr().out
    assert "[ok] function_catalog" in output
    assert output.endswith("verify: ok\n")


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
