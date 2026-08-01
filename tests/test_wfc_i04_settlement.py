"""WFC-I04 atomic settlement, lifetime capacity, replay, and convergence proof."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

pytestmark = pytest.mark.taskq_sql

POLICY = "a" * 64


@pytest_asyncio.fixture
async def active_wfc(pg: asyncpg.Connection) -> AsyncIterator[None]:
    """Activate 0016 only inside this scratch test's visibility window."""

    original = await pg.fetchval("SELECT value FROM taskq.meta WHERE key='capabilities'")
    await pg.execute(
        """
        UPDATE taskq.meta
        SET value = jsonb_set(
            value,
            '{active}',
            (value->'active') || '"workflow_continuations"'::jsonb
        ),
        updated_at = now()
        WHERE key = 'capabilities'
        """
    )
    assert await pg.fetchval("SELECT taskq.has_capability('workflow_continuations')")
    try:
        yield
    finally:
        await pg.execute(
            "UPDATE taskq.meta SET value=$1::jsonb,updated_at=now() WHERE key='capabilities'",
            original,
        )


async def _queue(operator: asyncpg.Connection, *names: str) -> None:
    for name in names:
        await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'wfc-i04')", name)


async def _workflow(
    producer: asyncpg.Connection,
    key: str,
    queues: list[str],
    *,
    limit: int,
) -> UUID:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,$2,'wfc-i04',$3,$4)",
        key,
        queues,
        limit,
        POLICY,
    )
    assert row is not None
    return row["workflow_id"]


async def _member(
    producer: asyncpg.Connection,
    workflow_id: UUID,
    queue: str,
    step: str,
    *,
    depends_on: list[UUID] | None = None,
) -> UUID:
    row = await producer.fetchrow(
        """
        SELECT * FROM taskq.enqueue(
            $1,'test.wfc','{}'::jsonb,
            p_depends_on=>$4,p_workflow_id=>$2,p_step_key=>$3
        )
        """,
        queue,
        workflow_id,
        step,
        depends_on,
    )
    assert row is not None and row["created"] is True
    return row["job_id"]


async def _claim(
    runner: asyncpg.Connection, queue: str, worker: str, job_id: UUID | None = None
) -> asyncpg.Record:
    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,$3,$4)",
        queue,
        worker,
        job_id,
        [POLICY],
    )
    assert batch is not None and batch["state"] == "claimed"
    assert len(batch["jobs"]) == 1
    return batch["jobs"][0]


def _member_followup(step: str, queue: str = "child") -> dict[str, object]:
    return {
        "step": step,
        "job_type": "test.child",
        "queue": queue,
        "payload": {"step": step},
        "workflow_member": True,
    }


async def test_atomic_settlement_derives_identity_promotes_and_replays_once(
    active_wfc: None,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child", "detached")
    workflow_id = await _workflow(producer, "wfc-i04-atomic", ["parent", "child"], limit=3)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    dependent_id = await _member(
        producer, workflow_id, "child", "predeclared", depends_on=[parent_id]
    )
    claim = await _claim(runner, "parent", "worker-a", parent_id)
    followups = [
        _member_followup("branch"),
        {
            "step": "audit",
            "job_type": "test.audit",
            "queue": "detached",
            "payload": {},
        },
    ]

    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,$4,$5,$6,$7)",
        parent_id,
        claim["attempt_id"],
        "worker-a",
        json.dumps({"ok": True}),
        json.dumps({"proof": 1}),
        json.dumps(followups),
        POLICY,
    )
    assert settled is not None and tuple(settled)[:2] == ("ok", "succeeded")

    rows = await pg.fetch(
        """
        SELECT id,queue,status,workflow_id,step_key,parent_job_id,
               continuation_policy_hash,idempotency_key
        FROM taskq.jobs
        WHERE id=$1 OR parent_job_id=$1 OR id=$2
        ORDER BY queue,id
        """,
        parent_id,
        dependent_id,
    )
    parent = next(row for row in rows if row["id"] == parent_id)
    dependent = next(row for row in rows if row["id"] == dependent_id)
    member = next(
        row
        for row in rows
        if row["parent_job_id"] == parent_id and row["workflow_id"] == workflow_id
    )
    detached = next(
        row for row in rows if row["parent_job_id"] == parent_id and row["workflow_id"] is None
    )
    assert parent["status"] == "succeeded"
    assert dependent["status"] == "queued"
    assert member["step_key"] == f"c:{parent_id}:branch"
    assert member["idempotency_key"] == f"chain:{parent_id}:branch"
    assert member["continuation_policy_hash"] == POLICY
    assert detached["idempotency_key"] == f"chain:{parent_id}:audit"
    assert detached["continuation_policy_hash"] is None
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 3
    )

    replay = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,$4,$5,$6,$7)",
        parent_id,
        claim["attempt_id"],
        "worker-a",
        None,
        None,
        json.dumps([{"malformed": "ignored-after-response-loss"}]),
        "f" * 64,
    )
    assert replay is not None and replay[0] == "already_settled"
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent_id) == 2
    )
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 3
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.job_events WHERE job_id=$1 AND event_type='succeeded'",
            parent_id,
        )
        == 1
    )


