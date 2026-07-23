"""SQL contract 0.2.2 — native recurring schedules."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import RoleConnect
from taskq.sql import _migrate_impl, discover_migrations, verify

pytestmark = pytest.mark.taskq_sql


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _definition(
    queue: str,
    *,
    recurrence: dict[str, Any] | None = None,
    policy: str = "fire_all",
    max_catchup: int = 3,
    paused: bool = False,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "target": {
            "kind": "job",
            "queue": queue,
            "job_type": "test.scheduled",
            "payload": payload or {},
        },
        "recurrence": recurrence or {"kind": "interval", "interval_seconds": 3600},
        "catchup_policy": policy,
        "max_catchup": max_catchup,
        "paused": paused,
    }


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'schedule-test')", name
    )


async def _put(
    operator: asyncpg.Connection,
    name: str,
    definition: dict[str, Any],
    *,
    version: int | None = None,
) -> asyncpg.Record:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.put_schedule($1,CAST($2 AS jsonb),'schedule-test',$3)",
        name,
        json.dumps(definition),
        version,
    )
    assert row is not None
    return row


async def _claim_named(
    housekeeper: asyncpg.Connection,
    name: str,
    *,
    limit: int = 100,
) -> asyncpg.Record:
    batch = await housekeeper.fetchrow(
        "SELECT * FROM taskq.claim_schedules('schedule-housekeeper',$1,60)",
        limit,
    )
    assert batch is not None
    for claim in batch["schedules"]:
        if claim["name"] == name:
            return claim
    raise AssertionError(f"schedule {name!r} was not claimed")


async def _pause_seed(pg: asyncpg.Connection) -> None:
    await pg.execute("UPDATE taskq.schedules SET state='paused' WHERE name='taskq-janitor-daily'")


async def _assert_sqlstate(
    conn: asyncpg.Connection,
    state: str,
    query: str,
    *args: object,
) -> asyncpg.PostgresError:
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await conn.fetchrow(query, *args)
    assert exc_info.value.sqlstate == state
    return exc_info.value


async def test_definition_lifecycle_projection_conflicts_and_retirement(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
) -> None:
    await _pause_seed(pg)
    await _queue(operator, "schedule_definition")
    definition = _definition("schedule_definition")

    created = await _put(operator, "definition-hourly", definition)
    assert created["outcome"] == "created"
    profile = created["profile"]
    assert profile["version"] == 1
    assert profile["state"] == "active"
    assert _json(profile["target"])["priority"] is None
    assert _json(profile["target"])["payload"] == {}

    replay = await _put(operator, "definition-hourly", definition)
    assert replay["outcome"] == "unchanged"
    assert replay["profile"]["schedule_id"] == profile["schedule_id"]

    mismatch = await _assert_sqlstate(
        operator,
        "TQ409",
        "SELECT * FROM taskq.put_schedule($1,CAST($2 AS jsonb),'schedule-test')",
        "definition-hourly",
        json.dumps(_definition("schedule_definition", max_catchup=4)),
    )
    assert json.loads(mismatch.detail) == {
        "reason": "schedule_mismatch",
        "current_version": 1,
    }
    stale = await _assert_sqlstate(
        operator,
        "TQ409",
        "SELECT * FROM taskq.put_schedule($1,CAST($2 AS jsonb),'schedule-test',$3)",
        "definition-hourly",
        json.dumps(_definition("schedule_definition", paused=True)),
        9,
    )
    assert json.loads(stale.detail) == {
        "reason": "schedule_version_conflict",
        "current_version": 1,
    }

    paused = await _put(
        operator,
        "definition-hourly",
        _definition("schedule_definition", paused=True),
        version=1,
    )
    assert (paused["outcome"], paused["profile"]["state"], paused["profile"]["version"]) == (
        "updated",
        "paused",
        2,
    )
    resumed = await _put(operator, "definition-hourly", definition, version=2)
    assert resumed["profile"]["state"] == "active"
    assert resumed["profile"]["version"] == 3
    assert (await operator.fetchrow("SELECT * FROM taskq.get_schedule('definition-hourly')"))[
        "version"
    ] == 3

    retired = await operator.fetchrow(
        "SELECT * FROM taskq.retire_schedule('definition-hourly',3,'schedule-test')"
    )
    assert retired is not None
    assert retired["outcome"] == "retired"
    assert retired["profile"]["state"] == "retired"
    assert retired["profile"]["version"] == 4
    retired_replay = await operator.fetchrow(
        "SELECT * FROM taskq.retire_schedule('definition-hourly',4,'schedule-test')"
    )
    assert retired_replay is not None and retired_replay["outcome"] == "already_retired"
    retired_update = await _assert_sqlstate(
        operator,
        "TQ409",
        "SELECT * FROM taskq.put_schedule($1,CAST($2 AS jsonb),'schedule-test',$3)",
        "definition-hourly",
        json.dumps(definition),
        4,
    )
    assert json.loads(retired_update.detail) == {
        "reason": "schedule_retired",
        "current_version": 4,
    }


async def test_definition_validation_cron_and_reserved_maintenance_wall(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
) -> None:
    await _pause_seed(pg)
    await _queue(operator, "schedule_validation")
    valid = await _put(
        operator,
        "cron-valid",
        _definition(
            "schedule_validation",
            recurrence={
                "kind": "cron",
                "expression": "*/15 1-5 * * 1,3,5",
                "timezone": "America/Argentina/Buenos_Aires",
            },
        ),
    )
    assert _json(valid["profile"]["recurrence"]) == {
        "kind": "cron",
        "expression": "*/15 1-5 * * 1,3,5",
        "timezone": "America/Argentina/Buenos_Aires",
    }

    for name, recurrence in (
        ("cron-six-fields", {"kind": "cron", "expression": "* * * * * *", "timezone": "UTC"}),
        ("cron-names", {"kind": "cron", "expression": "0 3 * JAN *", "timezone": "UTC"}),
        ("cron-range", {"kind": "cron", "expression": "61 * * * *", "timezone": "UTC"}),
        ("cron-zone", {"kind": "cron", "expression": "0 3 * * *", "timezone": "Mars/Base"}),
        ("interval-small", {"kind": "interval", "interval_seconds": 59}),
    ):
        await _assert_sqlstate(
            operator,
            "TQ422",
            "SELECT * FROM taskq.put_schedule($1,CAST($2 AS jsonb),'schedule-test')",
            name,
            json.dumps(_definition("schedule_validation", recurrence=recurrence)),
        )

    await _assert_sqlstate(
        operator,
        "TQ422",
        "SELECT * FROM taskq.put_schedule($1,CAST($2 AS jsonb),'schedule-test')",
        "taskq-janitor-daily",
        json.dumps(_definition("schedule_validation")),
    )
    await _assert_sqlstate(
        operator,
        "TQ001",
        "SELECT * FROM taskq.get_schedule('taskq-janitor-daily')",
    )


async def test_compile_first_fire_all_occurrence_identity_and_response_replay(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _pause_seed(pg)
    await _queue(operator, "schedule_fire_all")
    created = await _put(operator, "fire-all", _definition("schedule_fire_all"))
    schedule_id = created["profile"]["schedule_id"]

    initial = await _claim_named(housekeeper, "fire-all")
    initial_next = initial["as_of"] + timedelta(hours=1)
    initialized = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        initial["token"],
        initial["definition_version"],
        [],
        initial_next,
    )
    assert initialized is not None
    assert (initialized["outcome"], initialized["replayed"], initialized["jobs_enqueued"]) == (
        "initialized",
        False,
        0,
    )
    initial_replay = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        initial["token"],
        initial["definition_version"],
        [],
        initial_next,
    )
    assert initial_replay is not None
    assert (initial_replay["outcome"], initial_replay["replayed"]) == ("initialized", True)
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0

    due = datetime.now(UTC) - timedelta(hours=4)
    await pg.execute(
        """
        UPDATE taskq.schedules
        SET initialized=true, next_fire_at=$2, claim_token=NULL, claim_as_of=NULL,
            claimed_by=NULL, claim_expires_at=NULL
        WHERE id=$1
        """,
        schedule_id,
        due,
    )
    claim = await _claim_named(housekeeper, "fire-all")
    occurrences = [due, due + timedelta(hours=1), due + timedelta(hours=2)]
    next_due = due + timedelta(hours=3)
    fired = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        claim["token"],
        claim["definition_version"],
        occurrences,
        next_due,
    )
    assert fired is not None
    assert (fired["outcome"], fired["jobs_enqueued"], fired["next_fire_at"]) == (
        "fired",
        3,
        next_due,
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.schedule_occurrences WHERE schedule_id=$1",
            schedule_id,
        )
        == 3
    )
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE queue='schedule_fire_all'") == 3
    keys = await pg.fetch(
        "SELECT idempotency_key FROM taskq.jobs WHERE queue='schedule_fire_all' ORDER BY scheduled_at"
    )
    assert len({row["idempotency_key"] for row in keys}) == 3

    replay = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        claim["token"],
        claim["definition_version"],
        occurrences,
        next_due,
    )
    assert replay is not None
    assert (replay["outcome"], replay["replayed"], replay["jobs_enqueued"]) == (
        "fired",
        True,
        3,
    )
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE queue='schedule_fire_all'") == 3


@pytest.mark.parametrize(
    ("policy", "occurrence_count", "expected"),
    [("skip", 0, "skipped"), ("fire_once", 1, "fired")],
)
async def test_skip_and_fire_once_policy_shapes(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
    policy: str,
    occurrence_count: int,
    expected: str,
) -> None:
    await _pause_seed(pg)
    queue = f"schedule_{policy}"
    await _queue(operator, queue)
    created = await _put(
        operator,
        f"policy-{policy}",
        _definition(queue, policy=policy, max_catchup=5),
    )
    schedule_id = created["profile"]["schedule_id"]
    due = datetime.now(UTC) - timedelta(hours=4)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        schedule_id,
        due,
    )
    claim = await _claim_named(housekeeper, f"policy-{policy}")
    occurrences = [claim["as_of"] - timedelta(minutes=1)] if occurrence_count else []
    next_due = claim["as_of"] + timedelta(hours=1)
    result = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        schedule_id,
        claim["token"],
        claim["definition_version"],
        occurrences,
        next_due,
    )
    assert result is not None and result["outcome"] == expected
    assert result["jobs_enqueued"] == occurrence_count


async def test_error_replay_and_definition_change_fences_claim(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    await _pause_seed(pg)
    await _queue(operator, "schedule_error")
    created = await _put(operator, "error-path", _definition("schedule_error"))
    claim = await _claim_named(housekeeper, "error-path")
    recorded = await housekeeper.fetchrow(
        "SELECT * FROM taskq.schedule_error($1,$2,$3,$4,30)",
        created["profile"]["schedule_id"],
        claim["token"],
        claim["definition_version"],
        "x" * 3000,
    )
    assert recorded is not None
    assert (recorded["outcome"], recorded["replayed"]) == ("error_recorded", False)
    assert (
        await pg.fetchval(
            "SELECT octet_length(last_error) FROM taskq.schedules WHERE name='error-path'"
        )
        == 2048
    )
    replay = await housekeeper.fetchrow(
        "SELECT * FROM taskq.schedule_error($1,$2,$3,$4,30)",
        created["profile"]["schedule_id"],
        claim["token"],
        claim["definition_version"],
        "x" * 3000,
    )
    assert replay is not None
    assert (replay["outcome"], replay["replayed"]) == ("error_recorded", True)

    await pg.execute(
        "UPDATE taskq.schedules SET retry_not_before=NULL,next_fire_at=now() WHERE name='error-path'"
    )
    stale_claim = await _claim_named(housekeeper, "error-path")
    updated = await _put(
        operator,
        "error-path",
        _definition("schedule_error", payload={"revision": 2}),
        version=1,
    )
    assert updated["profile"]["version"] == 2
    stale = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        created["profile"]["schedule_id"],
        stale_claim["token"],
        stale_claim["definition_version"],
        [],
        stale_claim["as_of"] + timedelta(hours=1),
    )
    assert stale is not None and stale["outcome"] == "stale"
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0


async def test_two_housekeepers_claim_due_schedule_once(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    role_conn: RoleConnect,
) -> None:
    await _pause_seed(pg)
    await _queue(operator, "schedule_claim_race")
    await _put(operator, "claim-race", _definition("schedule_claim_race"))
    first = await role_conn("taskq_housekeeper")
    second = await role_conn("taskq_housekeeper")

    rows = await asyncio.gather(
        first.fetchrow("SELECT * FROM taskq.claim_schedules('race-a',1,60)"),
        second.fetchrow("SELECT * FROM taskq.claim_schedules('race-b',1,60)"),
    )
    claims = [claim for row in rows for claim in row["schedules"]]
    assert len(claims) == 1
    assert claims[0]["name"] == "claim-race"
    stored = await pg.fetchrow(
        "SELECT claim_token,claimed_by FROM taskq.schedules WHERE name='claim-race'"
    )
    assert stored is not None
    assert stored["claim_token"] == claims[0]["token"]
    assert stored["claimed_by"] in {"race-a", "race-b"}


async def test_seeded_janitor_is_one_only_and_tick_no_longer_runs_it(
    pg: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
) -> None:
    claim = await _claim_named(housekeeper, "taskq-janitor-daily")
    initialized = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        claim["schedule_id"],
        claim["token"],
        claim["definition_version"],
        [],
        claim["as_of"] + timedelta(days=1),
    )
    assert initialized is not None and initialized["outcome"] == "initialized"

    due = datetime.now(UTC) - timedelta(minutes=1)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$1 WHERE name='taskq-janitor-daily'",
        due,
    )
    fire_claim = await _claim_named(housekeeper, "taskq-janitor-daily")
    fired = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        fire_claim["schedule_id"],
        fire_claim["token"],
        fire_claim["definition_version"],
        [due],
        fire_claim["as_of"] + timedelta(days=1),
    )
    assert fired is not None
    assert (fired["outcome"], fired["jobs_enqueued"]) == ("fired", 0)
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.schedule_occurrences WHERE schedule_id=$1",
            fire_claim["schedule_id"],
        )
        == 1
    )
    replay = await housekeeper.fetchrow(
        "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
        fire_claim["schedule_id"],
        fire_claim["token"],
        fire_claim["definition_version"],
        [due],
        fire_claim["as_of"] + timedelta(days=1),
    )
    assert replay is not None and replay["replayed"] is True
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.schedule_occurrences WHERE schedule_id=$1",
            fire_claim["schedule_id"],
        )
        == 1
    )

    tick = await housekeeper.fetchval("SELECT taskq.tick()")
    assert "janitor" not in tick


async def test_0009_to_0010_transition_and_full_verify(taskq_dsn: str) -> None:
    database = f"taskq_schedule_transition_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        assert migrations[-1].id == "0010_schedules"
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:9])
            )
            assert applied[-1] == "0009_workflows"
            assert (
                await conn.exec_driver_sql("SELECT to_regclass('taskq.schedules')")
            ).scalar_one() is None
            meta = (await conn.exec_driver_sql("SELECT * FROM taskq.get_contract_meta()")).one()
            assert meta.contract_version == "0.2.1"
            assert "schedules" not in meta.capabilities["active"]

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[9:10])
            )
            assert applied == ["0010_schedules"]
            report = await verify(conn)
            assert report.ok, report
            meta = (await conn.exec_driver_sql("SELECT * FROM taskq.get_contract_meta()")).one()
            assert meta.contract_version == "0.2.2"
            assert meta.capabilities["active"] == [
                "admission_reservations",
                "dependencies_workflows",
                "followups",
                "read_model_list_ready",
                "schedules",
            ]
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
        try:
            await admin.execute(f'DROP DATABASE "{database}"')
        finally:
            await admin.close()
