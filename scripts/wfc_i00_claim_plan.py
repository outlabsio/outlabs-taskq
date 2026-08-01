#!/usr/bin/env python3
"""Reproducible WFC-I00 policy-aware claim-plan experiment.

The experiment runs only in a caller-supplied disposable database. It creates
and removes an isolated ``wfc_i00`` schema; it never installs or changes TaskQ.
Structural plan assertions decide the index/query shape. Timing and byte
figures are reported as evidence, not universal performance thresholds.
"""

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
UNSUPPORTED_POLICIES = tuple(character * 64 for character in "cdefghij")
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

BASELINE_INDEX = "wfc_i00_jobs_claim_idx"
BASELINE_AFFINITY_INDEX = "wfc_i00_jobs_affinity_idx"
SELECTED_INDEX = "wfc_i00_jobs_claim_policy_idx"
SELECTED_AFFINITY_INDEX = "wfc_i00_jobs_affinity_policy_idx"

_BASE_PREDICATE = """
queue = 'wfc_i00'
AND status = 'queued'
AND cancel_requested_at IS NULL
AND scheduled_at <= TIMESTAMPTZ '2026-07-28 12:00:00+00'
"""

QUERIES = {
    "null_policy": f"""
        SELECT id
          FROM wfc_i00.jobs
         WHERE {_BASE_PREDICATE}
           AND continuation_policy_hash IS NULL
         ORDER BY continuation_policy_hash, priority, scheduled_at, id
         LIMIT 50
    """,
    "supported_policy_direct": f"""
        SELECT id
          FROM wfc_i00.jobs
         WHERE {_BASE_PREDICATE}
           AND (
               continuation_policy_hash IS NULL
               OR continuation_policy_hash = ANY(
                   ARRAY['{POLICY_A}'::text, '{POLICY_B}'::text]
               )
           )
         ORDER BY priority, scheduled_at, id
         LIMIT 50
    """,
    "supported_policy_frontier": f"""
        WITH frontier AS MATERIALIZED (
            (
                SELECT id, priority, scheduled_at
                  FROM wfc_i00.jobs
                 WHERE {_BASE_PREDICATE}
                   AND continuation_policy_hash IS NULL
                 ORDER BY continuation_policy_hash, priority, scheduled_at, id
                 LIMIT 50
            )
            UNION ALL
            SELECT candidate.id, candidate.priority, candidate.scheduled_at
              FROM unnest(ARRAY['{POLICY_A}'::text, '{POLICY_B}'::text])
                   AS supported(policy)
              CROSS JOIN LATERAL (
                  SELECT id, priority, scheduled_at
                    FROM wfc_i00.jobs
                   WHERE {_BASE_PREDICATE}
                     AND continuation_policy_hash = supported.policy
                   ORDER BY continuation_policy_hash, priority, scheduled_at, id
                   LIMIT 50
              ) AS candidate
        )
        SELECT id
          FROM frontier
         ORDER BY priority, scheduled_at, id
         LIMIT 50
    """,
    "targeted_supported": f"""
        SELECT id
          FROM wfc_i00.jobs
         WHERE id = md5('999999')::uuid
           AND {_BASE_PREDICATE}
           AND continuation_policy_hash = ANY(
               ARRAY['{POLICY_A}'::text, '{POLICY_B}'::text]
           )
         LIMIT 1
    """,
    "affinity_null_policy": f"""
        SELECT id
          FROM wfc_i00.jobs
         WHERE {_BASE_PREDICATE}
           AND affinity_key = 'preferred'
           AND continuation_policy_hash IS NULL
         ORDER BY continuation_policy_hash, priority, scheduled_at, id
         LIMIT 50
    """,
    "affinity_supported_policy_frontier": f"""
        WITH frontier AS MATERIALIZED (
            (
                SELECT id, priority, scheduled_at
                  FROM wfc_i00.jobs
                 WHERE {_BASE_PREDICATE}
                   AND affinity_key = 'preferred'
                   AND continuation_policy_hash IS NULL
                 ORDER BY continuation_policy_hash, priority, scheduled_at, id
                 LIMIT 50
            )
            UNION ALL
            SELECT candidate.id, candidate.priority, candidate.scheduled_at
              FROM unnest(ARRAY['{POLICY_A}'::text, '{POLICY_B}'::text])
                   AS supported(policy)
              CROSS JOIN LATERAL (
                  SELECT id, priority, scheduled_at
                    FROM wfc_i00.jobs
                   WHERE {_BASE_PREDICATE}
                     AND affinity_key = 'preferred'
                     AND continuation_policy_hash = supported.policy
                   ORDER BY continuation_policy_hash, priority, scheduled_at, id
                   LIMIT 50
              ) AS candidate
        )
        SELECT id
          FROM frontier
         ORDER BY priority, scheduled_at, id
         LIMIT 50
    """,
}