async def test_settlement_replay_survives_workflow_finalization(
    active_wfc: None,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent")
    workflow_id = await _workflow(producer, "wfc-i04-finalized-replay", ["parent"], limit=1)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    claim = await _claim(runner, "parent", "worker-replay", parent_id)
    await producer.fetchrow("SELECT * FROM taskq.seal_workflow($1,'producer')", workflow_id)
    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,'[]'::jsonb,$4)",
        parent_id,
        claim["attempt_id"],
        "worker-replay",
        POLICY,
    )
    assert settled is not None and settled[0] == "ok"
    assert await pg.fetchval("SELECT taskq.finalize_workflows(100)") == 1
    assert (
        await pg.fetchval("SELECT status FROM taskq.workflows WHERE id=$1", workflow_id)
        == "succeeded"
    )

    replay = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
        parent_id,
        claim["attempt_id"],
        "worker-replay",
        json.dumps([{"malformed": "ignored-after-response-loss"}]),
        "f" * 64,
    )
    assert replay is not None and tuple(replay)[:2] == ("already_settled", "succeeded")
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.job_events WHERE job_id=$1 AND event_type='succeeded'",
            parent_id,
        )
        == 1
    )


async def test_old_and_new_claimers_are_executable_policy_cohorts(
    active_wfc: None,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "policy")
    workflow_id = await _workflow(producer, "wfc-i04-policy-cohorts", ["policy"], limit=2)
    first = await _member(producer, workflow_id, "policy", "a")
    second = await _member(producer, workflow_id, "policy", "b")

    legacy = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,p_batch=>1)", "policy", "legacy-worker"
    )
    assert legacy is not None and legacy["state"] == "empty" and legacy["jobs"] == []

    supported = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,$3,$4)",
        "policy",
        "new-worker",
        first,
        [POLICY],
    )
    assert supported is not None and supported["state"] == "claimed"
    assert [job["job_id"] for job in supported["jobs"]] == [first]

    unsupported = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,$3,$4)",
        "policy",
        "wrong-policy-worker",
        second,
        ["b" * 64],
    )
    assert unsupported is not None
    assert unsupported["state"] == "unavailable"
    assert unsupported["jobs"] == []


async def test_continuation_notification_is_commit_gated_and_not_replayed(
    active_wfc: None,
    taskq_dsn: str,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")
    workflow_id = await _workflow(producer, "wfc-i04-notify", ["parent", "child"], limit=2)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    claim = await _claim(runner, "parent", "worker-notify", parent_id)

    listener = await asyncpg.connect(taskq_dsn)
    notifications: list[str] = []
    delivered = asyncio.Event()

    def receive(
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        del connection, pid, payload
        notifications.append(channel)
        delivered.set()

    await listener.add_listener("taskq_child", receive)
    try:
        transaction = runner.transaction()
        await transaction.start()
        settled = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
            parent_id,
            claim["attempt_id"],
            "worker-notify",
            json.dumps([_member_followup("branch")]),
            POLICY,
        )
        assert settled is not None and settled[0] == "ok"
        assert notifications == []
        await transaction.commit()
        await asyncio.wait_for(delivered.wait(), timeout=3)
        assert notifications == ["taskq_child"]
        delivered.clear()

        replay = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,'[]'::jsonb,$4)",
            parent_id,
            claim["attempt_id"],
            "worker-notify",
            POLICY,
        )
        assert replay is not None and replay[0] == "already_settled"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(delivered.wait(), timeout=0.05)
        assert notifications == ["taskq_child"]
    finally:
        await listener.close()


