"""Smoke an installed distribution from outside the source checkout (R3-F05)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg

_A17_INITIAL_CHECKSUM = "6d5b8196c091bbf08a2ea5ddec99eb5d386a018c462761caee15dad54f0571e3"


def _database_dsn(dsn: str, database: str) -> str:
    parts = urlsplit(dsn)
    scheme = parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _create_database(admin_dsn: str, database: str) -> None:
    conn = await asyncpg.connect(_database_dsn(admin_dsn, "postgres"))
    try:
        await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


async def _drop_database(admin_dsn: str, database: str) -> None:
    conn = await asyncpg.connect(_database_dsn(admin_dsn, "postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await conn.close()


async def _assert_activation(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        assert (
            await conn.fetchval(
                "SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'"
            )
            == "0.6.12"
        )
        assert await conn.fetchval("SELECT taskq.has_capability('workflow_bulk_admission')") is True
        assert await conn.fetchval("SELECT taskq.has_capability('workflow_continuations')") is True
        assert await conn.fetchval("SELECT taskq.has_capability('queue_counters')") is True
        assert await conn.fetchval("SELECT taskq.has_capability('scheduler_v2')") is True
        assert await conn.fetchval("SELECT taskq.has_capability('target_attestation')") is True
        assert await conn.fetchval("SELECT taskq.has_capability('read_model_job_views_v2')") is True
        assert await conn.fetchval("SELECT taskq.has_capability('read_model_job_events')") is True
        assert (
            await conn.fetchval("SELECT taskq.has_capability('read_model_workflow_list')") is True
        )
        assert await conn.fetchval("SELECT taskq.has_capability('operator_schedule_list')") is True
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM taskq.schema_migrations WHERE id='0018_trusted_effect_fence'"
            )
            == 1
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM taskq.schema_migrations "
                "WHERE id IN "
                "('0019_scheduler_target_identity','0020_standalone_scheduler',"
                "'0021_cli_read_model','0022_queue_counters',"
                "'0023_activate_queue_counters')"
            )
            == 5
        )
    finally:
        await conn.close()


async def _bind_target(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        identity = await conn.fetchrow("SELECT * FROM taskq.get_target_identity()")
        assert identity is not None and identity["environment"] == "unbound"
        await conn.fetchrow(
            "SELECT * FROM taskq.bind_target_identity($1,$2,$3,$4,$5,$6)",
            identity["installation_id"],
            "test",
            "artifact-smoke",
            identity["binding_version"],
            False,
            None,
        )
    finally:
        await conn.close()


async def _set_initial_checksum(dsn: str, checksum: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE taskq.schema_migrations SET checksum = $1 WHERE id = '0001_initial'",
            checksum,
        )
    finally:
        await conn.close()


async def _prepare_inactive_upgrade(admin_dsn: str, database: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from taskq.sql import _migrate_impl, discover_migrations

    await _create_database(admin_dsn, database)
    engine_dsn = _database_dsn(admin_dsn, database).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    engine = create_async_engine(engine_dsn)
    try:
        async with engine.connect() as conn:
            migrations = discover_migrations()
            inactive_end = next(
                index
                for index, migration in enumerate(migrations)
                if migration.id == "0017_activate_workflow_continuations"
            )
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:inactive_end])
            )
            assert applied[-1] == "0016_workflow_continuations"
            assert (
                await conn.exec_driver_sql("SELECT taskq.has_capability('workflow_continuations')")
            ).scalar_one() is False
    finally:
        await engine.dispose()


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_json(command: list[str], *, cwd: Path) -> dict[str, object]:
    return json.loads(_run(command, cwd=cwd).stdout)


def _migration_plan(taskq_cli: Path, dsn: str, *, cwd: Path) -> str:
    envelope = _run_json(
        [
            str(taskq_cli),
            "--dsn",
            dsn,
            "--actor",
            "artifact-smoke",
            "db",
            "plan",
            "-o",
            "json",
        ],
        cwd=cwd,
    )
    data = envelope["data"]
    assert isinstance(data, dict)
    digest = data["plan_digest"]
    assert isinstance(digest, str)
    return digest


def _migrate(taskq_cli: Path, dsn: str, digest: str, *, cwd: Path) -> dict[str, object]:
    return _run_json(
        [
            str(taskq_cli),
            "--dsn",
            dsn,
            "--actor",
            "artifact-smoke",
            "--expected-environment",
            "test",
            "--yes",
            "db",
            "migrate",
            "--plan-digest",
            digest,
            "-o",
            "json",
        ],
        cwd=cwd,
    )


def _verify(taskq_cli: Path, dsn: str, *, cwd: Path) -> dict[str, object]:
    return _run_json(
        [str(taskq_cli), "--dsn", dsn, "db", "verify", "-o", "json"],
        cwd=cwd,
    )


_OLD_OWNER_ID = "0046_queue_admission_owner"
_OLD_OWNER_SHA256 = "7cc1aca7fe508c49886f808eb4dca98d52a21fd269a2b38e8b455370cc5562b9"
_OWNER_UPGRADE_ID = "0047_queue_admission_owner_upgrade"
_OWNER_RECOVERY_ID = "0048_queue_admission_owner_recovery"


def _owner_upgrade_roles(database: str) -> dict[str, str]:
    return {
        name: f"pr52_{name}_{database[-12:]}" for name in ("owner", "other", "runner", "operator")
    }


async def _prepare_owner_upgrade(admin_dsn: str, database: str, repo: Path) -> None:
    """Seed exact deployed 0046, never the candidate's replacement definitions."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from taskq.sql import Migration, _migrate_impl, discover_migrations

    original = (repo / "tests/fixtures/0046_queue_admission_owner.original.sql").read_bytes()
    assert hashlib.sha256(original).hexdigest() == _OLD_OWNER_SHA256
    migrations = discover_migrations()[:45] + [
        Migration(_OLD_OWNER_ID, _OLD_OWNER_ID + ".sql", _OLD_OWNER_SHA256, original.decode())
    ]
    await _create_database(admin_dsn, database)
    dsn = _database_dsn(admin_dsn, database)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as conn:
            await conn.run_sync(lambda sync: _migrate_impl(sync, migrations[:19]))
        await _bind_target(dsn)
        async with engine.connect() as conn:
            await conn.run_sync(lambda sync: _migrate_impl(sync, migrations[19:]))
    finally:
        await engine.dispose()

    admin = await asyncpg.connect(dsn)
    connections = {}
    roles = _owner_upgrade_roles(database)
    try:
        for name, role in roles.items():
            await admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'pr52-scratch'")
            capability = "producer" if name in ("owner", "other") else name
            await admin.execute(f'GRANT taskq_{capability} TO "{role}"')
            connections[name] = await asyncpg.connect(dsn, user=role, password="pr52-scratch")
            assert await connections[name].fetchval("SELECT session_user") == role
            assert not await admin.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=$1", role)
        owner, other, runner, operator = (connections[name] for name in roles)
        installation = await admin.fetchval(
            "SELECT installation_id FROM taskq.get_target_identity()"
        )
        for queue in ("upgrade_bound", "upgrade_target", "upgrade_plain"):
            await operator.fetchrow(
                "SELECT * FROM taskq.ensure_queue($1,'{\"max_depth\":1000}'::jsonb,'upgrade-smoke')",
                queue,
            )
            if queue != "upgrade_plain":
                await operator.fetchval(
                    "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
                    queue,
                    roles["owner"],
                    installation,
                )
        await owner.fetchrow(
            "SELECT * FROM taskq.enqueue('upgrade_bound','pr52.terminal','{\"private\":true}'::jsonb)"
        )
        async with runner.transaction():
            await runner.fetchrow("SELECT * FROM taskq.attest_target('test',NULL,false)")
            terminal = (
                await runner.fetchrow(
                    "SELECT * FROM taskq.claim_jobs('upgrade_bound','upgrade-runner')"
                )
            )["jobs"][0]
        await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,'upgrade-runner')",
            terminal["job_id"],
            terminal["attempt_id"],
        )
        workflow = await owner.fetchrow(
            "SELECT * FROM taskq.create_workflow('upgrade-workflow','dag','{}'::jsonb,"
            "ARRAY['upgrade_bound','upgrade_target'],'upgrade-smoke',3,$1)",
            "f" * 64,
        )
        await owner.fetchrow(
            "SELECT * FROM taskq.enqueue('upgrade_bound','pr52.workflow','{}'::jsonb,"
            "p_workflow_id=>$1,p_step_key=>'root',p_flow_key=>'provider-flow')",
            workflow["workflow_id"],
        )
        await owner.fetchrow(
            "SELECT * FROM taskq.seal_workflow($1,'upgrade-smoke')", workflow["workflow_id"]
        )
        for key in ("reserved", "cancelled", "admitted"):
            reserved = await owner.fetchrow(
                "SELECT * FROM taskq.reserve_admission('upgrade_bound',$1,$2,$3)",
                key,
                "a" * 64,
                uuid4(),
            )
            if key == "cancelled":
                await owner.fetchrow(
                    "SELECT * FROM taskq.cancel_admission('upgrade_bound',$1,$2)",
                    key,
                    reserved["handle"],
                )
            elif key == "admitted":
                await owner.fetchrow(
                    "SELECT * FROM taskq.finish_admission('upgrade_bound',$1,$2,"
                    '\'{"job_type":"pr52.receipt","payload":{}}\'::jsonb,\'{"host_receipt":true}\'::jsonb)',
                    key,
                    reserved["handle"],
                )
        # Prove this really is the old vulnerable state, not the fixed candidate.
        await other.fetchrow(
            "SELECT * FROM taskq.reserve_admission('upgrade_bound','legacy-foreign',$1,$2)",
            "a" * 64,
            uuid4(),
        )
        leaked = await other.fetchrow(
            "SELECT * FROM taskq.lock_terminal_effect_job($1,'upgrade_bound','pr52.terminal','test',$2,false)",
            terminal["job_id"],
            installation,
        )
        assert json.loads(leaked["payload"]) == {"private": True}
        for queue in ("upgrade_bound", "upgrade_target", "upgrade_plain"):
            await operator.fetchval(
                "SELECT taskq.pause_queue($1,'upgrade-smoke','preserve pause')", queue
            )
        await admin.execute(
            "CREATE TABLE public.api_reservations(id uuid PRIMARY KEY, receipt jsonb NOT NULL);"
            "CREATE VIEW public.pr52_dependencies AS SELECT "
            "taskq.reserve_admission('q','k',repeat('a',64),NULL::uuid) AS reserved,"
            "taskq.finish_admission('q','k',NULL::uuid,'{}'::jsonb) AS finished,"
            "taskq.cancel_admission('q','k',NULL::uuid) AS cancelled,"
            "(SELECT payload FROM taskq.lock_terminal_effect_job(NULL::uuid,'q','t','test',NULL::uuid,false)) AS terminal"
        )
        await admin.execute(
            "INSERT INTO public.api_reservations VALUES ($1,'{\"preserve\":true}')", uuid4()
        )
    finally:
        for conn in connections.values():
            await conn.close()
        await admin.close()


