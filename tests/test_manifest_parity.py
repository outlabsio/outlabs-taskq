"""R3-F04 manifest-complete public behavior, error, grant, and shadow vectors."""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg
import pytest

from taskq.sql import TASKQ_ROLES
from taskq.sql.manifest import FUNCTIONS, PUBLIC_ERRORS, PUBLIC_FUNCTIONS, REPLAY_RULES

pytestmark = pytest.mark.taskq_sql

ZERO_UUID = UUID(int=0)


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


# Every public function is named by at least one direct executable behavior
# group below. This closed ledger intentionally fails collection when the
# machine manifest grows without a corresponding vector update.
BEHAVIOR_GROUPS = {
    "admission": {
        "taskq.cancel_admission(text,text,uuid)",
        "taskq.finish_admission(text,text,uuid,jsonb,jsonb)",
        "taskq.reserve_admission(text,text,text,uuid,integer,integer)",
    },
    "bulk": {
        "taskq.enqueue_many(text,jsonb)",
        "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)",
    },
    "workflow": {
        "taskq.cancel_workflow(uuid,text,text)",
        "taskq.create_workflow(text,text,jsonb,text[],text)",
        "taskq.get_workflow_authorization_projection(uuid)",
        "taskq.seal_workflow(uuid,text)",
    },
    "schedule_operator": {
        "taskq.get_schedule(text)",
        "taskq.get_schedule_authorization_projection(text)",
        "taskq.put_schedule(text,jsonb,text,bigint)",
        "taskq.retire_schedule(text,bigint,text)",
    },
    "runner": {
        "taskq.cancel_running_job(uuid,uuid,text,text)",
        "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)",
        "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)",
        "taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)",
        "taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb)",
        "taskq.release_job(uuid,uuid,text,text,integer,jsonb)",
        "taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)",
        "taskq.worker_heartbeat(text,text[],text,integer,text,jsonb)",
    },
    "observer": {
        "taskq.get_authorization_projection(uuid)",
        "taskq.get_contract_meta()",
        "taskq.get_job(uuid,boolean,boolean,boolean,boolean)",
        "taskq.get_queue_profile(text)",
        "taskq.get_queue_stats(text)",
        "taskq.get_workflow_page(uuid,integer,uuid)",
        "taskq.list_jobs(text,text,integer,jsonb)",
        "taskq.metrics()",
    },
    "operator": {
        "taskq.cancel_job(uuid,text,text)",
        "taskq.ensure_queue(text,jsonb,text)",
        "taskq.expire_job(uuid,text)",
        "taskq.expire_worker_leases(text,text)",
        "taskq.pause_queue(text,text,text)",
        "taskq.purge_queued(text,integer,text,text)",
        "taskq.redrive_failed(text,integer,text)",
        "taskq.redrive_job(uuid,text,boolean)",
        "taskq.reprioritize(uuid,smallint,text)",
        "taskq.request_worker_shutdown(text,text,text)",
        "taskq.resume_queue(text,text)",
        "taskq.run_now(uuid,text)",
        "taskq.set_concurrency_limit(text,integer,text)",
        "taskq.update_queue_profile(text,jsonb,text,bigint)",
    },
    "housekeeping": {
        "taskq.claim_schedules(text,integer,integer)",
        "taskq.fire_schedule(uuid,uuid,bigint,timestamp with time zone[],timestamp with time zone)",
        "taskq.janitor()",
        "taskq.schedule_error(uuid,uuid,bigint,text,integer)",
        "taskq.tick(integer)",
    },
}


async def _make_queue(operator: asyncpg.Connection, name: str) -> asyncpg.Record:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'r3-f04')", name
    )
    assert row is not None
    return row


async def _enqueue(producer: asyncpg.Connection, queue: str, job_type: str = "r3.echo") -> UUID:
    row = await producer.fetchrow(
        'SELECT * FROM taskq.enqueue($1, $2, \'{"hello":"world"}\'::jsonb)',
        queue,
        job_type,
    )
    assert row is not None
    return row["job_id"]


