"""Generate candidate catalog evidence; never mutates source or runs DDL.

Usage: .venv/bin/python scripts/generate_catalog_fragment.py --dsn postgresql://...
The DSN is mandatory to prevent accidental default/live connections.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import _FUNCTIONS_SQL, _TABLE_SHAPES_SQL


async def generate(dsn: str) -> None:
    engine = create_async_engine(dsn)
    async with engine.connect() as conn:
        tables = [
            dict(row)
            for row in (await conn.execute(_TABLE_SHAPES_SQL, {"schema": "taskq"})).mappings()
        ]
        functions = [
            dict(row)
            for row in (await conn.execute(_FUNCTIONS_SQL, {"schema": "taskq"})).mappings()
        ]
        constraints = [
            dict(row)
            for row in (
                await conn.execute(
                    text("""
            SELECT rel.relname, count(*) AS item_count,
                   pg_catalog.md5(pg_catalog.string_agg(
                       con.conname || '|' || con.contype::text || '|' ||
                       pg_catalog.pg_get_constraintdef(con.oid, false), E'\\n' ORDER BY con.conname
                   )) AS digest
              FROM pg_catalog.pg_constraint con
              JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
              JOIN pg_catalog.pg_namespace n ON n.oid = rel.relnamespace
             WHERE n.nspname = 'taskq' AND con.contype <> 'n'
             GROUP BY rel.relname ORDER BY rel.relname
        """)
                )
            ).mappings()
        ]
        triggers = [
            dict(row)
            for row in (
                await conn.execute(
                    text("""
            SELECT t.tgname AS relname,
                   pg_catalog.md5(pg_catalog.pg_get_triggerdef(t.oid, false)) AS digest
              FROM pg_catalog.pg_trigger t
              JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'taskq' AND NOT t.tgisinternal
             ORDER BY t.tgname
        """)
                )
            ).mappings()
        ]
    await engine.dispose()
    print(
        json.dumps(
            {
                "tables": tables,
                "functions": functions,
                "triggers": triggers,
                "constraints": constraints,
            },
            default=str,
            sort_keys=True,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True, help="explicit disposable candidate PostgreSQL DSN")
    args = parser.parse_args()
    asyncio.run(generate(args.dsn.replace("postgresql://", "postgresql+asyncpg://", 1)))


if __name__ == "__main__":
    main()