async def _owner_upgrade_snapshot(dsn: str, database: str) -> dict[str, object]:
    """Compare complete small fixture rows and identity/ACL metadata, not counts alone."""
    conn = await asyncpg.connect(dsn)
    try:
        queries = {
            "functions": "SELECT oid,oid::regprocedure::text AS identity,proowner,proacl,proconfig,prosecdef FROM pg_proc WHERE pronamespace='taskq'::regnamespace",
            "relations": "SELECT oid,relname,relowner,relacl FROM pg_class WHERE relnamespace='taskq'::regnamespace OR oid IN ('public.pr52_dependencies'::regclass,'public.api_reservations'::regclass)",
            "schema": "SELECT oid,nspowner,nspacl FROM pg_namespace WHERE nspname='taskq'",
            "triggers": "SELECT oid,tgrelid,tgfoid,tgname,tgenabled FROM pg_trigger WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace='taskq'::regnamespace)",
            "roles": "SELECT * FROM pg_roles WHERE rolname LIKE 'taskq_%' OR rolname LIKE 'pr52_%'",
            "memberships": "SELECT * FROM pg_auth_members",
            "host_reservations": "SELECT * FROM public.api_reservations",
            "view": "SELECT pg_get_viewdef('public.pr52_dependencies'::regclass) AS definition",
        }
        for table in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='taskq'"):
            name = table["tablename"]
            query = f'SELECT * FROM taskq."{name}"'
            if name == "meta":
                query += " WHERE key <> 'contract_version'"
            queries[name] = query
        snapshot = {}
        for key, query in queries.items():
            snapshot[key] = json.loads(
                await conn.fetchval(
                    f"SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),'[]'::jsonb) FROM ({query}) r"
                )
            )
        assert len(snapshot["jobs"]) >= 3 and snapshot["job_attempts"] and snapshot["workflows"]
        assert len(snapshot["admissions"]) == 4 and snapshot["host_reservations"]
        assert all(
            row["paused_at"] is not None and row["max_depth"] == 1000 for row in snapshot["queues"]
        )
        assert snapshot["target_identity"] and snapshot["target_binding_events"]
        assert (
            await conn.fetchval(
                "SELECT admission_owner_role FROM taskq.queues WHERE name='upgrade_bound'"
            )
            == _owner_upgrade_roles(database)["owner"]
        )
        return snapshot
    finally:
        await conn.close()


