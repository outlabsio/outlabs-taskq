"""Framework-neutral scheduler, manifest, and diagnostics unit contract."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from taskq.errors import TaskqConfigError
from taskq.protocol import ScheduleClaimResult, SchedulerHealth, TargetIdentityProfile
from taskq.scheduler import (
    ScheduleManifest,
    SchedulerEngine,
    SchedulerService,
    apply_manifest,
    compile_manifest,
    load_manifest,
    plan_manifest,
    scheduler_doctor,
)

_INSTALLATION_ID = UUID("018f47b2-a6cd-7c64-8e19-123456789abc")


def _manifest() -> ScheduleManifest:
    return ScheduleManifest.model_validate(
        {
            "version": 1,
            "namespace": "qdarte",
            "source": "api",
            "schedules": {
                "cleanup": {
                    "task": "maintenance.cleanup",
                    "queue": "maintenance",
                    "interval_seconds": 3600,
                }
            },
        }
    )


def test_minimal_manifest_compiles_to_safe_stable_defaults() -> None:
    first = compile_manifest(_manifest())["cleanup"]
    second = compile_manifest(_manifest())["cleanup"]
    assert first.name == "qdarte.cleanup"
    assert first.definition.catchup_policy.value == "skip"
    assert first.definition.max_catchup == 1
    assert first.overlap_policy == "forbid"
    assert first.definition_hash == second.definition_hash


def test_manifest_loader_rejects_duplicate_keys_and_unknown_configuration(tmp_path: Path) -> None:
    path = tmp_path / "schedules.yaml"
    path.write_text(
        "version: 1\nnamespace: qdarte\nsource: api\nschedules:\n"
        "  cleanup:\n    task: maintenance.cleanup\n    queue: maintenance\n"
        "    interval_seconds: 60\n  cleanup:\n    task: maintenance.other\n"
        "    queue: maintenance\n    interval_seconds: 60\n",
        encoding="utf-8",
    )
    with pytest.raises(TaskqConfigError):
        load_manifest(path)


class _ManifestTransport:
    def __init__(self) -> None:
        self.puts = 0
        self.connections: list[object | None] = []

    async def list_managed_schedules(self, *_args: object, **_kwargs: object) -> list[object]:
        self.connections.append(_kwargs.get("connection"))
        return []

    async def put_managed_schedule(self, *_args: object, **_kwargs: object) -> object:
        self.puts += 1
        self.connections.append(_kwargs.get("connection"))
        return SimpleNamespace(outcome="created")


async def test_manifest_plan_and_apply_are_create_only_without_implicit_pruning() -> None:
    transport = _ManifestTransport()
    connection = object()
    plan = await plan_manifest(  # type: ignore[arg-type]
        transport, _manifest(), connection=connection
    )
    assert [(entry.key, entry.action) for entry in plan.entries] == [("cleanup", "create")]
    result = await apply_manifest(  # type: ignore[arg-type]
        transport, _manifest(), actor="test", connection=connection
    )
    assert (result.created, result.updated, result.drift, transport.puts) == (1, 0, 0, 1)
    assert transport.connections == [connection, connection, connection]


class _EmptyHousekeeper:
    def __init__(self) -> None:
        self.ticks = 0

    async def tick(self, _reap_limit: int = 100) -> dict[str, object]:
        self.ticks += 1
        return {}

    async def claim_schedules(self, *_args: object, **_kwargs: object) -> ScheduleClaimResult:
        return ScheduleClaimResult(state="empty", schedules=())


async def test_scheduler_once_is_bounded_and_exits_when_nothing_is_due() -> None:
    transport = _EmptyHousekeeper()
    engine = SchedulerEngine(transport, "scheduler:test")  # type: ignore[arg-type]
    service = SchedulerService(transport, engine)  # type: ignore[arg-type]
    result = await service.run_once(max_batches=2, max_runtime_seconds=1)
    assert result.outcome == "nothing_due"
    assert result.batches == 0
    assert transport.ticks == 1


async def test_scheduler_once_honors_an_existing_stop_before_tick() -> None:
    transport = _EmptyHousekeeper()
    engine = SchedulerEngine(transport, "scheduler:test")  # type: ignore[arg-type]
    service = SchedulerService(transport, engine)  # type: ignore[arg-type]
    service.stop()
    result = await service.run_once(max_batches=2, max_runtime_seconds=1)
    assert result.outcome == "nothing_due"
    assert result.batches == 0
    assert transport.ticks == 0


class _DoctorTransport:
    def __init__(self, *, environment: str = "staging") -> None:
        self.environment = environment

    async def get_target_identity(self) -> TargetIdentityProfile:
        return TargetIdentityProfile(
            installation_id=_INSTALLATION_ID,
            environment=self.environment,
            binding_version=1,
            bound_at=datetime(2026, 8, 3, tzinfo=UTC),
            bound_by="release-agent",
            contract_version="0.3.0",
            capabilities={"active": ["scheduler_v2", "target_attestation"]},
        )

    async def get_scheduler_health(self) -> SchedulerHealth:
        return SchedulerHealth(
            database_time=datetime(2026, 8, 3, tzinfo=UTC),
            active_schedules=1,
            due_schedules=0,
            auto_paused_schedules=0,
        )


async def test_scheduler_doctor_reports_mismatch_without_mutation() -> None:
    report = await scheduler_doctor(  # type: ignore[arg-type]
        _DoctorTransport(), expected_environment="production"
    )
    assert report.ready is False
    assert report.health is not None
    assert report.issues == ("target environment does not match the expected environment",)