async def test_multi_child_capacity_refusal_rolls_back_every_settlement_effect(
    active_wfc: None,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")
    workflow_id = await _workflow(producer, "wfc-i04-capacity", ["parent", "child"], limit=2)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    claim = await _claim(runner, "parent", "worker-capacity", parent_id)

    with pytest.raises(asyncpg.PostgresError) as refused:
        await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
            parent_id,
            claim["attempt_id"],
            "worker-capacity",
            json.dumps([_member_followup("a"), _member_followup("b")]),
            POLICY,
        )
    assert refused.value.sqlstate == "TQ409"
    assert refused.value.detail == '{"reason":"workflow_member_limit_exceeded"}'
    assert await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", parent_id) == "running"
    assert (
        await pg.fetchval("SELECT status FROM taskq.job_attempts WHERE id=$1", claim["attempt_id"])
        == "running"
    )
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 1
    )
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent_id) == 0
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.job_events WHERE job_id=$1 AND event_type='succeeded'",
            parent_id,
        )
        == 0
    )


async def test_reserved_key_invariant_failure_rolls_back_parent_and_capacity(
    active_wfc: None,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")
    workflow_id = await _workflow(producer, "wfc-i04-key-holder", ["parent", "child"], limit=2)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    claim = await _claim(runner, "parent", "worker-key", parent_id)
    await pg.execute(
        """
        INSERT INTO taskq.jobs(
            id,queue,job_type,status,priority,payload,idempotency_key,
            scheduled_at,lease_seconds,max_attempts,backoff_mode,
            backoff_base_seconds,backoff_cap_seconds
        ) VALUES (
            taskq.uuid7(),'child','preoccupied','queued',100,'{}'::jsonb,$1,
            now(),60,3,'exponential',1,60
        )
        """,
        f"chain:{parent_id}:branch",
    )

    with pytest.raises(asyncpg.PostgresError) as invariant:
        await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
            parent_id,
            claim["attempt_id"],
            "worker-key",
            json.dumps([_member_followup("branch")]),
            POLICY,
        )
    assert invariant.value.sqlstate == "TQ500"
    assert await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", parent_id) == "running"
    assert (
        await pg.fetchval(
            "SELECT admitted_total FROM taskq.workflow_member_counts WHERE workflow_id=$1",
            workflow_id,
        )
        == 1
    )
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", parent_id) == 0
    )


async def test_cancel_before_settlement_admits_then_sweeps_child(
    active_wfc: None,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")
    workflow_id = await _workflow(producer, "wfc-i04-cancel", ["parent", "child"], limit=2)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    claim = await _claim(runner, "parent", "worker-cancel", parent_id)
    requested = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','proof')", workflow_id
    )
    assert requested is not None and requested["outcome"] == "cancel_requested"

    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
        parent_id,
        claim["attempt_id"],
        "worker-cancel",
        json.dumps([_member_followup("branch")]),
        POLICY,
    )
    assert settled is not None and settled[0] == "ok"
    child_id = await pg.fetchval("SELECT id FROM taskq.jobs WHERE parent_job_id=$1", parent_id)
    assert child_id is not None
    assert await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", child_id) == "queued"
    assert await pg.fetchval("SELECT taskq.advance_workflow_cancellations(100)") == 1
    assert await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", child_id) == "cancelled"
    assert await pg.fetchval("SELECT taskq.finalize_workflows(100)") == 1
    assert (
        await pg.fetchval("SELECT status FROM taskq.workflows WHERE id=$1", workflow_id)
        == "cancelled"
    )


async def test_sealed_workflow_still_accepts_internal_member_continuation(
    active_wfc: None,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "parent", "child")
    workflow_id = await _workflow(producer, "wfc-i04-seal", ["parent", "child"], limit=2)
    parent_id = await _member(producer, workflow_id, "parent", "root")
    claim = await _claim(runner, "parent", "worker-seal", parent_id)
    sealed = await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'producer')", workflow_id
    )
    assert sealed is not None and sealed["outcome"] == "sealed"

    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,$4,$5)",
        parent_id,
        claim["attempt_id"],
        "worker-seal",
        json.dumps([_member_followup("branch")]),
        POLICY,
    )
    assert settled is not None and settled[0] == "ok"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1 AND workflow_id=$2",
            parent_id,
            workflow_id,
        )
        == 1
    )
    assert (
        await pg.fetchval("SELECT status FROM taskq.workflows WHERE id=$1", workflow_id)
        == "running"
    )