async def _exercise_upgraded_owner_rows(dsn: str, database: str) -> None:
    """Pre-upgrade receipts, terminal jobs and queued workflow parents still work."""
    from taskq.errors import taskq_error_from_exception
    from taskq.protocol import TQ_ERROR_REGISTRY, TqCode

    admin = await asyncpg.connect(dsn)
    connections = {}
    try:
        for name, role in _owner_upgrade_roles(database).items():
            connections[name] = await asyncpg.connect(dsn, user=role, password="pr52-scratch")
        owner, other, runner, operator = (connections[name] for name in connections)
        replay = await owner.fetchrow(
            "SELECT * FROM taskq.reserve_admission('upgrade_bound','admitted',$1,$2)",
            "a" * 64,
            uuid4(),
        )
        assert replay["outcome"] == "admitted"
        assert json.loads(replay["receipt"]) == {"host_receipt": True}
        installation = await admin.fetchval(
            "SELECT installation_id FROM taskq.get_target_identity()"
        )
        terminal = await admin.fetchval("SELECT id FROM taskq.jobs WHERE job_type='pr52.terminal'")
        for sql, args in (
            (
                "SELECT * FROM taskq.reserve_admission('upgrade_bound','admitted',$1,$2)",
                ("a" * 64, uuid4()),
            ),
            (
                "SELECT * FROM taskq.lock_terminal_effect_job($1,'upgrade_bound','pr52.terminal','test',$2,false)",
                (terminal, installation),
            ),
        ):
            try:
                await other.fetchrow(sql, *args)
            except asyncpg.PostgresError as source:
                error = taskq_error_from_exception(source)
                assert error.code.value == "TQ425" and not error.retryable
            else:
                raise AssertionError("upgraded pre-existing owner data disclosed")
        for state in ("TQ403", "TQ425"):
            spec = TQ_ERROR_REGISTRY[TqCode(state)]
            assert spec.http_status == 403 and not spec.retryable
        await operator.fetchval("SELECT taskq.resume_queue('upgrade_bound','upgrade-smoke')")
        async with runner.transaction():
            await runner.fetchrow("SELECT * FROM taskq.attest_target('test',NULL,false)")
            parent = (
                await runner.fetchrow(
                    "SELECT * FROM taskq.claim_jobs('upgrade_bound','upgrade-runner',1,NULL,NULL,NULL,NULL,ARRAY[$1],false)",
                    "f" * 64,
                )
            )["jobs"][0]
        result = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,'upgrade-runner',NULL,NULL,$3::jsonb,$4)",
            parent["job_id"],
            parent["attempt_id"],
            json.dumps(
                [
                    {
                        "step": "child",
                        "job_type": "pr52.child",
                        "queue": "upgrade_target",
                        "workflow_member": True,
                    }
                ]
            ),
            "f" * 64,
        )
        assert result["result"] == "ok"
        child = await admin.fetchrow(
            "SELECT * FROM taskq.jobs WHERE parent_job_id=$1", parent["job_id"]
        )
        assert child["queue"] == "upgrade_target" and child["flow_key"] == "provider-flow"
        assert child["workflow_id"] == await admin.fetchval(
            "SELECT workflow_id FROM taskq.jobs WHERE id=$1", parent["job_id"]
        )
    finally:
        for conn in connections.values():
            await conn.close()
        await admin.close()


