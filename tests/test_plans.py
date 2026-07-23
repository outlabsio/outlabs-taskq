"""Opt-in million-row structural plan checks for the Stage-1 SQL kernel.

Run with ``TASKQ_PLAN_CHECKS=1`` and ``TASKQ_TEST_DSN``. Assertions inspect
index families, scan shape, and bounded result rows—not cost estimates or
hardware-dependent timings (Harness §5 method rules).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

_MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class PlanBinding:
    functions: tuple[str, ...]
    body_fragments: tuple[str, ...]


_PLAN_QUERIES = {
    "claim": """
        SELECT j.id FROM taskq.jobs j
         WHERE j.queue = 'plan_a' AND j.status = 'queued'
           AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
           AND (
               j.workflow_id IS NULL
               OR NOT EXISTS (
                   SELECT 1 FROM taskq.workflows w
                    WHERE w.id = j.workflow_id
                      AND w.cancel_requested_at IS NOT NULL
               )
           )
         ORDER BY j.priority, j.scheduled_at, j.id
         LIMIT 1 FOR UPDATE OF j SKIP LOCKED
    """,
    "dedup": """
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
    "reap": """
        SELECT id FROM taskq.jobs
         WHERE status = 'running' AND lease_expires_at <= now() + interval '1 day'
         ORDER BY lease_expires_at
         LIMIT 100
    """,
    "ready_stats": """
        SELECT count(*) FROM taskq.jobs
         WHERE queue = 'plan_a' AND status = 'queued'
           AND cancel_requested_at IS NULL AND scheduled_at <= now()
    """,
    "running_stats": """
        SELECT count(*) FROM taskq.jobs
         WHERE queue = 'plan_a' AND status = 'running'
    """,
    "finished_stats": """
        SELECT count(*) FROM taskq.jobs
         WHERE status IN ('succeeded','failed','cancelled')
           AND finished_at <= now()
    """,
    "dependency_frontier": """
        SELECT d.job_id
          FROM taskq.job_deps d
          JOIN taskq.jobs j ON j.id = d.job_id
         WHERE d.depends_on = md5('workflow-plan-parent')::uuid
           AND j.status = 'blocked'
         ORDER BY d.job_id
         LIMIT 100
         FOR UPDATE OF j SKIP LOCKED
    """,
    "workflow_finalizer": """
        SELECT id
          FROM taskq.workflows
         WHERE sealed_at IS NOT NULL AND status = 'running'
         ORDER BY updated_at, id
         LIMIT 100
         FOR UPDATE SKIP LOCKED
    """,
    "schedule_claim": """
        SELECT id
          FROM taskq.schedules
         WHERE state = 'active'
           AND next_fire_at <= now()
           AND (retry_not_before IS NULL OR retry_not_before <= now())
           AND (claim_token IS NULL OR claim_expires_at <= now())
         ORDER BY next_fire_at, id
         LIMIT 100
         FOR UPDATE SKIP LOCKED
    """,
}

# ADR-019's B9 gate measures the exact finite H-08 view shapes.  These are
# owner-only plan probes: list_jobs() remains capability-gated, so a view
# cannot become observable merely because this structural evidence runs.
_READ_MODEL_PLAN_QUERIES = {
    "ready": """
        SELECT j.id FROM taskq.jobs j
         WHERE j.queue = 'plan_a' AND j.status = 'queued'
           AND j.cancel_requested_at IS NULL AND j.scheduled_at <= now()
         ORDER BY j.priority, j.scheduled_at, j.id
         LIMIT 101
    """,
    "running": """
        SELECT j.id FROM taskq.jobs j
         WHERE j.queue = 'plan_a' AND j.status = 'running'
         ORDER BY j.started_at DESC, j.id DESC
         LIMIT 101
    """,
    "finished": """
        SELECT j.id FROM taskq.jobs j
         WHERE j.queue = 'plan_a'
           AND j.status IN ('succeeded', 'failed', 'cancelled')
         ORDER BY j.finished_at DESC, j.id DESC
         LIMIT 101
    """,
    "workflow_members": """
        SELECT j.id FROM taskq.jobs j
         WHERE j.workflow_id = md5('workflow-plan:0')::uuid
         ORDER BY j.id
         LIMIT 101
    """,
    "workflow_counts": """
        SELECT blocked, queued, running, succeeded, failed, cancelled
          FROM taskq.workflow_member_counts
         WHERE workflow_id = md5('workflow-plan:0')::uuid
    """,
}