def _walk_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    pending = [plan]
    nodes: list[dict[str, Any]] = []
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(node.get("Plans", ()))
    return nodes


def _plan_summary(document: list[dict[str, Any]]) -> dict[str, Any]:
    plan = document[0]["Plan"]
    nodes = _walk_plan(plan)
    scans = [
        {
            "node_type": node["Node Type"],
            "index_name": node.get("Index Name"),
            "actual_rows": int(node.get("Actual Rows", 0)),
            "actual_loops": int(node.get("Actual Loops", 0)),
            "total_actual_rows": (
                int(node.get("Actual Rows", 0)) * int(node.get("Actual Loops", 0))
            ),
            "rows_removed_by_filter": int(node.get("Rows Removed by Filter", 0)),
            "shared_hit_blocks": int(node.get("Shared Hit Blocks", 0)),
            "shared_read_blocks": int(node.get("Shared Read Blocks", 0)),
        }
        for node in nodes
        if node.get("Relation Name") == "jobs" or node.get("Index Name")
    ]
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
                "node_type": node["Node Type"],
                "actual_rows": int(node.get("Actual Rows", 0)),
                "sort_method": node.get("Sort Method"),
            }
            for node in nodes
            if node.get("Node Type") in {"Sort", "Incremental Sort"}
        ],
        "cte_scans": [
            {
                "cte_name": node.get("CTE Name"),
                "actual_rows": int(node.get("Actual Rows", 0)),
            }
            for node in nodes
            if node.get("Node Type") == "CTE Scan"
        ],
        "scans": scans,
        "plan": document,
    }


async def _explain(connection: asyncpg.Connection, query: str) -> dict[str, Any]:
    raw = await connection.fetchval(
        f"EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) {query}"
    )
    document = json.loads(raw) if isinstance(raw, str) else raw
    return _plan_summary(document)


async def _settings(connection: asyncpg.Connection) -> dict[str, str]:
    rows = await connection.fetch(
        """
        SELECT name, setting
          FROM pg_catalog.pg_settings
         WHERE name = ANY($1::text[])
         ORDER BY name
        """,
        list(SETTINGS),
    )
    return {row["name"]: row["setting"] for row in rows}


async def _relation_bytes(connection: asyncpg.Connection) -> dict[str, int]:
    rows = await connection.fetch(
        """
        SELECT c.relname, pg_catalog.pg_relation_size(c.oid) AS bytes
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'wfc_i00'
           AND c.relkind IN ('r', 'i')
         ORDER BY c.relname
        """
    )
    return {row["relname"]: row["bytes"] for row in rows}


