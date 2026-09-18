"""Parent-owned PR #52 acceptance tests: real LOGIN boundaries, not SET ROLE."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.errors import TaskqError
from taskq.sql import lock_terminal_effect_job
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql

POLICY = "f" * 64
INTENT = "a" * 64
JOB = json.dumps({"job_type": "pr52.job", "payload": {"private": "owner-only"}})
RECEIPT = '{"private":"owner-receipt"}'
RESERVE = "SELECT * FROM taskq.reserve_admission($1,$2,$3,$4)"
FINISH = "SELECT * FROM taskq.finish_admission($1,$2,$3,$4::jsonb,$5::jsonb)"
CANCEL = "SELECT * FROM taskq.cancel_admission($1,$2,$3)"
BIND = "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)"
ADOPT = "SELECT taskq.adopt_queue_admission_owner($1,$2,$3,$4,'test',$5,false)"
ROTATE = "SELECT taskq.rotate_queue_admission_owner($1,$2,$3,$4,'test',$5,false)"
FENCE = "SELECT * FROM taskq.lock_terminal_effect_job($1,$2,'pr52.job','test',$3,false)"


@pytest.fixture(scope="module", params=["fresh", "old-0046-upgrade"])
def taskq_dsn(request, taskq_dsn, tmp_path_factory):
    """Run every adversarial/concurrency case against both supported install paths."""
    if request.param == "fresh":
        yield taskq_dsn
        return
    from scripts.artifact_smoke import (
        _database_dsn,
        _drop_owner_upgrade,
        _exercise_owner_upgrade,
        _prepare_owner_upgrade,
    )

    database = "pr52_boundary_upgrade_" + uuid4().hex
    try:
        asyncio.run(_prepare_owner_upgrade(taskq_dsn, database, Path(__file__).parents[1]))
        _exercise_owner_upgrade(
            Path(sys.executable).parent / "taskq",
            taskq_dsn,
            database,
            cwd=tmp_path_factory.mktemp("pr52-upgrade"),
        )
        yield _database_dsn(taskq_dsn, database)
    finally:
        asyncio.run(_drop_owner_upgrade(taskq_dsn, database))


@pytest.fixture(scope="module")
def migrated(taskq_dsn):
    from conftest import _migrate_once

    asyncio.run(_migrate_once(taskq_dsn))


@pytest.fixture
async def logins(taskq_dsn, pg):
    suffix = uuid4().hex[:10]
    roles = {
        name: f"pr52_{name}_{suffix}"
        for name in ("owner", "other", "runner", "operator", "housekeeper")
    }
    connections = {}
    parsed = urlparse(taskq_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for name, role in roles.items():
            await pg.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'pr52-scratch'")
            capability = "producer" if name in ("owner", "other") else name
            await pg.execute(f'GRANT taskq_{capability} TO "{role}"')
            conn = await asyncpg.connect(
                host=parsed.hostname,
                port=parsed.port,
                database=parsed.path[1:],
                user=role,
                password="pr52-scratch",
            )
            connections[name] = conn
            assert await conn.fetchval("SELECT session_user = current_user")
            assert not await pg.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=$1", role)
        assert not await pg.fetchval(
            "SELECT pg_has_role($1,'taskq_producer','member')", roles["runner"]
        )
        installation = await pg.fetchval("SELECT installation_id FROM taskq.get_target_identity()")
        yield roles, connections, installation
    finally:
        for conn in connections.values():
            await conn.close()
        for role in roles.values():
            await pg.execute(f'DROP ROLE IF EXISTS "{role}"')


async def queue(logins, owner="owner"):
    roles, connections, installation = logins
    name = "pr52_" + uuid4().hex[:12]
    await connections["operator"].fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'pr52')", name
    )
    if owner is not None:
        await connections["operator"].fetchval(BIND, name, roles[owner], installation)
    return name


async def reserve(conn, name, key="key", handle=None):
    handle = handle or uuid4()
    row = await conn.fetchrow(RESERVE, name, key, INTENT, handle)
    return handle, row


async def claim(logins, name, policy=False):
    runner = logins[1]["runner"]
    async with runner.transaction():
        await runner.fetchrow("SELECT * FROM taskq.attest_target('test',NULL,false)")
        if policy:
            row = await runner.fetchrow(
                "SELECT * FROM taskq.claim_jobs($1,'pr52-runner',1,NULL,NULL,NULL,NULL,ARRAY[$2],false)",
                name,
                POLICY,
            )
        else:
            row = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1,'pr52-runner')", name)
    assert row["state"] == "claimed"
    return row["jobs"][0]


async def complete(logins, job, followups=None, policy=False):
    args = [job["job_id"], job["attempt_id"], json.dumps(followups or [])]
    sql = "SELECT * FROM taskq.complete_job($1,$2,'pr52-runner',NULL,NULL,$3::jsonb"
    if policy:
        sql += ",$4"
        args.append(POLICY)
    return await logins[1]["runner"].fetchrow(sql + ")", *args)


async def blocked(pg, conn, task):
    """Observe an actual database lock wait; scheduler delays are not proof."""
    async with asyncio.timeout(5):
        while not await pg.fetchval(
            "SELECT cardinality(pg_blocking_pids($1)) > 0", conn.get_server_pid()
        ):
            assert not task.done(), "contender did not wait for the ownership decision"
            await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    "operation",
    [
        "reserve_new",
        "reserve_pending",
        "reserve_replay",
        "finish",
        "finish_replay",
        "cancel",
        "cancel_replay",
    ],
)
async def test_issue1_admission_entrypoints_deny_unrelated_login(logins, pg, operation):
    _, conns, _ = logins
    name = await queue(logins)
    handle, _ = await reserve(conns["owner"], name)
    if operation in ("reserve_replay", "finish_replay", "cancel_replay"):
        await conns["owner"].fetchrow(FINISH, name, "key", handle, JOB, RECEIPT)
    before = await pg.fetchval(
        "SELECT jsonb_agg(to_jsonb(a)) FROM taskq.admissions a WHERE queue=$1", name
    )
    with pytest.raises(asyncpg.PostgresError) as denied:
        if operation.startswith("reserve"):
            await reserve(conns["other"], name, "new" if operation == "reserve_new" else "key")
        elif operation.startswith("finish"):
            await conns["other"].fetchrow(FINISH, name, "key", handle, JOB, RECEIPT)
        else:
            await conns["other"].fetchrow(CANCEL, name, "key", handle)
    assert denied.value.sqlstate == "TQ425"
    assert (
        await pg.fetchval(
            "SELECT jsonb_agg(to_jsonb(a)) FROM taskq.admissions a WHERE queue=$1", name
        )
        == before
    )


@pytest.mark.parametrize("state", ["reserved", "cancelled", "admitted"])
async def test_issue1_binding_rejects_existing_admission_state(logins, pg, state):
    roles, conns, installation = logins
    name = await queue(logins, None)
    handle, _ = await reserve(conns["other"], name)
    if state == "cancelled":
        await conns["other"].fetchrow(CANCEL, name, "key", handle)
    elif state == "admitted":
        row = await conns["other"].fetchrow(FINISH, name, "key", handle, JOB, RECEIPT)
        # Retention may remove a job while its durable receipt remains.
        await pg.execute("DELETE FROM taskq.jobs WHERE id=$1", row["job_id"])
    assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE queue=$1", name) == 0
    with pytest.raises(asyncpg.PostgresError) as occupied:
        await conns["operator"].fetchval(BIND, name, roles["owner"], installation)
    assert occupied.value.sqlstate == "TQ409"
    assert (
        await pg.fetchval("SELECT admission_owner_role FROM taskq.queues WHERE name=$1", name)
        is None
    )


@pytest.mark.parametrize("bind_first", [True, False])
async def test_issue1_bind_reserve_race_both_orders(logins, pg, bind_first):
    roles, conns, installation = logins
    name = await queue(logins, None)
    first = conns["operator"] if bind_first else conns["other"]
    second = conns["other"] if bind_first else conns["operator"]
    task = None
    tx = first.transaction()
    await tx.start()
    try:
        if bind_first:
            await first.fetchval(BIND, name, roles["owner"], installation)
            task = asyncio.create_task(reserve(second, name))
        else:
            await reserve(first, name)
            task = asyncio.create_task(second.fetchval(BIND, name, roles["owner"], installation))
        await blocked(pg, second, task)
        await tx.commit()
        with pytest.raises(asyncpg.PostgresError) as rejected:
            await asyncio.wait_for(task, 5)
        assert rejected.value.sqlstate == ("TQ425" if bind_first else "TQ409")
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions WHERE queue=$1", name) == (
            0 if bind_first else 1
        )
    finally:
        if first.is_in_transaction():
            await tx.rollback()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("bind_first", [True, False])
async def test_issue1_bind_enqueue_race_both_orders(logins, pg, bind_first):
    """Queue row locks make owner binding atomic with job admission."""
    roles, conns, installation = logins
    name = await queue(logins, None)
    first = conns["operator"] if bind_first else conns["other"]
    second = conns["other"] if bind_first else conns["operator"]
    task = None
    tx = first.transaction()
    await tx.start()
    try:
        if bind_first:
            await first.fetchval(BIND, name, roles["owner"], installation)
            task = asyncio.create_task(
                second.fetchrow("SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name)
            )
        else:
            await first.fetchrow("SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name)
            task = asyncio.create_task(second.fetchval(BIND, name, roles["owner"], installation))
        await blocked(pg, second, task)
        await tx.commit()
        with pytest.raises(asyncpg.PostgresError) as rejected:
            await asyncio.wait_for(task, 5)
        assert rejected.value.sqlstate == ("TQ425" if bind_first else "TQ409")
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE queue=$1", name) == (
            0 if bind_first else 1
        )
    finally:
        if first.is_in_transaction():
            await tx.rollback()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("bound", [True, False])
async def test_issue1_owner_and_unbound_admission_lifecycle_preserved(logins, bound):
    conns = logins[1]
    name = await queue(logins, "owner" if bound else None)
    producer = conns["owner"] if bound else conns["other"]
    handle, row = await reserve(producer, name)
    assert row["outcome"] == "reserved"
    assert (await producer.fetchrow(FINISH, name, "key", handle, JOB, RECEIPT))[
        "outcome"
    ] == "created"
    assert (await producer.fetchrow(FINISH, name, "key", handle, JOB, RECEIPT))[
        "outcome"
    ] == "existed"
    assert (await reserve(producer, name))[1]["outcome"] == "admitted"
    handle, _ = await reserve(producer, name, "cancel")
    assert (await producer.fetchrow(CANCEL, name, "cancel", handle))["outcome"] == "cancelled"
    assert (await producer.fetchrow(CANCEL, name, "cancel", handle))[
        "outcome"
    ] == "already_cancelled"


@pytest.mark.parametrize("entrypoint", ["enqueue", "try_enqueue", "enqueue_many"])
async def test_issue1_bound_idempotent_replay_requires_current_owner(logins, pg, entrypoint):
    """A replay is an admission decision even when it creates no new row."""
    conns = logins[1]
    name = await queue(logins)
    original = await conns["owner"].fetchrow(
        "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb,p_idempotency_key=>'replay')",
        name,
    )
    before = await pg.fetchval(
        "SELECT to_jsonb(j) FROM taskq.jobs j WHERE id=$1", original["job_id"]
    )

    with pytest.raises(asyncpg.PostgresError) as denied:
        if entrypoint == "enqueue_many":
            await conns["other"].fetch(
                "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)",
                name,
                json.dumps([{"job_type": "pr52.job", "idempotency_key": "replay"}]),
            )
        else:
            await conns["other"].fetchrow(
                f"SELECT * FROM taskq.{entrypoint}("
                "$1,'pr52.job','{}'::jsonb,p_idempotency_key=>'replay')",
                name,
            )
    assert denied.value.sqlstate == "TQ425"
    assert (
        await pg.fetchval("SELECT to_jsonb(j) FROM taskq.jobs j WHERE id=$1", original["job_id"])
        == before
    )


async def test_issue1_bound_workflow_step_replay_requires_current_owner(logins, pg):
    """The workflow uniqueness path must not return another owner's job."""
    conns = logins[1]
    name = await queue(logins)
    workflow = await conns["owner"].fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,ARRAY[$2],'pr52',3,$3)",
        uuid4().hex,
        name,
        POLICY,
    )
    original = await conns["owner"].fetchrow(
        "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb,"
        "p_workflow_id=>$2,p_step_key=>'root',p_flow_key=>'pr52-flow')",
        name,
        workflow["workflow_id"],
    )
    with pytest.raises(asyncpg.PostgresError) as denied:
        await conns["other"].fetchrow(
            "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb,"
            "p_workflow_id=>$2,p_step_key=>'root',p_flow_key=>'pr52-flow')",
            name,
            workflow["workflow_id"],
        )
    assert denied.value.sqlstate == "TQ425"
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE workflow_id=$1 AND step_key='root'",
            workflow["workflow_id"],
        )
        == 1
    )
    assert (
        await pg.fetchval("SELECT id FROM taskq.jobs WHERE id=$1", original["job_id"])
        == original["job_id"]
    )


