"""SQL contract 0.6.0 — the per-queue circuit breaker (P10, S6 Wave 3).

Streak-based, off-by-default. State + config live on the queue_flow row; the
claim path consults _breaker_gate before delegating; a settle trigger feeds
trip/recover; recovery stamps the ramp. See docs/Task Queue 0.6 Circuit Breaker
Specification.md.
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
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c060')", name)


async def _enqueue(producer: asyncpg.Connection, queue: str, n: int) -> None:
    for i in range(n):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'j',$2::jsonb,p_idempotency_key=>$3)",
            queue,
            json.dumps({"i": i}),
            f"{queue}-{i}",
        )


async def _claim_and_fail(runner: asyncpg.Connection, queue: str) -> None:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    assert b is not None and b["state"] == "claimed", f"expected claimed, got {b['state']}"
    job = b["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1,$2,$3,$4,false)",
        job["job_id"],
        job["attempt_id"],
        "w",
        "boom",
    )


async def _claim_and_complete(runner: asyncpg.Connection, queue: str) -> None:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    assert b is not None and b["state"] == "claimed", f"expected claimed, got {b['state']}"
    job = b["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
    )


async def _breaker(pg: asyncpg.Connection, queue: str) -> asyncpg.Record | None:
    return await pg.fetchrow(
        "SELECT breaker_state, breaker_failure_streak, breaker_probe_successes,"
        " breaker_opened_total FROM taskq.queue_flow WHERE queue=$1",
        queue,
    )


async def test_set_breaker_config_validates(operator: asyncpg.Connection) -> None:
    await _queue(operator, "c060_cfg")
    assert (
        await operator.fetchval("SELECT taskq.set_breaker_config('c060_cfg',3,30,1,'a')")
        == "created"
    )
    assert (
        await operator.fetchval("SELECT taskq.set_breaker_config('c060_cfg',5,30,1,'a')")
        == "updated"
    )
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.set_breaker_config('c060_nope',3,30,1,'a')")
    assert exc.value.sqlstate == "TQ001"
    for bad in ("0,30,1", "3,0,1", "3,30,0", "3,90000,1"):
        with pytest.raises(asyncpg.PostgresError) as exc:
            await operator.fetchval(f"SELECT taskq.set_breaker_config('c060_cfg',{bad},'a')")
        assert exc.value.sqlstate == "TQ422"


async def test_breaker_trips_on_streak_and_throttles(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c060_trip"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,30,1,'a')")
    await _enqueue(producer, q, 6)
    # Two terminal failures: streak grows, still closed.
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "closed" and row["breaker_failure_streak"] == 2
    # Third failure trips.
    await _claim_and_fail(runner, q)
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "open" and row["breaker_opened_total"] == 1
    # Claims are now throttled with a retry hint, and no job is burned.
    b = await runner.fetchrow(_CLAIM, q, "w")
    assert b["state"] == "throttled" and b["retry_after_seconds"] >= 1
    running = await pg.fetchval(
        "SELECT count(*) FROM taskq.jobs WHERE queue=$1 AND status='running'", q
    )
    assert running == 0


async def test_success_resets_streak_no_trip(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c060_reset"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,30,1,'a')")
    await _enqueue(producer, q, 6)
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    await _claim_and_complete(runner, q)  # breaks the streak
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "closed" and row["breaker_failure_streak"] == 0
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "closed"  # only 2 in a row, threshold 3 not reached


async def test_half_open_probe_recovers_and_stamps_ramp(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c060_recover"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',2,1,1,'a')")  # 1s cooldown
    await _enqueue(producer, q, 6)
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    assert (await _breaker(pg, q))["breaker_state"] == "open"
    await asyncio.sleep(1.2)  # past cooldown
    # A claim now is the single-flight probe: it proceeds (claimed), state half_open.
    b = await runner.fetchrow(_CLAIM, q, "w")
    assert b["state"] == "claimed"
    assert (await _breaker(pg, q))["breaker_state"] == "half_open"
    job = b["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
    )
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "closed" and row["breaker_failure_streak"] == 0
    assert await pg.fetchval(
        "SELECT ramp_started_at IS NOT NULL FROM taskq.queue_flow WHERE queue=$1", q
    )


async def test_half_open_probe_failure_reopens(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c060_reopen"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',2,1,1,'a')")
    await _enqueue(producer, q, 6)
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    await asyncio.sleep(1.2)
    before = (await _breaker(pg, q))["breaker_opened_total"]
    await _claim_and_fail(runner, q)  # probe fails
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "open" and row["breaker_opened_total"] == before + 1


async def test_manual_trip_and_force_close(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    q = "c060_manual"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,30,1,'a')")
    assert await operator.fetchval(f"SELECT taskq.trip_breaker('{q}','a')") == "open"
    assert (await _breaker(pg, q))["breaker_state"] == "open"
    assert await operator.fetchval(f"SELECT taskq.force_close_breaker('{q}','a')") == "closed"
    row = await _breaker(pg, q)
    assert row["breaker_state"] == "closed" and row["breaker_failure_streak"] == 0
    # Verbs reject a queue with no configured breaker.
    await _queue(operator, "c060_bare")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.trip_breaker('c060_bare','a')")
    assert exc.value.sqlstate == "TQ001"


async def test_breaker_off_queue_is_unaffected(
    operator: asyncpg.Connection, producer: asyncpg.Connection, runner: asyncpg.Connection
) -> None:
    q = "c060_off"
    await _queue(operator, q)  # no set_breaker_config
    await _enqueue(producer, q, 4)
    # Terminal failures never trip an unconfigured breaker; claims keep flowing.
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    b = await runner.fetchrow(_CLAIM, q, "w")
    assert b["state"] == "claimed"


async def test_set_breaker_config_operator_only(
    runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    await _queue(operator, "c060_perm")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await runner.fetchval("SELECT taskq.set_breaker_config('c060_perm',3,30,1,'a')")
    assert exc.value.sqlstate == "42501"  # insufficient_privilege
