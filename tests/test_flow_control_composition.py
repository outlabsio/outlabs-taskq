"""Flow-control gate composition — the five claim-path gates exercised together.

Each gate (breaker streak+rate, max_running, queue rate limit, slow-start ramp,
priority aging) is unit/contract-tested alone; this verifies they compose in the
claim path without one silently defeating another. Deterministic (backdated
jobs, manual breaker verbs) rather than timing-heavy.
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

_CLAIM = (
    "SELECT * FROM taskq.claim_jobs("
    "$1,$2,$3::integer,NULL::text[],NULL::integer,NULL::text,NULL::uuid,true)"
)


async def _ensure(operator: asyncpg.Connection, name: str, profile: dict) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,$2::jsonb,'comp')", name, json.dumps(profile)
    )


async def _enq(producer: asyncpg.Connection, queue: str, jt: str, priority: int, key: str) -> None:
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,$2,'{}'::jsonb,p_priority=>$3::smallint,p_idempotency_key=>$4)",
        queue,
        jt,
        priority,
        key,
    )


async def _running(pg: asyncpg.Connection, queue: str) -> int:
    return int(
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE queue=$1 AND status='running'", queue
        )
        or 0
    )


async def test_open_breaker_throttles_before_cap_or_rate(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    # A cap of 5 and a generous rate would both admit work — but an open breaker
    # is checked first and must throttle with nothing running.
    q = "comp_precedence"
    await _ensure(operator, q, {"max_running": 5, "claim_rate_per_minute": 600, "claim_burst": 50})
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',3,30,1,'comp')")
    for i in range(10):
        await _enq(producer, q, "j", 0, f"{q}-{i}")
    await operator.fetchval(f"SELECT taskq.trip_breaker('{q}')")
    b = await runner.fetchrow(_CLAIM, q, "w", 1)
    assert b["state"] == "throttled"
    assert await _running(pg, q) == 0


async def test_ramp_engages_after_breaker_recovery(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    # max_running is 10, but after the breaker recovers the ramp holds the
    # effective cap near its floor, so a sequential burst admits ~1, not 10.
    q = "comp_ramp"
    await _ensure(operator, q, {"max_running": 10, "ramp_seconds": 30})
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',2,1,1,'comp')")
    for i in range(20):
        await _enq(producer, q, "j", 0, f"{q}-{i}")

    async def claim_fail() -> None:
        b = await runner.fetchrow(_CLAIM, q, "w", 1)
        assert b["state"] == "claimed"
        job = b["jobs"][0]
        await runner.fetchrow(
            "SELECT * FROM taskq.fail_job($1,$2,$3,$4,false)",
            job["job_id"],
            job["attempt_id"],
            "w",
            "boom",
        )

    await claim_fail()
    await claim_fail()  # trips
    assert (
        await pg.fetchval(f"SELECT breaker_state FROM taskq.queue_flow WHERE queue='{q}'") == "open"
    )
    await asyncio.sleep(1.2)
    # Probe claim + complete -> breaker closes and stamps the ramp.
    b = await runner.fetchrow(_CLAIM, q, "w", 1)
    assert b["state"] == "claimed"
    job = b["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
    )
    flow = await pg.fetchrow(
        f"SELECT breaker_state, ramp_started_at FROM taskq.queue_flow WHERE queue='{q}'"
    )
    assert flow["breaker_state"] == "closed" and flow["ramp_started_at"] is not None
    # Sequential claims (jobs stay running): the ramp caps admission near 1.
    admitted = 0
    for _ in range(12):
        b = await runner.fetchrow(_CLAIM, q, "w", 1)
        if b["state"] == "claimed":
            admitted += 1
    assert admitted <= 2, f"ramp did not cap post-recovery admission (got {admitted})"


async def test_aging_orders_candidates_within_a_rate_limit(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    pg: asyncpg.Connection,
) -> None:
    # A rate-limited queue with aging: aging must still reorder the candidate
    # scan, so an aged low-priority job is claimed before fresh high-priority work.
    q = "comp_aging_rate"
    await _ensure(operator, q, {"claim_rate_per_minute": 600, "claim_burst": 10})
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',1,'comp')")
    await _enq(producer, q, "low", 10, "low")
    await pg.execute(
        "UPDATE taskq.jobs SET scheduled_at = now() - interval '600 seconds'"
        " WHERE queue=$1 AND idempotency_key='low'",
        q,
    )
    for i in range(3):
        await _enq(producer, q, "high", 0, f"high{i}")
    b = await runner.fetchrow(_CLAIM, q, "w", 1)
    assert b["state"] == "claimed"
    assert b["jobs"][0]["job_type"] == "low"


async def test_full_stack_drains_without_starvation(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    pg: asyncpg.Connection,
) -> None:
    # Everything on one queue (breaker streak+rate configured but not tripping,
    # cap, generous rate, aging): the aged low-priority cohort is claimed first
    # and every job drains.
    q = "comp_full"
    await _ensure(operator, q, {"max_running": 8, "claim_rate_per_minute": 6000, "claim_burst": 50})
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',100,30,1,'comp')")
    await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q}',0.9,60,100,'comp')")
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',1,'comp')")
    for i in range(3):
        await _enq(producer, q, "low", 10, f"low{i}")
    await pg.execute(
        "UPDATE taskq.jobs SET scheduled_at = now() - interval '600 seconds'"
        " WHERE queue=$1 AND job_type='low'",
        q,
    )
    for i in range(10):
        await _enq(producer, q, "high", 0, f"high{i}")

    order: list[str] = []
    for _ in range(40):
        b = await runner.fetchrow(_CLAIM, q, "w", 4)
        if b["state"] != "claimed":
            break
        for job in b["jobs"]:
            order.append(str(job["job_type"]))
            await runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
            )

    succeeded = int(
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE queue=$1 AND status='succeeded'", q
        )
        or 0
    )
    assert succeeded == 13, f"not all jobs drained ({succeeded}/13)"
    # The three aged low-priority jobs were promoted to the front, not starved.
    assert order[:3] == ["low", "low", "low"], order[:6]
    # The breaker never tripped under normal mixed load.
    assert (
        await pg.fetchval(f"SELECT breaker_state FROM taskq.queue_flow WHERE queue='{q}'")
        == "closed"
    )