async def _claim(
    runner: asyncpg.Connection, queue: str, worker: str = "r3-f04-worker"
) -> tuple[UUID, UUID]:
    row = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1, $2)", queue, worker)
    assert row is not None and row["state"] == "claimed"
    job = row["jobs"][0]
    return job["job_id"], job["attempt_id"]


def test_manifest_coverage_ledgers_are_closed() -> None:
    declared = set().union(*BEHAVIOR_GROUPS.values())
    assert declared == set(PUBLIC_FUNCTIONS)
    assert set(PUBLIC_ERRORS) == set(PUBLIC_FUNCTIONS)
    assert set(REPLAY_RULES) == set(PUBLIC_FUNCTIONS)
    assert set().union(*PUBLIC_ERRORS.values()) == {
        "TQ001",
        "TQ409",
        "TQ422",
        "TQ429",
        "TQ500",
        "TQ501",
    }


async def test_bulk_enqueue_is_ordered_atomic_and_idempotent(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _make_queue(operator, "r3_bulk")
    specs = [
        {"job_type": "r3.echo", "payload": {"i": 0}, "idempotency_key": "same"},
        {"job_type": "r3.echo", "payload": {"i": 1}, "idempotency_key": "same"},
        {"job_type": "r3.echo", "payload": {"i": 2}, "idempotency_key": "other"},
    ]
    rows = await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1, $2::jsonb)", "r3_bulk", json.dumps(specs)
    )
    assert [row["input_index"] for row in rows] == [1, 2, 3]
    assert [row["outcome"] for row in rows] == ["created", "existed", "created"]
    assert rows[0]["job_id"] == rows[1]["job_id"]
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 2

    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await producer.fetch(
            "SELECT * FROM taskq.enqueue_many($1, $2::jsonb)",
            "r3_bulk",
            json.dumps([{"job_type": "r3.echo"}, {"payload": {}}]),
        )
    assert excinfo.value.sqlstate == "TQ422"
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 2


