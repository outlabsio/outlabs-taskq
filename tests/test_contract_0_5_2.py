"""SQL contract 0.5.2 — the schedule-smear write verb (set_schedule_smear).

Completes the schedule-firing smear activated in 0.5.1: 0024 added the inactive
column, 0029 taught the scheduler to read it, and 0030 adds the supported
operator verb to write it. Smear stays an operational knob (like the flow/
concurrency limit verbs) — not part of the schedule definition — so these
vectors exercise the verb directly.
"""

from __future__ import annotations

import json

import asyncpg
import pytest

from taskq.sql.manifest import CONTRACT_VERSION

pytestmark = pytest.mark.taskq_sql


def test_contract_version_is_0_5_2() -> None:
    assert CONTRACT_VERSION == "0.5.2"


async def _make_schedule(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue('c052_q', '{}'::jsonb, 'c052')")
    definition = {
        "target": {"kind": "job", "queue": "c052_q", "job_type": "c052.job"},
        "recurrence": {"kind": "cron", "expression": "0 * * * *", "timezone": "UTC"},
        "catchup_policy": "skip",
        "max_catchup": 1,
    }
    row = await operator.fetchrow(
        "SELECT * FROM taskq.put_schedule($1, $2::jsonb, 'c052')", name, json.dumps(definition)
    )
    assert row is not None


async def test_set_schedule_smear_sets_updates_and_clears(
    pg: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    await _make_schedule(operator, "c052.sched")

    async def stored() -> int | None:
        return await pg.fetchval(
            "SELECT smear_seconds FROM taskq.schedules WHERE name='c052.sched'"
        )

    assert await stored() is None
    assert (
        await operator.fetchval("SELECT taskq.set_schedule_smear('c052.sched', 300, 'c052')")
        == "updated"
    )
    assert await stored() == 300
    # State-derived idempotency: re-setting the same value is a no-op verdict.
    assert (
        await operator.fetchval("SELECT taskq.set_schedule_smear('c052.sched', 300, 'c052')")
        == "unchanged"
    )
    assert (
        await operator.fetchval("SELECT taskq.set_schedule_smear('c052.sched', 1800, 'c052')")
        == "updated"
    )
    assert await stored() == 1800
    # NULL clears the smear — schedules fire on the exact lattice again.
    assert (
        await operator.fetchval("SELECT taskq.set_schedule_smear('c052.sched', NULL, 'c052')")
        == "cleared"
    )
    assert await stored() is None
    # The schedule version is deliberately NOT bumped by smear (operational knob,
    # not definition) — declarative manifest reconciliation stays undisturbed.
    version = await pg.fetchval("SELECT version FROM taskq.schedules WHERE name='c052.sched'")
    assert version == 1


@pytest.mark.parametrize("bad", [0, 3601, -5])
async def test_set_schedule_smear_rejects_out_of_range(
    operator: asyncpg.Connection, bad: int
) -> None:
    await _make_schedule(operator, "c052.range")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval(f"SELECT taskq.set_schedule_smear('c052.range', {bad}, 'c052')")
    assert exc.value.sqlstate == "TQ422"


async def test_set_schedule_smear_unknown_schedule_raises(operator: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.PostgresError) as exc:
        await operator.fetchval("SELECT taskq.set_schedule_smear('c052.nope', 300, 'c052')")
    assert exc.value.sqlstate == "TQ001"


async def test_set_schedule_smear_denied_to_non_operator(
    runner: asyncpg.Connection, operator: asyncpg.Connection
) -> None:
    await _make_schedule(operator, "c052.perm")
    with pytest.raises(asyncpg.PostgresError) as exc:
        await runner.fetchval("SELECT taskq.set_schedule_smear('c052.perm', 300, 'c052')")
    assert exc.value.sqlstate == "42501"  # insufficient_privilege
