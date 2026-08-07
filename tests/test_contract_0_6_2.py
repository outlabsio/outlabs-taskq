"""SQL contract 0.6.2 — breaker observability.

queue_health gains a `breaker_open` verdict + breaker detail; the settle trigger
emits breaker_opened / breaker_reopened / breaker_closed job events on the job
that drove each transition. See docs/Task Queue 0.6 Circuit Breaker Specification.md.
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
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c062')", name)


async def _enqueue(producer: asyncpg.Connection, queue: str, n: int) -> None:
    for i in range(n):
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'j','{}'::jsonb,p_idempotency_key=>$2)",
            queue,
            f"{queue}-{i}",
        )


async def _claim_and_fail(runner: asyncpg.Connection, queue: str) -> None:
    b = await runner.fetchrow(_CLAIM, queue, "w")
    assert b is not None and b["state"] == "claimed", f"got {b['state']}"
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
    assert b is not None and b["state"] == "claimed", f"got {b['state']}"
    job = b["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3)", job["job_id"], job["attempt_id"], "w"
    )


async def _verdict(pg: asyncpg.Connection, queue: str) -> str:
    return str(await pg.fetchval("SELECT verdict FROM taskq.queue_health($1)", queue))


async def _breaker_events(pg: asyncpg.Connection, queue: str) -> list[str]:
    rows = await pg.fetch(
        "SELECT e.event_type FROM taskq.job_events e JOIN taskq.jobs j ON j.id = e.job_id"
        " WHERE j.queue=$1 AND e.event_type LIKE 'breaker_%' ORDER BY e.id",
        queue,
    )
    return [r["event_type"] for r in rows]


async def test_queue_health_breaker_open_verdict_and_detail(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c062_verdict"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',2,1,1,'a')")
    await _enqueue(producer, q, 6)
    # Closed: verdict is not breaker_open, detail carries closed state.
    row = await pg.fetchrow("SELECT verdict, detail FROM taskq.queue_health($1)", q)
    detail = row["detail"] if isinstance(row["detail"], dict) else json.loads(row["detail"])
    assert row["verdict"] != "breaker_open"
    assert detail["breaker"]["state"] == "closed"
    # Trip -> breaker_open verdict + open detail.
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    row = await pg.fetchrow("SELECT verdict, detail FROM taskq.queue_health($1)", q)
    detail = row["detail"] if isinstance(row["detail"], dict) else json.loads(row["detail"])
    assert row["verdict"] == "breaker_open"
    assert detail["breaker"]["state"] == "open"
    assert detail["breaker"]["opened_total"] == 1
    assert detail["breaker"]["tripped_at"] is not None
    # Recover -> no longer breaker_open.
    await asyncio.sleep(1.2)
    await _claim_and_complete(runner, q)
    assert await _verdict(pg, q) != "breaker_open"


async def test_breaker_events_track_transitions(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c062_events"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',2,1,1,'a')")
    await _enqueue(producer, q, 8)
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)  # trips
    assert await _breaker_events(pg, q) == ["breaker_opened"]
    # Probe fails -> reopened.
    await asyncio.sleep(1.2)
    await _claim_and_fail(runner, q)
    assert await _breaker_events(pg, q) == ["breaker_opened", "breaker_reopened"]
    # Probe succeeds -> closed.
    await asyncio.sleep(1.2)
    await _claim_and_complete(runner, q)
    assert await _breaker_events(pg, q) == [
        "breaker_opened",
        "breaker_reopened",
        "breaker_closed",
    ]
    # The breaker_opened event carries useful data.
    data = await pg.fetchval(
        "SELECT e.data FROM taskq.job_events e JOIN taskq.jobs j ON j.id=e.job_id"
        " WHERE j.queue=$1 AND e.event_type='breaker_opened' LIMIT 1",
        q,
    )
    payload = data if isinstance(data, dict) else json.loads(data)
    assert payload["threshold"] == 2 and payload["queue"] == q


async def test_breaker_off_queue_has_no_breaker_detail_or_events(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c062_off"
    await _queue(operator, q)  # no breaker configured
    await _enqueue(producer, q, 4)
    await _claim_and_fail(runner, q)
    await _claim_and_fail(runner, q)
    row = await pg.fetchrow("SELECT verdict, detail FROM taskq.queue_health($1)", q)
    detail = row["detail"] if isinstance(row["detail"], dict) else json.loads(row["detail"])
    assert row["verdict"] != "breaker_open"
    assert detail["breaker"] is None
    assert await _breaker_events(pg, q) == []
