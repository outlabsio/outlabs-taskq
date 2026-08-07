"""SQL contract 0.6.5 — queue-scoped operator audit log.

Closes two flow-control observability gaps: manual breaker verbs (trip / force-close)
now leave a record, and config setters record before/after (config-history). Every
queue-scoped operator verb writes one taskq.queue_audit row on success, attributed to
its actor; taskq.list_queue_audit reads them back (operator + observer).
"""

from __future__ import annotations

import json

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'c065')", name)


async def _audit(reader: asyncpg.Connection, queue: str, limit: int = 50) -> list[asyncpg.Record]:
    return await reader.fetch(
        "SELECT * FROM taskq.list_queue_audit($1,$2,NULL::bigint)", queue, limit
    )


def _detail(row: asyncpg.Record) -> dict:
    d = row["detail"]
    return d if isinstance(d, dict) else json.loads(d)


async def test_breaker_config_records_before_after(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    q = "c065_cfg"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',5,30,1,'operator:alice')")
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',9,60,2,'operator:bob')")
    rows = await _audit(operator, q)
    # Newest first: the update, then the create.
    assert [r["event_type"] for r in rows] == ["breaker_config_set", "breaker_config_set"]
    assert rows[0]["actor"] == "operator:bob"
    assert rows[1]["actor"] == "operator:alice"
    create, update = rows[1], rows[0]
    assert _detail(create)["before"] is None
    assert _detail(create)["after"]["failure_threshold"] == 5
    assert _detail(update)["before"]["failure_threshold"] == 5
    assert _detail(update)["after"] == {
        "failure_threshold": 9,
        "cooldown_seconds": 60,
        "half_open_successes": 2,
    }


async def test_rate_latency_aging_are_audited(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    q = "c065_more"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',5,30,1,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q}',0.5,60,10,'a')")
    await operator.fetchval(f"SELECT taskq.set_breaker_latency('{q}',2000,60,10,'a')")
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',120,'a')")
    events = [r["event_type"] for r in await _audit(operator, q)]
    # Newest first.
    assert events == ["aging_set", "breaker_latency_set", "breaker_rate_set", "breaker_config_set"]
    rows = await _audit(operator, q)
    rate = next(r for r in rows if r["event_type"] == "breaker_rate_set")
    assert _detail(rate)["after"]["failure_ratio"] == 0.5
    latency = next(r for r in rows if r["event_type"] == "breaker_latency_set")
    assert _detail(latency)["after"]["threshold_ms"] == 2000
    aging = next(r for r in rows if r["event_type"] == "aging_set")
    assert _detail(aging)["after"]["aging_seconds"] == 120


async def test_manual_trip_and_force_close_leave_a_record(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    # The gap this slice closes: manual verbs used to emit nothing.
    q = "c065_manual"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',5,30,1,'a')")
    await operator.fetchval(f"SELECT taskq.trip_breaker('{q}','operator:carol')")
    await operator.fetchval(f"SELECT taskq.force_close_breaker('{q}','operator:dave')")
    rows = await _audit(operator, q)
    trip = next(r for r in rows if r["event_type"] == "breaker_tripped")
    close = next(r for r in rows if r["event_type"] == "breaker_force_closed")
    assert trip["actor"] == "operator:carol"
    assert _detail(trip) == {"before": "closed", "after": "open"}
    assert close["actor"] == "operator:dave"
    assert _detail(close) == {"before": "open", "after": "closed"}


async def test_failed_verb_writes_no_audit_row(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    q = "c065_fail"
    await _queue(operator, q)
    # No configured breaker -> TQ001, and the (absent) audit row rolls back with it.
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval(f"SELECT taskq.trip_breaker('{q}','a')")
    assert exc.value.sqlstate == "TQ001"
    # Bad validation on an otherwise-valid queue -> TQ422, still no row.
    await operator.fetchval(f"SELECT taskq.set_breaker_config('{q}',5,30,1,'a')")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval(f"SELECT taskq.set_breaker_rate('{q}',5,60,10,'a')")  # ratio > 1
    assert exc.value.sqlstate == "TQ422"
    events = [r["event_type"] for r in await _audit(operator, q)]
    assert events == ["breaker_config_set"]  # only the one successful call


async def test_list_queue_audit_scopes_paginates_validates(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    a, b = "c065_a", "c065_b"
    await _queue(operator, a)
    await _queue(operator, b)
    for i in (1, 2, 3):
        await operator.fetchval(f"SELECT taskq.set_priority_aging('{a}',{i * 10},'a')")
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{b}',99,'a')")
    # Scoped to the queue.
    assert len(await _audit(operator, a)) == 3
    assert len(await _audit(operator, b)) == 1
    # Newest-first + limit.
    top = await _audit(operator, a, limit=1)
    assert len(top) == 1 and _detail(top[0])["after"]["aging_seconds"] == 30
    # Keyset pagination: older than the newest id.
    page = await operator.fetch("SELECT * FROM taskq.list_queue_audit($1,50,$2)", a, top[0]["id"])
    assert [_detail(r)["after"]["aging_seconds"] for r in page] == [20, 10]
    # Input validation.
    for bad in ("0", "101"):
        with pytest.raises(asyncpg.PostgresError) as exc:
            await operator.fetch(f"SELECT * FROM taskq.list_queue_audit('{a}',{bad},NULL::bigint)")
        assert exc.value.sqlstate == "TQ422"


async def test_list_queue_audit_readable_by_operator_and_observer_not_runner(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    observer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    q = "c065_perm"
    await _queue(operator, q)
    await operator.fetchval(f"SELECT taskq.set_priority_aging('{q}',30,'a')")
    assert len(await _audit(operator, q)) == 1
    assert len(await _audit(observer, q)) == 1
    with pytest.raises(asyncpg.PostgresError) as exc:
        await _audit(runner, q)
    assert exc.value.sqlstate == "42501"
