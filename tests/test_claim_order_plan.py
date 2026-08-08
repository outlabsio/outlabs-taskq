"""Regression gate for review finding H1 -- the unconfigured claim path must stay
index-backed, in the normal CI lane.

0033 (priority aging) made the claim ORDER BY `j.priority - CASE WHEN aging IS NULL
THEN 0 ELSE <age offset> END`. For an unconfigured queue that folds to `j.priority - 0`,
which Postgres will not match to jobs_claim_idx's `priority` pathkey -- so every claim on
every queue ran a Seq Scan + Sort of the ready backlog (O(ready depth)) instead of a
one-row Index Scan. It survived every gate because all three plan checks EXPLAINed the
pre-aging query *text* while the function ran the folded text. 0042 branches the order so
unconfigured queues use the bare, index-matching order.

The two ORDER BY strings below MUST mirror the branches in taskq._claim_jobs_unattested
(0042). If you change the claim order, update them -- and if the unconfigured order ever
stops using jobs_claim_idx, this fails.
"""

from __future__ import annotations

import json
from uuid import uuid4

import asyncpg
import pytest
from taskq.bench import _bulk, _connect_role, _database_dsn, _ensure_queue, _migrate

pytestmark = pytest.mark.taskq_sql

_UNCONFIGURED_ORDER = "j.priority, j.scheduled_at, j.id"
_AGED_ORDER = (
    "j.priority - LEAST(1000, floor(extract(epoch FROM (now() - j.scheduled_at)) / 60)"
    "::integer), j.scheduled_at, j.id"
)


def _scan(plan: dict) -> tuple[set[str], bool, bool]:
    indexes: set[str] = set()
    sort = False
    seqscan_jobs = False
    stack = [plan]
    while stack:
        node = stack.pop()
        if node.get("Index Name"):
            indexes.add(node["Index Name"])
        if node.get("Node Type") == "Sort":
            sort = True
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "jobs":
            seqscan_jobs = True
        stack.extend(node.get("Plans", ()))
    return indexes, sort, seqscan_jobs


async def _plan(conn: asyncpg.Connection, order: str) -> tuple[set[str], bool, bool]:
    raw = await conn.fetchval(
        f"""
        EXPLAIN (FORMAT JSON)
        SELECT j.* FROM taskq.jobs AS j
        WHERE j.queue = 'claim_plan_q' AND j.status = 'queued'
          AND j.continuation_policy_hash IS NULL
          AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
          AND (j.expires_at IS NULL OR j.expires_at > now())
        ORDER BY {order} LIMIT 1 FOR UPDATE OF j SKIP LOCKED
        """
    )
    return _scan(json.loads(raw)[0]["Plan"])


async def test_unconfigured_claim_order_is_index_backed(taskq_dsn: str) -> None:
    database = f"taskq_claim_plan_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    await admin.close()
    dsn = _database_dsn(taskq_dsn, database)
    op = prod = probe = None
    try:
        await _migrate(dsn)  # 0001 -> HEAD (+ bind through the checkpoint)
        op = await _connect_role(dsn, "taskq_operator")
        prod = await _connect_role(dsn, "taskq_producer")
        await _ensure_queue(op, "claim_plan_q")  # NO aging / flow config
        for i in range(4):
            await _bulk(prod, "claim_plan_q", f"cp-{i}", 1000)  # 4k ready rows
        probe = await asyncpg.connect(dsn)
        await probe.execute("ANALYZE taskq.jobs")

        indexes, sort, seqscan = await _plan(probe, _UNCONFIGURED_ORDER)
        assert "jobs_claim_idx" in indexes, (indexes, sort, seqscan)
        assert not sort, "unconfigured claim must not sort the ready backlog"
        assert not seqscan, "unconfigured claim must not seq-scan jobs"

        # Configured aging inherently cannot use the index ordering, so it sorts -- the
        # documented O(ready-depth) cost paid only by queues that opt into aging.
        _, aged_sort, _ = await _plan(probe, _AGED_ORDER)
        assert aged_sort, "aged claim order is expected to sort (documented cost)"

        # Tether to the live function, not just these hand-written shapes. An EXPLAIN-only
        # gate passes even against a reverted function body (0042 re-review finding #1 --
        # the same "gate tests a proxy" flaw that let H1 through), so assert both branch
        # orders appear verbatim in _claim_jobs_unattested. Changing the claim order fails
        # this until the strings are updated in lockstep.
        functiondef = await probe.fetchval(
            "SELECT pg_get_functiondef('taskq._claim_jobs_unattested"
            "(text,text,integer,text[],integer,text,uuid,boolean)'::regprocedure)"
        )
        body = " ".join(functiondef.lower().split())
        assert "order by j.priority, j.scheduled_at, j.id" in body, (
            "bare (unconfigured) order missing"
        )
        assert (
            "order by j.priority - least(1000, floor(extract(epoch from "
            "(now() - j.scheduled_at)) / v_aging_seconds)::integer), j.scheduled_at, j.id"
        ) in body, "aged order missing"
    finally:
        for conn in (op, prod, probe):
            if conn is not None:
                await conn.close()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()