@pytest.mark.parametrize("bound", [True, False])
async def test_issue1_owner_check_does_not_block_queue_pause(logins, bound):
    """The owner check itself must not retain a queue-row lock."""
    conns = logins[1]
    name = await queue(logins, "owner" if bound else None)
    job_id = await terminal_job(logins, name)
    caller = conns["owner"] if bound else conns["other"]
    installation = logins[2]
    tx = caller.transaction()
    await tx.start()
    pause = None
    try:
        await caller.fetchrow(FENCE, job_id, name, installation)
        pause = asyncio.create_task(
            conns["operator"].fetchrow(
                "SELECT * FROM taskq.pause_queue($1,'pr52','ownership rollout')", name
            )
        )
        result = await asyncio.wait_for(asyncio.shield(pause), 1)
        assert result is not None
    finally:
        await tx.rollback()
        if pause is not None and not pause.done():
            pause.cancel()
            await asyncio.gather(pause, return_exceptions=True)


async def test_issue1_role_rename_preserves_bound_owner_identity(logins, pg, taskq_dsn):
    """Binding follows the database role identity rather than a mutable role name."""
    roles, conns, _ = logins
    name = await queue(logins)
    old_name = roles["owner"]
    new_name = old_name + "_renamed"
    await conns["owner"].close()
    await pg.execute(f'ALTER ROLE "{old_name}" RENAME TO "{new_name}"')
    roles["owner"] = new_name
    parsed = urlparse(taskq_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    renamed = await asyncpg.connect(
        host=parsed.hostname,
        port=parsed.port,
        database=parsed.path[1:],
        user=new_name,
        password="pr52-scratch",
    )
    conns["owner"] = renamed
    try:
        created = await renamed.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name
        )
        assert created["created"] is True
        profile = await pg.fetchrow(
            "SELECT * FROM taskq.get_queue_admission_owner_identity($1)", name
        )
        assert profile["owner_role"] == new_name
        assert profile["owner_oid"] == await pg.fetchval(
            "SELECT oid FROM pg_roles WHERE rolname=$1", new_name
        )
    finally:
        await renamed.close()
        conns.pop("owner", None)


