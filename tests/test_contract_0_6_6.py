"""SQL contract 0.6.6 — queue_audit retention/prune verb.

taskq.prune_queue_audit(older_than_hours) deletes queue_audit rows older than the
cutoff and returns how many it removed. A bounded maintenance op granted to
taskq_housekeeper + taskq_operator; off any hot path.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c066')", name)


async def _audit_count(pg: asyncpg.Connection, queue: str) -> int:
    return int(await pg.fetchval("SELECT count(*) FROM taskq.queue_audit WHERE queue=$1", queue))


async def test_prune_deletes_old_keeps_recent_across_queues(
    pg: asyncpg.Connection, operator: asyncpg.Connection, housekeeper: asyncpg.Connection
) -> None:
    a, b = "c066_a", "c066_b"
    await _queue(operator, a)
    await _queue(operator, b)
    # Two audit rows per queue (each set_priority_aging writes one).
    for q in (a, b):
        await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',10,'a')")
        await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',20,'a')")
    # Backdate the oldest row of each queue to 100 days ago.
    await pg.execute(
        "UPDATE taskq.queue_audit SET created_at = now() - interval '100 days'"
        " WHERE id IN (SELECT min(id) FROM taskq.queue_audit GROUP BY queue)"
    )
    # Prune everything older than 30 days (720h): removes one row per queue, globally.
    deleted = await housekeeper.fetchval("SELECT taskq.prune_queue_audit(720)")
    assert deleted == 2
    assert await _audit_count(pg, a) == 1
    assert await _audit_count(pg, b) == 1
    # Re-running with nothing old left removes nothing.
    assert await housekeeper.fetchval("SELECT taskq.prune_queue_audit(720)") == 0


async def test_prune_validates(operator: asyncpg.Connection) -> None:
    for bad in ("0", "-5", "NULL"):
        with pytest.raises(asyncpg.PostgresError) as exc:
            await operator.fetchval(f"SELECT taskq.prune_queue_audit({bad})")
        assert exc.value.sqlstate == "TQ422"


async def test_prune_granted_to_operator_and_housekeeper_not_runner(
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    assert await operator.fetchval("SELECT taskq.prune_queue_audit(720)") == 0
    assert await housekeeper.fetchval("SELECT taskq.prune_queue_audit(720)") == 0
    with pytest.raises(asyncpg.PostgresError) as exc:
        await runner.fetchval("SELECT taskq.prune_queue_audit(720)")
    assert exc.value.sqlstate == "42501"


async def test_prune_cutoff_unit_is_hours(
    pg: asyncpg.Connection, operator: asyncpg.Connection, housekeeper: asyncpg.Connection
) -> None:
    # Pin the unit: an hours->seconds slip (3600x) would prune a 2-hour-old row at
    # prune(720). A 2h row must survive prune(720) and be deleted by prune(1).
    q = "c066_unit"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',10,'a')")
    await pg.execute(
        "UPDATE taskq.queue_audit SET created_at = now() - interval '2 hours' WHERE queue=$1", q
    )
    assert await housekeeper.fetchval("SELECT taskq.prune_queue_audit(720)") == 0
    assert await _audit_count(pg, q) == 1
    assert await housekeeper.fetchval("SELECT taskq.prune_queue_audit(1)") == 1
    assert await _audit_count(pg, q) == 0
