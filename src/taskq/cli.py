"""taskq CLI — `taskq migrate <dsn>` / `taskq verify <dsn>` (ADR-004).

DSN handling: a bare ``postgresql://`` (or ``postgres://``) DSN runs on the
bundled asyncpg driver (the CLI drives it with a private event loop, so the
command itself is synchronous). A DSN that names an explicit synchronous
driver — e.g. ``postgresql+psycopg2://`` — runs on a plain synchronous
engine, provided that driver is installed in the host environment.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from taskq.sql import VerifyReport, migrate, migrate_sync, verify, verify_sync

_DSN_HELP = (
    "postgresql://user:pass@host:port/dbname "
    "(bare DSNs use the bundled asyncpg driver; postgresql+psycopg2://... "
    "selects a synchronous engine instead)"
)


def _normalized_url(dsn: str) -> URL:
    url = make_url(dsn)
    if url.drivername == "postgres":  # legacy alias SQLAlchemy no longer accepts
        url = url.set(drivername="postgresql")
    return url


def _is_asyncpg_url(url: URL) -> bool:
    return url.drivername == "postgresql" or url.drivername.endswith("+asyncpg")


def _run_migrate(dsn: str) -> list[str]:
    url = _normalized_url(dsn)
    if _is_asyncpg_url(url):

        async def _go() -> list[str]:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
            try:
                async with engine.connect() as conn:
                    return await migrate(conn)
            finally:
                await engine.dispose()

        return asyncio.run(_go())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return migrate_sync(conn)
    finally:
        engine.dispose()


def _run_verify(dsn: str) -> VerifyReport:
    url = _normalized_url(dsn)
    if _is_asyncpg_url(url):

        async def _go() -> VerifyReport:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
            try:
                async with engine.connect() as conn:
                    return await verify(conn)
            finally:
                await engine.dispose()

        return asyncio.run(_go())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return verify_sync(conn)
    finally:
        engine.dispose()


def _print_report(report: VerifyReport) -> None:
    for check in report.checks:
        print(f"[{'ok' if check.ok else 'FAIL'}] {check.name}")
        for detail in check.details:
            print(f"       - {detail}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="taskq",
        description="Postgres-native task queue — schema install/upgrade and drift checks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_migrate = subparsers.add_parser(
        "migrate",
        help="apply missing packaged migrations under an advisory lock (ADR-004)",
    )
    p_migrate.add_argument("dsn", help=_DSN_HELP)

    p_verify = subparsers.add_parser(
        "verify",
        help="read-only exact-manifest drift check: catalog, grants, roles, seeds, checksums",
    )
    p_verify.add_argument("dsn", help=_DSN_HELP)

    args = parser.parse_args(argv)

    if args.command == "migrate":
        applied = _run_migrate(args.dsn)
        if applied:
            for migration_id in applied:
                print(f"applied {migration_id}")
        else:
            print("schema is up to date (no pending migrations)")
    elif args.command == "verify":
        report = _run_verify(args.dsn)
        _print_report(report)
        if not report.ok:
            raise SystemExit(1)
        print("verify: ok")


if __name__ == "__main__":
    main()