async def test_issue1_adopt_and_rotate_quiesced_queue_with_audit(logins, pg):
    roles, conns, installation = logins
    name = await queue(logins, None)
    await conns["other"].fetchrow(
        "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb,p_idempotency_key=>'history')",
        name,
    )
    job = await claim(logins, name)
    await complete(logins, job)
    await conns["operator"].fetchrow(
        "SELECT * FROM taskq.pause_queue($1,'pr52','adoption window')", name
    )
    adopted = await conns["operator"].fetchval(
        ADOPT, name, roles["owner"], "pr52", "adopt retained history", installation
    )
    assert adopted == roles["owner"]
    with pytest.raises(asyncpg.PostgresError) as old_denied:
        await conns["other"].fetchrow(
            "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb,p_idempotency_key=>'history')",
            name,
        )
    assert old_denied.value.sqlstate == "TQ425"

    rotated = await conns["operator"].fetchval(
        ROTATE, name, roles["other"], "pr52", "owner credential rotation", installation
    )
    assert rotated == roles["other"]
    with pytest.raises(asyncpg.PostgresError) as prior_denied:
        await conns["owner"].fetchrow(
            "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name
        )
    assert prior_denied.value.sqlstate == "TQ425"
    assert (
        await conns["other"].fetchrow(
            "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name
        )
    )["created"] is True
    audit = await pg.fetch(
        "SELECT event_type,detail FROM taskq.queue_audit WHERE queue=$1 "
        "AND event_type LIKE 'admission_owner_%' ORDER BY id",
        name,
    )
    assert [row["event_type"] for row in audit] == [
        "admission_owner_adopted",
        "admission_owner_rotated",
    ]