async def _setup(connection: asyncpg.Connection, rows: int) -> dict[str, float]:
    started = time.monotonic()
    await connection.execute("DROP SCHEMA IF EXISTS wfc_i00 CASCADE")
    await connection.execute("CREATE SCHEMA wfc_i00")
    await connection.execute(
        """
        CREATE UNLOGGED TABLE wfc_i00.jobs (
            id uuid PRIMARY KEY,
            queue text NOT NULL,
            status text NOT NULL,
            priority smallint NOT NULL,
            scheduled_at timestamptz NOT NULL,
            cancel_requested_at timestamptz,
            affinity_key text,
            continuation_policy_hash text
        )
        """
    )
    await connection.execute(
        f"""
        CREATE INDEX {BASELINE_AFFINITY_INDEX}
            ON wfc_i00.jobs (
                queue, affinity_key, priority, scheduled_at, id
            )
            WHERE status = 'queued'
              AND cancel_requested_at IS NULL
              AND affinity_key IS NOT NULL
        """
    )
    await connection.execute(
        """
        INSERT INTO wfc_i00.jobs (
            id, queue, status, priority, scheduled_at,
            affinity_key, continuation_policy_hash
        )
        SELECT
            md5(g::text)::uuid,
            'wfc_i00',
            'queued',
            CASE WHEN g <= ($1 * 96 / 100) THEN 0 ELSE 100 END,
            TIMESTAMPTZ '2026-07-27 12:00:00+00'
                + (g % 1000) * INTERVAL '1 microsecond',
            CASE
                WHEN g % 100 = 0 THEN 'preferred'
                ELSE 'other-' || (g % 100)::text
            END,
            CASE
                WHEN g <= ($1 * 96 / 100)
                    THEN (
                        ARRAY[
                            $2::text, $3::text, $4::text, $5::text,
                            $6::text, $7::text, $8::text, $9::text
                        ]
                    )[1 + (g % 8)]
                WHEN g <= ($1 * 98 / 100) THEN NULL
                WHEN g <= ($1 * 99 / 100) THEN $10::text
                ELSE $11::text
            END
          FROM generate_series(1, $1) AS seed(g)
        """,
        rows,
        *UNSUPPORTED_POLICIES,
        POLICY_A,
        POLICY_B,
        timeout=300,
    )
    inserted = time.monotonic()
    await connection.execute(
        f"""
        CREATE INDEX {BASELINE_INDEX}
            ON wfc_i00.jobs (queue, priority, scheduled_at, id)
            WHERE status = 'queued' AND cancel_requested_at IS NULL
        """
    )
    await connection.execute("ANALYZE wfc_i00.jobs")
    return {
        "insert_seconds": inserted - started,
        "baseline_index_and_analyze_seconds": time.monotonic() - inserted,
    }


