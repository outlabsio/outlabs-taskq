"""Contract 0.5.0: flow enforcement — throttle verdicts, rate limits, TTL, try_enqueue."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


async def _ensure(operator: asyncpg.Connection, queue: str, profile: dict[str, Any]) -> None:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 'c050')", queue, json.dumps(profile)
    )
    assert row is not None


async def _enqueue(producer: asyncpg.Connection, queue: str, key: str, **params: Any) -> None:
    cols = ["p_idempotency_key => $2"]
    args: list[Any] = [queue, key]
    for name, value in params.items():
        args.append(value)
        cols.append(f"p_{name} => ${len(args)}")
    await producer.fetchrow(
        f"SELECT * FROM taskq.enqueue($1, 'c050.t', '{{}}'::jsonb, {', '.join(cols)})", *args
    )


async def _claim(
    runner: asyncpg.Connection, queue: str, worker: str, *, accept_throttled: bool = True
) -> asyncpg.Record:
    row = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1, $2, 5, NULL, NULL, NULL, NULL, $3::boolean)",
        queue,
        worker,
        accept_throttled,
    )
    assert row is not None
    return row


async def test_max_running_throttles_declaring_callers_only(
    producer: asyncpg.Connection, runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    queue = "c050_cap"
    await _ensure(operator, queue, {"max_running": 1})
    for i in range(3):
        await _enqueue(producer, queue, f"cap-{i}")
    first = await _claim(runner, queue, "w1")
    assert first["state"] == "claimed"
    # cap reached: declaring caller gets throttled + hint; legacy caller gets empty
    throttled = await _claim(runner, queue, "w2", accept_throttled=True)
    assert throttled["state"] == "throttled"
    assert throttled["retry_after_seconds"] >= 1
    legacy = await _claim(runner, queue, "w2", accept_throttled=False)
    assert legacy["state"] == "empty"
    assert legacy["retry_after_seconds"] is None


async def test_claim_rate_limit_grants_burst_then_throttles(
    producer: asyncpg.Connection, runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    queue = "c050_rate"
    await _ensure(operator, queue, {"claim_rate_per_minute": 6, "claim_burst": 1})
    for i in range(4):
        await _enqueue(producer, queue, f"rate-{i}")
    first = await _claim(runner, queue, "w1")
    assert first["state"] == "claimed" and len(first["jobs"]) == 1
    throttled = await _claim(runner, queue, "w1")
    assert throttled["state"] == "throttled"
    # emission interval is 60/6 = 10s
    assert 1 <= throttled["retry_after_seconds"] <= 11


async def test_flow_key_limits_one_candidate_but_unlimited_flow(
    pg: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
) -> None:
    queue = "c050_fk"
    await _ensure(operator, queue, {})
    await operator.fetchval("SELECT taskq.set_flow_limit('c050.site', 6, 1, 'c050')")
    for i in range(4):
        await _enqueue(producer, queue, f"fk-{i}")
    limited = [
        r["id"]
        for r in await pg.fetch(
            "SELECT id FROM taskq.jobs WHERE queue=$1 AND status='queued' ORDER BY id LIMIT 2",
            queue,
        )
    ]
    await pg.execute(
        "UPDATE taskq.jobs SET flow_key='c050.site' WHERE id = ANY($1::uuid[])", limited
    )
    result = await _claim(runner, queue, "w1")
    assert result["state"] == "claimed"
    by_key = {
        r["flow_key"]: r["count"]
        for r in await pg.fetch(
            "SELECT flow_key, count(*) FROM taskq.jobs WHERE status='running'"
            " AND worker_id='w1' GROUP BY flow_key"
        )
    }
    # exactly one of the rate-limited key admitted; the two unlimited ones flow
    assert by_key.get("c050.site") == 1
    assert by_key.get(None, 0) >= 1


async def test_job_ttl_skips_and_the_tick_settles_expired(
    pg: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    queue = "c050_ttl"
    await _ensure(operator, queue, {})
    await _enqueue(producer, queue, "ttl-live")
    await _enqueue(producer, queue, "ttl-doomed", ttl_seconds=1)
    stamped = await pg.fetchval(
        "SELECT count(*) FROM taskq.jobs WHERE queue=$1 AND expires_at IS NOT NULL", queue
    )
    assert stamped == 1
    await asyncio.sleep(1.2)
    # expired candidate is invisible to claims; only the live one is claimable
    result = await _claim(runner, queue, "w1")
    assert result["state"] == "claimed"
    assert all(job["job_type"] == "c050.t" for job in result["jobs"])
    live_claimed = {job["job_id"] for job in result["jobs"]}
    doomed = await pg.fetchval(
        "SELECT id FROM taskq.jobs WHERE queue=$1 AND idempotency_key='ttl-doomed'", queue
    )
    assert doomed not in live_claimed
    await pg.execute("SET ROLE taskq_owner")
    try:
        n = await pg.fetchval("SELECT taskq.expire_ttl(200)")
    finally:
        await pg.execute("RESET ROLE")
    assert n == 1
    row = await pg.fetchrow(
        "SELECT status, outcome FROM taskq.jobs WHERE queue=$1 AND idempotency_key='ttl-doomed'",
        queue,
    )
    assert row["status"] == "cancelled" and row["outcome"] == "expired_ttl"


async def test_try_enqueue_verdicts_and_replay_at_cap(
    producer: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    queue = "c050_try"
    await _ensure(operator, queue, {"max_depth": 2})

    async def attempt(key: str) -> asyncpg.Record:
        row = await producer.fetchrow(
            "SELECT * FROM taskq.try_enqueue($1, 'c050.t', '{}'::jsonb, p_idempotency_key => $2)",
            queue,
            key,
        )
        assert row is not None
        return row

    a = await attempt("a")
    b = await attempt("b")
    assert a["outcome"] == "accepted" and b["outcome"] == "accepted"
    # depth cap reached: a fresh key is rejected with a typed retry hint
    c = await attempt("c")
    assert c["outcome"] == "rejected_depth"
    assert c["retry_after_seconds"] >= 1
    # the L4 fix: replaying an ALREADY-ADMITTED key succeeds at cap (existed adds no depth)
    replay = await attempt("a")
    assert replay["outcome"] == "existed"
    assert replay["job_id"] == a["job_id"]


async def test_notify_mode_idle_transition_fires_only_on_edge(
    pg: asyncpg.Connection, producer: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    queue = "c050_notify"
    await _ensure(operator, queue, {"notify_mode": "on_idle_transition"})
    async with pg.transaction():
        listener = await pg.fetchval("SELECT 1")  # keep the txn open marker
        del listener
    notices: list[str] = []
    await pg.add_listener(f"taskq_{queue}", lambda *a: notices.append(a[-1]))
    await _enqueue(producer, queue, "n0")  # idle -> non-idle: notifies
    await _enqueue(producer, queue, "n1")  # already non-idle: suppressed
    await asyncio.sleep(0.2)
    await pg.remove_listener(f"taskq_{queue}", lambda *a: None)
    assert len(notices) == 1


async def test_resume_ramp_scales_effective_cap(
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
    pg: asyncpg.Connection,
) -> None:
    queue = "c050_ramp"
    # max_running 10 but a long ramp just started -> effective cap floors low
    await _ensure(operator, queue, {"max_running": 10, "ramp_seconds": 3600})
    for i in range(5):
        await _enqueue(producer, queue, f"ramp-{i}")
    await operator.fetchval("SELECT taskq.pause_queue($1, 'c050', NULL)", queue)
    assert await operator.fetchval("SELECT taskq.resume_queue($1, 'c050')", queue) == "resumed"
    # immediately after resume the ramp fraction is ~0, so the effective cap is 1
    result = await _claim(runner, queue, "w1")
    assert result["state"] == "claimed"
    running = await pg.fetchval("SELECT running FROM taskq.queue_counters WHERE queue=$1", queue)
    assert running == 1
    throttled = await _claim(runner, queue, "w2")
    assert throttled["state"] == "throttled"