@pytest.mark.parametrize("policy", [False, True])
@pytest.mark.parametrize("cross_queue", [False, True])
async def test_issue2_runner_continuation_preserves_owner_and_atomic_replay(
    logins, pg, policy, cross_queue
):
    conns = logins[1]
    source = await queue(logins)
    target = await queue(logins) if cross_queue else source
    workflow_id = None
    if policy:
        workflow = await conns["owner"].fetchrow(
            "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,$2,'pr52',3,$3)",
            uuid4().hex,
            list(dict.fromkeys([source, target])),
            POLICY,
        )
        workflow_id = workflow["workflow_id"]
    await conns["owner"].fetchrow(
        "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb,p_workflow_id=>$2,p_step_key=>$3,p_flow_key=>'pr52-flow')",
        source,
        workflow_id,
        "root" if policy else None,
    )
    if policy:
        await conns["owner"].fetchrow("SELECT * FROM taskq.seal_workflow($1,'pr52')", workflow_id)
    job = await claim(logins, source, policy)
    spec = {"step": "child", "job_type": "pr52.child", "queue": target}
    if policy:
        spec["workflow_member"] = True
    result = await complete(logins, job, [spec], policy)
    assert result["result"] == "ok"
    assert (await complete(logins, job, [spec], policy))["result"] == "already_settled"
    children = await pg.fetch("SELECT * FROM taskq.jobs WHERE parent_job_id=$1", job["job_id"])
    assert len(children) == 1 and children[0]["queue"] == target
    assert children[0]["workflow_id"] == workflow_id
    assert children[0]["flow_key"] == ("pr52-flow" if policy else None)
    assert not await pg.fetchval(
        "SELECT pg_has_role($1,$2,'member')", logins[0]["runner"], logins[0]["owner"]
    )


