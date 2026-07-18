"""Opt-in million-row structural plan checks for the Stage-1 SQL kernel.

Run with ``TASKQ_PLAN_CHECKS=1`` and ``TASKQ_TEST_DSN``. Assertions inspect
index families, scan shape, and bounded result rows—not cost estimates or
hardware-dependent timings (Harness §5 method rules).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

_MILLION = 1_000_000


def _walk_plan(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("Plans", ()):  # PostgreSQL JSON plans are recursive trees.
        yield from _walk_plan(child)


async def _explain(pg: asyncpg.Connection, query: str) -> dict[str, Any]:
    raw = await pg.fetchval(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}")
    assert isinstance(raw, str)
    document = json.loads(raw)
    return document[0]["Plan"]


def _assert_index_family(plan: dict[str, Any], expected: str) -> None:
    nodes = list(_walk_plan(plan))
    indexes = {node.get("Index Name") for node in nodes if node.get("Index Name")}
    assert expected in indexes, f"expected {expected!r}, saw indexes={sorted(indexes)!r}"
    assert not any(
        node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "jobs"
        for node in nodes
    ), f"unbounded jobs Seq Scan in plan: {plan!r}"


async def _seed_million(pg: asyncpg.Connection) -> None:
    for queue in ("plan_a", "plan_b"):
        row = await pg.fetchrow(
            "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'plans')", queue
        )
        assert row is not None

    # This is plan-fixture data, not a state-transition test. One set-based
    # owner seed is necessary to build a million-row cardinality in practical
    # time; every behavioral suite continues to mutate only through functions.
    await pg.execute(
        """
        WITH seed AS (
            SELECT g,
                   CASE
                       WHEN g % 100 < 70 THEN 'queued'
                       WHEN g % 100 < 72 THEN 'running'
                       WHEN g % 100 < 85 THEN 'succeeded'
                       WHEN g % 100 < 93 THEN 'failed'
                       ELSE 'cancelled'
                   END AS status
              FROM generate_series(1, 1000000) AS g
        )
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, idempotency_key,
            concurrency_key, scheduled_at, lease_seconds, lease_expires_at,
            worker_id, current_attempt_id, attempt_count, failure_count,
            expiry_streak, max_attempts, backoff_mode, backoff_base_seconds,
            backoff_cap_seconds, outcome, created_at, updated_at, started_at,
            finished_at
        )
        SELECT md5('plan-job:' || g::text)::uuid,
               CASE WHEN g % 2 = 0 THEN 'plan_a' ELSE 'plan_b' END,
               'test.plan',
               status,
               (g % 10)::smallint,
               '{}'::jsonb,
               'plan-key-' || g::text,
               CASE WHEN status = 'running' THEN 'plan-cap-' || (g % 100)::text END,
               CASE
                   WHEN status = 'queued' AND g % 10 = 0 THEN now() + interval '1 day'
                   ELSE now() - interval '1 hour'
               END,
               300,
               CASE WHEN status = 'running' THEN now() + interval '5 minutes' END,
               CASE WHEN status = 'running' THEN 'plan-worker-' || (g % 100)::text END,
               CASE WHEN status = 'running'
                    THEN md5('plan-attempt:' || g::text)::uuid END,
               CASE WHEN status = 'running' THEN 1 ELSE 0 END,
               CASE WHEN status = 'failed' THEN 1 ELSE 0 END,
               0,
               5,
               'fixed',
               30,
               300,
               CASE status
                   WHEN 'succeeded' THEN 'success'
                   WHEN 'failed' THEN 'non_retryable'
                   WHEN 'cancelled' THEN 'canceled'
               END,
               now() - interval '2 hours',
               now() - interval '1 hour',
               CASE WHEN status IN ('running','succeeded','failed','cancelled')
                    THEN now() - interval '90 minutes' END,
               CASE WHEN status IN ('succeeded','failed','cancelled')
                    THEN now() - interval '1 hour' END
          FROM seed
        """
    )
    # Stabilize both statistics and the visibility map after the artificial
    # bulk load. Without VACUUM, a one-off freshly loaded heap may correctly
    # prefer a sequential count because an index-only scan still needs every
    # heap visibility check; steady-state autovacuum supplies this condition.
    await pg.execute("VACUUM (ANALYZE) taskq.jobs")
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == _MILLION


async def test_million_row_index_plan_families(pg: asyncpg.Connection) -> None:
    if os.environ.get("TASKQ_PLAN_CHECKS") != "1":
        pytest.skip("set TASKQ_PLAN_CHECKS=1 to seed 1M rows and run structural EXPLAIN checks")

    await _seed_million(pg)
    await pg.execute("SET jit = off")

    claim = await _explain(
        pg,
        """
        SELECT j.id FROM taskq.jobs j
         WHERE j.queue = 'plan_a' AND j.status = 'queued'
           AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
         ORDER BY j.priority, j.scheduled_at, j.id
         LIMIT 1 FOR UPDATE OF j SKIP LOCKED
        """,
    )
    _assert_index_family(claim, "jobs_claim_idx")
    assert claim["Actual Rows"] <= 1

    dedup = await _explain(
        pg,
        """
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, idempotency_key,
            scheduled_at, lease_seconds, max_attempts, backoff_mode,
            backoff_base_seconds, backoff_cap_seconds
        ) VALUES (
            taskq.uuid7(), 'plan_a', 'test.plan', 'queued', 100, '{}'::jsonb,
            'plan-key-500000', now(), 300, 5, 'fixed', 30, 300
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
        DO NOTHING
        """,
    )
    arbiter_indexes = {
        index
        for node in _walk_plan(dedup)
        for index in node.get("Conflict Arbiter Indexes", ())
    }
    assert "jobs_idem_uq" in arbiter_indexes

    reap = await _explain(
        pg,
        """
        SELECT id FROM taskq.jobs
         WHERE status = 'running' AND lease_expires_at <= now() + interval '1 day'
         ORDER BY lease_expires_at
         LIMIT 100
        """,
    )
    _assert_index_family(reap, "jobs_running_idx")
    assert reap["Actual Rows"] <= 100

    ready_stats = await _explain(
        pg,
        """
        SELECT count(*) FROM taskq.jobs
         WHERE queue = 'plan_a' AND status = 'queued'
           AND cancel_requested_at IS NULL AND scheduled_at <= now()
        """,
    )
    _assert_index_family(ready_stats, "jobs_claim_idx")

    running_stats = await _explain(
        pg,
        """
        SELECT count(*) FROM taskq.jobs
         WHERE queue = 'plan_a' AND status = 'running'
        """,
    )
    _assert_index_family(running_stats, "jobs_running_idx")

    finished_stats = await _explain(
        pg,
        """
        SELECT count(*) FROM taskq.jobs
         WHERE status IN ('succeeded','failed','cancelled')
           AND finished_at <= now()
        """,
    )
    _assert_index_family(finished_stats, "jobs_finished_idx")

    # Execute the exact owner helper too; EXPLAIN cannot expose SQL nested in
    # PL/pgSQL, so the representative subqueries above provide plan evidence.
    await pg.fetchval("SELECT taskq.refresh_stats_snapshot()")
