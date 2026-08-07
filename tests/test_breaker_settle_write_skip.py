"""Migration 0038 — breaker settle write-skip (body-only, no contract change).

The closed-state settle handler must not UPDATE queue_flow when nothing changed
(a healthy success on a breaker with no rate/latency config and a zero streak),
restoring the 0.6.3 skip that the 0.6.4 unified handler dropped — while still
writing (and tripping) whenever the streak or a rate/latency window advances.

We detect "did it write?" with the row's xmin system column: an UPDATE changes it.
"""

from __future__ import annotations

import json

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

_CLAIM = (
    "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL::text[],NULL::integer,NULL::text,NULL::uuid,true)"
)


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'wskip')", name)


async def _enqueue(producer: asyncpg.Connection, queue: str, n: int) -> None:
    for i in range(n):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'j','{}'::jsonb,p_idempotency_key=>$2)",
            queue,
            f"{queue}-{i}",
        )


async def _settle(runner: asyncpg.Connection, queue: str, fail: bool) -> None:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    job = b["jobs"][0]
    if fail:
        await runner.fetchrow(
            "SELECT * FROM taskq.fail_job($1,$2,$3,$4,false)",
            job["job_id"],
            job["attempt_id"],
            "w",
            "boom",
        )
    else:
        await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
        )


async def _settle_slow(runner: asyncpg.Connection, pg: asyncpg.Connection, queue: str) -> None:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    job = b["jobs"][0]
    await pg.execute(
        "UPDATE taskq.job_attempts SET claimed_at = now() - interval '2 seconds' WHERE id = $1",
        job["attempt_id"],
    )
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
    )


async def _xmin(pg: asyncpg.Connection, queue: str) -> str:
    return str(await pg.fetchval("SELECT xmin::text FROM taskq.queue_flow WHERE queue=$1", queue))


async def _state(pg: asyncpg.Connection, queue: str) -> str:
    return str(
        await pg.fetchval("SELECT breaker_state FROM taskq.queue_flow WHERE queue=$1", queue)
    )


async def _trip_reason(pg: asyncpg.Connection, queue: str) -> str | None:
    data = await pg.fetchval(
        "SELECT e.data FROM taskq.job_events e JOIN taskq.jobs j ON j.id=e.job_id"
        " WHERE j.queue=$1 AND e.event_type='breaker_opened' ORDER BY e.id LIMIT 1",
        queue,
    )
    if data is None:
        return None
    payload = data if isinstance(data, dict) else json.loads(data)
    return str(payload["reason"])


async def test_healthy_success_does_not_write_queue_flow(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "wskip_healthy"
    await _queue(operator, q)
    # Breaker configured, NO rate/latency: a healthy success changes nothing.
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,30,1,'a')")
    await _enqueue(producer, q, 3)
    x0 = await _xmin(pg, q)
    await _settle(runner, q, fail=False)
    assert await _xmin(pg, q) == x0, "a no-op healthy success must not write queue_flow"
    # A failure advances the streak, so it must write.
    await _settle(runner, q, fail=True)
    assert await _xmin(pg, q) != x0, "a failure must write (streak advanced)"


async def test_latency_window_advance_still_writes_and_trips(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "wskip_lat"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,30,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_latency('{q}',1000,60,3,'a')")
    await _enqueue(producer, q, 4)
    x0 = await _xmin(pg, q)
    # A slow success advances the latency window -> must write even though it succeeded.
    await _settle_slow(runner, pg, q)
    assert await _xmin(pg, q) != x0, "a latency-window advance must write queue_flow"
    # And latency tripping still fires at min_volume.
    await _settle_slow(runner, pg, q)
    await _settle_slow(runner, pg, q)
    assert await _state(pg, q) == "open"
    assert await _trip_reason(pg, q) == "latency"


async def test_streak_still_trips(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "wskip_streak"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,30,1,'a')")
    await _enqueue(producer, q, 4)
    for _ in range(3):
        await _settle(runner, q, fail=True)
    assert await _state(pg, q) == "open"
    assert await _trip_reason(pg, q) == "streak"


async def test_rate_window_success_is_persisted_under_skip(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    # The 0038 skip must still persist the rate window on a SUCCESS — a success dilutes
    # the failure ratio. If a skip-list regression drops the success write, the window
    # over-counts failures and the breaker false-trips on rate.
    q = "wskip_rate"
    await _queue(operator, q)
    await operator.fetchval(
        f"SELECT taskq.set_breaker_config('{q}',100,30,1,'a')"
    )  # streak won't trip
    await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q}',0.7,300,3,'a')")
    await _enqueue(producer, q, 6)
    x0 = await _xmin(pg, q)
    await _settle(runner, q, fail=False)
    assert await _xmin(pg, q) != x0, "a success under rate config must write the rate window"
    await _settle(runner, q, fail=False)
    for _ in range(3):
        await _settle(runner, q, fail=True)
    # True ratio 3F/2S = 0.60 < 0.70 -> no trip. A dropped success write reads 3F/0S -> false trip.
    assert await _state(pg, q) == "closed"
    row = await pg.fetchrow(
        "SELECT breaker_window_failures f, breaker_window_successes s"
        " FROM taskq.queue_flow WHERE queue=$1",
        q,
    )
    assert (row["f"], row["s"]) == (3, 2)
