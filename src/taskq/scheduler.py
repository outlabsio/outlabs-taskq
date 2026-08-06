"""Framework-neutral scheduler runtime and source-owned YAML manifests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from pathlib import Path
import socket
import time
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection
import yaml

from taskq.errors import TaskqConfigError
from taskq.protocol import (
    ManagedScheduleProfile,
    ScheduleActionResult,
    ScheduleDefinition,
    ScheduleJobTarget,
    SchedulerHealth,
    TargetIdentityProfile,
)
from taskq.schedules import evaluate_schedule, smear_offset_seconds
from taskq.sql.transport import SqlTaskqTransport
from taskq.transport import HousekeeperTransport

logger = logging.getLogger("taskq.scheduler")

_NAME_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"
_OWNER_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
_MAX_MANIFEST_BYTES = 1_048_576


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class ManifestSchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
    )
    queue: str = Field(pattern=r"^[a-z0-9_]{1,57}$")
    interval_seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    cron: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str = Field(default="UTC", min_length=1, max_length=255)
    catchup: Literal["skip", "fire_once", "fire_all"] = "skip"
    max_catchup: int = Field(default=1, ge=1, le=100)
    overlap: Literal["forbid", "allow"] = "forbid"
    max_lateness_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    state: Literal["active", "paused"] = "active"
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = Field(default=None, ge=0, le=1000)
    max_attempts: int | None = Field(default=None, ge=1, le=100)
    lease_seconds: int | None = Field(default=None, ge=15, le=86_400)
    backoff_mode: Literal["fixed", "exponential"] | None = None
    backoff_base: int | None = Field(default=None, ge=0, le=86_400)
    backoff_cap: int | None = Field(default=None, ge=0, le=604_800)
    concurrency_key: str | None = Field(default=None, min_length=1, max_length=255)
    affinity_key: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("headers")
    @classmethod
    def _reserved_header(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "taskq_schedule" in value:
            raise ValueError("taskq_schedule header is package-owned")
        return value

    @model_validator(mode="after")
    def _recurrence_shape(self) -> ManifestSchedule:
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("configure exactly one of interval_seconds or cron")
        if self.interval_seconds is not None and self.timezone != "UTC":
            raise ValueError("timezone applies only to cron schedules")
        if self.catchup == "skip" and self.max_catchup != 1:
            raise ValueError("skip schedules use max_catchup=1")
        return self

    def definition(self) -> ScheduleDefinition:
        recurrence: dict[str, Any]
        if self.interval_seconds is not None:
            recurrence = {"kind": "interval", "interval_seconds": self.interval_seconds}
        else:
            recurrence = {
                "kind": "cron",
                "expression": self.cron,
                "timezone": self.timezone,
            }
        target = ScheduleJobTarget(
            queue=self.queue,
            job_type=self.task,
            payload=self.payload,
            headers=self.headers,
            priority=self.priority,
            max_attempts=self.max_attempts,
            lease_seconds=self.lease_seconds,
            backoff_mode=self.backoff_mode,
            backoff_base=self.backoff_base,
            backoff_cap=self.backoff_cap,
            concurrency_key=self.concurrency_key,
            affinity_key=self.affinity_key,
        )
        return ScheduleDefinition(
            target=target,
            recurrence=recurrence,
            catchup_policy=self.catchup,
            max_catchup=self.max_catchup,
            paused=self.state == "paused",
        )


class ScheduleManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    namespace: str = Field(pattern=_OWNER_PATTERN)
    source: str = Field(pattern=_OWNER_PATTERN)
    schedules: dict[str, ManifestSchedule] = Field(min_length=1, max_length=500)

    @field_validator("schedules")
    @classmethod
    def _stable_keys(
        cls, value: dict[str, ManifestSchedule], info: Any
    ) -> dict[str, ManifestSchedule]:
        namespace = info.data.get("namespace")
        for key in value:
            if not key or len(key.encode("utf-8")) > 120:
                raise ValueError("manifest key must be 1..120 UTF-8 bytes")
            import re

            if re.fullmatch(_NAME_PATTERN, key) is None:
                raise ValueError(f"invalid manifest key: {key!r}")
            if namespace is not None and len(f"{namespace}.{key}".encode("utf-8")) > 120:
                raise ValueError("combined namespace and manifest key exceed 120 UTF-8 bytes")
        return value


class CompiledSchedule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    manifest_key: str
    display_name: str
    definition: ScheduleDefinition
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    overlap_policy: Literal["forbid", "allow"]
    max_lateness_seconds: int | None


class ManifestPlanEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    action: Literal["create", "update", "unchanged", "drift"]
    current_version: int | None = None
    reason: str | None = None


class ManifestPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str
    source: str
    entries: tuple[ManifestPlanEntry, ...]
    warnings: tuple[str, ...] = ()

    @property
    def changes(self) -> int:
        return sum(entry.action in {"create", "update"} for entry in self.entries)


class ManifestApplyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str
    source: str
    created: int
    updated: int
    unchanged: int
    drift: int


def load_manifest(path: str | Path) -> ScheduleManifest:
    manifest_path = Path(path)
    data = manifest_path.read_bytes()
    if len(data) > _MAX_MANIFEST_BYTES:
        raise TaskqConfigError("schedule manifest exceeds 1 MiB")
    try:
        decoded = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
        return ScheduleManifest.model_validate(decoded)
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise TaskqConfigError("invalid schedule manifest") from exc


def _canonical_hash(
    definition: ScheduleDefinition,
    *,
    overlap_policy: str,
    max_lateness_seconds: int | None,
) -> str:
    canonical = {
        **definition.model_dump(mode="json", exclude_none=True),
        "overlap": overlap_policy,
        "max_lateness_seconds": max_lateness_seconds,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compile_manifest(manifest: ScheduleManifest) -> dict[str, CompiledSchedule]:
    compiled: dict[str, CompiledSchedule] = {}
    for key, item in sorted(manifest.schedules.items()):
        definition = item.definition()
        compiled[key] = CompiledSchedule(
            name=f"{manifest.namespace}.{key}",
            manifest_key=key,
            display_name=item.display_name or key,
            definition=definition,
            definition_hash=_canonical_hash(
                definition,
                overlap_policy=item.overlap,
                max_lateness_seconds=item.max_lateness_seconds,
            ),
            overlap_policy=item.overlap,
            max_lateness_seconds=item.max_lateness_seconds,
        )
    return compiled


def _profile_actual_hash(profile: ManagedScheduleProfile) -> str | None:
    try:
        definition = ScheduleDefinition.model_validate(
            {
                "target": profile.target,
                "recurrence": profile.recurrence,
                "catchup_policy": profile.catchup_policy,
                "max_catchup": profile.max_catchup,
                "paused": profile.state == "paused",
            }
        )
    except ValueError:
        return None
    return _canonical_hash(
        definition,
        overlap_policy=profile.overlap_policy,
        max_lateness_seconds=profile.max_lateness_seconds,
    )


async def _current_owned(
    transport: SqlTaskqTransport,
    namespace: str,
    source: str,
    *,
    connection: AsyncConnection | None = None,
) -> dict[str, ManagedScheduleProfile]:
    result: dict[str, ManagedScheduleProfile] = {}
    after: str | None = None
    while True:
        page = await transport.list_managed_schedules(
            namespace, source, limit=500, after_name=after, connection=connection
        )
        for profile in page:
            result[profile.manifest_key] = profile
        if len(page) < 500:
            return result
        after = page[-1].name


async def plan_manifest(
    transport: SqlTaskqTransport,
    manifest: ScheduleManifest,
    *,
    connection: AsyncConnection | None = None,
) -> ManifestPlan:
    desired = compile_manifest(manifest)
    current = await _current_owned(
        transport, manifest.namespace, manifest.source, connection=connection
    )
    entries: list[ManifestPlanEntry] = []
    warnings: list[str] = []
    missing_by_hash = {
        profile.definition_hash: key for key, profile in current.items() if key not in desired
    }
    for key, compiled in desired.items():
        profile = current.get(key)
        if profile is None:
            entries.append(ManifestPlanEntry(key=key, name=compiled.name, action="create"))
            previous = missing_by_hash.get(compiled.definition_hash)
            if previous is not None:
                warnings.append(
                    f"{key}: definition matches missing owned key {previous}; possible rename"
                )
            continue
        actual_hash = _profile_actual_hash(profile)
        if actual_hash is None:
            action = "update"
            reason = "live definition is invalid"
        elif profile.definition_hash != actual_hash:
            action = "update"
            reason = "stored definition hash does not match live definition"
        elif compiled.definition_hash != actual_hash:
            action = "update"
            reason = "desired definition differs"
        elif compiled.display_name != profile.display_name:
            action = "update"
            reason = "display name differs"
        else:
            action = "unchanged"
            reason = None
        entries.append(
            ManifestPlanEntry(
                key=key,
                name=compiled.name,
                action=action,
                current_version=profile.version,
                reason=reason,
            )
        )
    for key, profile in sorted(current.items()):
        if key not in desired and profile.state != "retired":
            entries.append(
                ManifestPlanEntry(
                    key=key,
                    name=profile.name,
                    action="drift",
                    current_version=profile.version,
                    reason="owned key is absent from the manifest; explicit retirement required",
                )
            )
    return ManifestPlan(
        namespace=manifest.namespace,
        source=manifest.source,
        entries=tuple(entries),
        warnings=tuple(warnings),
    )


async def apply_manifest(
    transport: SqlTaskqTransport,
    manifest: ScheduleManifest,
    *,
    actor: str,
    connection: AsyncConnection | None = None,
) -> ManifestApplyResult:
    plan = await plan_manifest(transport, manifest, connection=connection)
    compiled = compile_manifest(manifest)
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for entry in plan.entries:
        if entry.action == "drift":
            continue
        item = compiled[entry.key]
        result = await transport.put_managed_schedule(
            item.name,
            item.definition,
            namespace=manifest.namespace,
            source=manifest.source,
            manifest_key=item.manifest_key,
            display_name=item.display_name,
            definition_hash=item.definition_hash,
            overlap_policy=item.overlap_policy,
            max_lateness_seconds=item.max_lateness_seconds,
            actor=actor,
            expected_version=entry.current_version,
            connection=connection,
        )
        counts[result.outcome] += 1
    return ManifestApplyResult(
        namespace=manifest.namespace,
        source=manifest.source,
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        drift=sum(entry.action == "drift" for entry in plan.entries),
    )


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        extra="forbid",
        env_prefix="TASKQ_",
        env_file=None,
    )

    dsn: SecretStr
    expected_environment: str = Field(
        min_length=1,
        max_length=63,
        validation_alias=AliasChoices("expected_environment", "TASKQ_EXPECTED_ENV"),
    )
    expected_installation_id: UUID | None = None
    allow_production: bool = False
    worker_id: str | None = Field(default=None, min_length=1, max_length=200)
    poll_interval: float = Field(default=5.0, ge=0.1, le=3600)
    jitter: float = Field(default=0.1, ge=0, le=0.5)
    backoff_cap: float = Field(default=30.0, ge=0.1, le=3600)
    claim_limit: int = Field(default=10, ge=1, le=100)
    lease_seconds: int = Field(default=60, ge=5, le=300)
    error_retry_seconds: int = Field(default=30, ge=1, le=3600)
    pool_size: int = Field(default=2, ge=1, le=100)

    @model_validator(mode="after")
    def _production_interlock(self) -> SchedulerSettings:
        if self.expected_environment == "production" and not self.allow_production:
            raise ValueError("production requires allow_production=True")
        if self.expected_environment == "production" and self.expected_installation_id is None:
            raise ValueError("production requires expected_installation_id")
        return self


class SchedulerRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["nothing_due", "fired", "budget_exhausted"]
    batches: int
    schedules_claimed: int
    jobs_enqueued: int
    errors_recorded: int


class SchedulerDoctorReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ready: bool
    target: TargetIdentityProfile
    health: SchedulerHealth | None = None
    issues: tuple[str, ...] = ()


async def scheduler_doctor(
    transport: SqlTaskqTransport,
    *,
    expected_environment: str | None = None,
    expected_installation_id: UUID | None = None,
    allow_production: bool = False,
) -> SchedulerDoctorReport:
    """Inspect identity and advancement state without mutating or attesting."""

    target = await transport.get_target_identity()
    active = set(target.capabilities.get("active", []))
    issues: list[str] = []
    if target.environment == "unbound":
        issues.append("target is unbound")
    if expected_environment and target.environment != expected_environment:
        issues.append("target environment does not match the expected environment")
    if expected_installation_id and target.installation_id != expected_installation_id:
        issues.append("target installation does not match the expected installation")
    if target.environment == "production" and not allow_production:
        issues.append("production is not explicitly allowed")
    if "target_attestation" not in active:
        issues.append("target_attestation capability is not active")
    if "scheduler_v2" not in active:
        issues.append("scheduler_v2 capability is not active")
        health = None
    else:
        health = await transport.get_scheduler_health()
    return SchedulerDoctorReport(
        ready=not issues,
        target=target,
        health=health,
        issues=tuple(issues),
    )


class SchedulerEngine:
    def __init__(
        self,
        transport: HousekeeperTransport,
        worker_id: str,
        *,
        claim_limit: int = 10,
        lease_seconds: int = 60,
        error_retry_seconds: int = 30,
    ) -> None:
        self.transport = transport
        self.worker_id = worker_id
        self.claim_limit = claim_limit
        self.lease_seconds = lease_seconds
        self.error_retry_seconds = error_retry_seconds

    async def process_batch(self) -> tuple[int, int, int]:
        batch = await self.transport.claim_schedules(
            self.worker_id, limit=self.claim_limit, lease_seconds=self.lease_seconds
        )
        jobs = 0
        errors = 0
        for claim in batch.schedules:
            try:
                evaluation = evaluate_schedule(
                    recurrence=claim.recurrence,
                    catchup_policy=claim.catchup_policy.value,
                    max_catchup=claim.max_catchup,
                    initialized=claim.initialized,
                    next_fire_at=claim.next_fire_at,
                    as_of=claim.as_of,
                    smear_offset_seconds=smear_offset_seconds(
                        claim.schedule_id, claim.smear_seconds
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.transport.schedule_error(
                    claim.schedule_id,
                    claim.token,
                    claim.definition_version,
                    f"calendar:{type(exc).__name__}",
                    retry_seconds=self.error_retry_seconds,
                    deterministic=True,
                )
                errors += 1
                continue
            result: ScheduleActionResult = await self.transport.fire_schedule(
                claim.schedule_id,
                claim.token,
                claim.definition_version,
                evaluation.occurrences,
                evaluation.next_fire_at,
            )
            jobs += int(getattr(result, "jobs_enqueued", 0))
        return len(batch.schedules), jobs, errors


class SchedulerService:
    def __init__(
        self,
        transport: HousekeeperTransport,
        engine: SchedulerEngine,
        *,
        poll_interval: float = 5.0,
        jitter: float = 0.1,
        backoff_cap: float = 30.0,
    ) -> None:
        self.transport = transport
        self.engine = engine
        self.poll_interval = poll_interval
        self.jitter = jitter
        self.backoff_cap = backoff_cap
        self.stop_requested = asyncio.Event()

    def stop(self) -> None:
        self.stop_requested.set()

    async def run_once(
        self, *, max_batches: int = 100, max_runtime_seconds: float = 300
    ) -> SchedulerRunSummary:
        if max_batches < 1 or max_runtime_seconds <= 0:
            raise TaskqConfigError("once budgets must be positive")
        started = time.monotonic()
        batches = claimed = jobs = errors = 0
        if self.stop_requested.is_set():
            return SchedulerRunSummary(
                outcome="nothing_due",
                batches=0,
                schedules_claimed=0,
                jobs_enqueued=0,
                errors_recorded=0,
            )
        await self.transport.tick()
        while batches < max_batches and time.monotonic() - started < max_runtime_seconds:
            if self.stop_requested.is_set():
                break
            batch_claimed, batch_jobs, batch_errors = await self.engine.process_batch()
            if batch_claimed == 0:
                return SchedulerRunSummary(
                    outcome="nothing_due" if claimed == 0 else "fired",
                    batches=batches,
                    schedules_claimed=claimed,
                    jobs_enqueued=jobs,
                    errors_recorded=errors,
                )
            batches += 1
            claimed += batch_claimed
            jobs += batch_jobs
            errors += batch_errors
        return SchedulerRunSummary(
            outcome="budget_exhausted",
            batches=batches,
            schedules_claimed=claimed,
            jobs_enqueued=jobs,
            errors_recorded=errors,
        )

    async def run(self) -> None:
        backoff = self.poll_interval
        while not self.stop_requested.is_set():
            try:
                await self.transport.tick()
                if not self.stop_requested.is_set():
                    await self.engine.process_batch()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("scheduler.cycle_failed", extra={"error_type": type(exc).__name__})
                delay = backoff
                backoff = min(self.backoff_cap, max(self.poll_interval, backoff * 2))
            else:
                delay = self.poll_interval
                backoff = self.poll_interval
            delay *= 1 + random.uniform(-self.jitter, self.jitter)
            try:
                await asyncio.wait_for(self.stop_requested.wait(), timeout=max(0.01, delay))
            except TimeoutError:
                pass


def scheduler_from_settings(
    settings: SchedulerSettings,
) -> tuple[SqlTaskqTransport, SchedulerService]:
    transport = SqlTaskqTransport.from_dsn(
        settings.dsn.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=0,
        expected_environment=settings.expected_environment,
        expected_installation_id=settings.expected_installation_id,
        allow_production=settings.allow_production,
    )
    worker_id = settings.worker_id or f"scheduler:{socket.gethostname()}:{time.time_ns()}"
    engine = SchedulerEngine(
        transport,
        worker_id,
        claim_limit=settings.claim_limit,
        lease_seconds=settings.lease_seconds,
        error_retry_seconds=settings.error_retry_seconds,
    )
    return transport, SchedulerService(
        transport,
        engine,
        poll_interval=settings.poll_interval,
        jitter=settings.jitter,
        backoff_cap=settings.backoff_cap,
    )


__all__ = [
    "CompiledSchedule",
    "ManifestApplyResult",
    "ManifestPlan",
    "ManifestPlanEntry",
    "ManifestSchedule",
    "ScheduleManifest",
    "SchedulerEngine",
    "SchedulerDoctorReport",
    "SchedulerRunSummary",
    "SchedulerService",
    "SchedulerSettings",
    "apply_manifest",
    "compile_manifest",
    "load_manifest",
    "plan_manifest",
    "scheduler_from_settings",
    "scheduler_doctor",
]
