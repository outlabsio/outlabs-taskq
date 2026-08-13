"""Smoke an installed distribution from outside the source checkout (R3-F05)."""

from __future__ import annotations

import argparse
import asyncio
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
            == "0.6.6"
        )
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
    assert taskq.__version__ == "0.1.0a34"
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
    assert PROTOCOL_DOCUMENT_REVISION == "1.0.17"

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
    ]
    assert len(FUNCTIONS) == 114

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


if __name__ == "__main__":
    main()
