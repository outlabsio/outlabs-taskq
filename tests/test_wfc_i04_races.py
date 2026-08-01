"""WFC-I04 deterministic workflow-control and settlement races.

Open transactions hold production locks.  Contenders are released only after
their lock wait is visible in ``pg_stat_activity``; sleeps are polling, never
ordering.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

from conftest import RoleConnect

pytestmark = pytest.mark.taskq_sql

POLICY = "a" * 64
_WAIT_TIMEOUT = 3.0


@pytest_asyncio.fixture
async def active_wfc(pg: asyncpg.Connection) -> AsyncIterator[None]:
    original = await pg.fetchval("SELECT value FROM taskq.meta WHERE key='capabilities'")
    await pg.execute(
        """
        UPDATE taskq.meta SET value=jsonb_set(
            value,'{active}',(value->'active') || '"workflow_continuations"'::jsonb
        ),updated_at=now() WHERE key='capabilities'
        """
    )
    try:
        yield
    finally:
        await pg.execute(
            "UPDATE taskq.meta SET value=$1::jsonb,updated_at=now() WHERE key='capabilities'",
            original,
        )


async def _wait_for_lock(monitor: asyncpg.Connection, pid: int, events: Iterable[str]) -> str:
    expected = set(events)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WAIT_TIMEOUT
    last: tuple[str | None, str | None] | None = None
    while loop.time() < deadline:
        row = await monitor.fetchrow(
            "SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=$1", pid
        )
        if row is not None:
            last = (row["wait_event_type"], row["wait_event"])
            if row["wait_event_type"] == "Lock" and row["wait_event"] in expected:
                blockers = await monitor.fetchval("SELECT cardinality(pg_blocking_pids($1))", pid)
                assert blockers >= 1
                return row["wait_event"]
        await asyncio.sleep(0.005)
    raise AssertionError(f"pid {pid} did not wait for {sorted(expected)}; last={last!r}")


async def _queue(operator: asyncpg.Connection, *names: str) -> None:
    for name in names:
        await operator.fetchrow(
            "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'wfc-i04-race')", name
        )


async def _workflow(producer: asyncpg.Connection, key: str, queues: list[str], limit: int) -> UUID:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,$2,'race',$3,$4)",
        key,
        queues,
        limit,
        POLICY,
    )
    assert row is not None
    return row["workflow_id"]


async def _member(producer: asyncpg.Connection, workflow_id: UUID, queue: str, step: str) -> UUID:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,'test.race','{}'::jsonb,p_workflow_id=>$2,p_step_key=>$3)",
        queue,
        workflow_id,
        step,
    )
    assert row is not None and row["created"] is True
    return row["job_id"]


async def _claim(
    runner: asyncpg.Connection, queue: str, worker: str, job_id: UUID
) -> asyncpg.Record:
    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,$3,$4)",
        queue,
        worker,
        job_id,
        [POLICY],
    )
    assert batch is not None and len(batch["jobs"]) == 1
    return batch["jobs"][0]


def _followup(step: str) -> str:
    return json.dumps(
        [
            {
                "step": step,
                "job_type": "test.child",
                "queue": "child",
                "payload": {},
                "workflow_member": True,
            }
        ]
    )


async def test_external_admit_and_seal_linearize_in_both_orders(
    active_wfc: None,
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent")

    admit_first = await _workflow(producer, "race-admit-first", ["parent"], 2)
    first = await role_conn("taskq_producer")
    second = await role_conn("taskq_producer")
    transaction = first.transaction()
    await transaction.start()
    await _member(first, admit_first, "parent", "root")
    contender = asyncio.create_task(
        second.fetchrow("SELECT * FROM taskq.seal_workflow($1,'seal')", admit_first)
    )
    await _wait_for_lock(pg, second.get_server_pid(), {"transactionid", "tuple"})
    await transaction.commit()
    sealed = await asyncio.wait_for(contender, _WAIT_TIMEOUT)
    assert sealed is not None and sealed["outcome"] == "sealed"
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1", admit_first) == 1
    )

    seal_first = await _workflow(producer, "race-seal-first", ["parent"], 2)
    sealer = await role_conn("taskq_producer")
    admit = await role_conn("taskq_producer")
    transaction = sealer.transaction()
    await transaction.start()
    await sealer.fetchrow("SELECT * FROM taskq.seal_workflow($1,'seal')", seal_first)
    contender = asyncio.create_task(_member(admit, seal_first, "parent", "late"))
    await _wait_for_lock(pg, admit.get_server_pid(), {"transactionid", "tuple"})
    await transaction.commit()
    with pytest.raises(asyncpg.PostgresError) as refused:
        await asyncio.wait_for(contender, _WAIT_TIMEOUT)
    assert refused.value.sqlstate == "TQ409"
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            seal_first,
        )
        == 0
    )


async def test_external_admit_and_cancel_linearize_without_escape(
    active_wfc: None,
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent")
    workflow_id = await _workflow(producer, "race-admit-cancel", ["parent"], 2)
    admit = await role_conn("taskq_producer")
    cancel = await role_conn("taskq_operator")
    transaction = admit.transaction()
    await transaction.start()
    job_id = await _member(admit, workflow_id, "parent", "root")
    contender = asyncio.create_task(
        cancel.fetchrow("SELECT * FROM taskq.cancel_workflow($1,'operator','race')", workflow_id)
    )
    await _wait_for_lock(pg, cancel.get_server_pid(), {"transactionid", "tuple"})
    await transaction.commit()
    result = await asyncio.wait_for(contender, _WAIT_TIMEOUT)
    assert result is not None and result["outcome"] == "cancel_requested"
    assert await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", job_id) == "cancelled"
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 1
    )


async def test_cancellation_sweep_skips_inflight_heartbeat_then_converges(
    active_wfc: None,
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent")
    workflow_id = await _workflow(producer, "race-cancel-heartbeat", ["parent"], 1)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    runner = await role_conn("taskq_runner")
    claim = await _claim(runner, "parent", "worker-heartbeat", parent_id)

    transaction = runner.transaction()
    await transaction.start()
    heartbeat = await runner.fetchrow(
        "SELECT * FROM taskq.heartbeat($1,$2,$3)",
        parent_id,
        claim["attempt_id"],
        "worker-heartbeat",
    )
    assert heartbeat is not None
    assert heartbeat["ok"] is True
    assert heartbeat["cancel_requested"] is False

    cancelled = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','race')", workflow_id
    )
    assert cancelled is not None and cancelled["outcome"] == "cancel_requested"
    assert (
        await pg.fetchval(
            "SELECT cancel_requested_at IS NULL FROM taskq.jobs WHERE id=$1",
            parent_id,
        )
        is True
    )

    await transaction.commit()
    assert await pg.fetchval("SELECT taskq.advance_workflow_cancellations(100)") == 1
    assert (
        await pg.fetchval(
            "SELECT cancel_requested_at IS NOT NULL FROM taskq.jobs WHERE id=$1",
            parent_id,
        )
        is True
    )
    observed = await runner.fetchrow(
        "SELECT * FROM taskq.heartbeat($1,$2,$3)",
        parent_id,
        claim["attempt_id"],
        "worker-heartbeat",
    )
    assert observed is not None
    assert observed["ok"] is True
    assert observed["cancel_requested"] is True


async def test_settlement_is_compatible_with_seal_cancel_and_finalizer(
    active_wfc: None,
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")

    for control in ("seal", "cancel", "finalize"):
        workflow_id = await _workflow(producer, f"race-settle-{control}", ["parent", "child"], 2)
        parent_id = await _member(producer, workflow_id, "parent", "root")
        runner = await role_conn("taskq_runner")
        claim = await _claim(runner, "parent", f"worker-{control}", parent_id)
        transaction = runner.transaction()
        await transaction.start()
        settled = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
            parent_id,
            claim["attempt_id"],
            f"worker-{control}",
            _followup(control),
            POLICY,
        )
        assert settled is not None and settled[0] == "ok"

        if control == "seal":
            controller = await role_conn("taskq_producer")
            outcome = await asyncio.wait_for(
                controller.fetchrow(
                    "SELECT * FROM taskq.seal_workflow($1,'producer')", workflow_id
                ),
                _WAIT_TIMEOUT,
            )
            assert outcome is not None and outcome["outcome"] == "sealed"
        elif control == "cancel":
            controller = await role_conn("taskq_operator")
            outcome = await asyncio.wait_for(
                controller.fetchrow(
                    "SELECT * FROM taskq.cancel_workflow($1,'operator','race')",
                    workflow_id,
                ),
                _WAIT_TIMEOUT,
            )
            assert outcome is not None and outcome["outcome"] == "cancel_requested"
        else:
            await asyncio.wait_for(
                pg.fetchval("SELECT taskq.finalize_workflows(100)"), _WAIT_TIMEOUT
            )
            assert (
                await pg.fetchval("SELECT status FROM taskq.workflows WHERE id=$1", workflow_id)
                == "running"
            )
        await transaction.commit()

        child_id = await pg.fetchval("SELECT id FROM taskq.jobs WHERE parent_job_id=$1", parent_id)
        assert child_id is not None
        if control == "cancel":
            assert await pg.fetchval("SELECT taskq.advance_workflow_cancellations(100)") == 1
            assert (
                await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", child_id)
                == "cancelled"
            )
        else:
            assert (
                await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", child_id) == "queued"
            )
        assert (
            await pg.fetchval("SELECT status FROM taskq.workflows WHERE id=$1", workflow_id)
            == "running"
        )


async def test_competing_settlements_admit_one_complete_set_at_final_capacity(
    active_wfc: None,
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")
    workflow_id = await _workflow(producer, "race-final-capacity", ["parent", "child"], 3)
    first_parent = await _member(producer, workflow_id, "parent", "first")
    second_parent = await _member(producer, workflow_id, "parent", "second")
    first_runner = await role_conn("taskq_runner")
    second_runner = await role_conn("taskq_runner")
    first_claim = await _claim(first_runner, "parent", "worker-first", first_parent)
    second_claim = await _claim(second_runner, "parent", "worker-second", second_parent)

    transaction = first_runner.transaction()
    await transaction.start()
    first = await first_runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
        first_parent,
        first_claim["attempt_id"],
        "worker-first",
        _followup("first"),
        POLICY,
    )
    assert first is not None and first[0] == "ok"
    contender = asyncio.create_task(
        second_runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
            second_parent,
            second_claim["attempt_id"],
            "worker-second",
            _followup("second"),
            POLICY,
        )
    )
    await _wait_for_lock(pg, second_runner.get_server_pid(), {"transactionid", "tuple"})
    await transaction.commit()
    with pytest.raises(asyncpg.PostgresError) as refused:
        await asyncio.wait_for(contender, _WAIT_TIMEOUT)
    assert refused.value.sqlstate == "TQ409"
    assert refused.value.detail == '{"reason":"workflow_member_limit_exceeded"}'
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 3
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE parent_job_id IN ($1,$2)",
            first_parent,
            second_parent,
        )
        == 1
    )
    statuses = await pg.fetch(
        "SELECT id,status FROM taskq.jobs WHERE id=ANY($1::uuid[]) ORDER BY id",
        [first_parent, second_parent],
    )
    assert sorted(row["status"] for row in statuses) == ["running", "succeeded"]


async def test_cancel_and_finalize_are_serialized_terminal_noops(
    active_wfc: None,
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent")
    workflow_id = await _workflow(producer, "race-cancel-finalize", ["parent"], 1)
    cancel = await role_conn("taskq_operator")
    transaction = cancel.transaction()
    await transaction.start()
    result = await cancel.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','race')", workflow_id
    )
    assert result is not None and result["status"] == "cancelled"
    assert (
        await asyncio.wait_for(pg.fetchval("SELECT taskq.finalize_workflows(100)"), _WAIT_TIMEOUT)
        == 0
    )
    await transaction.commit()
    assert (
        await pg.fetchval("SELECT status FROM taskq.workflows WHERE id=$1", workflow_id)
        == "cancelled"
    )

    other = await _workflow(producer, "race-finalize-cancel", ["parent"], 1)
    await pg.execute(
        "UPDATE taskq.workflows SET sealed_at=now(),sealed_by='proof' WHERE id=$1",
        other,
    )
    assert await pg.fetchval("SELECT taskq.finalize_workflows(100)") == 1
    replay = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','late')", other
    )
    assert replay is not None and replay["outcome"] == "already_terminal"
    assert replay["status"] == "succeeded"