async def _run(dsn: str, rows: int) -> dict[str, Any]:
    connection = await asyncpg.connect(dsn)
    try:
        version = await connection.fetchval("SHOW server_version")
        database = await connection.fetchval("SELECT current_database()")
        setup = await _setup(connection, rows)
        baseline = {
            name: await _explain(connection, query)
            for name, query in QUERIES.items()
            if name
            in {
                "affinity_null_policy",
                "null_policy",
                "supported_policy_direct",
                "targeted_supported",
            }
        }

        index_started = time.monotonic()
        await connection.execute(
            f"""
            CREATE INDEX {SELECTED_INDEX}
                ON wfc_i00.jobs (
                    queue, continuation_policy_hash, priority, scheduled_at, id
                )
                WHERE status = 'queued' AND cancel_requested_at IS NULL
            """
        )
        await connection.execute(
            f"""
            CREATE INDEX {SELECTED_AFFINITY_INDEX}
                ON wfc_i00.jobs (
                    queue, affinity_key, continuation_policy_hash,
                    priority, scheduled_at, id
                )
                WHERE status = 'queued'
                  AND cancel_requested_at IS NULL
                  AND affinity_key IS NOT NULL
            """
        )
        await connection.execute("ANALYZE wfc_i00.jobs")
        candidate_index_seconds = time.monotonic() - index_started
        selected = {name: await _explain(connection, query) for name, query in QUERIES.items()}
        settings = await _settings(connection)
        settings_fingerprint = hashlib.sha256(
            json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        null_plan = selected["null_policy"]
        frontier_plan = selected["supported_policy_frontier"]
        targeted_plan = selected["targeted_supported"]
        affinity_null_plan = selected["affinity_null_policy"]
        affinity_frontier_plan = selected["affinity_supported_policy_frontier"]
        max_frontier_scan_rows = max(
            (scan["actual_rows"] for scan in frontier_plan["scans"]),
            default=0,
        )
        total_frontier_scan_rows = sum(scan["total_actual_rows"] for scan in frontier_plan["scans"])
        max_frontier_sort_rows = max(
            (sort["actual_rows"] for sort in frontier_plan["sorts"]),
            default=0,
        )
        max_frontier_cte_rows = max(
            (scan["actual_rows"] for scan in frontier_plan["cte_scans"]),
            default=0,
        )
        baseline_null = baseline["null_policy"]
        direct_policy = selected["supported_policy_direct"]
        baseline_null_removed = sum(
            scan["rows_removed_by_filter"] for scan in baseline_null["scans"]
        )
        direct_policy_removed = sum(
            scan["rows_removed_by_filter"] for scan in direct_policy["scans"]
        )
        assertions = {
            "baseline_null_is_not_structurally_bounded": (
                baseline_null["jobs_sequential_scan"] or baseline_null_removed > 50
            ),
            "direct_policy_or_is_not_structurally_bounded": (
                direct_policy["jobs_sequential_scan"] or direct_policy_removed > 50
            ),
            "null_uses_policy_index": SELECTED_INDEX in null_plan["indexes"],
            "null_has_no_jobs_seq_scan": not null_plan["jobs_sequential_scan"],
            "frontier_uses_policy_index": SELECTED_INDEX in frontier_plan["indexes"],
            "frontier_has_no_jobs_seq_scan": not frontier_plan["jobs_sequential_scan"],
            "frontier_each_scan_at_most_batch": max_frontier_scan_rows <= 50,
            "frontier_total_index_rows_at_most_supported_sets_times_batch": (
                total_frontier_scan_rows <= 150
            ),
            "frontier_cte_rows_at_most_supported_sets_times_batch": (max_frontier_cte_rows <= 150),
            "frontier_sort_is_bounded_to_supported_sets_times_batch": (
                max_frontier_sort_rows <= 150
            ),
            "frontier_returns_at_most_batch": frontier_plan["actual_rows"] <= 50,
            "targeted_uses_primary_key": "jobs_pkey" in targeted_plan["indexes"],
            "targeted_has_no_jobs_seq_scan": not targeted_plan["jobs_sequential_scan"],
            "targeted_returns_at_most_one": targeted_plan["actual_rows"] <= 1,
            "affinity_null_uses_affinity_policy_index": (
                SELECTED_AFFINITY_INDEX in affinity_null_plan["indexes"]
            ),
            "affinity_null_has_no_jobs_seq_scan": (not affinity_null_plan["jobs_sequential_scan"]),
            "affinity_frontier_uses_affinity_policy_index": (
                SELECTED_AFFINITY_INDEX in affinity_frontier_plan["indexes"]
            ),
            "affinity_frontier_has_no_jobs_seq_scan": (
                not affinity_frontier_plan["jobs_sequential_scan"]
            ),
            "affinity_frontier_returns_at_most_batch": (
                affinity_frontier_plan["actual_rows"] <= 50
            ),
        }
        return {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "postgres": {
                "version": version,
                "database": database,
                "settings": settings,
                "settings_sha256": settings_fingerprint,
            },
            "fixture": {
                "rows": rows,
                "unsupported_percent": 96,
                "null_percent": 2,
                "policy_a_percent": 1,
                "policy_b_percent": 1,
                **setup,
                "candidate_index_and_analyze_seconds": candidate_index_seconds,
            },
            "indexes": {
                "baseline": (
                    f"{BASELINE_INDEX} ON (queue, priority, scheduled_at, id) "
                    "WHERE status='queued' AND cancel_requested_at IS NULL"
                ),
                "baseline_affinity": (
                    f"{BASELINE_AFFINITY_INDEX} ON (queue, affinity_key, "
                    "priority, scheduled_at, id) WHERE status='queued' "
                    "AND cancel_requested_at IS NULL "
                    "AND affinity_key IS NOT NULL"
                ),
                "selected": (
                    f"{SELECTED_INDEX} ON (queue, continuation_policy_hash, "
                    "priority, scheduled_at, id) WHERE status='queued' "
                    "AND cancel_requested_at IS NULL"
                ),
                "selected_affinity": (
                    f"{SELECTED_AFFINITY_INDEX} ON (queue, affinity_key, "
                    "continuation_policy_hash, priority, scheduled_at, id) "
                    "WHERE status='queued' AND cancel_requested_at IS NULL "
                    "AND affinity_key IS NOT NULL"
                ),
                "relation_bytes": await _relation_bytes(connection),
            },
            "baseline_plans": baseline,
            "selected_plans": selected,
            "assertions": assertions,
            "passed": all(assertions.values()),
        }
    finally:
        await connection.execute("DROP SCHEMA IF EXISTS wfc_i00 CASCADE")
        await connection.close()


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
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
