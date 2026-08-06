"""SQL contract 0.6.1 — per-queue priority aging (P15, S6 Wave 3 fairness).

Opt-in, off by default. A waiting job's effective claim priority improves with
age (priority is ascending — lower = more urgent — so aging subtracts), computed
in the normal claim ORDER BY. Continuation claims keep strict priority. See
docs/Task Queue 0.6 Circuit Breaker Specification.md S7.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

_CLAIM = (
    "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL::text[],NULL::integer,NULL::text,NULL::uuid,true)"
)


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c061')", name)


async def _enqueue(
    producer: asyncpg.Connection, queue: str, job_type: str, priority: int, key: str
) -> None:
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,$2,'{}'::jsonb,p_priority=>$3::smallint,p_idempotency_key=>$4)",
        queue,
        job_type,
        priority,
        key,
    )


async def _first_claim(runner: asyncpg.Connection, queue: str) -> str:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    assert b is not None and b["state"] == "claimed", f"got {b['state']}"
    return str(b["jobs"][0]["job_type"])


async def test_set_priority_aging_validates(operator: asyncpg.Connection) -> None:
    await _queue(operator, "c061_cfg")
    assert await operator.fetchval("SELECT taskq.set_priority_aging('c061_cfg',5,'a')") == "created"
    assert await operator.fetchval("SELECT taskq.set_priority_aging('c061_cfg',2,'a')") == "updated"
    # NULL clears (back to strict priority)
    assert await operator.fetchval("SELECT taskq.set_priority_aging('c061_cfg',NULL,'a')") in (
        "updated",
        "created",
    )
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.set_priority_aging('c061_nope',5,'a')")
    assert exc.value.sqlstate == "TQ001"
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.set_priority_aging('c061_cfg',0,'a')")
    assert exc.value.sqlstate == "TQ422"


async def test_aging_off_is_strict_priority(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c061_strict"
    await _queue(operator, q)  # no aging configured
    await _enqueue(producer, q, "low", 10, "low")
    await pg.execute(
        "UPDATE taskq.jobs SET scheduled_at = now() - interval '600 seconds'"
        " WHERE queue=$1 AND idempotency_key='low'",
        q,
    )
    await _enqueue(producer, q, "high", 0, "high")
    # Strict priority: the fresh high-priority job wins despite the low one's age.
    assert await _first_claim(runner, q) == "high"


async def test_aging_on_promotes_waiting_low_priority(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c061_age"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',1,'a')")  # 1s per step
    await _enqueue(producer, q, "low", 10, "low")
    # Waited 15s at aging_seconds=1 -> boost 15 -> effective 10-15 = -5, beats 0.
    await pg.execute(
        "UPDATE taskq.jobs SET scheduled_at = now() - interval '15 seconds'"
        " WHERE queue=$1 AND idempotency_key='low'",
        q,
    )
    await _enqueue(producer, q, "high", 0, "high")
    assert await _first_claim(runner, q) == "low"


async def test_aging_boost_is_capped(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    # A barely-aged low-priority job does NOT jump a high-priority one — aging is
    # a gradual floor, not an inversion of fresh work.
    q = "c061_gradual"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',60,'a')")  # 60s per step
    await _enqueue(producer, q, "low", 10, "low")
    await pg.execute(
        "UPDATE taskq.jobs SET scheduled_at = now() - interval '30 seconds'"
        " WHERE queue=$1 AND idempotency_key='low'",
        q,
    )  # 30s < 60s -> boost 0
    await _enqueue(producer, q, "high", 0, "high")
    assert await _first_claim(runner, q) == "high"


async def test_set_priority_aging_operator_only(
    runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    await _queue(operator, "c061_perm")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await runner.fetchval("SELECT taskq.set_priority_aging('c061_perm',5,'a')")
    assert exc.value.sqlstate == "42501"