_PLAN_BINDINGS = {
    "claim": PlanBinding(
        functions=("taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)",),
        body_fragments=(
            "from taskq.jobs as j where j.queue = p_queue and j.status = 'queued'",
            "and j.scheduled_at <= now() and j.cancel_requested_at is null",
            "or not exists ( select 1 from taskq.workflows as w",
            "order by j.priority, j.scheduled_at, j.id limit 1 for update of j skip locked",
        ),
    ),
    "dedup": PlanBinding(
        functions=(
            "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)",
            "taskq.enqueue_many(text,jsonb)",
        ),
        body_fragments=(
            "on conflict (queue, idempotency_key)",
            "where idempotency_key is not null and status in ('blocked','queued','running')",
            "do nothing",
        ),
    ),
    "reap": PlanBinding(
        functions=("taskq.reap_expired(integer)",),
        body_fragments=(
            "from taskq.jobs where status = 'running' and lease_expires_at <= now()",
            "order by lease_expires_at limit greatest(coalesce(p_limit, 0), 0)",
        ),
    ),
    "ready_stats": PlanBinding(
        functions=("taskq.refresh_stats_snapshot()",),
        body_fragments=(
            "from taskq.jobs j where j.queue=q.name and j.status='queued'",
            "and j.cancel_requested_at is null and j.scheduled_at <= now()",
        ),
    ),
    "running_stats": PlanBinding(
        functions=("taskq.refresh_stats_snapshot()",),
        body_fragments=("from taskq.jobs j where j.queue=q.name and j.status='running'",),
    ),
    "finished_stats": PlanBinding(
        functions=("taskq.janitor()",),
        body_fragments=(
            "where j.queue = q.name and j.status in ('succeeded','cancelled')",
            "and j.finished_at < now() - make_interval(hours => q.retention_hours)",
        ),
    ),
    "dependency_frontier": PlanBinding(
        functions=("taskq.cancel_dependents(uuid,text,integer)",),
        body_fragments=(
            "from taskq.job_deps as d join taskq.jobs as j on j.id = d.job_id",
            "where d.depends_on = p_job_id and j.status = 'blocked'",
            "order by d.job_id limit p_limit for update of j skip locked",
        ),
    ),
    "workflow_finalizer": PlanBinding(
        functions=("taskq.finalize_workflows(integer)",),
        body_fragments=(
            "from taskq.workflows where sealed_at is not null and status = 'running'",
            "order by updated_at, id limit p_limit for update skip locked",
        ),
    ),
    "schedule_claim": PlanBinding(
        functions=("taskq.claim_schedules(text,integer,integer)",),
        body_fragments=(
            "from taskq.schedules where state = 'active'",
            "and next_fire_at <= v_now",
            "order by next_fire_at, id limit p_limit for update skip locked",
        ),
    ),
}


def _normalize_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


async def _assert_hot_path_bindings(pg: asyncpg.Connection, names: set[str] | None = None) -> None:
    selected = names or set(_PLAN_BINDINGS)
    assert selected <= set(_PLAN_QUERIES) == set(_PLAN_BINDINGS)
    for name in sorted(selected):
        binding = _PLAN_BINDINGS[name]
        for identity in binding.functions:
            definition = await pg.fetchval(
                "SELECT pg_catalog.pg_get_functiondef($1::regprocedure::oid)", identity
            )
            normalized = _normalize_sql(definition)
            for fragment in binding.body_fragments:
                assert _normalize_sql(fragment) in normalized, (
                    f"plan binding {name!r} drifted from {identity}: missing {fragment!r}"
                )


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


def _sort_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        node for node in _walk_plan(plan) if node.get("Node Type") in {"Sort", "Incremental Sort"}
    ]


async def _seed_million(pg: asyncpg.Connection) -> None:
    for queue in ("plan_a", "plan_b"):
        row = await pg.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'plans')", queue)
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