@pytest.mark.parametrize("source_owner,target_owner", [("owner", "other"), (None, "owner")])
async def test_issue2_runner_cannot_launder_foreign_or_unbound_parent(
    logins, pg, source_owner, target_owner
):
    conns = logins[1]
    source = await queue(logins, source_owner)
    target = await queue(logins, target_owner)
    await conns["owner"].fetchrow("SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", source)
    job = await claim(logins, source)
    specs = [
        {"step": "allowed", "job_type": "pr52.child", "queue": source},
        {"step": "forbidden", "job_type": "pr52.child", "queue": target},
    ]
    with pytest.raises(asyncpg.PostgresError) as denied:
        await complete(logins, job, specs)
    assert denied.value.sqlstate == "TQ425"
    assert (
        await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", job["job_id"]) == "running"
    )
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", job["job_id"])
        == 0
    )


async def _claim_schedule(conns, installation, name):
    async with conns["housekeeper"].transaction():
        await conns["housekeeper"].fetchrow(
            "SELECT * FROM taskq.attest_target('test',$1,false)", installation
        )
        batch = await conns["housekeeper"].fetchrow(
            "SELECT * FROM taskq.claim_schedules('pr52-scheduler',100,60)"
        )
    for schedule in batch["schedules"]:
        if schedule["name"] == name:
            return schedule
    raise AssertionError(f"schedule not claimed: {name}")


