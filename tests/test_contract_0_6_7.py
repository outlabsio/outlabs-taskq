"""SQL contract 0.6.7 — workflow-safe set-based bulk admission."""

from __future__ import annotations

import json
import os
import time
from uuid import UUID

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


async def _queue(operator: asyncpg.Connection, name: str, *, max_depth: int | None = None) -> None:
    profile = {} if max_depth is None else {"max_depth": max_depth}
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,$2::jsonb,'contract-0.6.7')",
        name,
        json.dumps(profile),
    )
    assert row is not None


async def _workflow(
    producer: asyncpg.Connection,
    queue: str,
    key: str,
    *,
    member_limit: int,
) -> UUID:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'batch','{}'::jsonb,ARRAY[$2],$3,$4,$5)",
        key,
        queue,
        "contract-0.6.7",
        member_limit,
        "a" * 64,
    )
    assert row is not None and row["outcome"] == "created"
    return row["workflow_id"]


def _spec(workflow_id: UUID, index: int, *, value: int | None = None) -> dict[str, object]:
    return {
        "job_type": "tests.workflow_bulk",
        "payload": {"value": index if value is None else value},
        "idempotency_key": f"workflow-bulk:{workflow_id}:{index}",
        "workflow_id": str(workflow_id),
        "step_key": f"member-{index:06d}",
        "priority": 2,
        "max_attempts": 2,
        "concurrency_key": "provider:test",
    }


async def test_contract_and_capability_are_active(pg: asyncpg.Connection) -> None:
    assert (
        await pg.fetchval("SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'")
        == "0.6.12"
    )
    assert await pg.fetchval("SELECT taskq.has_capability('workflow_bulk_admission')") is True
    definition = await pg.fetchval(
        "SELECT pg_get_functiondef('taskq.enqueue_many(text,jsonb)'::regprocedure)"
    )
    assert "taskq.queue_counters" in definition
    assert "taskq._reserve_workflow_members" in definition
    single_definition = await pg.fetchval(
        "SELECT pg_get_functiondef('taskq.enqueue(text,text,jsonb,smallint,timestamptz,"
        "text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,"
        "integer,text)'::regprocedure)"
    )
    assert "taskq.queue_counters" in single_definition


async def test_workflow_bulk_is_ordered_replay_safe_and_seal_safe(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    queue = "workflow_bulk_ordered"
    await _queue(operator, queue)
    workflow_id = await _workflow(producer, queue, "workflow-bulk-ordered", member_limit=3)
    specs = [_spec(workflow_id, 0), _spec(workflow_id, 1), _spec(workflow_id, 2)]

    created = await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)", queue, json.dumps(specs)
    )
    assert [row["input_index"] for row in created] == [1, 2, 3]
    assert [row["outcome"] for row in created] == ["created", "created", "created"]
    assert len({row["job_id"] for row in created}) == 3

    counts = await pg.fetchrow(
        "SELECT queued,admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
        workflow_id,
    )
    assert counts is not None and tuple(counts) == (3, 3)
    members = await pg.fetch(
        "SELECT step_key,workflow_intent_hash,status FROM taskq.jobs "
        "WHERE workflow_id=$1 ORDER BY step_key",
        workflow_id,
    )
    assert [row["step_key"] for row in members] == [
        "member-000000",
        "member-000001",
        "member-000002",
    ]
    assert all(row["workflow_intent_hash"] for row in members)
    assert {row["status"] for row in members} == {"queued"}

    await producer.fetchrow("SELECT * FROM taskq.seal_workflow($1,$2)", workflow_id, "planner")
    replay = await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)", queue, json.dumps(specs)
    )
    assert [row["outcome"] for row in replay] == ["existed", "existed", "existed"]
    assert [row["job_id"] for row in replay] == [row["job_id"] for row in created]
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 3
    )


async def test_workflow_bulk_conflicts_and_limits_roll_back_the_whole_call(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    queue = "workflow_bulk_atomic"
    await _queue(operator, queue)
    workflow_id = await _workflow(producer, queue, "workflow-bulk-atomic", member_limit=2)
    original = _spec(workflow_id, 0)
    await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)", queue, json.dumps([original])
    )

    mismatch = _spec(workflow_id, 0, value=999)
    with pytest.raises(asyncpg.PostgresError) as conflict:
        await producer.fetch(
            "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)",
            queue,
            json.dumps([mismatch, _spec(workflow_id, 1)]),
        )
    assert conflict.value.sqlstate == "TQ409"
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1", workflow_id) == 1
    )
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 1
    )

    with pytest.raises(asyncpg.PostgresError) as over_limit:
        await producer.fetch(
            "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)",
            queue,
            json.dumps([_spec(workflow_id, 1), _spec(workflow_id, 2)]),
        )
    assert over_limit.value.sqlstate == "TQ409"
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1", workflow_id) == 1
    )


async def test_workflow_bulk_replay_bypasses_depth_but_new_members_do_not(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    queue = "workflow_bulk_depth"
    await _queue(operator, queue, max_depth=1)
    workflow_id = await _workflow(producer, queue, "workflow-bulk-depth", member_limit=2)
    first = _spec(workflow_id, 0)
    await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)", queue, json.dumps([first])
    )

    replay = await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)", queue, json.dumps([first])
    )
    assert replay[0]["outcome"] == "existed"
    with pytest.raises(asyncpg.PostgresError) as full:
        await producer.fetch(
            "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)",
            queue,
            json.dumps([_spec(workflow_id, 1)]),
        )
    assert full.value.sqlstate == "TQ429"
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1", workflow_id) == 1
    )


async def test_workflow_bulk_admits_10000_members_inside_the_production_budget(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    queue = "workflow_bulk_10k"
    await _queue(operator, queue, max_depth=20_000)
    workflow_id = await _workflow(producer, queue, "workflow-bulk-10k", member_limit=10_000)
    started = time.perf_counter()
    created = 0
    for offset in range(0, 10_000, 1000):
        specs = [_spec(workflow_id, index) for index in range(offset, offset + 1000)]
        rows = await producer.fetch(
            "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)", queue, json.dumps(specs)
        )
        created += sum(row["outcome"] == "created" for row in rows)
    elapsed = time.perf_counter() - started

    assert created == 10_000
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1", workflow_id)
        == 10_000
    )
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 10_000
    )
    maximum = float(os.environ.get("TASKQ_WORKFLOW_BULK_10K_MAX_SECONDS", "30"))
    assert elapsed < maximum, f"10k workflow admission took {elapsed:.3f}s (budget {maximum:.3f}s)"