async def _seed_workflow_plan_frontiers(pg: asyncpg.Connection) -> None:
    # Synthetic owner-only plan data: 5,000 direct edges make an accidental
    # graph-wide scan visible, while 1,000 sealed workflows prove the ordered
    # finalizer frontier uses its partial index.
    await pg.execute(
        """
        INSERT INTO taskq.workflows (
            id, workflow_key, kind, status, params, stats, created_by,
            declared_queues, sealed_at, sealed_by, created_at, updated_at
        )
        SELECT md5('workflow-plan:' || g::text)::uuid,
               'workflow-plan-' || g::text,
               'dag', 'running', '{}'::jsonb, '{}'::jsonb, 'plans',
               ARRAY['plan_a'], now(), 'plans',
               now() - interval '2 hours',
               now() - interval '1 hour' + g * interval '1 microsecond'
          FROM generate_series(0, 999) AS g
        """
    )
    await pg.execute(
        """
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload,
            workflow_id, step_key, workflow_intent_hash, pending_deps,
            scheduled_at, lease_seconds, max_attempts, backoff_mode,
            backoff_base_seconds, backoff_cap_seconds, finished_at
        )
        SELECT md5('workflow-plan-parent')::uuid,
               'plan_a', 'test.plan', 'succeeded', 100, '{}'::jsonb,
               md5('workflow-plan:0')::uuid, 'parent', repeat('a', 64), 0,
               now() - interval '1 hour', 300, 5, 'fixed', 30, 300, now()
        UNION ALL
        SELECT md5('workflow-plan-child:' || g::text)::uuid,
               'plan_a', 'test.plan', 'blocked', 100, '{}'::jsonb,
               md5('workflow-plan:0')::uuid, 'child-' || g::text,
               repeat('b', 64), 1,
               now() - interval '1 hour', 300, 5, 'fixed', 30, 300, NULL
          FROM generate_series(1, 5000) AS g
        """
    )
    await pg.execute(
        """
        INSERT INTO taskq.job_deps(job_id, depends_on)
        SELECT md5('workflow-plan-child:' || g::text)::uuid,
               md5('workflow-plan-parent')::uuid
          FROM generate_series(1, 5000) AS g
        """
    )
    await pg.execute("ANALYZE taskq.jobs")
    await pg.execute("ANALYZE taskq.workflows")
    await pg.execute("ANALYZE taskq.job_deps")


async def _seed_schedule_plan_frontier(pg: asyncpg.Connection) -> None:
    # 100k definitions are enough to make accidental schedule scans/sorts
    # visible without multiplying the already-million-row job fixture's disk
    # footprint. The schedule target/recurrence are static contract-valid JSON.
    await pg.execute(
        """
        INSERT INTO taskq.schedules (
            id, name, target, recurrence, catchup_policy, max_catchup, state,
            initialized, next_fire_at, version, created_by, updated_by
        )
        SELECT md5('schedule-plan:' || g::text)::uuid,
               'schedule-plan-' || g::text,
               '{"kind":"job","queue":"plan_a","job_type":"test.plan",'
               '"payload":{},"headers":{},"priority":null,"max_attempts":null,'
               '"lease_seconds":null,"backoff_mode":null,"backoff_base":null,'
               '"backoff_cap":null,"concurrency_key":null,"affinity_key":null}'::jsonb,
               '{"kind":"interval","interval_seconds":3600}'::jsonb,
               'fire_all', 10,
               CASE WHEN g % 10 = 0 THEN 'paused' ELSE 'active' END,
               true,
               now() - interval '1 hour' + g * interval '1 microsecond',
               1, 'plans', 'plans'
          FROM generate_series(1, 100000) AS g
        """
    )
    await pg.execute("ANALYZE taskq.schedules")