async def _fire_schedule(conns, installation, claim, occurrences, next_fire_at):
    async with conns["housekeeper"].transaction():
        await conns["housekeeper"].fetchrow(
            "SELECT * FROM taskq.attest_target('test',$1,false)", installation
        )
        return await conns["housekeeper"].fetchrow(
            "SELECT * FROM taskq.fire_schedule($1,$2,$3,$4,$5)",
            claim["schedule_id"],
            claim["token"],
            claim["definition_version"],
            occurrences,
            next_fire_at,
        )


async def test_issue2_separate_housekeeper_fires_bound_queue_schedule(logins, pg):
    """A scheduler login gets narrow, row-backed provenance without producer membership."""
    roles, conns, installation = logins
    name = await queue(logins)
    schedule_name = "pr52." + uuid4().hex[:12]
    definition = {
        "target": {
            "kind": "job",
            "queue": name,
            "job_type": "pr52.scheduled",
            "payload": {"scheduled": True},
        },
        "recurrence": {"kind": "interval", "interval_seconds": 60},
        "catchup_policy": "fire_once",
        "max_catchup": 1,
        "paused": False,
    }
    async with conns["operator"].transaction():
        await conns["operator"].fetchrow(
            "SELECT * FROM taskq.attest_target('test',$1,false)", installation
        )
        created = await conns["operator"].fetchrow(
            "SELECT * FROM taskq.put_schedule($1,$2::jsonb,'pr52',NULL)",
            schedule_name,
            json.dumps(definition),
        )
    assert created["outcome"] == "created"
    assert not await pg.fetchval(
        "SELECT pg_has_role($1,'taskq_producer','member')", roles["housekeeper"]
    )
    assert not await pg.fetchval(
        "SELECT pg_has_role($1,$2,'member')", roles["housekeeper"], roles["owner"]
    )

    initialized = await _claim_schedule(conns, installation, schedule_name)
    result = await _fire_schedule(
        conns,
        installation,
        initialized,
        [],
        initialized["as_of"] + timedelta(seconds=60),
    )
    assert result["outcome"] == "initialized"

    due = datetime.now(UTC) - timedelta(minutes=1)
    await pg.execute(
        "UPDATE taskq.schedules SET initialized=true,next_fire_at=$2 WHERE id=$1",
        initialized["schedule_id"],
        due,
    )
    claimed = await _claim_schedule(conns, installation, schedule_name)
    fired = await _fire_schedule(
        conns,
        installation,
        claimed,
        [due],
        claimed["as_of"] + timedelta(seconds=60),
    )
    assert fired["outcome"] == "fired" and fired["jobs_enqueued"] == 1
    job = await pg.fetchrow(
        "SELECT queue,job_type,headers->'taskq_schedule' AS schedule_header "
        "FROM taskq.jobs WHERE queue=$1 AND job_type='pr52.scheduled'",
        name,
    )
    assert job is not None and job["queue"] == name
    schedule_header = (
        json.loads(job["schedule_header"])
        if isinstance(job["schedule_header"], str)
        else job["schedule_header"]
    )
    assert schedule_header["schedule_id"] == str(initialized["schedule_id"])


async def test_issue2_producer_cannot_forge_continuation_parent(logins, pg):
    conns = logins[1]
    # A mixed-capability login still must not get a blanket runner admission bypass.
    await pg.execute(f'GRANT taskq_runner TO "{logins[0]["other"]}"')
    name = await queue(logins)
    parent = await conns["owner"].fetchrow(
        "SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name
    )
    job = await claim(logins, name)
    await complete(logins, job)
    with pytest.raises(asyncpg.PostgresError) as denied:
        await conns["other"].fetchrow(
            "SELECT * FROM taskq.enqueue($1,'pr52.child','{}'::jsonb,p_parent_job_id=>$2)",
            name,
            parent["job_id"],
        )
    assert denied.value.sqlstate == "TQ425"
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conns["runner"].fetchrow(
            'SELECT * FROM taskq._enqueue_followup($1,$2,\'{"step":"forged","job_type":"pr52.child"}\'::jsonb,1)',
            parent["job_id"],
            name,
        )


