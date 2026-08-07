"""SQL contract 0.6.4 — breaker latency tripping.

Completes the breaker trip-trigger set (streak / rate / latency). The streak and
rate triggers catch a downstream that FAILS; latency catches one that is slow but
still SUCCEEDING — it trips when the average execution latency over a rolling
window reaches a threshold. set_breaker_latency configures it; a configured
breaker now trips on streak OR rate OR latency, reason-tagged in breaker_opened.
See the 0.6 spec.

Execution latency is deterministically simulated by backdating the settling
attempt's ``job_attempts.claimed_at`` (the trigger measures ``now() - claimed_at``
for the attempt in ``finished_by_attempt_id``), so these tests carry no wall-clock
dependency on how fast the harness itself runs.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

_CLAIM = (
    "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL::text[],NULL::integer,NULL::text,NULL::uuid,true)"
)


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c064')", name)


async def _enqueue(producer: asyncpg.Connection, queue: str, n: int) -> None:
    for i in range(n):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'j','{}'::jsonb,p_idempotency_key=>$2)",
            queue,
            f"{queue}-{i}",
        )


async def _settle_slow(
    runner: asyncpg.Connection,
    pg: asyncpg.Connection,
    queue: str,
    latency_ms: int,
    fail: bool = False,
) -> str:
    """Claim a job, backdate its attempt's claim time by ``latency_ms``, then settle
    — so the breaker trigger measures exactly ``latency_ms`` of execution latency."""
    b = await runner.fetchrow(_CLAIM, queue, "w")
    if b is None or b["state"] != "claimed":
        return str(b["state"]) if b else "empty"
    job = b["jobs"][0]
    await pg.execute(
        "UPDATE taskq.job_attempts SET claimed_at = now() - make_interval(secs => $2::numeric / 1000)"
        " WHERE id = $1",
        job["attempt_id"],
        latency_ms,
    )
    if fail:
        await runner.fetchrow(
            "SELECT * FROM taskq.fail_job($1,$2,$3,$4,false)",
            job["job_id"],
            job["attempt_id"],
            "w",
            "slow",
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


async def test_set_breaker_latency_validates(operator: asyncpg.Connection) -> None:
    await _queue(operator, "c064_cfg")
    # Requires a configured breaker.
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.set_breaker_latency('c064_cfg',1000,60,10,'a')")
    assert exc.value.sqlstate == "TQ001"
    await operator.fetchval("SELECT taskq.set_breaker_config('c064_cfg',5,30,1,'a')")
    assert (
        await operator.fetchval("SELECT taskq.set_breaker_latency('c064_cfg',1000,60,10,'a')")
        == "updated"
    )
    assert (
        await operator.fetchval("SELECT taskq.set_breaker_latency('c064_cfg',NULL,60,10,'a')")
        == "cleared"
    )
    # threshold<=0, window out of range (low + high), min_volume<=0.
    for bad in ("0,60,10", "1000,0,10", "1000,90000,10", "1000,60,0"):
        with pytest.raises(asyncpg.PostgresError) as exc:
            await operator.fetchval(f"SELECT taskq.set_breaker_latency('c064_cfg',{bad},'a')")
        assert exc.value.sqlstate == "TQ422"


async def test_latency_trips_on_slow_successes(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c064_lat"
    await _queue(operator, q)
    # High streak threshold + no rate config, so ONLY latency can trip.
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,1,1,'a')")
    # Avg latency >= 1000ms over the window, min 3 samples.
    await operator.fetchval(f"SELECT taskq.set_breaker_latency('{q}',1000,60,3,'a')")
    await _enqueue(producer, q, 5)
    # Two slow successes: count 2 < min_volume 3 -> not yet.
    for _ in range(2):
        await _settle_slow(runner, pg, q, 2000)
        assert await _state(pg, q) == "closed"
    # Third slow success: count 3 >= 3 and avg 2000ms >= 1000ms -> trip on latency.
    await _settle_slow(runner, pg, q, 2000)
    assert await _state(pg, q) == "open"
    assert await _trip_reason(pg, q) == "latency"


async def test_latency_does_not_trip_when_fast_or_below_min_volume(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    # Fast successes stay well under the threshold.
    q = "c064_fast"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,1,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_latency('{q}',1000,60,3,'a')")
    await _enqueue(producer, q, 5)
    for _ in range(5):
        await _settle_slow(runner, pg, q, 50)  # 50ms << 1000ms
    assert await _state(pg, q) == "closed"

    # Slow, but too few samples to reach min_volume.
    q2 = "c064_minvol"
    await _queue(operator, q2)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q2}',100,1,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_latency('{q2}',1000,60,10,'a')")
    await _enqueue(producer, q2, 5)
    for _ in range(4):
        await _settle_slow(runner, pg, q2, 3000)  # slow, but 4 < min_volume 10
    assert await _state(pg, q2) == "closed"


async def test_no_latency_config_behaves_as_0_6_3(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c064_nolat"
    await _queue(operator, q)
    # Breaker configured with NO latency trip: slow successes must never trip it.
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,1,1,'a')")
    await _enqueue(producer, q, 8)
    for _ in range(5):
        await _settle_slow(runner, pg, q, 5000)  # very slow successes
    assert await _state(pg, q) == "closed"
    # And the streak trigger still trips exactly as under 0.6.3.
    for _ in range(3):
        await _settle_slow(runner, pg, q, 5000, fail=True)
    assert await _state(pg, q) == "open"
    assert await _trip_reason(pg, q) == "streak"


async def test_recovery_resets_latency_window(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c064_recover"
    await _queue(operator, q)
    # 1s cooldown so a claim past it is the single-flight half-open probe.
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,1,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_latency('{q}',1000,60,3,'a')")
    await _enqueue(producer, q, 6)
    for _ in range(3):
        await _settle_slow(runner, pg, q, 2000)
    assert await _state(pg, q) == "open"
    # While open, the trip-time latency window is retained (frozen).
    assert (
        await pg.fetchval("SELECT breaker_latency_count FROM taskq.queue_flow WHERE queue=$1", q)
        >= 3
    )
    # Recover: past cooldown a claim is the probe; a (fast) success closes it.
    await asyncio.sleep(1.2)
    await _settle_slow(runner, pg, q, 10)
    assert await _state(pg, q) == "closed"
    row = await pg.fetchrow(
        "SELECT breaker_latency_count, breaker_latency_sum_ms, breaker_latency_window_start"
        " FROM taskq.queue_flow WHERE queue=$1",
        q,
    )
    assert row["breaker_latency_count"] == 0
    assert row["breaker_latency_sum_ms"] == 0
    assert row["breaker_latency_window_start"] is None


async def test_set_breaker_latency_operator_only(
    runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    await _queue(operator, "c064_perm")
    await operator.fetchval("SELECT taskq.set_breaker_config('c064_perm',3,30,1,'a')")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await runner.fetchval("SELECT taskq.set_breaker_latency('c064_perm',1000,60,10,'a')")
    assert exc.value.sqlstate == "42501"