async def test_million_row_index_plan_families(pg: asyncpg.Connection) -> None:
    if os.environ.get("TASKQ_PLAN_CHECKS") != "1":
        pytest.skip("set TASKQ_PLAN_CHECKS=1 to seed 1M rows and run structural EXPLAIN checks")

    await _assert_hot_path_bindings(pg)
    await _seed_million(pg)
    await _seed_workflow_plan_frontiers(pg)
    await _seed_schedule_plan_frontier(pg)
    await pg.execute("SET jit = off")

    claim = await _explain(
        pg,
        _PLAN_QUERIES["claim"],
    )
    _assert_index_family(claim, "jobs_claim_idx")
    assert claim["Actual Rows"] <= 1

    dedup = await _explain(
        pg,
        _PLAN_QUERIES["dedup"],
    )
    arbiter_indexes = {
        index for node in _walk_plan(dedup) for index in node.get("Conflict Arbiter Indexes", ())
    }
    assert "jobs_idem_uq" in arbiter_indexes

    reap = await _explain(
        pg,
        _PLAN_QUERIES["reap"],
    )
    _assert_index_family(reap, "jobs_running_idx")
    assert reap["Actual Rows"] <= 100

    ready_stats = await _explain(
        pg,
        _PLAN_QUERIES["ready_stats"],
    )
    _assert_index_family(ready_stats, "jobs_claim_idx")

    running_stats = await _explain(
        pg,
        _PLAN_QUERIES["running_stats"],
    )
    _assert_index_family(running_stats, "taskq_jobs_running_page_idx")

    finished_stats = await _explain(
        pg,
        _PLAN_QUERIES["finished_stats"],
    )
    _assert_index_family(finished_stats, "jobs_finished_idx")

    dependency_frontier = await _explain(pg, _PLAN_QUERIES["dependency_frontier"])
    dependency_indexes = {node.get("Index Name") for node in _walk_plan(dependency_frontier)}
    assert "job_deps_reverse_idx" in dependency_indexes
    assert not _sort_nodes(dependency_frontier)
    assert dependency_frontier["Actual Rows"] <= 100
    assert not any(
        node.get("Node Type") == "Seq Scan" and node.get("Relation Name") in {"job_deps", "jobs"}
        for node in _walk_plan(dependency_frontier)
    )

    workflow_finalizer = await _explain(pg, _PLAN_QUERIES["workflow_finalizer"])
    workflow_indexes = {node.get("Index Name") for node in _walk_plan(workflow_finalizer)}
    assert "workflows_finalize_idx" in workflow_indexes
    assert not _sort_nodes(workflow_finalizer)
    assert workflow_finalizer["Actual Rows"] <= 100

    schedule_claim = await _explain(pg, _PLAN_QUERIES["schedule_claim"])
    schedule_indexes = {node.get("Index Name") for node in _walk_plan(schedule_claim)}
    assert "schedules_due_idx" in schedule_indexes
    assert not _sort_nodes(schedule_claim)
    assert schedule_claim["Actual Rows"] <= 100
    assert not any(
        node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "schedules"
        for node in _walk_plan(schedule_claim)
    )

    # Execute the exact owner helper too; EXPLAIN cannot expose SQL nested in
    # PL/pgSQL, so the representative subqueries above provide plan evidence.
    await pg.fetchval("SELECT taskq.refresh_stats_snapshot()")

    # B9 / ADR-019/029: each finite view has its own queue/order or workflow
    # keyset index. Exact workflow counts are a single primary-key lookup, not
    # a request-time member scan. Capability metadata remains unchanged until
    # the separate immutable activation migration.
    ready = await _explain(pg, _READ_MODEL_PLAN_QUERIES["ready"])
    _assert_index_family(ready, "jobs_claim_idx")
    assert not _sort_nodes(ready)
    assert ready["Actual Rows"] <= 101

    running = await _explain(pg, _READ_MODEL_PLAN_QUERIES["running"])
    _assert_index_family(running, "taskq_jobs_running_page_idx")
    assert not _sort_nodes(running)
    assert running["Actual Rows"] <= 101

    finished = await _explain(pg, _READ_MODEL_PLAN_QUERIES["finished"])
    _assert_index_family(finished, "taskq_jobs_finished_page_idx")
    assert not _sort_nodes(finished)
    assert finished["Actual Rows"] <= 101

    workflow_members = await _explain(pg, _READ_MODEL_PLAN_QUERIES["workflow_members"])
    _assert_index_family(workflow_members, "taskq_jobs_workflow_page_idx")
    assert not _sort_nodes(workflow_members)
    assert workflow_members["Actual Rows"] <= 101

    workflow_counts = await _explain(pg, _READ_MODEL_PLAN_QUERIES["workflow_counts"])
    _assert_index_family(workflow_counts, "workflow_member_counts_pkey")
    assert workflow_counts["Actual Rows"] <= 1

    capabilities = await pg.fetchval("SELECT value FROM taskq.meta WHERE key = 'capabilities'")
    assert json.loads(capabilities) == {
        "active": [
            "admission_reservations",
            "dependencies_workflows",
            "followups",
            "read_model_list_ready",
            "schedules",
        ]
    }


async def test_plan_binding_detects_rollback_only_function_drift(
    pg: asyncpg.Connection,
) -> None:
    await _assert_hot_path_bindings(pg)
    transaction = pg.transaction()
    await transaction.start()
    try:
        await pg.execute(
            """
            CREATE OR REPLACE FUNCTION taskq.reap_expired(p_limit int DEFAULT 100)
            RETURNS int LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp AS $function$
            BEGIN
                PERFORM count(*) FROM taskq.jobs;
                RETURN 0;
            END
            $function$
            """
        )
        with pytest.raises(AssertionError, match="plan binding 'reap' drifted"):
            await _assert_hot_path_bindings(pg, {"reap"})
    finally:
        await transaction.rollback()
    await _assert_hot_path_bindings(pg, {"reap"})