@pytest.mark.parametrize("entrypoint", ["enqueue", "enqueue_many", "try_enqueue"])
async def test_issue2_public_admission_cannot_forge_reserved_chain(logins, pg, entrypoint):
    conns = logins[1]
    await pg.execute(f'GRANT taskq_runner TO "{logins[0]["other"]}"')
    name = await queue(logins)
    await conns["owner"].fetchrow("SELECT * FROM taskq.enqueue($1,'pr52.job','{}'::jsonb)", name)
    job = await claim(logins, name)
    await complete(logins, job)
    key = f"chain:{job['job_id']}:forged"
    with pytest.raises(asyncpg.PostgresError) as denied:
        if entrypoint == "enqueue_many":
            await conns["other"].fetch(
                "SELECT * FROM taskq.enqueue_many($1,$2::jsonb)",
                name,
                json.dumps(
                    [
                        {
                            "job_type": "pr52.child",
                            "parent_job_id": str(job["job_id"]),
                            "idempotency_key": key,
                        }
                    ]
                ),
            )
        else:
            await conns["other"].fetchrow(
                f"SELECT * FROM taskq.{entrypoint}($1,'pr52.child','{{}}'::jsonb,"
                "p_parent_job_id=>$2,p_idempotency_key=>$3)",
                name,
                job["job_id"],
                key,
            )
    assert denied.value.sqlstate in {"TQ422", "TQ425"}
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE parent_job_id=$1", job["job_id"])
        == 0
    )


async def terminal_job(logins, name):
    await logins[1]["owner"].fetchrow(
        "SELECT * FROM taskq.enqueue($1,'pr52.job',$2::jsonb)", name, '{"private":"owner-only"}'
    )
    job = await claim(logins, name)
    await complete(logins, job)
    return job["job_id"]


async def test_issue3_foreign_fence_denied_before_payload_or_retention_lock(logins, pg):
    _, conns, installation = logins
    name = await queue(logins)
    job_id = await terminal_job(logins, name)
    # Keep outer transaction alive after denial; a successful fence would retain its lock.
    async with conns["other"].transaction():
        with pytest.raises(asyncpg.PostgresError) as denied:
            async with conns["other"].transaction():
                await conns["other"].fetchrow(FENCE, job_id, name, installation)
        assert denied.value.sqlstate == "TQ425"
        async with pg.transaction():
            await pg.execute("SET LOCAL lock_timeout='250ms'")
            assert await pg.execute("DELETE FROM taskq.jobs WHERE id=$1", job_id) == "DELETE 1"


@pytest.mark.parametrize("bound", [True, False])
async def test_issue3_authorized_fence_keeps_transaction_lock_and_unbound_behavior(
    logins, pg, bound
):
    _, conns, installation = logins
    name = await queue(logins, "owner" if bound else None)
    job_id = await terminal_job(logins, name)
    caller = conns["owner"] if bound else conns["other"]
    async with caller.transaction():
        row = await caller.fetchrow(FENCE, job_id, name, installation)
        assert json.loads(row["payload"]) == {"private": "owner-only"}
        with pytest.raises(asyncpg.LockNotAvailableError):
            async with pg.transaction():
                await pg.execute("SET LOCAL lock_timeout='100ms'")
                await pg.execute("DELETE FROM taskq.jobs WHERE id=$1", job_id)
    assert await pg.execute("DELETE FROM taskq.jobs WHERE id=$1", job_id) == "DELETE 1"