async def test_runner_presence_heartbeat_snooze_and_cancel_replays(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    await _make_queue(operator, "r3_runner")
    await _enqueue(producer, "r3_runner")
    job_id, attempt_id = await _claim(runner, "r3_runner")
    heartbeat = await runner.fetchrow(
        "SELECT * FROM taskq.heartbeat($1, $2, $3, 30, '{\"cursor\":1}'::jsonb, "
        "'{\"cpu\":2}'::jsonb)",
        job_id,
        attempt_id,
        "r3-f04-worker",
    )
    assert heartbeat is not None and heartbeat["ok"] is True
    snoozed = await runner.fetchrow(
        "SELECT * FROM taskq.snooze_job($1, $2, $3, 0, 'later')",
        job_id,
        attempt_id,
        "r3-f04-worker",
    )
    assert snoozed is not None and snoozed["job_status"] == "queued"

    job_id, attempt_id = await _claim(runner, "r3_runner")
    cancelled = await runner.fetchrow(
        "SELECT * FROM taskq.cancel_running_job($1, $2, $3, 'stop')",
        job_id,
        attempt_id,
        "r3-f04-worker",
    )
    assert cancelled is not None and cancelled["result"] == "ok"
    replay = await runner.fetchrow(
        "SELECT * FROM taskq.cancel_running_job($1, $2, $3, 'stop')",
        job_id,
        attempt_id,
        "r3-f04-worker",
    )
    assert replay is not None and replay["result"] == "already_settled"

    presence = await runner.fetchrow(
        "SELECT * FROM taskq.worker_heartbeat($1, $2, 'host', 42, '0.1.2', '{}'::jsonb)",
        "presence-worker",
        ["r3_runner"],
    )
    assert presence is not None and presence["shutdown_requested"] is False
    assert (
        await operator.fetchval(
            "SELECT taskq.request_worker_shutdown($1, NULL, 'r3-f04')", "presence-worker"
        )
        == 1
    )
    presence = await runner.fetchrow(
        "SELECT * FROM taskq.worker_heartbeat($1, $2)",
        "presence-worker",
        ["r3_runner"],
    )
    assert presence is not None and presence["shutdown_requested"] is True


async def test_observer_projections_metrics_and_views(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    observer: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _make_queue(operator, "r3_observe")
    job_id = await _enqueue(producer, "r3_observe", "r3.visible")
    projection = await observer.fetchrow(
        "SELECT * FROM taskq.get_authorization_projection($1)", job_id
    )
    assert projection is not None and tuple(projection.keys()) == (
        "job_id",
        "queue",
        "job_type",
        "status",
    )
    redacted = await observer.fetchrow("SELECT * FROM taskq.get_job($1)", job_id)
    assert redacted is not None and redacted["payload"] is None
    revealed = await observer.fetchrow(
        "SELECT * FROM taskq.get_job($1, p_include_payload => true)", job_id
    )
    assert revealed is not None and _json(revealed["payload"]) == {"hello": "world"}
    meta = await observer.fetchrow("SELECT * FROM taskq.get_contract_meta()")
    assert meta is not None
    assert meta["contract_version"] == "0.2.3"
    assert _json(meta["capabilities"]) == {
        "active": [
            "admission_reservations",
            "dependencies_workflows",
            "followups",
            "read_model_list_finished",
            "read_model_list_ready",
            "read_model_list_running",
            "read_model_workflow",
            "schedules",
        ]
    }

    await runner.fetchrow(
        "SELECT * FROM taskq.worker_heartbeat('view-worker', ARRAY[$1])", "r3_observe"
    )
    tick = _json(await housekeeper.fetchval("SELECT taskq.tick()"))
    assert isinstance(tick, dict)
    assert "reaped" in tick
    stats = await observer.fetchrow("SELECT * FROM taskq.get_queue_stats($1)", "r3_observe")
    assert stats is not None and _json(stats["stats"])["ready"] == 1
    metric_names = {row["name"] for row in await observer.fetch("SELECT * FROM taskq.metrics()")}
    assert {"taskq_ready", "taskq_tick_age_seconds", "taskq_workers_online"} <= metric_names
    assert (
        await observer.fetchval("SELECT ready FROM taskq.queue_stats WHERE queue=$1", "r3_observe")
        == 1
    )
    assert await observer.fetchval("SELECT count(*) FROM taskq.dead_jobs") == 0
    assert (
        await observer.fetchval(
            "SELECT online FROM taskq.worker_status WHERE worker_id='view-worker'"
        )
        is True
    )


async def test_operator_queue_and_job_controls(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    created = await _make_queue(operator, "r3_control")
    assert created["result"] == "created"
    unchanged = await _make_queue(operator, "r3_control")
    assert unchanged["result"] == "unchanged"
    assert (
        await operator.fetchval("SELECT taskq.pause_queue($1, 'r3-f04')", "r3_control") == "paused"
    )
    assert (
        await operator.fetchval("SELECT taskq.resume_queue($1, 'r3-f04')", "r3_control")
        == "resumed"
    )
    assert (
        await operator.fetchval("SELECT taskq.set_concurrency_limit('r3.key', 2, 'r3-f04')")
        == "created"
    )

    scheduled = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'r3.echo', '{}'::jsonb, "
        "p_scheduled_at => now() + interval '1 hour')",
        "r3_control",
    )
    assert scheduled is not None
    assert (
        await operator.fetchval(
            "SELECT taskq.reprioritize($1, 7::smallint, 'r3-f04')",
            scheduled["job_id"],
        )
        == "ok"
    )
    assert (
        await operator.fetchval("SELECT taskq.run_now($1, 'r3-f04')", scheduled["job_id"]) == "ok"
    )
    await _enqueue(producer, "r3_control")
    assert (
        await operator.fetchval(
            "SELECT taskq.purge_queued($1, 1, 'r3-f04', 'bounded')", "r3_control"
        )
        == 1
    )
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 2


async def test_expire_worker_bulk_redrive_and_housekeeping(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _make_queue(operator, "r3_maintenance")
    await _enqueue(producer, "r3_maintenance")
    job_id, attempt_id = await _claim(runner, "r3_maintenance", "dead-worker")
    expired = _json(
        await operator.fetchval("SELECT taskq.expire_worker_leases('dead-worker', 'r3-f04')")
    )
    assert expired == {"matched": 1, "reaped": 1, "skipped": 0}
    assert await operator.fetchval("SELECT taskq.expire_job($1, 'r3-f04')", job_id) == "not_running"

    await _enqueue(producer, "r3_maintenance")
    job_id, attempt_id = await _claim(runner, "r3_maintenance", "fail-worker")
    failed = await runner.fetchrow(
        "SELECT * FROM taskq.fail_job($1, $2, 'fail-worker', 'terminal', false)",
        job_id,
        attempt_id,
    )
    assert failed is not None and failed["job_status"] == "failed"
    redriven = await operator.fetchrow(
        "SELECT * FROM taskq.redrive_failed($1, 10, 'r3-f04')", "r3_maintenance"
    )
    assert dict(redriven) == {"redriven": 1, "skipped": 0}
    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await operator.fetchval("SELECT taskq.redrive_job($1, 'r3-f04')", job_id)
    assert excinfo.value.sqlstate == "TQ409"

    await pg.execute(
        "UPDATE taskq.control_state SET data=jsonb_build_object('next_due', now()-interval '1 second') "
        "WHERE key='janitor_daily'"
    )
    janitor = _json(await operator.fetchval("SELECT taskq.janitor()"))
    assert isinstance(janitor, dict)
    assert {"terminal_deleted", "failed_deleted", "events_pruned", "workers_pruned"} <= set(janitor)
    tick = _json(await housekeeper.fetchval("SELECT taskq.tick(10)"))
    assert isinstance(tick, dict)
    assert "janitor" not in tick


async def test_every_function_has_exact_grants_and_no_public_execute(
    pg: asyncpg.Connection,
) -> None:
    for identity, spec in FUNCTIONS.items():
        for role in TASKQ_ROLES[1:]:
            actual = await pg.fetchval(
                "SELECT has_function_privilege($1, $2, 'EXECUTE')", role, identity
            )
            assert actual is (role in spec.grants), (identity, role)
        assert (
            await pg.fetchval("SELECT has_function_privilege('public', $1, 'EXECUTE')", identity)
            is False
        )


async def test_shadow_objects_cannot_redirect_definer_resolution(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    await _make_queue(operator, "r3_shadow")
    await producer.execute("CREATE TEMP TABLE jobs (id text)")
    await producer.execute("SET search_path = pg_temp, public")
    job_id = await _enqueue(producer, "r3_shadow")
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE id=$1", job_id) == 1
    assert await producer.fetchval("SELECT count(*) FROM jobs") == 0


async def test_registered_error_representatives(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.PostgresError) as unknown:
        await operator.fetchval("SELECT taskq.pause_queue('missing', 'r3-f04')")
    assert unknown.value.sqlstate == "TQ001"

    await _make_queue(operator, "r3_errors")
    with pytest.raises(asyncpg.PostgresError) as invalid:
        await runner.fetchrow("SELECT * FROM taskq.worker_heartbeat('', ARRAY[]::text[])")
    assert invalid.value.sqlstate == "TQ422"

    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{\"max_depth\":1}'::jsonb, 'r3-f04')",
        "r3_depth",
    )
    await _enqueue(producer, "r3_depth")
    with pytest.raises(asyncpg.PostgresError) as depth:
        await _enqueue(producer, "r3_depth")
    assert depth.value.sqlstate == "TQ429"

    job_id = await _enqueue(producer, "r3_errors")
    cancelled = await operator.fetchrow("SELECT * FROM taskq.cancel_job($1, 'r3-f04')", job_id)
    assert cancelled is not None and cancelled["result"] == "cancelled"
    with pytest.raises(asyncpg.PostgresError) as conflict:
        await operator.fetchval("SELECT taskq.run_now($1, 'r3-f04')", job_id)
    assert conflict.value.sqlstate == "TQ409"

    with pytest.raises(asyncpg.PostgresError) as capability:
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1, 'r3.echo', '{}'::jsonb, p_depends_on => ARRAY[$2]::uuid[])",
            "r3_errors",
            ZERO_UUID,
        )
    assert capability.value.sqlstate == "TQ422"
