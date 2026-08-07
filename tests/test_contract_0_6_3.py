"""SQL contract 0.6.3 — breaker rate/window tripping.

Adds an optional rolling-window failure-rate trip alongside the streak, closing
the streak-only breaker's intermittent-failure blindness. set_breaker_rate
configures it; a configured breaker trips on EITHER a consecutive-failure streak
or a sustained failure ratio over the window. See the 0.6 spec.
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
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c063')", name)


async def _enqueue(producer: asyncpg.Connection, queue: str, n: int) -> None:
    for i in range(n):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'j','{}'::jsonb,p_idempotency_key=>$2)",
            queue,
            f"{queue}-{i}",
        )


async def _settle(runner: asyncpg.Connection, queue: str, fail: bool) -> str:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    if b is None or b["state"] != "claimed":
        return str(b["state"]) if b else "empty"
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
    return "claimed"


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


async def test_set_breaker_rate_validates(operator: asyncpg.Connection) -> None:
    await _queue(operator, "c063_cfg")
    # Requires a configured breaker.
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.set_breaker_rate('c063_cfg',0.5,60,10,'a')")
    assert exc.value.sqlstate == "TQ001"
    await operator.fetchval("SELECT taskq.set_breaker_config('c063_cfg',5,30,1,'a')")
    assert (
        await operator.fetchval("SELECT taskq.set_breaker_rate('c063_cfg',0.5,60,10,'a')")
        == "updated"
    )
    assert (
        await operator.fetchval("SELECT taskq.set_breaker_rate('c063_cfg',NULL,60,10,'a')")
        == "cleared"
    )
    for bad in ("0,60,10", "1.5,60,10", "0.5,0,10", "0.5,60,0"):
        with pytest.raises(asyncpg.PostgresError) as exc:
            await operator.fetchval(f"SELECT taskq.set_breaker_rate('c063_cfg',{bad},'a')")
        assert exc.value.sqlstate == "TQ422"


async def test_rate_trips_on_sustained_failure_ratio(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c063_rate"
    await _queue(operator, q)
    # High streak threshold so streak never trips; rate: 50% over the window, min 5.
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,1,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q}',0.5,60,5,'a')")
    await _enqueue(producer, q, 10)
    # F,S,F,S,F -> on the 5th settle (F): 3F/2S = 60% of 5 >= 50%, 5 >= min -> trip.
    for fail in (True, False, True, False):
        await _settle(runner, q, fail)
        assert await _state(pg, q) == "closed"
    await _settle(runner, q, True)
    assert await _state(pg, q) == "open"
    assert await _trip_reason(pg, q) == "rate"


async def test_rate_does_not_trip_below_ratio_or_min_volume(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    # Below the ratio: one failure among many successes.
    q = "c063_low"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,1,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q}',0.5,60,4,'a')")
    await _enqueue(producer, q, 10)
    for fail in (True, False, False, False, False):
        await _settle(runner, q, fail)
    assert await _state(pg, q) == "closed"  # 1/5 = 20% < 50%

    # Below min_volume: 100% failure but too few settles to evaluate the ratio.
    q2 = "c063_minvol"
    await _queue(operator, q2)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q2}',100,1,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q2}',0.5,60,10,'a')")
    await _enqueue(producer, q2, 6)
    for _ in range(4):
        await _settle(runner, q2, True)
    assert await _state(pg, q2) == "closed"  # 4 < min_volume 10


async def test_streak_still_trips_independently(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c063_streak"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,1,1,'a')")
    await operator.fetchval(
        f"SELECT taskq.set_breaker_rate('{q}',0.9,60,100,'a')"
    )  # rate won't fire
    await _enqueue(producer, q, 6)
    for _ in range(3):
        await _settle(runner, q, True)
    assert await _state(pg, q) == "open"
    assert await _trip_reason(pg, q) == "streak"


async def test_set_breaker_rate_operator_only(
    runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    await _queue(operator, "c063_perm")
    await operator.fetchval("SELECT taskq.set_breaker_config('c063_perm',3,30,1,'a')")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await runner.fetchval("SELECT taskq.set_breaker_rate('c063_perm',0.5,60,10,'a')")
    assert exc.value.sqlstate == "42501"
