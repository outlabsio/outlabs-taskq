"""SQL contract 0.2.1 — sealed workflows and native dependencies."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import _migrate_impl, discover_migrations, verify
from conftest import RoleConnect

pytestmark = pytest.mark.taskq_sql


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'workflow-test')",
        name,
    )
    assert row is not None


async def _workflow(
    producer: asyncpg.Connection,
    key: str,
    queues: list[str],
) -> asyncpg.Record:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,$2,'planner')",
        key,
        queues,
    )
    assert row is not None
    return row


async def _member(
    producer: asyncpg.Connection,
    queue: str,
    workflow_id: UUID,
    step: str,
    *,
    depends_on: list[UUID] | None = None,
    payload: dict[str, object] | None = None,
) -> asyncpg.Record:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue("
        "$1,'test.workflow',CAST($2 AS jsonb),"
        "p_depends_on=>$3,p_workflow_id=>$4,p_step_key=>$5)",
        queue,
        json.dumps(payload or {}),
        depends_on,
        workflow_id,
        step,
    )
    assert row is not None
    return row


async def _claim(runner: asyncpg.Connection, queue: str, worker: str) -> asyncpg.Record:
    batch = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1,$2)", queue, worker)
    assert batch is not None and batch["state"] == "claimed"
    return batch["jobs"][0]


async def _assert_waiting_on_lock(
    observer: asyncpg.Connection,
    connection: asyncpg.Connection,
    *,
    operation: str,
) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        waiting = await observer.fetchrow(
            "SELECT wait_event_type FROM pg_catalog.pg_stat_activity WHERE pid=$1",
            connection.get_server_pid(),
        )
        if waiting is not None and waiting["wait_event_type"] == "Lock":
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{operation} never blocked on the expected tuple lock")


async def test_create_replay_mismatch_projection_and_empty_seal(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    observer: asyncpg.Connection,
) -> None:
    del pg
    await _queue(operator, "workflow_create_a")
    await _queue(operator, "workflow_create_b")
    first = await _workflow(producer, "create-replay", ["workflow_create_b", "workflow_create_a"])
    replay = await _workflow(producer, "create-replay", ["workflow_create_a", "workflow_create_b"])
    assert first["outcome"] == "created"
    assert replay["outcome"] == "existed"
    assert replay["workflow_id"] == first["workflow_id"]

    projection = await observer.fetchrow(
        "SELECT * FROM taskq.get_workflow_authorization_projection($1)",
        first["workflow_id"],
    )
    assert projection is not None
    assert projection["declared_queues"] == [
        "workflow_create_a",
        "workflow_create_b",
    ]

    with pytest.raises(asyncpg.PostgresError) as mismatch:
        await producer.fetchrow(
            "SELECT * FROM taskq.create_workflow($1,'batch','{}'::jsonb,$2,'other-planner')",
            "create-replay",
            ["workflow_create_a", "workflow_create_b"],
        )
    assert mismatch.value.sqlstate == "TQ409"
    assert mismatch.value.detail == '{"reason":"workflow_mismatch"}'

    sealed = await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        first["workflow_id"],
    )
    assert sealed is not None
    assert (sealed["outcome"], sealed["status"]) == ("sealed", "succeeded")
    sealed_replay = await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        first["workflow_id"],
    )
    assert sealed_replay is not None and sealed_replay["outcome"] == "already_sealed"


async def test_dependency_promotion_and_workflow_finalization(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_success")
    workflow = await _workflow(producer, "success", ["workflow_success"])
    root = await _member(producer, "workflow_success", workflow["workflow_id"], "root")
    child = await _member(
        producer,
        "workflow_success",
        workflow["workflow_id"],
        "child",
        depends_on=[root["job_id"]],
    )
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        workflow["workflow_id"],
    )
    before = await pg.fetchrow(
        "SELECT status,pending_deps FROM taskq.jobs WHERE id=$1", child["job_id"]
    )
    assert before is not None and tuple(before.values()) == ("blocked", 1)

    claimed_root = await _claim(runner, "workflow_success", "workflow-success")
    completed = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'workflow-success')",
        claimed_root["job_id"],
        claimed_root["attempt_id"],
    )
    assert completed is not None and completed["result"] == "ok"
    promoted = await pg.fetchrow(
        "SELECT status,pending_deps FROM taskq.jobs WHERE id=$1", child["job_id"]
    )
    assert promoted is not None and tuple(promoted.values()) == ("queued", 0)

    claimed_child = await _claim(runner, "workflow_success", "workflow-success")
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'workflow-success')",
        claimed_child["job_id"],
        claimed_child["attempt_id"],
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    assert (
        await pg.fetchval(
            "SELECT status FROM taskq.workflows WHERE id=$1",
            workflow["workflow_id"],
        )
        == "succeeded"
    )


async def test_diamond_graph_promotes_siblings_and_fan_in_exactly_once(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_diamond")
    workflow = await _workflow(producer, "diamond", ["workflow_diamond"])
    root = await _member(producer, "workflow_diamond", workflow["workflow_id"], "root")
    left = await _member(
        producer,
        "workflow_diamond",
        workflow["workflow_id"],
        "left",
        depends_on=[root["job_id"]],
    )
    right = await _member(
        producer,
        "workflow_diamond",
        workflow["workflow_id"],
        "right",
        depends_on=[root["job_id"]],
    )
    joined = await _member(
        producer,
        "workflow_diamond",
        workflow["workflow_id"],
        "joined",
        depends_on=[left["job_id"], right["job_id"]],
    )
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        workflow["workflow_id"],
    )

    claimed_root = await _claim(runner, "workflow_diamond", "diamond")
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'diamond')",
        claimed_root["job_id"],
        claimed_root["attempt_id"],
    )
    siblings = await pg.fetch(
        "SELECT step_key,status,pending_deps FROM taskq.jobs "
        "WHERE id=ANY($1::uuid[]) ORDER BY step_key",
        [left["job_id"], right["job_id"]],
    )
    assert [tuple(row.values()) for row in siblings] == [
        ("left", "queued", 0),
        ("right", "queued", 0),
    ]

    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,2)",
        "workflow_diamond",
        "diamond",
    )
    assert batch is not None and batch["state"] == "claimed"
    claimed_siblings = {job["job_id"]: job for job in batch["jobs"]}
    assert set(claimed_siblings) == {left["job_id"], right["job_id"]}
    first, second = sorted(claimed_siblings)
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'diamond')",
        first,
        claimed_siblings[first]["attempt_id"],
    )
    halfway = await pg.fetchrow(
        "SELECT status,pending_deps FROM taskq.jobs WHERE id=$1",
        joined["job_id"],
    )
    assert halfway is not None and tuple(halfway.values()) == ("blocked", 1)
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'diamond')",
        second,
        claimed_siblings[second]["attempt_id"],
    )
    promoted = await pg.fetchrow(
        "SELECT status,pending_deps FROM taskq.jobs WHERE id=$1",
        joined["job_id"],
    )
    assert promoted is not None and tuple(promoted.values()) == ("queued", 0)
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.job_deps WHERE job_id=$1",
            joined["job_id"],
        )
        == 0
    )

    claimed_join = await _claim(runner, "workflow_diamond", "diamond")
    assert claimed_join["job_id"] == joined["job_id"]
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'diamond')",
        claimed_join["job_id"],
        claimed_join["attempt_id"],
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    assert (
        await pg.fetchval(
            "SELECT status FROM taskq.workflows WHERE id=$1",
            workflow["workflow_id"],
        )
        == "succeeded"
    )


async def test_failure_cascade_converges_without_claiming_descendants(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_failure")
    workflow = await _workflow(producer, "failure", ["workflow_failure"])
    root = await _member(producer, "workflow_failure", workflow["workflow_id"], "root")
    child = await _member(
        producer,
        "workflow_failure",
        workflow["workflow_id"],
        "child",
        depends_on=[root["job_id"]],
    )
    grandchild = await _member(
        producer,
        "workflow_failure",
        workflow["workflow_id"],
        "grandchild",
        depends_on=[child["job_id"]],
    )
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        workflow["workflow_id"],
    )
    claimed = await _claim(runner, "workflow_failure", "workflow-failure")
    failed = await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1,$2,'workflow-failure','boom',false)",
        claimed["job_id"],
        claimed["attempt_id"],
    )
    assert failed is not None and failed["result"] == "dead"
    assert (
        await pg.fetchval("SELECT outcome FROM taskq.jobs WHERE id=$1", child["job_id"])
        == "dep_failed"
    )
    assert (
        await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", grandchild["job_id"])
        == "blocked"
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    rows = await pg.fetch(
        "SELECT step_key,status,outcome FROM taskq.jobs WHERE workflow_id=$1 ORDER BY step_key",
        workflow["workflow_id"],
    )
    assert [tuple(row.values()) for row in rows] == [
        ("child", "cancelled", "dep_failed"),
        ("grandchild", "cancelled", "dep_failed"),
        ("root", "failed", "non_retryable"),
    ]
    assert (
        await pg.fetchval(
            "SELECT status FROM taskq.workflows WHERE id=$1",
            workflow["workflow_id"],
        )
        == "failed"
    )


async def test_cancel_requests_running_and_terminalizes_waiting_members(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_cancel")
    workflow = await _workflow(producer, "cancel", ["workflow_cancel"])
    running = await _member(producer, "workflow_cancel", workflow["workflow_id"], "running")
    waiting = await _member(producer, "workflow_cancel", workflow["workflow_id"], "waiting")
    claimed = await _claim(runner, "workflow_cancel", "workflow-cancel")
    assert claimed["job_id"] == running["job_id"]
    cancelled = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','stop')",
        workflow["workflow_id"],
    )
    assert cancelled is not None and cancelled["outcome"] == "cancel_requested"
    replay = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','stop')",
        workflow["workflow_id"],
    )
    assert replay is not None and replay["outcome"] == "already_requested"
    assert (
        await pg.fetchval(
            "SELECT cancel_requested_at IS NOT NULL FROM taskq.jobs WHERE id=$1",
            running["job_id"],
        )
        is True
    )
    assert (
        await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", waiting["job_id"])
        == "cancelled"
    )
    await runner.fetchrow(
        "SELECT * FROM taskq.cancel_running_job($1,$2,'workflow-cancel','stop')",
        running["job_id"],
        claimed["attempt_id"],
    )
    await housekeeper.fetchval("SELECT taskq.tick()")
    assert (
        await pg.fetchval(
            "SELECT status FROM taskq.workflows WHERE id=$1",
            workflow["workflow_id"],
        )
        == "cancelled"
    )
    terminal_replay = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','stop')",
        workflow["workflow_id"],
    )
    assert terminal_replay is not None
    assert terminal_replay["outcome"] == "already_terminal"
    with pytest.raises(asyncpg.PostgresError) as redrive:
        await operator.fetchval("SELECT taskq.redrive_job($1,'operator',false)", running["job_id"])
    assert redrive.value.sqlstate == "TQ409"
    assert redrive.value.detail == '{"reason":"workflow_member_redrive_forbidden"}'


async def test_cancel_intent_blocks_claim_when_bounded_pass_skips_locked_member(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_cancel_claim")
    workflow = await _workflow(producer, "cancel-claim", ["workflow_cancel_claim"])
    member = await _member(
        producer,
        "workflow_cancel_claim",
        workflow["workflow_id"],
        "locked",
    )

    transaction = pg.transaction()
    await transaction.start()
    await pg.fetchval("SELECT id FROM taskq.jobs WHERE id=$1 FOR UPDATE", member["job_id"])
    cancelled = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','stop')",
        workflow["workflow_id"],
    )
    assert cancelled is not None and cancelled["outcome"] == "cancel_requested"
    assert (
        await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", member["job_id"]) == "queued"
    )
    await transaction.commit()

    batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,p_job_id=>$3)",
        "workflow_cancel_claim",
        "workflow-cancel-claim",
        member["job_id"],
    )
    assert batch is not None
    assert batch["state"] == "unavailable"
    assert batch["jobs"] == []


async def test_workflow_cancel_intent_wins_during_concurrent_settlement(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    role_conn: RoleConnect,
    housekeeper: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_cancel_settle")
    producer = await role_conn("taskq_producer")
    runner = await role_conn("taskq_runner")
    workflow = await _workflow(
        producer,
        "cancel-settle",
        ["workflow_cancel_settle"],
    )
    member = await _member(
        producer,
        "workflow_cancel_settle",
        workflow["workflow_id"],
        "running",
    )
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        workflow["workflow_id"],
    )
    claimed = await _claim(runner, "workflow_cancel_settle", "cancel-settle")
    assert claimed["job_id"] == member["job_id"]

    transaction = runner.transaction()
    await transaction.start()
    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'cancel-settle')",
        member["job_id"],
        claimed["attempt_id"],
    )
    assert settled is not None and settled["result"] == "ok"
    cancelled = await operator.fetchrow(
        "SELECT * FROM taskq.cancel_workflow($1,'operator','stop')",
        workflow["workflow_id"],
    )
    assert cancelled is not None and cancelled["outcome"] == "cancel_requested"
    await transaction.commit()
    await housekeeper.fetchval("SELECT taskq.tick()")

    assert (
        await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", member["job_id"])
        == "succeeded"
    )
    assert (
        await pg.fetchval(
            "SELECT status FROM taskq.workflows WHERE id=$1",
            workflow["workflow_id"],
        )
        == "cancelled"
    )
    await producer.close()
    await runner.close()


async def test_bounded_finalizer_rotates_active_workflows_without_starvation(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_finalizer")
    for index in range(101):
        active = await _workflow(
            producer,
            f"finalizer-active-{index:03d}",
            ["workflow_finalizer"],
        )
        await _member(
            producer,
            "workflow_finalizer",
            active["workflow_id"],
            "waiting",
        )
        await producer.fetchrow(
            "SELECT * FROM taskq.seal_workflow($1,'planner')",
            active["workflow_id"],
        )

    candidate = await _workflow(
        producer,
        "finalizer-candidate",
        ["workflow_finalizer"],
    )
    member = await _member(
        producer,
        "workflow_finalizer",
        candidate["workflow_id"],
        "terminal",
        payload={"candidate": True},
    )
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        candidate["workflow_id"],
    )
    claimed = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,p_job_id=>$3)",
        "workflow_finalizer",
        "finalizer",
        member["job_id"],
    )
    assert claimed is not None and claimed["state"] == "claimed"
    job = claimed["jobs"][0]
    await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,'finalizer')",
        job["job_id"],
        job["attempt_id"],
    )

    assert await pg.fetchval("SELECT taskq.finalize_workflows(100)") == 0
    assert await pg.fetchval("SELECT taskq.finalize_workflows(100)") == 1
    assert (
        await pg.fetchval(
            "SELECT status FROM taskq.workflows WHERE id=$1",
            candidate["workflow_id"],
        )
        == "succeeded"
    )


async def test_exact_step_replay_survives_seal_but_changed_intent_conflicts(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    del pg
    await _queue(operator, "workflow_replay")
    workflow = await _workflow(producer, "step-replay", ["workflow_replay"])
    first = await _member(
        producer,
        "workflow_replay",
        workflow["workflow_id"],
        "step",
        payload={"value": 1},
    )
    await producer.fetchrow(
        "SELECT * FROM taskq.seal_workflow($1,'planner')",
        workflow["workflow_id"],
    )
    replay = await _member(
        producer,
        "workflow_replay",
        workflow["workflow_id"],
        "step",
        payload={"value": 1},
    )
    assert replay["created"] is False and replay["job_id"] == first["job_id"]
    with pytest.raises(asyncpg.PostgresError) as mismatch:
        await _member(
            producer,
            "workflow_replay",
            workflow["workflow_id"],
            "step",
            payload={"value": 2},
        )
    assert mismatch.value.sqlstate == "TQ409"
    assert mismatch.value.detail == '{"reason":"workflow_step_mismatch"}'
    with pytest.raises(asyncpg.PostgresError) as sealed:
        await _member(
            producer,
            "workflow_replay",
            workflow["workflow_id"],
            "new-step",
        )
    assert sealed.value.sqlstate == "TQ409"
    assert sealed.value.detail == '{"reason":"workflow_sealed"}'


async def test_queue_idempotency_collision_cannot_impersonate_workflow_member(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _queue(operator, "workflow_idem_collision")
    ordinary = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue("
        "$1,'test.ordinary','{}'::jsonb,p_idempotency_key=>'shared-key')",
        "workflow_idem_collision",
    )
    assert ordinary is not None and ordinary["created"] is True
    workflow = await _workflow(
        producer,
        "idem-collision",
        ["workflow_idem_collision"],
    )

    with pytest.raises(asyncpg.PostgresError) as conflict:
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue("
            "$1,'test.workflow','{}'::jsonb,"
            "p_idempotency_key=>'shared-key',p_workflow_id=>$2,p_step_key=>'step')",
            "workflow_idem_collision",
            workflow["workflow_id"],
        )
    assert conflict.value.sqlstate == "TQ409"
    assert conflict.value.detail == '{"reason":"workflow_step_mismatch"}'
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1",
            workflow["workflow_id"],
        )
        == 0
    )
    assert (
        await pg.fetchval("SELECT workflow_id FROM taskq.jobs WHERE id=$1", ordinary["job_id"])
        is None
    )


async def test_enqueue_and_seal_serialize_on_the_workflow_row(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    role_conn: RoleConnect,
) -> None:
    await _queue(operator, "workflow_seal_race")
    first = await role_conn("taskq_producer")
    second = await role_conn("taskq_producer")
    workflow = await _workflow(first, "seal-race", ["workflow_seal_race"])

    transaction = first.transaction()
    await transaction.start()
    member = await _member(first, "workflow_seal_race", workflow["workflow_id"], "winner")
    seal_task = asyncio.create_task(
        second.fetchrow(
            "SELECT * FROM taskq.seal_workflow($1,'planner')",
            workflow["workflow_id"],
        )
    )
    await _assert_waiting_on_lock(pg, second, operation="seal")
    await transaction.commit()
    sealed = await seal_task
    assert sealed is not None and sealed["outcome"] == "sealed"
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE id=$1", member["job_id"]) == 1

    with pytest.raises(asyncpg.PostgresError) as rejected:
        await _member(first, "workflow_seal_race", workflow["workflow_id"], "loser")
    assert rejected.value.sqlstate == "TQ409"
    await first.close()
    await second.close()


async def test_parent_terminalization_wins_over_concurrent_child_admission(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    role_conn: RoleConnect,
) -> None:
    await _queue(operator, "workflow_parent_race")
    runner = await role_conn("taskq_runner")
    producer = await role_conn("taskq_producer")
    workflow = await _workflow(producer, "parent-race", ["workflow_parent_race"])
    root = await _member(
        producer,
        "workflow_parent_race",
        workflow["workflow_id"],
        "root",
    )
    claimed = await _claim(runner, "workflow_parent_race", "parent-race")
    assert claimed["job_id"] == root["job_id"]

    transaction = runner.transaction()
    await transaction.start()
    failed = await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1,$2,'parent-race','terminal',false)",
        root["job_id"],
        claimed["attempt_id"],
    )
    assert failed is not None and failed["result"] == "dead"
    child_task = asyncio.create_task(
        _member(
            producer,
            "workflow_parent_race",
            workflow["workflow_id"],
            "child",
            depends_on=[root["job_id"]],
        )
    )
    await _assert_waiting_on_lock(pg, producer, operation="child admission")
    await transaction.commit()

    with pytest.raises(asyncpg.PostgresError) as rejected:
        await child_task
    assert rejected.value.sqlstate == "TQ409"
    assert rejected.value.detail == '{"reason":"dependency_terminal"}'
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1 AND step_key='child'",
            workflow["workflow_id"],
        )
        == 0
    )
    await runner.close()
    await producer.close()


async def test_0008_to_0009_transition_is_atomic_and_capability_gated(
    taskq_dsn: str,
) -> None:
    database = f"taskq_workflow_transition_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:8])
            )
            assert applied[-1] == "0008_followups"
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.ensure_queue('workflow_transition','{}'::jsonb,'test')"
            )
            with pytest.raises(DBAPIError) as inactive:
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.enqueue("
                    "'workflow_transition','test.workflow','{}'::jsonb,"
                    "p_workflow_id=>'00000000-0000-0000-0000-000000000001'::uuid,"
                    "p_step_key=>'step')"
                )
            assert getattr(inactive.value, "orig", inactive.value).sqlstate == "TQ501"
            await conn.rollback()

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[8:9])
            )
            assert applied == ["0009_workflows"]
            meta = (await conn.exec_driver_sql("SELECT * FROM taskq.get_contract_meta()")).one()
            assert meta.contract_version == "0.2.1"
            assert meta.capabilities == {
                "active": [
                    "admission_reservations",
                    "dependencies_workflows",
                    "followups",
                    "read_model_list_ready",
                ]
            }
            report = await verify(conn)
            assert report.ok
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()


async def test_0009_refuses_nonempty_dormant_workflow_state_atomically(
    taskq_dsn: str,
) -> None:
    database = f"taskq_workflow_precondition_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:8])
            )
            assert applied[-1] == "0008_followups"
            await conn.exec_driver_sql(
                """
                INSERT INTO taskq.workflows (
                    workflow_key, kind, status, params, stats, created_by
                ) VALUES (
                    'dormant-state', 'dag', 'running', '{}'::jsonb,
                    '{}'::jsonb, 'precondition-test'
                )
                """
            )
            await conn.commit()

            with pytest.raises(DBAPIError, match="empty inactive workflow"):
                await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, migrations[8:9]))
            await conn.rollback()
            assert (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM taskq.schema_migrations WHERE id='0009_workflows'"
                )
            ).scalar_one() == 0
            assert (
                await conn.exec_driver_sql(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema='taskq'
                      AND table_name='workflows'
                      AND column_name='declared_queues'
                    """
                )
            ).scalar_one() == 0
            meta = (await conn.exec_driver_sql("SELECT * FROM taskq.get_contract_meta()")).one()
            assert meta.contract_version == "0.2.0"
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()