async def _drop_owner_upgrade(admin_dsn: str, database: str) -> None:
    await _drop_database(admin_dsn, database)
    conn = await asyncpg.connect(_database_dsn(admin_dsn, "postgres"))
    try:
        for role in _owner_upgrade_roles(database).values():
            await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
    finally:
        await conn.close()


def _exercise_owner_upgrade(taskq_cli: Path, admin_dsn: str, database: str, *, cwd: Path) -> None:
    """Run the normal packaged CLI; no ledger rewrites or direct corrective SQL."""
    from taskq.sql import discover_migrations

    dsn = _database_dsn(admin_dsn, database)
    before = asyncio.run(_owner_upgrade_snapshot(dsn, database))
    old_ledger = before.pop("schema_migrations")
    assert len(old_ledger) == 46
    assert (
        next(row for row in old_ledger if row["id"] == _OLD_OWNER_ID)["checksum"]
        == _OLD_OWNER_SHA256
    )
    digest = _migration_plan(taskq_cli, dsn, cwd=cwd)
    result = _migrate(taskq_cli, dsn, digest, cwd=cwd)
    assert result["data"]["applied"] == [_OWNER_UPGRADE_ID, _OWNER_RECOVERY_ID], result
    assert _verify(taskq_cli, dsn, cwd=cwd)["ok"] is True
    after = asyncio.run(_owner_upgrade_snapshot(dsn, database))
    new_ledger = after.pop("schema_migrations")
    new_ids = {_OWNER_UPGRADE_ID, _OWNER_RECOVERY_ID}
    assert [row for row in new_ledger if row["id"] not in new_ids] == old_ledger
    additions = {row["id"]: row for row in new_ledger if row["id"] in new_ids}
    migrations = {migration.id: migration for migration in discover_migrations()}
    assert set(additions) == new_ids
    assert all(additions[key]["checksum"] == migrations[key].checksum for key in new_ids)

    # 0048 adds identity metadata and catalog objects, so compare durable host
    # and TaskQ application rows separately from their expected schema growth.
    for key in (
        "host_reservations",
        "view",
        "jobs",
        "job_attempts",
        "workflows",
        "admissions",
        "target_identity",
        "target_binding_events",
        "roles",
        "memberships",
        "schema",
    ):
        assert before[key] == after[key], key
    for table in ("queues", "schedules"):
        normalized = []
        for row in after[table]:
            row = dict(row)
            row.pop("admission_owner_oid", None)
            normalized.append(row)
        assert before[table] == normalized, table
    before_functions = {row["identity"]: row["oid"] for row in before["functions"]}
    after_functions = {row["identity"]: row["oid"] for row in after["functions"]}
    assert all(after_functions[identity] == oid for identity, oid in before_functions.items())
    before_relations = {row["relname"]: row["oid"] for row in before["relations"]}
    after_relations = {row["relname"]: row["oid"] for row in after["relations"]}
    assert all(after_relations[name] == oid for name, oid in before_relations.items())
    asyncio.run(_assert_activation(dsn))
    repeated = _migrate(taskq_cli, dsn, _migration_plan(taskq_cli, dsn, cwd=cwd), cwd=cwd)
    assert repeated["data"] == {"applied": [], "up_to_date": True}
    replay = asyncio.run(_owner_upgrade_snapshot(dsn, database))
    assert replay.pop("schema_migrations") == new_ledger
    assert replay == after
    assert _verify(taskq_cli, dsn, cwd=cwd)["ok"] is True
    asyncio.run(_exercise_upgraded_owner_rows(dsn, database))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("core", "http", "outlabs", "all"), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--admin-dsn")
    args = parser.parse_args()

    import taskq
    import taskq.client
    import taskq.errors
    import taskq.execution
    import taskq.protocol
    import taskq.registry
    import taskq.scheduler
    import taskq.settings
    import taskq.sql.transport
    import taskq.testing
    import taskq.transport
    import taskq.worker
    from taskq import (
        AdmissionFinishOutcome,
        AdmissionReserveOutcome,
        CancellationToken,
        Complete,
        Followup,
        FollowupTarget,
        JobContext,
        ScheduleDefinition,
        ScheduleState,
        TaskQ,
        Task,
        TaskRegistry,
        WorkerOptions,
        WorkerSupervisor,
        WorkflowKind,
        WorkflowPage,
        WorkflowReadProfile,
        WorkflowStateCounts,
        WorkflowResult,
        WorkflowStatus,
    )
    from pydantic import BaseModel
    from taskq.protocol import PROTOCOL_DOCUMENT_REVISION
    from taskq.sql import discover_migrations
    from taskq.sql.manifest import FUNCTIONS
    from taskq.testing import FakeTaskQClient, require_enqueued

    package_file = Path(taskq.__file__).resolve()
    repo = args.repo.resolve()
    assert not package_file.is_relative_to(repo), (package_file, repo)
    assert taskq.__version__ == "0.1.0a40"
    assert importlib.metadata.version("outlabs-taskq") == taskq.__version__
    assert "fastapi" not in sys.modules
    assert "outlabs_auth" not in sys.modules
    assert "pytest" not in sys.modules
    if args.mode == "core":
        assert importlib.util.find_spec("fastapi") is None
        assert importlib.util.find_spec("outlabs_auth") is None
        try:
            import taskq.http  # noqa: F401
        except ModuleNotFoundError as exc:
            assert str(exc) == ("taskq.http requires the HTTP extra: install 'outlabs-taskq[http]'")
        else:
            raise AssertionError("core-only taskq.http import must name the missing HTTP extra")
    elif args.mode == "http":
        assert importlib.util.find_spec("fastapi") is not None
        assert importlib.util.find_spec("outlabs_auth") is None
        import fastapi  # noqa: F401
        import taskq.http  # noqa: F401

        try:
            import taskq.http.outlabs  # noqa: F401
        except ModuleNotFoundError as exc:
            assert str(exc) == (
                "taskq.http.outlabs requires the OutLabs extra: install 'outlabs-taskq[outlabs]'"
            )
        else:
            raise AssertionError(
                "http-only taskq.http.outlabs import must name the missing OutLabs extra"
            )
    else:
        assert importlib.util.find_spec("fastapi") is not None
        assert importlib.util.find_spec("outlabs_auth") is not None
        if args.mode == "all":
            assert importlib.util.find_spec("pytest") is not None
        import fastapi  # noqa: F401
        import outlabs_auth  # noqa: F401
        import taskq.http  # noqa: F401

    assert TaskQ is not None
    assert TaskRegistry is not None
    assert Complete is not None
    assert Followup(step="artifact-child", job_type="artifact.child").model_dump(
        mode="json", exclude_none=True
    ) == {
        "step": "artifact-child",
        "job_type": "artifact.child",
        "payload": {},
        "headers": {},
    }
    assert PROTOCOL_DOCUMENT_REVISION == "1.0.18"

    class ArtifactInput(BaseModel):
        value: int

    class ArtifactOutput(BaseModel):
        value: int

    child = Task(
        name="artifact.child",
        queue="artifact_child",
        input_model=ArtifactInput,
        output_model=ArtifactOutput,
    )
    parent = Task(
        name="artifact.parent",
        queue="artifact",
        input_model=ArtifactInput,
        output_model=ArtifactOutput,
        followup_targets=(
            FollowupTarget(
                queue="artifact_child",
                job_type="artifact.child",
                workflow_member=True,
                continuation_revision="1",
            ),
        ),
    )
    continuation_policy = TaskRegistry((parent, child)).compile_continuation_policy((parent,))
    assert continuation_policy.reachable_queues == ("artifact", "artifact_child")
    assert len(continuation_policy.continuation_policy_hash) == 64
    assert CancellationToken is not None
    assert JobContext is not None
    assert WorkerOptions().concurrency == 1
    workflow = WorkflowResult(
        outcome="created",
        workflow_id=uuid4(),
        status=WorkflowStatus.RUNNING,
    )
    assert workflow.status is WorkflowStatus.RUNNING
    assert WorkflowKind.DAG.value == "dag"
    assert WorkflowPage is not None
    assert WorkflowReadProfile is not None
    assert WorkflowStateCounts is not None

    async def smoke_testing() -> None:
        fake = FakeTaskQClient(queues=("artifact",))
        facade = TaskQ(fake, validate_job_types=False)
        result = await facade.enqueue_raw(
            queue="artifact", job_type="artifact.testing", payload={"ok": True}
        )
        job = await require_enqueued(fake, job_type="artifact.testing")
        assert job.job_id == result.job_id
        reserved = await facade.reserve_admission("artifact", "artifact-key", "a" * 64)
        assert reserved.outcome is AdmissionReserveOutcome.RESERVED
        admitted = await facade.finish_admission(
            "artifact",
            "artifact-key",
            reserved.handle,
            {"job_type": "artifact.admitted", "payload": {"ok": True}},
            {"source": "artifact-smoke"},
        )
        assert admitted.outcome is AdmissionFinishOutcome.CREATED
        replay = await facade.reserve_admission("artifact", "artifact-key", "a" * 64)
        assert replay.outcome is AdmissionReserveOutcome.ADMITTED
        assert replay.job_id == admitted.job_id
        workflow = await facade.create_workflow(
            "artifact-workflow",
            "dag",
            declared_queues=("artifact",),
            actor="artifact-smoke",
        )
        parent = await facade.enqueue_raw(
            queue="artifact",
            job_type="artifact.workflow",
            payload={"ok": True},
            workflow_id=workflow.workflow_id,
            step_key="parent",
        )
        replayed_parent = await facade.enqueue_raw(
            queue="artifact",
            job_type="artifact.workflow",
            payload={"ok": True},
            workflow_id=workflow.workflow_id,
            step_key="parent",
        )
        assert replayed_parent.job_id == parent.job_id
        sealed = await facade.seal_workflow(workflow.workflow_id, actor="artifact-smoke")
        assert sealed.outcome == "sealed"
        schedule_definition = ScheduleDefinition.model_validate(
            {
                "target": {
                    "kind": "job",
                    "queue": "artifact",
                    "job_type": "artifact.scheduled",
                },
                "recurrence": {"kind": "interval", "interval_seconds": 60},
                "catchup_policy": "fire_all",
                "max_catchup": 1,
            }
        )
        schedule = await fake.put_schedule("artifact.minute", schedule_definition, "artifact-smoke")
        assert schedule.outcome == "created"
        assert (await fake.get_schedule("artifact.minute")).state is ScheduleState.ACTIVE
        retired = await fake.retire_schedule(
            "artifact.minute", schedule.profile.version, "artifact-smoke"
        )
        assert retired.outcome == "retired"
        assert retired.profile.state is ScheduleState.RETIRED
        await fake.worker_heartbeat(
            "artifact-worker",
            ("artifact",),
            version="artifact-smoke",
        )
        presence = await fake.list_worker_presence()
        assert [item.worker_id for item in presence.items] == ["artifact-worker"]
        assert presence.items[0].declared_queues == ("artifact",)

    asyncio.run(smoke_testing())

    supervisor = WorkerSupervisor(object(), TaskRegistry(), "artifact-smoke")  # type: ignore[arg-type]
    assert supervisor.available_slots == 0
    asyncio.run(supervisor.aclose())

    assert [migration.id for migration in discover_migrations()] == [
        "0001_initial",
        "0002_contract_0_1_1",
        "0003_contract_0_1_2",
        "0004_read_models",
        "0005_read_model_conformance",
        "0006_activate_ready_read_model",
        "0007_admission_reservations",
        "0008_followups",
        "0009_workflows",
        "0010_schedules",
        "0011_finite_projections",
        "0012_activate_finite_projections",
        "0013_workflow_page_composite_repair",
        "0014_worker_presence_projection",
        "0015_activate_worker_presence",
        "0016_workflow_continuations",
        "0017_activate_workflow_continuations",
        "0018_trusted_effect_fence",
        "0019_scheduler_target_identity",
        "0020_standalone_scheduler",
        "0021_cli_read_model",
        "0022_queue_counters",
        "0023_activate_queue_counters",
        "0024_flow_enforcement_producer",
        "0025_flow_enforcement_claim",
        "0026_flow_enforcement_enqueue",
        "0027_activate_flow_control",
        "0028_redrive_null_limit_guard",
        "0029_schedule_claim_smear",
        "0030_schedule_smear_write",
        "0031_circuit_breaker",
        "0032_activate_circuit_breaker",
        "0033_priority_aging",
        "0034_breaker_observability",
        "0035_breaker_rate_tripping",
        "0036_breaker_latency_tripping",
        "0037_queue_audit",
        "0038_breaker_settle_write_skip",
        "0039_queue_audit_prune",
        "0040_breaker_manual_window_reset",
        "0041_breaker_half_open_atomic",
        "0042_claim_order_index_restore",
        "0043_workflow_bulk_admission",
        "0044_continuation_flow_inheritance",
        "0045_terminal_effect_fence",
        "0046_queue_admission_owner",
        "0047_queue_admission_owner_upgrade",
        "0048_queue_admission_owner_recovery",
    ]
    assert len(FUNCTIONS) == 124
    assert {
        "taskq.adopt_queue_admission_owner(text,text,text,text,text,uuid,boolean)",
        "taskq.bind_queue_admission_owner(text,text,text,uuid,boolean)",
        "taskq.get_queue_admission_owner(text)",
        "taskq.get_queue_admission_owner_identity(text)",
        "taskq.lock_terminal_effect_job(uuid,text,text,text,uuid,boolean)",
        "taskq.rotate_queue_admission_owner(text,text,text,text,text,uuid,boolean)",
    } <= set(FUNCTIONS)

    if args.mode != "core":
        return
    if not args.admin_dsn:
        parser.error("--admin-dsn is required in core mode")

    # Keep the venv shim path; resolving it follows uv's interpreter symlink
    # out of the environment and loses the installed console scripts.
    bin_dir = Path(sys.executable).parent
    taskq_cli = bin_dir / "taskq"
    bench_cli = bin_dir / "taskq-bench"
    assert "Usage: taskq" in _run([str(taskq_cli), "--help"], cwd=Path.cwd()).stdout
    assert (
        "Usage: taskq worker" in _run([str(taskq_cli), "worker", "--help"], cwd=Path.cwd()).stdout
    )
    assert "usage: taskq-bench" in _run([str(bench_cli), "--help"], cwd=Path.cwd()).stdout

    database = f"taskq_artifact_{uuid4().hex}"
    asyncio.run(_create_database(args.admin_dsn, database))
    try:
        dsn = _database_dsn(args.admin_dsn, database)
        digest = _migration_plan(taskq_cli, dsn, cwd=Path.cwd())
        try:
            _migrate(taskq_cli, dsn, digest, cwd=Path.cwd())
        except subprocess.CalledProcessError as checkpoint:
            assert checkpoint.stderr
            surfaced = (checkpoint.stdout or "") + (checkpoint.stderr or "")
            assert "target bind" in surfaced, surfaced[:500]
        else:
            raise AssertionError("fresh install must stop at the unbound target checkpoint")
        asyncio.run(_bind_target(dsn))
        digest = _migration_plan(taskq_cli, dsn, cwd=Path.cwd())
        migrated = _migrate(taskq_cli, dsn, digest, cwd=Path.cwd())
        assert "0021_cli_read_model" in migrated["data"]["applied"]  # type: ignore[index]
        assert _verify(taskq_cli, dsn, cwd=Path.cwd())["ok"] is True
        asyncio.run(_assert_activation(dsn))
    finally:
        asyncio.run(_drop_database(args.admin_dsn, database))

    upgrade_database = f"taskq_artifact_upgrade_{uuid4().hex}"
    asyncio.run(_prepare_inactive_upgrade(args.admin_dsn, upgrade_database))
    try:
        upgrade_dsn = _database_dsn(args.admin_dsn, upgrade_database)
        asyncio.run(_set_initial_checksum(upgrade_dsn, _A17_INITIAL_CHECKSUM))
        digest = _migration_plan(taskq_cli, upgrade_dsn, cwd=Path.cwd())
        try:
            _migrate(taskq_cli, upgrade_dsn, digest, cwd=Path.cwd())
        except subprocess.CalledProcessError as checkpoint:
            assert checkpoint.stderr
            surfaced = (checkpoint.stdout or "") + (checkpoint.stderr or "")
            assert "target bind" in surfaced, surfaced[:500]
        else:
            raise AssertionError("upgrade must stop at the unbound target checkpoint")
        asyncio.run(_bind_target(upgrade_dsn))
        digest = _migration_plan(taskq_cli, upgrade_dsn, cwd=Path.cwd())
        migrated = _migrate(taskq_cli, upgrade_dsn, digest, cwd=Path.cwd())
        assert "0021_cli_read_model" in migrated["data"]["applied"]  # type: ignore[index]
        assert _verify(taskq_cli, upgrade_dsn, cwd=Path.cwd())["ok"] is True
        asyncio.run(_assert_activation(upgrade_dsn))
    finally:
        asyncio.run(_drop_database(args.admin_dsn, upgrade_database))

    owner_database = f"taskq_owner_upgrade_{uuid4().hex}"
    try:
        asyncio.run(_prepare_owner_upgrade(args.admin_dsn, owner_database, repo))
        _exercise_owner_upgrade(taskq_cli, args.admin_dsn, owner_database, cwd=Path.cwd())
    finally:
        asyncio.run(_drop_owner_upgrade(args.admin_dsn, owner_database))


if __name__ == "__main__":
    main()
