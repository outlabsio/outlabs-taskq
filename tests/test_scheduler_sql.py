"""SQL 0.3 scheduler safety, ownership, and durable-decision evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from taskq.errors import TaskqConfigError, TaskqValidationError
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql


def _definition(
    queue: str,
    *,
    interval_seconds: int = 60,
    catchup_policy: str = "fire_once",
    max_catchup: int = 1,
) -> dict[str, Any]:
    return {
        "target": {
            "kind": "job",
            "queue": queue,
            "job_type": "scheduler.evidence",
            "payload": {"evidence": True},
        },
        "recurrence": {"kind": "interval", "interval_seconds": interval_seconds},
        "catchup_policy": catchup_policy,
        "max_catchup": max_catchup,
        "paused": False,
    }


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'scheduler-v2')", name)


async def _put_managed(
    operator: asyncpg.Connection,
    *,
    name: str,
    queue: str,
    key: str,
    source: str = "test-source",
    overlap: str = "forbid",
    max_lateness_seconds: int | None = None,
    catchup_policy: str = "fire_once",
    max_catchup: int = 1,
) -> asyncpg.Record:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.put_managed_schedule("
        "$1,$2::jsonb,'testns',$3,$4,$4,$5,$6,$7,'scheduler-v2',NULL)",
        name,
        json.dumps(
            _definition(
                queue,
                catchup_policy=catchup_policy,
                max_catchup=max_catchup,
            )
        ),
        source,
        key,
        "a" * 64,
        overlap,
        max_lateness_seconds,
    )
    assert row is not None
    return row


async def _claim_named(housekeeper: asyncpg.Connection, name: str) -> asyncpg.Record:
    batch = await housekeeper.fetchrow(
        "SELECT * FROM taskq.claim_schedules('scheduler-v2-test',100,60)"
    )
    assert batch is not None
    for claim in batch["schedules"]:
        if claim["name"] == name:
            return claim
    raise AssertionError(f"schedule not claimed: {name}")


async def _initialize(housekeeper: asyncpg.Connection, claim: asyncpg.Record) -> asyncpg.Record:
    row = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        claim["schedule_id"],
        claim["token"],
        claim["definition_version"],
        [],
        claim["as_of"] + timedelta(seconds=60),
    )
    assert row is not None and row["outcome"] == "initialized"
    return row


async def test_wrong_target_and_forged_guc_fail_before_protected_sql(
    taskq_dsn: str,
    operator: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _queue(operator, "scheduler_target")
    raw = await asyncpg.connect(taskq_dsn)
    try:
        await raw.execute("SET ROLE taskq_runner")
        async with raw.transaction():
            await raw.execute("SELECT set_config('taskq.target_attestation',$1,true)", "f" * 64)
            with pytest.raises(asyncpg.PostgresError) as forged:
                await raw.fetchrow("SELECT * FROM taskq.claim_jobs('scheduler_target','forged')")
            assert forged.value.sqlstate == "TQ422"
            assert forged.value.detail == '{"reason":"target_attestation_required"}'
    finally:
        await raw.close()

    monkeypatch.delenv("TASKQ_EXPECTED_ENV", raising=False)
    missing = SqlTaskqTransport.from_dsn(taskq_dsn)
    wrong = SqlTaskqTransport.from_dsn(taskq_dsn, expected_environment="staging")
    try:
        with pytest.raises(TaskqConfigError):
            await missing.claim("scheduler_target", "missing-static-config")
        with pytest.raises(TaskqValidationError):
            await wrong.claim("scheduler_target", "wrong-target")
    finally:
        await missing.aclose()
        await wrong.aclose()


async def test_manifest_ownership_is_idempotent_and_cross_source_fail_closed(
    operator: asyncpg.Connection,
) -> None:
    await _queue(operator, "scheduler_owner")
    created = await _put_managed(
        operator,
        name="testns.owned",
        queue="scheduler_owner",
        key="owned",
    )
    assert created["outcome"] == "created"
    unchanged = await _put_managed(
        operator,
        name="testns.owned",
        queue="scheduler_owner",
        key="owned",
    )
    assert unchanged["outcome"] == "unchanged"
    with pytest.raises(asyncpg.PostgresError) as mismatch:
        await _put_managed(
            operator,
            name="testns.owned",
            queue="scheduler_owner",
            key="owned",
            source="foreign-source",
        )
    assert mismatch.value.sqlstate == "TQ409"
    assert mismatch.value.detail == '{"reason":"schedule_owner_mismatch"}'


async def test_overlap_lateness_headers_and_auto_pause_are_durable(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await pg.execute("UPDATE taskq.schedules SET state='paused' WHERE name='taskq-janitor-daily'")
    await _queue(operator, "scheduler_decisions")

    overlap = await _put_managed(
        operator,
        name="testns.overlap",
        queue="scheduler_decisions",
        key="overlap",
    )
    overlap_id = overlap["profile"]["schedule_id"]
    await _initialize(housekeeper, await _claim_named(housekeeper, "testns.overlap"))
    due = datetime.now(UTC) - timedelta(minutes=2)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        overlap_id,
        due,
    )
    first = await _claim_named(housekeeper, "testns.overlap")
    fired = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        overlap_id,
        first["token"],
        first["definition_version"],
        [due],
        first["as_of"] + timedelta(minutes=1),
    )
    assert fired is not None and fired["jobs_enqueued"] == 1
    second_due = due + timedelta(minutes=1)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        overlap_id,
        second_due,
    )
    second = await _claim_named(housekeeper, "testns.overlap")
    skipped = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        overlap_id,
        second["token"],
        second["definition_version"],
        [second_due],
        second["as_of"] + timedelta(minutes=1),
    )
    assert skipped is not None and skipped["jobs_enqueued"] == 0
    occurrence = await pg.fetchrow(
        "SELECT outcome,job_id FROM taskq.schedule_occurrences WHERE schedule_id=$1 AND due_at=$2",
        overlap_id,
        second_due,
    )
    assert occurrence is not None and tuple(occurrence) == ("overlap_skipped", None)
    header = await pg.fetchval(
        "SELECT j.headers->'taskq_schedule' FROM taskq.schedule_occurrences o "
        "JOIN taskq.jobs j ON j.id=o.job_id WHERE o.schedule_id=$1 AND o.job_id IS NOT NULL",
        overlap_id,
    )
    decoded_header = json.loads(header) if isinstance(header, str) else header
    assert decoded_header is not None
    assert decoded_header["schedule_key"] == "testns.overlap"

    late = await _put_managed(
        operator,
        name="testns.late",
        queue="scheduler_decisions",
        key="late",
        max_lateness_seconds=0,
    )
    late_id = late["profile"]["schedule_id"]
    await _initialize(housekeeper, await _claim_named(housekeeper, "testns.late"))
    late_due = datetime.now(UTC) - timedelta(minutes=1)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        late_id,
        late_due,
    )
    late_claim = await _claim_named(housekeeper, "testns.late")
    await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        late_id,
        late_claim["token"],
        late_claim["definition_version"],
        [late_due],
        late_claim["as_of"] + timedelta(minutes=1),
    )
    assert (
        await pg.fetchval(
            "SELECT outcome FROM taskq.schedule_occurrences WHERE schedule_id=$1 AND due_at=$2",
            late_id,
            late_due,
        )
        == "late_skipped"
    )

    broken = await _put_managed(
        operator,
        name="testns.broken",
        queue="scheduler_decisions",
        key="broken",
    )
    broken_id = broken["profile"]["schedule_id"]
    outcomes: list[str] = []
    for attempt in range(3):
        claim = await _claim_named(housekeeper, "testns.broken")
        result = await housekeeper.fetchrow(
            "SELECT * FROM taskq.schedule_error($1,$2,$3,$4,1,true)",
            broken_id,
            claim["token"],
            claim["definition_version"],
            f"calendar:invalid-{attempt}",
        )
        assert result is not None
        outcomes.append(result["outcome"])
        if attempt < 2:
            await pg.execute(
                "UPDATE taskq.schedules SET retry_not_before=NULL,next_fire_at=now() WHERE id=$1",
                broken_id,
            )
    assert outcomes == ["error_recorded", "error_recorded", "auto_paused"]
    assert await pg.fetchval("SELECT state FROM taskq.schedules WHERE id=$1", broken_id) == "paused"
    assert (
        await pg.fetchval(
            "SELECT action FROM taskq.schedule_decisions WHERE schedule_id=$1 "
            "ORDER BY created_at DESC LIMIT 1",
            broken_id,
        )
        == "auto_paused"
    )


async def test_fire_all_forbid_fires_first_and_skips_later_batch_occurrences(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await pg.execute("UPDATE taskq.schedules SET state='paused' WHERE name='taskq-janitor-daily'")
    await _queue(operator, "scheduler_fire_all_forbid")
    created = await _put_managed(
        operator,
        name="testns.fire-all-forbid",
        queue="scheduler_fire_all_forbid",
        key="fire-all-forbid",
        catchup_policy="fire_all",
        max_catchup=3,
    )
    schedule_id = created["profile"]["schedule_id"]
    await _initialize(housekeeper, await _claim_named(housekeeper, "testns.fire-all-forbid"))
    first_due = datetime.now(UTC) - timedelta(minutes=3)
    second_due = first_due + timedelta(minutes=1)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        schedule_id,
        first_due,
    )
    claim = await _claim_named(housekeeper, "testns.fire-all-forbid")
    result = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        claim["token"],
        claim["definition_version"],
        [first_due, second_due],
        claim["as_of"] + timedelta(minutes=1),
    )
    assert result is not None and result["jobs_enqueued"] == 1
    rows = await pg.fetch(
        "SELECT due_at,outcome,job_id FROM taskq.schedule_occurrences "
        "WHERE schedule_id=$1 ORDER BY due_at",
        schedule_id,
    )
    assert [(row["due_at"], row["outcome"], row["job_id"] is not None) for row in rows] == [
        (first_due, "fired", True),
        (second_due, "overlap_skipped", False),
    ]


async def test_backend_kill_before_fire_commit_rolls_back_and_reclaims_once(
    taskq_dsn: str,
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await pg.execute("UPDATE taskq.schedules SET state='paused' WHERE name='taskq-janitor-daily'")
    await _queue(operator, "scheduler_kill")
    created = await _put_managed(
        operator,
        name="testns.kill",
        queue="scheduler_kill",
        key="kill",
        overlap="allow",
    )
    schedule_id = created["profile"]["schedule_id"]
    await _initialize(housekeeper, await _claim_named(housekeeper, "testns.kill"))
    due = datetime.now(UTC) - timedelta(minutes=1)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        schedule_id,
        due,
    )
    claim = await _claim_named(housekeeper, "testns.kill")

    doomed = await asyncpg.connect(taskq_dsn)
    backend_pid = doomed.get_server_pid()
    transaction = doomed.transaction()
    await doomed.execute("SET ROLE taskq_housekeeper")
    await transaction.start()
    await doomed.fetchrow("SELECT * FROM taskq.attest_target('test',NULL,false)")
    provisional = await doomed.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        claim["token"],
        claim["definition_version"],
        [due],
        claim["as_of"] + timedelta(minutes=1),
    )
    assert provisional is not None and provisional["jobs_enqueued"] == 1
    assert await pg.fetchval("SELECT pg_terminate_backend($1)", backend_pid) is True
    try:
        await doomed.close()
    except (asyncpg.ConnectionDoesNotExistError, ConnectionError):
        pass

    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.schedule_occurrences WHERE schedule_id=$1",
            schedule_id,
        )
        == 0
    )
    await pg.execute(
        "UPDATE taskq.schedules SET claim_expires_at=now()-interval '1 second' WHERE id=$1",
        schedule_id,
    )
    reclaimed = await _claim_named(housekeeper, "testns.kill")
    committed = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        reclaimed["token"],
        reclaimed["definition_version"],
        [due],
        reclaimed["as_of"] + timedelta(minutes=1),
    )
    assert committed is not None and committed["jobs_enqueued"] == 1
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.schedule_occurrences WHERE schedule_id=$1",
            schedule_id,
        )
        == 1
    )
