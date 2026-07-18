"""Smoke an installed distribution from outside the source checkout (R3-F05)."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg


def _database_dsn(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    scheme = parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _create_database(admin_dsn: str, database: str) -> None:
    conn = await asyncpg.connect(_database_dsn(admin_dsn, "postgres"))
    try:
        await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


async def _drop_database(admin_dsn: str, database: str) -> None:
    conn = await asyncpg.connect(_database_dsn(admin_dsn, "postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await conn.close()


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("core", "http", "outlabs"), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--admin-dsn")
    args = parser.parse_args()

    import taskq
    import taskq.client
    import taskq.errors
    import taskq.protocol
    import taskq.registry
    import taskq.sql.transport
    import taskq.transport
    from taskq import TaskQ, TaskRegistry
    from taskq.sql import discover_migrations
    from taskq.sql.manifest import FUNCTIONS

    package_file = Path(taskq.__file__).resolve()
    repo = args.repo.resolve()
    assert not package_file.is_relative_to(repo), (package_file, repo)
    assert "fastapi" not in sys.modules
    assert "outlabs_auth" not in sys.modules
    if args.mode == "core":
        assert importlib.util.find_spec("fastapi") is None
        assert importlib.util.find_spec("outlabs_auth") is None
    elif args.mode == "http":
        assert importlib.util.find_spec("fastapi") is not None
        assert importlib.util.find_spec("outlabs_auth") is None
        import fastapi  # noqa: F401
    else:
        assert importlib.util.find_spec("fastapi") is not None
        assert importlib.util.find_spec("outlabs_auth") is not None
        import fastapi  # noqa: F401
        import outlabs_auth  # noqa: F401

    assert TaskQ is not None
    assert TaskRegistry is not None

    assert [migration.id for migration in discover_migrations()] == [
        "0001_initial",
        "0002_contract_0_1_1",
    ]
    assert len(FUNCTIONS) == 40

    if args.mode != "core":
        return
    if not args.admin_dsn:
        parser.error("--admin-dsn is required in core mode")

    # Keep the venv shim path; resolving it follows uv's interpreter symlink
    # out of the environment and loses the installed console scripts.
    bin_dir = Path(sys.executable).parent
    taskq_cli = bin_dir / "taskq"
    bench_cli = bin_dir / "taskq-bench"
    assert "usage: taskq" in _run([str(taskq_cli), "--help"], cwd=Path.cwd()).stdout
    assert "usage: taskq-bench" in _run([str(bench_cli), "--help"], cwd=Path.cwd()).stdout

    database = f"taskq_artifact_{uuid4().hex}"
    asyncio.run(_create_database(args.admin_dsn, database))
    try:
        dsn = _database_dsn(args.admin_dsn, database)
        migrated = _run([str(taskq_cli), "migrate", dsn], cwd=Path.cwd()).stdout
        assert "applied 0001_initial" in migrated
        assert "applied 0002_contract_0_1_1" in migrated
        verified = _run([str(taskq_cli), "verify", dsn], cwd=Path.cwd()).stdout
        assert "[ok] function_catalog" in verified
        assert verified.endswith("verify: ok\n")
    finally:
        asyncio.run(_drop_database(args.admin_dsn, database))


if __name__ == "__main__":
    main()
