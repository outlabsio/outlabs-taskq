"""Contract 0.4.0: queue counters, snapshot rates, and health verdicts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def _counters(pg: asyncpg.Connection, queue: str) -> dict[str, int]:
    row = await pg.fetchrow("SELECT * FROM taskq.queue_counters WHERE queue = $1", queue)
    assert row is not None
    return {key: value for key, value in dict(row).items() if isinstance(value, int)}


async def _ground_truth(pg: asyncpg.Connection, queue: str) -> dict[str, int]:
    row = await pg.fetchrow(
        "SELECT count(*) FILTER (WHERE status='blocked') AS blocked,"
        "       count(*) FILTER (WHERE status='queued') AS queued,"
        "       count(*) FILTER (WHERE status='running') AS running"
        "  FROM taskq.jobs WHERE queue = $1",
        queue,
    )
    assert row is not None
    return dict(row)


async def _claim(runner: asyncpg.Connection, queue: str) -> tuple[Any, Any]:
    row = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1, 'c040-worker')", queue)
    assert row is not None and row["state"] == "claimed"
    job = row["jobs"][0]
    return job["job_id"], job["attempt_id"]


async def test_counters_track_every_transition(
    pg: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    queue = "c040_counts"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'c040')", queue)
    for index in range(5):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1, 'c040.t', '{}'::jsonb, p_idempotency_key => $2)",
            queue,
            f"c040-{index}",
        )
    counters = await _counters(pg, queue)
    assert counters["enqueued_total"] == 5 and counters["queued"] == 5

    # complete
    job_id, attempt_id = await _claim(runner, queue)
    assert (await _counters(pg, queue))["running"] == 1
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1, $2, 'c040-worker')", job_id, attempt_id
    )
    # fail retryable (running -> queued: requeued_total)
    job_id, attempt_id = await _claim(runner, queue)
    await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1, $2, 'c040-worker', 'boom', p_retryable => true)",
        job_id,
        attempt_id,
    )
    # fail non-retryable (terminal failed)
    job_id, attempt_id = await _claim(runner, queue)
    await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1, $2, 'c040-worker', 'dead', p_retryable => false)",
        job_id,
        attempt_id,
    )
    # snooze (running -> queued: requeued_total)
    job_id, attempt_id = await _claim(runner, queue)
    await runner.fetchrow(
        "SELECT * FROM taskq.snooze_job($1, $2, 'c040-worker', 60)", job_id, attempt_id
    )
    # release (running -> queued: requeued_total)
    job_id, attempt_id = await _claim(runner, queue)
    await runner.fetchrow(
        "SELECT * FROM taskq.release_job($1, $2, 'c040-worker', 'released')", job_id, attempt_id
    )
    # cancel a queued job (terminal cancelled)
    cancelled_id = await pg.fetchval(
        "SELECT id FROM taskq.jobs WHERE queue = $1 AND status = 'queued'"
        " AND cancel_requested_at IS NULL AND scheduled_at <= now() LIMIT 1",
        queue,
    )
    assert cancelled_id is not None
    await operator.fetchrow("SELECT * FROM taskq.cancel_job($1, 'c040', 'test')", cancelled_id)

    counters = await _counters(pg, queue)
    assert counters["enqueued_total"] == 5
    assert counters["succeeded_total"] == 1
    assert counters["failed_total"] == 1
    assert counters["cancelled_total"] == 1
    assert counters["requeued_total"] == 3  # fail-retryable + snooze + release
    truth = await _ground_truth(pg, queue)
    assert counters["blocked"] == truth["blocked"]
    assert counters["queued"] == truth["queued"]
    assert counters["running"] == truth["running"]

    # retention deletes decrement levels only; terminal rows are not levels,
    # so the janitor pass must leave counters untouched while rows disappear.
    before = await _counters(pg, queue)
    await pg.execute(
        "UPDATE taskq.jobs SET finished_at = now() - interval '30 days'"
        " WHERE queue = $1 AND status IN ('succeeded','failed','cancelled')",
        queue,
    )
    await housekeeper.fetchval("SELECT taskq.janitor()")
    remaining = await pg.fetchval(
        "SELECT count(*) FROM taskq.jobs WHERE queue = $1"
        " AND status IN ('succeeded','failed','cancelled')",
        queue,
    )
    assert remaining == 0
    assert await _counters(pg, queue) == {**before, **(await _ground_truth(pg, queue))}


async def test_snapshot_carries_levels_rates_and_eta(
    pg: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    queue = "c040_rates"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'c040')", queue)
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'c040.t', '{}'::jsonb, p_idempotency_key => 'r0')", queue
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    await asyncio.sleep(1.1)
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'c040.t', '{}'::jsonb, p_idempotency_key => 'r1')", queue
    )
    job_id, attempt_id = await _claim(runner, queue)
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1, $2, 'c040-worker')", job_id, attempt_id
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    data = _json(
        await pg.fetchval("SELECT data FROM taskq.control_state WHERE key = 'stats_snapshot'")
    )
    entry = data["queues"][queue]
    assert entry["levels"]["enqueued_total"] == 2
    assert entry["rates"]["settled_per_s"] > 0
    assert entry["rates"]["enqueued_per_s"] > 0
    assert entry["drain_eta_seconds"] is not None
    assert data["totals"][queue]["succeeded_total"] == 1


async def test_queue_health_verdicts(
    pg: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    observer: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    async def verdict(queue: str) -> str:
        row = await observer.fetchrow("SELECT * FROM taskq.queue_health($1)", queue)
        assert row is not None and row["queue"] == queue
        return row["verdict"]

    idle = "c040_idle"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'c040')", idle)
    await housekeeper.fetchval("SELECT taskq.tick()")
    assert await verdict(idle) == "inactive"

    orphan = "c040_orphan"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'c040')", orphan)
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'c040.t', '{}'::jsonb, p_idempotency_key => 'o0')", orphan
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    assert await verdict(orphan) == "no_consumer"

    await runner.fetchrow(
        "SELECT * FROM taskq.worker_heartbeat('c040-presence', ARRAY[$1])", orphan
    )
    assert await verdict(orphan) == "ok"

    choked = "c040_choked"
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{\"max_depth\": 2}'::jsonb, 'c040')", choked
    )
    for index in range(2):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1, 'c040.t', '{}'::jsonb, p_idempotency_key => $2)",
            choked,
            f"ch-{index}",
        )
    await runner.fetchrow(
        "SELECT * FROM taskq.worker_heartbeat('c040-presence-2', ARRAY[$1])", choked
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    assert await verdict(choked) == "choking"

    await operator.fetchval("SELECT taskq.pause_queue($1, 'c040', 'test')", choked)
    assert await verdict(choked) == "paused"

    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await observer.fetchrow("SELECT * FROM taskq.queue_health('c040_missing')")
    assert excinfo.value.sqlstate == "TQ001"


async def test_queue_health_gates_on_capability(
    pg: asyncpg.Connection,
    observer: asyncpg.Connection,
) -> None:
    active = await pg.fetchval("SELECT value FROM taskq.meta WHERE key = 'capabilities'")
    reduced = _json(active)
    reduced["active"] = [name for name in reduced["active"] if name != "queue_counters"]
    try:
        await pg.execute(
            "UPDATE taskq.meta SET value = $1::jsonb WHERE key = 'capabilities'",
            json.dumps(reduced),
        )
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await observer.fetchrow("SELECT * FROM taskq.queue_health(NULL)")
        assert excinfo.value.sqlstate == "TQ501"
        assert await pg.fetch("SELECT * FROM taskq.queue_health_internal()") == []
        metric_rows = await observer.fetch("SELECT * FROM taskq.metrics()")
        assert all(row["name"] != "taskq_health" for row in metric_rows)
    finally:
        await pg.execute(
            "UPDATE taskq.meta SET value = $1::jsonb WHERE key = 'capabilities'", active
        )
    assert await observer.fetch("SELECT * FROM taskq.queue_health(NULL)") is not None