async def test_issue4_live_sql_denials_are_nonretryable(logins, taskq_dsn):
    roles, conns, installation = logins
    name = await queue(logins)
    job_id = await terminal_job(logins, name)
    parsed = urlparse(taskq_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    engine = create_async_engine(
        f"postgresql+asyncpg://{roles['other']}:pr52-scratch@{parsed.hostname}:{parsed.port}/{parsed.path[1:]}"
    )
    transport = SqlTaskqTransport(engine)
    try:
        with pytest.raises(TaskqError) as admission_denied:
            await transport.reserve_admission(name, "foreign", INTENT)
        assert admission_denied.value.code.value == "TQ425"
        assert admission_denied.value.retryable is False
        async with engine.begin() as connection:
            with pytest.raises(TaskqError) as fence_denied:
                await lock_terminal_effect_job(
                    connection,
                    job_id=job_id,
                    queue=name,
                    job_type="pr52.job",
                    expected_environment="test",
                    expected_installation_id=installation,
                )
            assert fence_denied.value.code.value == "TQ425"
            assert fence_denied.value.retryable is False
    finally:
        await engine.dispose()


async def test_issue1_upgrade_preserves_existing_function_dependencies(taskq_dsn):
    from conftest import activate_scheduler_contract

    from taskq.sql import _migrate_impl, discover_migrations
    from tests.test_wfc_i05_activation import _create_database, _database_dsn

    database, admin = await _create_database(taskq_dsn, "pr52_upgrade")
    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        async with engine.connect() as connection:
            await connection.run_sync(lambda conn: _migrate_impl(conn, migrations[:18]))
            await activate_scheduler_contract(connection, migrations[:45])
            identities = (
                "SELECT oid FROM pg_proc WHERE pronamespace='taskq'::regnamespace "
                "AND proname IN ('reserve_admission','finish_admission','cancel_admission') "
                "ORDER BY proname"
            )
            before = (await connection.exec_driver_sql(identities)).scalars().all()
            assert len(before) == 3
            await connection.exec_driver_sql(
                "CREATE VIEW public.pr52_admission_dependencies AS SELECT "
                "taskq.reserve_admission('q','k',repeat('a',64),NULL::uuid) AS reserved, "
                "taskq.finish_admission('q','k',NULL::uuid,'{}'::jsonb) AS finished, "
                "taskq.cancel_admission('q','k',NULL::uuid) AS cancelled"
            )
            await connection.commit()
            await connection.run_sync(lambda conn: _migrate_impl(conn, migrations[45:]))
            assert (await connection.exec_driver_sql(identities)).scalars().all() == before
            assert (
                await connection.exec_driver_sql(
                    "SELECT to_regclass('public.pr52_admission_dependencies') IS NOT NULL"
                )
            ).scalar_one()
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()


async def test_issue4_http_denial_round_trip_has_no_retry(logins, taskq_dsn):
    import httpx

    from taskq.http import AsyncTaskqHttpClient, create_taskq_app, no_auth_for_tests
    from tests.test_s5_admission_surface import _mounted, _resources

    roles = logins[0]
    name = await queue(logins)
    parsed = urlparse(taskq_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    transport = SqlTaskqTransport.from_dsn(
        f"postgresql://{roles['other']}:pr52-scratch@{parsed.hostname}:{parsed.port}/{parsed.path[1:]}"
    )
    app = _mounted(
        create_taskq_app(
            _resources(transport, admission_enabled=True), authorizer=no_auth_for_tests()
        )
    )
    responses = []

    async def record(response):
        responses.append(response)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            event_hooks={"response": [record]},
        ) as raw:
            client = AsyncTaskqHttpClient(
                "http://test", bearer_token="scratch", client=raw, max_retries=2
            )
            with pytest.raises(TaskqError) as denied:
                await client.reserve_admission(name, "foreign", INTENT)
            assert denied.value.code.value == "TQ425"
            assert denied.value.retryable is False
            assert len(responses) == 1 and responses[0].status_code == 403
            assert responses[0].json()["error"]["retryable"] is False
            await client.aclose()
    finally:
        await transport.aclose()
