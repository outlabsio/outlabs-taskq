#!/usr/bin/env python3
"""WFC-I04 conditional million-member B9 proof on a disposable database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

POLICY_A = "a" * 64
POLICY_B = "b" * 64
UNSUPPORTED = tuple(character * 64 for character in "cdefghij")
WORKFLOW_ID = "00000000-0000-7000-8000-000000000001"
NOW = "2026-07-28 12:00:00+00"
NOW_DT = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
SETTINGS = (
    "block_size",
    "effective_cache_size",
    "jit",
    "max_parallel_workers_per_gather",
    "random_page_cost",
    "seq_page_cost",
    "shared_buffers",
    "work_mem",
)


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    pending = [plan]
    result: list[dict[str, Any]] = []
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(node.get("Plans", ()))
    return result


async def _explain(conn: asyncpg.Connection, query: str) -> dict[str, Any]:
    raw = await conn.fetchval(f"EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) {query}")
    document = json.loads(raw) if isinstance(raw, str) else raw
    plan = document[0]["Plan"]
    nodes = _nodes(plan)
    return {
        "execution_time_ms": document[0].get("Execution Time", 0.0),
        "planning_time_ms": document[0].get("Planning Time", 0.0),
        "actual_rows": int(plan.get("Actual Rows", 0)),
        "indexes": sorted({node["Index Name"] for node in nodes if node.get("Index Name")}),
        "jobs_sequential_scan": any(
            node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "jobs"
            for node in nodes
        ),
        "sorts": [
            {
                "actual_rows": int(node.get("Actual Rows", 0)),
                "method": node.get("Sort Method"),
            }
            for node in nodes
            if node.get("Node Type") in {"Sort", "Incremental Sort"}
        ],
        "max_scan_rows": max(
            (
                int(node.get("Actual Rows", 0)) * int(node.get("Actual Loops", 1))
                for node in nodes
                if node.get("Relation Name") == "jobs"
            ),
            default=0,
        ),
        "shared_hit_blocks": sum(int(node.get("Shared Hit Blocks", 0)) for node in nodes),
        "shared_read_blocks": sum(int(node.get("Shared Read Blocks", 0)) for node in nodes),
        "wal_records": sum(int(node.get("WAL Records", 0)) for node in nodes),
        "wal_bytes": sum(int(node.get("WAL Bytes", 0)) for node in nodes),
        "plan": document,
    }


async def _setup(conn: asyncpg.Connection, rows: int) -> dict[str, float]:
    started = time.monotonic()
    await conn.execute("DROP SCHEMA IF EXISTS wfc_i04 CASCADE")
    await conn.execute(
        """
        CREATE SCHEMA wfc_i04;
        CREATE TABLE wfc_i04.workflows (
            id uuid PRIMARY KEY,
            status text NOT NULL,
            sealed_at timestamptz,
            cancel_requested_at timestamptz,
            updated_at timestamptz NOT NULL
        );
        CREATE TABLE wfc_i04.workflow_member_counts (
            workflow_id uuid PRIMARY KEY,
            blocked bigint NOT NULL,
            queued bigint NOT NULL,
            running bigint NOT NULL,
            succeeded bigint NOT NULL,
            failed bigint NOT NULL,
            cancelled bigint NOT NULL,
            admitted_total bigint NOT NULL
        );
        CREATE TABLE wfc_i04.jobs (
            id uuid PRIMARY KEY,
            workflow_id uuid NOT NULL,
            queue text NOT NULL,
            status text NOT NULL,
            priority smallint NOT NULL,
            scheduled_at timestamptz NOT NULL,
            cancel_requested_at timestamptz,
            continuation_policy_hash text,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX jobs_claim_policy_idx
            ON wfc_i04.jobs (
                queue,continuation_policy_hash,priority,scheduled_at,id
            )
            WHERE status='queued' AND cancel_requested_at IS NULL;
        CREATE INDEX jobs_workflow_state_idx
            ON wfc_i04.jobs (workflow_id,status,id);
        CREATE INDEX jobs_workflow_cancel_idx
            ON wfc_i04.jobs (workflow_id,id)
            WHERE status IN ('blocked','queued')
               OR (status='running' AND cancel_requested_at IS NULL);
        CREATE INDEX taskq_jobs_workflow_page_idx
            ON wfc_i04.jobs (workflow_id,id)
            INCLUDE (queue,status,priority,scheduled_at,continuation_policy_hash);
        CREATE INDEX workflows_cancel_idx
            ON wfc_i04.workflows(cancel_requested_at,id)
            WHERE cancel_requested_at IS NOT NULL AND status='running';
        """
    )
    await conn.execute(
        """
        INSERT INTO wfc_i04.workflows
        VALUES ($1,'running',NULL,NULL,$2::timestamptz)
        """,
        WORKFLOW_ID,
        NOW_DT,
    )
    await conn.execute(
        "INSERT INTO wfc_i04.workflow_member_counts VALUES ($1,0,$2,0,0,0,0,$2)",
        WORKFLOW_ID,
        rows,
    )
    created = time.monotonic()
    await conn.execute(
        """
        INSERT INTO wfc_i04.jobs(
            id,workflow_id,queue,status,priority,scheduled_at,
            continuation_policy_hash
        )
        SELECT
            md5(g::text)::uuid,$2,'wfc_i04','queued',
            CASE WHEN g <= ($1 * 96 / 100) THEN 0 ELSE 100 END,
            $3::timestamptz + (g % 1000) * interval '1 microsecond',
            CASE
                WHEN g <= ($1 * 96 / 100)
                    THEN (ARRAY[$4,$5,$6,$7,$8,$9,$10,$11])[1 + (g % 8)]
                WHEN g <= ($1 * 98 / 100) THEN NULL
                WHEN g <= ($1 * 99 / 100) THEN $12
                ELSE $13
            END
        FROM generate_series(1,$1) AS seed(g)
        """,
        rows,
        WORKFLOW_ID,
        NOW_DT,
        *UNSUPPORTED,
        POLICY_A,
        POLICY_B,
        timeout=300,
    )
    inserted = time.monotonic()
    await conn.execute("ANALYZE wfc_i04.jobs")
    return {
        "ddl_seconds": created - started,
        "insert_seconds": inserted - created,
        "analyze_seconds": time.monotonic() - inserted,
    }


def _queries() -> dict[str, str]:
    base = (
        "queue='wfc_i04' AND status='queued' "
        "AND cancel_requested_at IS NULL "
        f"AND scheduled_at<=timestamptz '{NOW}'"
    )
    return {
        "supported_claim": f"""
            WITH frontier AS MATERIALIZED (
                (SELECT id,priority,scheduled_at FROM wfc_i04.jobs
                 WHERE {base} AND continuation_policy_hash IS NULL
                 ORDER BY continuation_policy_hash,priority,scheduled_at,id LIMIT 50)
                UNION ALL
                SELECT candidate.id,candidate.priority,candidate.scheduled_at
                FROM unnest(ARRAY['{POLICY_A}'::text,'{POLICY_B}'::text]) supported(policy)
                CROSS JOIN LATERAL (
                    SELECT id,priority,scheduled_at FROM wfc_i04.jobs
                    WHERE {base} AND continuation_policy_hash=supported.policy
                    ORDER BY continuation_policy_hash,priority,scheduled_at,id LIMIT 50
                ) candidate
            )
            SELECT id FROM frontier ORDER BY priority,scheduled_at,id LIMIT 50
        """,
        "targeted_claim": f"""
            SELECT id FROM wfc_i04.jobs
            WHERE id=md5('999999')::uuid AND {base}
              AND continuation_policy_hash=ANY(
                  ARRAY['{POLICY_A}'::text,'{POLICY_B}'::text]
              ) LIMIT 1
        """,
        "workflow_page": f"""
            SELECT id,queue,status,priority,scheduled_at,continuation_policy_hash
            FROM wfc_i04.jobs
            WHERE workflow_id='{WORKFLOW_ID}'::uuid
            ORDER BY id LIMIT 101
        """,
        "finalization_nonterminal": f"""
            SELECT blocked+queued+running AS live,failed,cancelled
            FROM wfc_i04.workflow_member_counts
            WHERE workflow_id='{WORKFLOW_ID}'::uuid
        """,
        "cancellation_frontier": f"""
            SELECT j.id
            FROM wfc_i04.jobs j
            WHERE j.workflow_id='{WORKFLOW_ID}'::uuid
              AND (
                j.status IN ('blocked','queued')
                OR (j.status='running' AND j.cancel_requested_at IS NULL)
              )
            ORDER BY j.id LIMIT 100
            FOR UPDATE OF j SKIP LOCKED
        """,
        "cancelling_workflows_frontier": """
            SELECT id FROM wfc_i04.workflows
            WHERE cancel_requested_at IS NOT NULL AND status='running'
            ORDER BY cancel_requested_at,id LIMIT 100
        """,
        "reservation": f"""
            UPDATE wfc_i04.workflow_member_counts
            SET admitted_total=admitted_total+1
            WHERE workflow_id='{WORKFLOW_ID}'::uuid
              AND admitted_total+1<=1000001
            RETURNING admitted_total
        """,
    }


async def _relation_bytes(conn: asyncpg.Connection) -> dict[str, int]:
    rows = await conn.fetch(
        """
        SELECT c.relname,pg_relation_size(c.oid) AS bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='wfc_i04' AND c.relkind IN ('r','i')
        ORDER BY c.relname
        """
    )
    return {row["relname"]: row["bytes"] for row in rows}


async def _run(dsn: str, rows: int) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("SHOW server_version")
        setup = await _setup(conn, rows)
        await conn.execute(
            "UPDATE wfc_i04.workflows SET cancel_requested_at=now() WHERE id=$1",
            WORKFLOW_ID,
        )
        plans: dict[str, Any] = {}
        for name, query in _queries().items():
            transaction = conn.transaction()
            await transaction.start()
            plans[name] = await _explain(conn, query)
            await transaction.rollback()

        terminalize_started = time.monotonic()
        await conn.execute("UPDATE wfc_i04.jobs SET status='succeeded'", timeout=300)
        await conn.execute(
            """
            UPDATE wfc_i04.workflow_member_counts
            SET blocked=0,queued=0,running=0,succeeded=$2,failed=0,cancelled=0
            WHERE workflow_id=$1
            """,
            WORKFLOW_ID,
            rows,
        )
        await conn.execute("ANALYZE wfc_i04.jobs")
        terminalize_seconds = time.monotonic() - terminalize_started
        plans["finalization_terminal"] = await _explain(
            conn,
            f"""
            SELECT blocked+queued+running AS live,failed,cancelled
            FROM wfc_i04.workflow_member_counts
            WHERE workflow_id='{WORKFLOW_ID}'::uuid
            """,
        )
        settings_rows = await conn.fetch(
            "SELECT name,setting FROM pg_settings WHERE name=ANY($1::text[]) ORDER BY name",
            list(SETTINGS),
        )
        settings = {row["name"]: row["setting"] for row in settings_rows}
        assertions = {
            "supported_claim_no_seq_scan": not plans["supported_claim"]["jobs_sequential_scan"],
            "supported_claim_uses_policy_index": "jobs_claim_policy_idx"
            in plans["supported_claim"]["indexes"],
            "targeted_uses_primary_key": "jobs_pkey" in plans["targeted_claim"]["indexes"],
            "workflow_page_no_seq_scan": not plans["workflow_page"]["jobs_sequential_scan"],
            "workflow_page_no_sort": not plans["workflow_page"]["sorts"],
            "workflow_page_bounded": plans["workflow_page"]["actual_rows"] <= 101,
            "workflow_page_scans_at_most_limit_plus_one": plans["workflow_page"]["max_scan_rows"]
            <= 101,
            "nonterminal_finalization_uses_counter_pk": (
                "workflow_member_counts_pkey" in plans["finalization_nonterminal"]["indexes"]
            ),
            "terminal_finalization_uses_counter_pk": (
                "workflow_member_counts_pkey" in plans["finalization_terminal"]["indexes"]
            ),
            "cancellation_no_seq_scan": not plans["cancellation_frontier"]["jobs_sequential_scan"],
            "cancellation_uses_frontier_index": "jobs_workflow_cancel_idx"
            in plans["cancellation_frontier"]["indexes"],
            "cancelling_workflows_uses_frontier_index": "workflows_cancel_idx"
            in plans["cancelling_workflows_frontier"]["indexes"],
            "cancellation_bounded": plans["cancellation_frontier"]["actual_rows"] <= 100,
            "reservation_uses_counter_pk": "workflow_member_counts_pkey"
            in plans["reservation"]["indexes"],
        }
        return {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "postgres": {
                "version": version,
                "database": await conn.fetchval("SELECT current_database()"),
                "settings": settings,
                "settings_sha256": hashlib.sha256(
                    json.dumps(settings, sort_keys=True).encode()
                ).hexdigest(),
            },
            "fixture": {
                "rows": rows,
                "unsupported_percent": 96,
                **setup,
                "terminalize_seconds": terminalize_seconds,
            },
            "relation_bytes": await _relation_bytes(conn),
            "plans": plans,
            "assertions": assertions,
            "passed": all(assertions.values()),
        }
    finally:
        await conn.execute("DROP SCHEMA IF EXISTS wfc_i04 CASCADE")
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 10_000:
        parser.error("--rows must be at least 10000")
    evidence = asyncio.run(_run(args.dsn, args.rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "postgres": evidence["postgres"]["version"],
                "rows": evidence["fixture"]["rows"],
                "passed": evidence["passed"],
                "assertions": evidence["assertions"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
