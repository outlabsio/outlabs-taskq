"""Consumer test helpers; fake behavior is not PostgreSQL protocol proof."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from taskq.client import TaskQ
from taskq.errors import (
    TaskqConfigError,
    TaskqConflictError,
    TaskqInternalError,
    TaskqNotFoundError,
    TaskqValidationError,
)
from taskq.execution import Cancel, Complete, HandlerResult, NonRetryable, Retry, Snooze
from taskq.protocol import (
    ADMISSION_RESERVATION_ADAPTER,
    AdmissionCancelOutcome,
    AdmissionCancelRequest,
    AdmissionCancelResult,
    AdmissionFinishOutcome,
    AdmissionFinishRequest,
    AdmissionFinishResult,
    AdmissionJobCommand,
    AdmissionReservationResult,
    AdmissionReserveRequest,
    ClaimedJob,
    ClaimResult,
    ClaimState,
    CreateWorkflowWireRequest,
    EnqueueCommand,
    EnqueueCreatedResult,
    EnqueueExistedResult,
    EnqueueManyItem,
    EnqueueResult,
    Followup,
    HeartbeatResult,
    JobStatus,
    ScheduleActionResult,
    ScheduleAuthorizationProjection,
    ScheduleClaim,
    ScheduleClaimResult,
    ScheduleDefinition,
    ScheduleProfile,
    ScheduleState,
    ScheduleWriteResult,
    SettleAlreadySettledResult,
    SettleConflictResult,
    SettleDeadResult,
    SettleLostResult,
    SettleOkResult,
    SettleOutcome,
    SettleResult,
    SettleRetryScheduledResult,
    WorkflowAuthorizationProjection,
    WorkflowKind,
    WorkflowCancelWireRequest,
    WorkflowResult,
    WorkflowStatus,
)
from taskq.registry import Task, TaskRegistry
from taskq.sql.transport import SqlTaskqTransport
from taskq.worker import JobRunReport, WorkerOptions, WorkerSupervisor


class _TestingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EnqueuedJob(_TestingModel):
    job_id: UUID
    queue: str
    job_type: str
    payload: dict[str, Any]
    headers: dict[str, Any]
    idempotency_key: str | None = None
    status: JobStatus
    scheduled_at: datetime | None = None
    parent_job_id: UUID | None = None


class RecordedEnqueue(_TestingModel):
    job_id: UUID
    queue: str
    job_type: str
    payload: dict[str, Any]
    headers: dict[str, Any]
    idempotency_key: str | None = None
    status: Literal["created", "existed"]
    parent_job_id: UUID | None = None


class RecordedSettlement(_TestingModel):
    job_id: UUID
    queue: str
    job_type: str
    command: Literal["complete", "fail", "snooze", "release", "cancel_running"]
    intent: HandlerResult | None = None
    outcome: Literal["ok", "retry_scheduled", "dead"]
    cause: str | None = None

    @property
    def is_complete(self) -> bool:
        return isinstance(self.intent, Complete)


class DrainReport(_TestingModel):
    claimed: int = Field(ge=0)
    completed: int = Field(ge=0)
    retried: int = Field(ge=0)
    snoozed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    released: int = Field(ge=0)
    failed: int = Field(ge=0)
    capped: bool = False


class InlineRecorder:
    """Read-only view over an inline client's typed records."""

    def __init__(self, client: FakeTaskQClient) -> None:
        self._client = client

    @property
    def enqueues(self) -> tuple[RecordedEnqueue, ...]:
        return self._client.enqueues

    @property
    def settlements(self) -> tuple[RecordedSettlement, ...]:
        return self._client.settlements

    def enqueued(self, job_type: str) -> tuple[RecordedEnqueue, ...]:
        return tuple(item for item in self.enqueues if item.job_type == job_type)

    def settled(self, job_type: str) -> tuple[RecordedSettlement, ...]:
        return tuple(item for item in self.settlements if item.job_type == job_type)


@dataclass(slots=True)
class _FakeJob:
    job_id: UUID
    command: EnqueueCommand
    status: JobStatus
    attempt_id: UUID | None = None
    worker_id: str | None = None
    attempt_number: int = 0
    failure_count: int = 0
    settled_command: str | None = None
    parent_job_id: UUID | None = None


@dataclass(slots=True)
class _FakeAdmission:
    intent_hash: str
    handle: UUID
    reservation_expires_at: datetime
    receipt_ttl_seconds: int
    state: Literal["reserved", "admitted", "cancelled"] = "reserved"
    finish_hash: str | None = None
    job_id: UUID | None = None
    receipt: dict[str, Any] | None = None
    receipt_expires_at: datetime | None = None


@dataclass(slots=True)
class _FakeWorkflow:
    workflow_id: UUID
    workflow_key: str
    kind: WorkflowKind
    params: dict[str, Any]
    declared_queues: tuple[str, ...]
    sealed: bool = False
    cancel_requested: bool = False
    status: WorkflowStatus = WorkflowStatus.RUNNING
    steps: dict[str, UUID] | None = None

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = {}


@dataclass(slots=True)
class _FakeSchedule:
    schedule_id: UUID
    name: str
    definition: ScheduleDefinition
    state: ScheduleState
    next_fire_at: datetime
    version: int = 1
    initialized: bool = False
    last_fire_at: datetime | None = None
    token: UUID | None = None
    claim_as_of: datetime | None = None
    claim_expires_at: datetime | None = None
    retry_not_before: datetime | None = None
    last_action_token: UUID | None = None
    last_action: ScheduleActionResult | None = None


_SAFE_FIELDS = {
    "job_id",
    "queue",
    "job_type",
    "idempotency_key",
    "status",
    "scheduled_at",
    "parent_job_id",
}


def _value_at(record: RecordedEnqueue | EnqueuedJob, path: str) -> Any:
    parts = path.split(".")
    if not parts or any(not part or not part.replace("_", "a").isalnum() for part in parts):
        raise TaskqConfigError("matcher paths must contain safe dotted names")
    if parts[0] in {"payload", "headers"}:
        value: Any = getattr(record, parts[0])
        for part in parts[1:]:
            if not isinstance(value, Mapping) or part not in value:
                return None
            value = value[part]
        return value
    if len(parts) != 1 or parts[0] not in _SAFE_FIELDS:
        raise TaskqConfigError("matcher field is not available to testing assertions")
    return getattr(record, parts[0])


def _matches(record: RecordedEnqueue | EnqueuedJob, where: Mapping[str, object] | None) -> bool:
    return where is None or all(
        _value_at(record, path) == expected for path, expected in where.items()
    )


def _workflow_command_identity(command: EnqueueCommand) -> dict[str, Any]:
    identity = command.model_dump(mode="json")
    if command.depends_on is not None:
        identity["depends_on"] = sorted(str(item) for item in command.depends_on)
    return identity


class FakeTaskQClient:
    """Typed unit-test double for producer and runner paths only."""

    _SUPPORTED = {
        "enqueue",
        "enqueue_many",
        "reserve_admission",
        "finish_admission",
        "cancel_admission",
        "create_workflow",
        "seal_workflow",
        "cancel_workflow",
        "get_workflow_authorization_projection",
        "put_schedule",
        "get_schedule",
        "retire_schedule",
        "get_schedule_authorization_projection",
        "claim_schedules",
        "fire_schedule",
        "schedule_error",
        "claim",
        "heartbeat",
        "complete",
        "fail",
        "snooze",
        "release",
        "cancel_running",
        "worker_heartbeat",
        "aclose",
    }

    def __init__(
        self,
        *,
        queues: Sequence[str] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._known_queues = set(queues)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._jobs: dict[UUID, _FakeJob] = {}
        self._order: list[UUID] = []
        self._active_keys: dict[tuple[str, str], UUID] = {}
        self._admissions: dict[tuple[str, str], _FakeAdmission] = {}
        self._workflows: dict[UUID, _FakeWorkflow] = {}
        self._workflow_keys: dict[str, UUID] = {}
        self._schedules: dict[str, _FakeSchedule] = {}
        self._enqueues: list[RecordedEnqueue] = []
        self._settlements: list[RecordedSettlement] = []
        self._closed = False

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise TaskqConfigError("FakeTaskQClient clock must return an aware datetime")
        return value.astimezone(UTC)

    def __repr__(self) -> str:
        return (
            f"FakeTaskQClient(enqueues={len(self._enqueues)!r}, "
            f"settlements={len(self._settlements)!r}, closed={self._closed!r})"
        )

    @property
    def enqueues(self) -> tuple[RecordedEnqueue, ...]:
        return tuple(self._enqueues)

    @property
    def settlements(self) -> tuple[RecordedSettlement, ...]:
        return tuple(self._settlements)

    @property
    def pending(self) -> tuple[EnqueuedJob, ...]:
        return tuple(
            self._safe_job(self._jobs[job_id])
            for job_id in self._order
            if self._jobs[job_id].status is JobStatus.QUEUED
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise TaskqConfigError("FakeTaskQClient is closed")

    @staticmethod
    def _safe_job(job: _FakeJob) -> EnqueuedJob:
        return EnqueuedJob(
            job_id=job.job_id,
            queue=job.command.queue,
            job_type=job.command.job_type,
            payload=deepcopy(job.command.payload),
            headers=deepcopy(job.command.headers or {}),
            idempotency_key=job.command.idempotency_key,
            status=job.status,
            scheduled_at=job.command.scheduled_at,
            parent_job_id=job.parent_job_id,
        )

    async def enqueue(self, command: EnqueueCommand) -> EnqueueResult:
        self._ensure_open()
        if command.workflow_id is not None:
            return await self._enqueue_workflow_member(command)
        key = (
            (command.queue, command.idempotency_key)
            if command.idempotency_key is not None
            else None
        )
        existing = self._active_keys.get(key) if key is not None else None
        if existing is not None:
            job = self._jobs[existing]
            result: EnqueueResult = EnqueueExistedResult(
                job_id=job.job_id,
                queue=command.queue,
                job_type=command.job_type,
                idempotency_key=command.idempotency_key,
                scheduled_at=job.command.scheduled_at,
            )
            status: Literal["created", "existed"] = "existed"
        else:
            job = _FakeJob(job_id=uuid4(), command=command, status=JobStatus.QUEUED)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._known_queues.add(command.queue)
            if key is not None:
                self._active_keys[key] = job.job_id
            result = EnqueueCreatedResult(
                job_id=job.job_id,
                queue=command.queue,
                job_type=command.job_type,
                idempotency_key=command.idempotency_key,
                scheduled_at=command.scheduled_at,
            )
            status = "created"
        self._enqueues.append(
            RecordedEnqueue(
                job_id=job.job_id,
                queue=command.queue,
                job_type=command.job_type,
                payload=deepcopy(command.payload),
                headers=deepcopy(command.headers or {}),
                idempotency_key=command.idempotency_key,
                status=status,
                parent_job_id=job.parent_job_id,
            )
        )
        return result

    async def _enqueue_workflow_member(self, command: EnqueueCommand) -> EnqueueResult:
        assert command.workflow_id is not None and command.step_key is not None
        workflow = self._workflows.get(command.workflow_id)
        if workflow is None:
            raise TaskqNotFoundError()
        if command.queue not in workflow.declared_queues:
            raise TaskqValidationError(details={"reason": "workflow_queue_not_declared"})
        assert workflow.steps is not None
        existing_id = workflow.steps.get(command.step_key)
        if existing_id is not None:
            existing = self._jobs[existing_id]
            if _workflow_command_identity(existing.command) != _workflow_command_identity(command):
                raise TaskqConflictError(details={"reason": "workflow_step_mismatch"})
            return self._record_enqueue(existing, command, "existed")
        if workflow.sealed or workflow.cancel_requested:
            raise TaskqConflictError(details={"reason": "workflow_sealed"})
        parents: list[_FakeJob] = []
        for parent_id in command.depends_on or ():
            parent = self._jobs.get(parent_id)
            if parent is None or parent.command.workflow_id != command.workflow_id:
                raise TaskqNotFoundError()
            if parent.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                raise TaskqConflictError(details={"reason": "dependency_terminal"})
            parents.append(parent)
        status = (
            JobStatus.BLOCKED
            if any(parent.status is not JobStatus.SUCCEEDED for parent in parents)
            else JobStatus.QUEUED
        )
        job = _FakeJob(job_id=uuid4(), command=command, status=status)
        self._jobs[job.job_id] = job
        self._order.append(job.job_id)
        workflow.steps[command.step_key] = job.job_id
        self._known_queues.add(command.queue)
        if command.idempotency_key is not None:
            identity = (command.queue, command.idempotency_key)
            collision = self._active_keys.get(identity)
            if collision is not None and collision != job.job_id:
                del self._jobs[job.job_id]
                self._order.remove(job.job_id)
                del workflow.steps[command.step_key]
                raise TaskqConflictError(details={"reason": "workflow_step_mismatch"})
            self._active_keys[identity] = job.job_id
        return self._record_enqueue(job, command, "created")

    def _record_enqueue(
        self,
        job: _FakeJob,
        command: EnqueueCommand,
        status: Literal["created", "existed"],
    ) -> EnqueueResult:
        result_type = EnqueueCreatedResult if status == "created" else EnqueueExistedResult
        result: EnqueueResult = result_type(
            job_id=job.job_id,
            queue=command.queue,
            job_type=command.job_type,
            idempotency_key=command.idempotency_key,
            scheduled_at=job.command.scheduled_at,
        )
        self._enqueues.append(
            RecordedEnqueue(
                job_id=job.job_id,
                queue=command.queue,
                job_type=command.job_type,
                payload=deepcopy(command.payload),
                headers=deepcopy(command.headers or {}),
                idempotency_key=command.idempotency_key,
                status=status,
                parent_job_id=job.parent_job_id,
            )
        )
        return result

    async def create_workflow(
        self,
        workflow_key: str,
        kind: WorkflowKind | Literal["dag", "batch"],
        *,
        params: Mapping[str, Any] | None = None,
        declared_queues: Sequence[str],
        actor: str,
    ) -> WorkflowResult:
        self._ensure_open()
        del actor
        request = CreateWorkflowWireRequest(
            workflow_key=workflow_key,
            kind=kind,
            params=dict(params or {}),
            declared_queues=tuple(declared_queues),
        )
        queues = tuple(sorted(request.declared_queues))
        existing_id = self._workflow_keys.get(workflow_key)
        if existing_id is not None:
            workflow = self._workflows[existing_id]
            if (
                workflow.kind != request.kind
                or workflow.params != request.params
                or workflow.declared_queues != queues
            ):
                raise TaskqConflictError(details={"reason": "workflow_mismatch"})
            return WorkflowResult(
                outcome="existed",
                workflow_id=workflow.workflow_id,
                status=workflow.status,
            )
        if not queues or any(queue not in self._known_queues for queue in queues):
            raise TaskqNotFoundError()
        workflow = _FakeWorkflow(
            workflow_id=uuid4(),
            workflow_key=request.workflow_key,
            kind=request.kind,
            params=deepcopy(request.params),
            declared_queues=queues,
        )
        self._workflows[workflow.workflow_id] = workflow
        self._workflow_keys[workflow_key] = workflow.workflow_id
        return WorkflowResult(
            outcome="created", workflow_id=workflow.workflow_id, status=workflow.status
        )

    async def seal_workflow(self, workflow_id: UUID, actor: str) -> WorkflowResult:
        self._ensure_open()
        del actor
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            raise TaskqNotFoundError()
        outcome = "already_sealed" if workflow.sealed else "sealed"
        workflow.sealed = True
        self._refresh_workflow(workflow)
        return WorkflowResult(
            outcome=outcome, workflow_id=workflow.workflow_id, status=workflow.status
        )

    async def cancel_workflow(
        self, workflow_id: UUID, actor: str, reason: str | None = None
    ) -> WorkflowResult:
        self._ensure_open()
        del actor
        WorkflowCancelWireRequest(reason=reason)
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            raise TaskqNotFoundError()
        if workflow.status is not WorkflowStatus.RUNNING:
            outcome = "already_terminal"
        elif workflow.cancel_requested:
            outcome = "already_requested"
        else:
            outcome = "cancel_requested"
            workflow.cancel_requested = True
            workflow.sealed = True
            assert workflow.steps is not None
            for job_id in workflow.steps.values():
                job = self._jobs[job_id]
                if job.status in {JobStatus.BLOCKED, JobStatus.QUEUED}:
                    job.status = JobStatus.CANCELLED
            self._refresh_workflow(workflow)
        return WorkflowResult(
            outcome=outcome, workflow_id=workflow.workflow_id, status=workflow.status
        )

    async def get_workflow_authorization_projection(
        self, workflow_id: UUID
    ) -> WorkflowAuthorizationProjection:
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            raise TaskqNotFoundError()
        return WorkflowAuthorizationProjection(
            workflow_id=workflow.workflow_id,
            declared_queues=workflow.declared_queues,
        )

    def _refresh_workflow(self, workflow: _FakeWorkflow) -> None:
        if not workflow.sealed:
            return
        assert workflow.steps is not None
        statuses = [self._jobs[job_id].status for job_id in workflow.steps.values()]
        if workflow.cancel_requested:
            if all(status is not JobStatus.RUNNING for status in statuses):
                workflow.status = WorkflowStatus.CANCELLED
            return
        if any(status is JobStatus.FAILED for status in statuses):
            workflow.status = WorkflowStatus.FAILED
        elif any(status is JobStatus.CANCELLED for status in statuses):
            workflow.status = WorkflowStatus.CANCELLED
        elif all(status is JobStatus.SUCCEEDED for status in statuses):
            workflow.status = WorkflowStatus.SUCCEEDED

    @staticmethod
    def _schedule_profile(schedule: _FakeSchedule) -> ScheduleProfile:
        return ScheduleProfile(
            schedule_id=schedule.schedule_id,
            name=schedule.name,
            target=schedule.definition.target.model_dump(mode="json", exclude_none=True),
            recurrence=schedule.definition.recurrence.model_dump(mode="json", exclude_none=True),
            catchup_policy=schedule.definition.catchup_policy,
            max_catchup=schedule.definition.max_catchup,
            state=schedule.state,
            next_fire_at=schedule.next_fire_at,
            last_fire_at=schedule.last_fire_at,
            version=schedule.version,
        )

    async def put_schedule(
        self,
        name: str,
        definition: ScheduleDefinition | Mapping[str, Any],
        actor: str,
        *,
        expected_version: int | None = None,
    ) -> ScheduleWriteResult:
        self._ensure_open()
        del actor
        request = (
            definition
            if isinstance(definition, ScheduleDefinition)
            else ScheduleDefinition.model_validate(definition)
        )
        if request.target.queue not in self._known_queues:
            raise TaskqNotFoundError()
        existing = self._schedules.get(name)
        if existing is None:
            if expected_version is not None:
                raise TaskqNotFoundError()
            schedule = _FakeSchedule(
                schedule_id=uuid4(),
                name=name,
                definition=request,
                state=ScheduleState.PAUSED if request.paused else ScheduleState.ACTIVE,
                next_fire_at=self._now(),
            )
            self._schedules[name] = schedule
            return ScheduleWriteResult(outcome="created", profile=self._schedule_profile(schedule))
        if expected_version is None:
            if existing.definition != request:
                raise TaskqConflictError(
                    details={
                        "reason": "schedule_mismatch",
                        "current_version": existing.version,
                    }
                )
            return ScheduleWriteResult(
                outcome="unchanged", profile=self._schedule_profile(existing)
            )
        if expected_version != existing.version:
            raise TaskqConflictError(
                details={
                    "reason": "schedule_version_conflict",
                    "current_version": existing.version,
                }
            )
        if existing.state is ScheduleState.RETIRED:
            raise TaskqConflictError(
                details={
                    "reason": "schedule_retired",
                    "current_version": existing.version,
                }
            )
        target_state = ScheduleState.PAUSED if request.paused else ScheduleState.ACTIVE
        if existing.definition == request and existing.state is target_state:
            return ScheduleWriteResult(
                outcome="unchanged", profile=self._schedule_profile(existing)
            )
        pause_only = (
            existing.state is ScheduleState.ACTIVE
            and target_state is ScheduleState.PAUSED
            and existing.definition.model_copy(update={"paused": True}) == request
        )
        existing.definition = request
        existing.state = target_state
        existing.version += 1
        existing.token = None
        existing.claim_as_of = None
        existing.claim_expires_at = None
        existing.last_action_token = None
        existing.last_action = None
        if not pause_only:
            existing.initialized = False
            existing.next_fire_at = self._now()
        return ScheduleWriteResult(outcome="updated", profile=self._schedule_profile(existing))

    async def get_schedule(self, name: str) -> ScheduleProfile:
        self._ensure_open()
        schedule = self._schedules.get(name)
        if schedule is None:
            raise TaskqNotFoundError()
        return self._schedule_profile(schedule)

    async def retire_schedule(
        self, name: str, expected_version: int, actor: str
    ) -> ScheduleWriteResult:
        self._ensure_open()
        del actor
        schedule = self._schedules.get(name)
        if schedule is None:
            raise TaskqNotFoundError()
        if expected_version != schedule.version:
            raise TaskqConflictError(
                details={
                    "reason": "schedule_version_conflict",
                    "current_version": schedule.version,
                }
            )
        if schedule.state is ScheduleState.RETIRED:
            outcome: Literal["retired", "already_retired"] = "already_retired"
        else:
            schedule.state = ScheduleState.RETIRED
            schedule.version += 1
            schedule.token = None
            schedule.claim_as_of = None
            schedule.claim_expires_at = None
            schedule.last_action_token = None
            schedule.last_action = None
            outcome = "retired"
        return ScheduleWriteResult(outcome=outcome, profile=self._schedule_profile(schedule))

    async def get_schedule_authorization_projection(
        self, name: str
    ) -> ScheduleAuthorizationProjection:
        self._ensure_open()
        schedule = self._schedules.get(name)
        if schedule is None:
            return ScheduleAuthorizationProjection(name=name, queue=None)
        return ScheduleAuthorizationProjection(name=name, queue=schedule.definition.target.queue)

    async def claim_schedules(
        self, worker_id: str, *, limit: int = 10, lease_seconds: int = 60
    ) -> ScheduleClaimResult:
        self._ensure_open()
        del worker_id
        if not 1 <= limit <= 100 or not 5 <= lease_seconds <= 300:
            raise TaskqValidationError()
        now = self._now()
        claims: list[ScheduleClaim] = []
        candidates = sorted(
            self._schedules.values(), key=lambda value: (value.next_fire_at, value.name)
        )
        for schedule in candidates:
            if (
                schedule.state is not ScheduleState.ACTIVE
                or schedule.next_fire_at > now
                or (schedule.retry_not_before is not None and schedule.retry_not_before > now)
                or (
                    schedule.token is not None
                    and schedule.claim_expires_at is not None
                    and schedule.claim_expires_at > now
                )
            ):
                continue
            token = uuid4()
            schedule.token = token
            schedule.claim_as_of = now
            schedule.claim_expires_at = now + timedelta(seconds=lease_seconds)
            claims.append(
                ScheduleClaim(
                    schedule_id=schedule.schedule_id,
                    name=schedule.name,
                    definition_version=schedule.version,
                    as_of=now,
                    target=schedule.definition.target.model_dump(mode="json", exclude_none=True),
                    recurrence=schedule.definition.recurrence.model_dump(
                        mode="json", exclude_none=True
                    ),
                    catchup_policy=schedule.definition.catchup_policy,
                    max_catchup=schedule.definition.max_catchup,
                    initialized=schedule.initialized,
                    next_fire_at=schedule.next_fire_at,
                    token=token,
                    lease_seconds=lease_seconds,
                )
            )
            if len(claims) == limit:
                break
        return ScheduleClaimResult(state="claimed" if claims else "empty", schedules=tuple(claims))

    def _schedule_by_id(self, schedule_id: UUID) -> _FakeSchedule:
        for schedule in self._schedules.values():
            if schedule.schedule_id == schedule_id:
                return schedule
        raise TaskqNotFoundError()

    async def fire_schedule(
        self,
        schedule_id: UUID,
        token: UUID,
        definition_version: int,
        occurrences: Sequence[datetime],
        next_fire_at: datetime,
    ) -> ScheduleActionResult:
        self._ensure_open()
        schedule = self._schedule_by_id(schedule_id)
        if schedule.last_action_token == token and schedule.last_action is not None:
            return schedule.last_action.model_copy(update={"replayed": True})
        if (
            schedule.state is not ScheduleState.ACTIVE
            or schedule.version != definition_version
            or schedule.token != token
            or (schedule.claim_expires_at is not None and schedule.claim_expires_at < self._now())
        ):
            return ScheduleActionResult(
                outcome="stale",
                replayed=False,
                schedule_id=schedule.schedule_id,
                jobs_enqueued=0,
                next_fire_at=schedule.next_fire_at,
                state=schedule.state,
                version=schedule.version,
            )
        jobs = 0
        target = schedule.definition.target
        for due_at in occurrences:
            result = await self.enqueue(
                EnqueueCommand(
                    queue=target.queue,
                    job_type=target.job_type,
                    payload=deepcopy(target.payload),
                    headers=deepcopy(target.headers),
                    priority=target.priority,
                    scheduled_at=due_at,
                    idempotency_key=(
                        f"schedule:{schedule.schedule_id}:{int(due_at.timestamp() * 1_000_000)}"
                    ),
                    concurrency_key=target.concurrency_key,
                    affinity_key=target.affinity_key,
                    max_attempts=target.max_attempts,
                    lease_seconds=target.lease_seconds,
                    backoff_mode=target.backoff_mode,
                    backoff_base=target.backoff_base,
                    backoff_cap=target.backoff_cap,
                )
            )
            if isinstance(result, EnqueueCreatedResult):
                jobs += 1
        if not schedule.initialized:
            outcome: Literal["initialized", "fired", "skipped"] = "initialized"
        elif schedule.definition.catchup_policy.value == "skip":
            outcome = "skipped"
        else:
            outcome = "fired"
        schedule.initialized = True
        schedule.next_fire_at = next_fire_at
        if occurrences:
            schedule.last_fire_at = occurrences[-1]
        schedule.token = None
        schedule.claim_as_of = None
        schedule.claim_expires_at = None
        schedule.retry_not_before = None
        result = ScheduleActionResult(
            outcome=outcome,
            replayed=False,
            schedule_id=schedule.schedule_id,
            jobs_enqueued=jobs,
            next_fire_at=schedule.next_fire_at,
            state=schedule.state,
            version=schedule.version,
        )
        schedule.last_action_token = token
        schedule.last_action = result
        return result

    async def schedule_error(
        self,
        schedule_id: UUID,
        token: UUID,
        definition_version: int,
        error: str,
        *,
        retry_seconds: int = 30,
    ) -> ScheduleActionResult:
        self._ensure_open()
        del error
        schedule = self._schedule_by_id(schedule_id)
        if (
            schedule.version != definition_version
            or schedule.token != token
            or schedule.state is not ScheduleState.ACTIVE
        ):
            outcome: Literal["error_recorded", "stale"] = "stale"
        else:
            outcome = "error_recorded"
            schedule.token = None
            schedule.claim_as_of = None
            schedule.claim_expires_at = None
            schedule.retry_not_before = self._now() + timedelta(seconds=retry_seconds)
        return ScheduleActionResult(
            outcome=outcome,
            replayed=False,
            schedule_id=schedule.schedule_id,
            jobs_enqueued=0,
            next_fire_at=schedule.next_fire_at,
            state=schedule.state,
            version=schedule.version,
        )

    async def enqueue_many(
        self, queue: str, items: Sequence[EnqueueManyItem]
    ) -> list[EnqueueResult]:
        return [
            await self.enqueue(EnqueueCommand(queue=queue, **item.model_dump())) for item in items
        ]

    async def reserve_admission(
        self,
        queue: str,
        idempotency_key: str,
        intent_hash: str,
        *,
        handle: UUID | None = None,
        reservation_ttl_seconds: int = 300,
        receipt_ttl_seconds: int = 2_592_000,
    ) -> AdmissionReservationResult:
        self._ensure_open()
        request = AdmissionReserveRequest(
            idempotency_key=idempotency_key,
            intent_hash=intent_hash,
            handle=handle or uuid4(),
            reservation_ttl_seconds=reservation_ttl_seconds,
            receipt_ttl_seconds=receipt_ttl_seconds,
        )
        if queue not in self._known_queues:
            raise TaskqNotFoundError()
        now = self._now()
        identity = (queue, request.idempotency_key)
        admission = self._admissions.get(identity)
        if (
            admission is None
            or admission.state == "cancelled"
            or (admission.state == "reserved" and admission.reservation_expires_at <= now)
        ):
            admission = _FakeAdmission(
                intent_hash=request.intent_hash,
                handle=request.handle,
                reservation_expires_at=now + timedelta(seconds=request.reservation_ttl_seconds),
                receipt_ttl_seconds=request.receipt_ttl_seconds,
            )
            self._admissions[identity] = admission
            return ADMISSION_RESERVATION_ADAPTER.validate_python(
                {
                    "outcome": "reserved",
                    "handle": admission.handle,
                    "reservation_expires_at": admission.reservation_expires_at,
                }
            )
        if admission.intent_hash != request.intent_hash:
            raise TaskqConflictError(details={"reason": "idempotency_mismatch"})
        if admission.state == "admitted":
            return ADMISSION_RESERVATION_ADAPTER.validate_python(
                {
                    "outcome": "admitted",
                    "job_id": admission.job_id,
                    "receipt": deepcopy(admission.receipt),
                    "receipt_expires_at": admission.receipt_expires_at,
                }
            )
        if admission.handle == request.handle:
            return ADMISSION_RESERVATION_ADAPTER.validate_python(
                {
                    "outcome": "reserved",
                    "handle": admission.handle,
                    "reservation_expires_at": admission.reservation_expires_at,
                }
            )
        return ADMISSION_RESERVATION_ADAPTER.validate_python(
            {
                "outcome": "pending",
                "reservation_expires_at": admission.reservation_expires_at,
                "retry_after_seconds": max(
                    1, int((admission.reservation_expires_at - now).total_seconds() + 0.999)
                ),
            }
        )

    async def finish_admission(
        self,
        queue: str,
        idempotency_key: str,
        handle: UUID,
        job: AdmissionJobCommand | Mapping[str, Any],
        receipt: Mapping[str, Any] | None = None,
    ) -> AdmissionFinishResult:
        self._ensure_open()
        request = AdmissionFinishRequest(
            idempotency_key=idempotency_key,
            handle=handle,
            job=job,
            receipt=dict(receipt or {}),
        )
        identity = (queue, request.idempotency_key)
        admission = self._admissions.get(identity)
        if admission is None:
            raise TaskqNotFoundError()
        if admission.handle != request.handle:
            raise TaskqConflictError(details={"reason": "reservation_conflict"})
        finish_hash = hashlib.sha256(
            json.dumps(
                {
                    "job": request.job.model_dump(mode="json", exclude_none=True),
                    "receipt": request.receipt,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if admission.state == "admitted":
            if admission.finish_hash != finish_hash:
                raise TaskqConflictError(details={"reason": "finish_mismatch"})
            return AdmissionFinishResult(
                outcome=AdmissionFinishOutcome.EXISTED,
                job_id=admission.job_id,
                receipt=deepcopy(admission.receipt),
                receipt_expires_at=admission.receipt_expires_at,
            )
        if admission.state == "cancelled":
            raise TaskqConflictError(details={"reason": "reservation_cancelled"})
        now = self._now()
        if admission.reservation_expires_at <= now:
            raise TaskqConflictError(details={"reason": "reservation_expired"})
        command = EnqueueCommand(queue=queue, **request.job.model_dump())
        enqueued = await self.enqueue(command)
        admission.state = "admitted"
        admission.finish_hash = finish_hash
        admission.job_id = enqueued.job_id
        admission.receipt = deepcopy(request.receipt)
        admission.receipt_expires_at = now + timedelta(seconds=admission.receipt_ttl_seconds)
        return AdmissionFinishResult(
            outcome=AdmissionFinishOutcome.CREATED,
            job_id=enqueued.job_id,
            receipt=deepcopy(request.receipt),
            receipt_expires_at=admission.receipt_expires_at,
        )

    async def cancel_admission(
        self, queue: str, idempotency_key: str, handle: UUID
    ) -> AdmissionCancelResult:
        self._ensure_open()
        request = AdmissionCancelRequest(idempotency_key=idempotency_key, handle=handle)
        admission = self._admissions.get((queue, request.idempotency_key))
        if admission is None:
            raise TaskqNotFoundError()
        if admission.handle != request.handle:
            raise TaskqConflictError(details={"reason": "reservation_conflict"})
        if admission.state == "admitted":
            return AdmissionCancelResult(
                outcome=AdmissionCancelOutcome.ALREADY_ADMITTED,
                job_id=admission.job_id,
                receipt=deepcopy(admission.receipt),
                receipt_expires_at=admission.receipt_expires_at,
            )
        if admission.state == "cancelled":
            return AdmissionCancelResult(outcome=AdmissionCancelOutcome.ALREADY_CANCELLED)
        if admission.reservation_expires_at <= self._now():
            return AdmissionCancelResult(outcome=AdmissionCancelOutcome.EXPIRED)
        admission.state = "cancelled"
        return AdmissionCancelResult(outcome=AdmissionCancelOutcome.CANCELLED)

    async def claim(
        self,
        queue: str,
        worker_id: str,
        *,
        batch: int = 1,
        job_types: Sequence[str] | None = None,
        lease_seconds: int | None = None,
        affinity_key: str | None = None,
        job_id: UUID | None = None,
    ) -> ClaimResult:
        self._ensure_open()
        if queue not in self._known_queues:
            return ClaimResult(state=ClaimState.UNKNOWN_QUEUE)
        now = self._now()
        selected: list[ClaimedJob] = []
        for candidate_id in self._order:
            candidate = self._jobs[candidate_id]
            command = candidate.command
            if candidate.status is not JobStatus.QUEUED or command.queue != queue:
                continue
            if job_id is not None and candidate.job_id != job_id:
                continue
            if job_types is not None and command.job_type not in job_types:
                continue
            if affinity_key is not None and command.affinity_key != affinity_key:
                continue
            if command.scheduled_at is not None and command.scheduled_at > now:
                continue
            duration = lease_seconds or command.lease_seconds or 60
            candidate.status = JobStatus.RUNNING
            candidate.attempt_id = uuid4()
            candidate.worker_id = worker_id
            candidate.attempt_number += 1
            candidate.settled_command = None
            selected.append(
                ClaimedJob(
                    job_id=candidate.job_id,
                    queue=command.queue,
                    job_type=command.job_type,
                    priority=command.priority or 0,
                    payload=deepcopy(command.payload),
                    headers=deepcopy(command.headers or {}),
                    progress=None,
                    attempt_id=candidate.attempt_id,
                    attempt_number=candidate.attempt_number,
                    failure_count=candidate.failure_count,
                    max_attempts=command.max_attempts or 10,
                    lease_expires_at=now + timedelta(seconds=duration),
                    lease_seconds=duration,
                )
            )
            if len(selected) >= batch:
                break
        return ClaimResult(
            state=ClaimState.CLAIMED if selected else ClaimState.EMPTY,
            jobs=tuple(selected),
        )

    def _attempt(self, job_id: UUID, attempt_id: UUID, worker_id: str) -> _FakeJob | None:
        job = self._jobs.get(job_id)
        if (
            job is None
            or job.attempt_id != attempt_id
            or job.worker_id != worker_id
            or job.status is not JobStatus.RUNNING
        ):
            return None
        return job

    async def heartbeat(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> HeartbeatResult:
        self._ensure_open()
        duration = lease_seconds or 60
        job = self._jobs.get(job_id)
        workflow_cancel_requested = bool(
            job is not None
            and job.command.workflow_id is not None
            and self._workflows[job.command.workflow_id].cancel_requested
        )
        return HeartbeatResult(
            ok=self._attempt(job_id, attempt_id, worker_id) is not None,
            cancel_requested=workflow_cancel_requested,
            lease_expires_at=self._now() + timedelta(seconds=duration),
        )

    def _replay_or_lost(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, command: str
    ) -> tuple[_FakeJob | None, SettleResult | None]:
        job = self._jobs.get(job_id)
        if job is None or job.attempt_id != attempt_id or job.worker_id != worker_id:
            return None, SettleLostResult(job_status=None, scheduled_at=None)
        if job.settled_command is not None:
            if job.settled_command == command:
                return None, SettleAlreadySettledResult(
                    job_status=job.status, scheduled_at=job.command.scheduled_at
                )
            return None, SettleConflictResult(
                job_status=job.status, scheduled_at=job.command.scheduled_at
            )
        if job.status is not JobStatus.RUNNING:
            return None, SettleLostResult(job_status=job.status, scheduled_at=None)
        return job, None

    def _finish(
        self,
        job: _FakeJob,
        *,
        command: Literal["complete", "fail", "snooze", "release", "cancel_running"],
        status: JobStatus,
        intent: HandlerResult | None,
        outcome: Literal["ok", "retry_scheduled", "dead"],
        cause: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> None:
        job.status = status
        job.settled_command = command
        if scheduled_at is not None:
            job.command = job.command.model_copy(update={"scheduled_at": scheduled_at})
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            key = job.command.idempotency_key
            if key is not None:
                self._active_keys.pop((job.command.queue, key), None)
        self._settlements.append(
            RecordedSettlement(
                job_id=job.job_id,
                queue=job.command.queue,
                job_type=job.command.job_type,
                command=command,
                intent=intent,
                outcome=outcome,
                cause=cause,
            )
        )
        if job.command.workflow_id is not None and status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            self._advance_fake_workflow(job)

    def _advance_fake_workflow(self, parent: _FakeJob) -> None:
        workflow_id = parent.command.workflow_id
        assert workflow_id is not None
        workflow = self._workflows[workflow_id]
        if parent.status is JobStatus.SUCCEEDED:
            for candidate in self._jobs.values():
                if (
                    candidate.command.workflow_id != workflow_id
                    or candidate.status is not JobStatus.BLOCKED
                    or parent.job_id not in (candidate.command.depends_on or ())
                ):
                    continue
                parents = [self._jobs[item] for item in candidate.command.depends_on or ()]
                if all(item.status is JobStatus.SUCCEEDED for item in parents):
                    candidate.status = JobStatus.QUEUED
        else:
            frontier = [parent.job_id]
            seen: set[UUID] = set()
            while frontier:
                failed_id = frontier.pop()
                if failed_id in seen:
                    continue
                seen.add(failed_id)
                for candidate in self._jobs.values():
                    if (
                        candidate.command.workflow_id == workflow_id
                        and candidate.status in {JobStatus.BLOCKED, JobStatus.QUEUED}
                        and failed_id in (candidate.command.depends_on or ())
                    ):
                        candidate.status = JobStatus.CANCELLED
                        frontier.append(candidate.job_id)
        self._refresh_workflow(workflow)

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Followup] | None = None,
    ) -> SettleResult:
        self._ensure_open()
        job, replay = self._replay_or_lost(job_id, attempt_id, worker_id, "complete")
        if replay is not None:
            return replay
        assert job is not None
        children: list[tuple[_FakeJob, Literal["created", "existed"]]] = []
        seen_steps: set[str] = set()
        for followup in followups or ():
            if followup.step in seen_steps:
                raise TaskqConfigError("followup steps must be distinct")
            seen_steps.add(followup.step)
            queue = followup.queue or job.command.queue
            if queue not in self._known_queues:
                raise TaskqNotFoundError()
            key = f"chain:{job.job_id}:{followup.step}"
            identity = (queue, key)
            existing_id = self._active_keys.get(identity)
            command = EnqueueCommand(
                queue=queue,
                job_type=followup.job_type,
                payload=deepcopy(followup.payload),
                priority=followup.priority,
                scheduled_at=followup.scheduled_at,
                idempotency_key=key,
                max_attempts=followup.max_attempts,
                lease_seconds=followup.lease_seconds,
                headers=deepcopy(followup.headers),
            )
            if existing_id is None:
                child = _FakeJob(
                    job_id=uuid4(),
                    command=command,
                    status=JobStatus.QUEUED,
                    parent_job_id=job.job_id,
                )
                children.append((child, "created"))
                continue
            child = self._jobs[existing_id]
            if child.parent_job_id != job.job_id or child.command != command:
                raise TaskqInternalError()
            children.append((child, "existed"))
        self._finish(
            job,
            command="complete",
            status=JobStatus.SUCCEEDED,
            intent=Complete(result=dict(result or {}), followups=tuple(followups or ())),
            outcome="ok",
        )
        for child, status in children:
            if status == "created":
                self._jobs[child.job_id] = child
                self._order.append(child.job_id)
                assert child.command.idempotency_key is not None
                self._active_keys[(child.command.queue, child.command.idempotency_key)] = (
                    child.job_id
                )
            self._enqueues.append(
                RecordedEnqueue(
                    job_id=child.job_id,
                    queue=child.command.queue,
                    job_type=child.command.job_type,
                    payload=deepcopy(child.command.payload),
                    headers=deepcopy(child.command.headers or {}),
                    idempotency_key=child.command.idempotency_key,
                    status=status,
                    parent_job_id=job.job_id,
                )
            )
        return SettleOkResult(job_status=JobStatus.SUCCEEDED, scheduled_at=None)

    async def fail(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        self._ensure_open()
        job, replay = self._replay_or_lost(job_id, attempt_id, worker_id, "fail")
        if replay is not None:
            return replay
        assert job is not None
        job.failure_count += 1
        retry = retryable and job.failure_count < (job.command.max_attempts or 10)
        if retry:
            scheduled = self._now() + timedelta(seconds=retry_after_seconds or 0)
            intent: HandlerResult = Retry(
                after_seconds=retry_after_seconds,
                error=error,
                progress=dict(progress or {}) or None,
            )
            self._finish(
                job,
                command="fail",
                status=JobStatus.QUEUED,
                intent=intent,
                outcome="retry_scheduled",
                scheduled_at=scheduled,
            )
            return SettleRetryScheduledResult(job_status=JobStatus.QUEUED, scheduled_at=scheduled)
        intent = NonRetryable(error=error, progress=dict(progress or {}) or None)
        self._finish(
            job,
            command="fail",
            status=JobStatus.FAILED,
            intent=intent,
            outcome="dead",
        )
        return SettleDeadResult(job_status=JobStatus.FAILED, scheduled_at=None)

    async def snooze(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        delay_seconds: int,
        *,
        reason: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        self._ensure_open()
        job, replay = self._replay_or_lost(job_id, attempt_id, worker_id, "snooze")
        if replay is not None:
            return replay
        assert job is not None
        scheduled = self._now() + timedelta(seconds=delay_seconds)
        self._finish(
            job,
            command="snooze",
            status=JobStatus.QUEUED,
            intent=Snooze(
                delay_seconds=delay_seconds,
                reason=reason,
                progress=dict(progress or {}) or None,
            ),
            outcome="ok",
            scheduled_at=scheduled,
        )
        return SettleOkResult(job_status=JobStatus.QUEUED, scheduled_at=scheduled)

    async def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: Literal["released", "worker_shutdown", "no_handler"],
        *,
        delay_seconds: int = 0,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        self._ensure_open()
        job, replay = self._replay_or_lost(job_id, attempt_id, worker_id, "release")
        if replay is not None:
            return replay
        assert job is not None
        scheduled = self._now() + timedelta(seconds=delay_seconds)
        self._finish(
            job,
            command="release",
            status=JobStatus.QUEUED,
            intent=None,
            outcome="ok",
            cause=cause,
            scheduled_at=scheduled,
        )
        return SettleOkResult(job_status=JobStatus.QUEUED, scheduled_at=scheduled)

    async def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult:
        self._ensure_open()
        job, replay = self._replay_or_lost(job_id, attempt_id, worker_id, "cancel_running")
        if replay is not None:
            return replay
        assert job is not None
        self._finish(
            job,
            command="cancel_running",
            status=JobStatus.CANCELLED,
            intent=Cancel(reason=reason),
            outcome="ok",
        )
        return SettleOkResult(job_status=JobStatus.CANCELLED, scheduled_at=None)

    async def worker_heartbeat(
        self,
        worker_id: str,
        queues: Sequence[str],
        *,
        hostname: str | None = None,
        pid: int | None = None,
        version: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> bool:
        self._ensure_open()
        return False

    def assert_enqueued(
        self,
        job_type: str,
        *,
        count: int = 1,
        where: Mapping[str, object] | None = None,
    ) -> tuple[RecordedEnqueue, ...]:
        matches = tuple(
            record
            for record in self.enqueues
            if record.job_type == job_type and _matches(record, where)
        )
        if len(matches) != count:
            raise AssertionError(
                f"expected {count} enqueue(s) for {job_type!r}, found {len(matches)}"
            )
        return matches

    async def aclose(self) -> None:
        self._closed = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in self._SUPPORTED:
            raise AttributeError(name)

        async def unsupported(*args: Any, **kwargs: Any) -> Any:
            self._ensure_open()
            raise TaskqConfigError("unsupported by FakeTaskQClient")

        return unsupported


async def _connection(
    supplied: AsyncConnection | AsyncSession,
) -> AsyncConnection:
    if isinstance(supplied, AsyncSession):
        return await supplied.connection()
    if isinstance(supplied, AsyncConnection):
        return supplied
    raise TaskqConfigError("expected an AsyncConnection or AsyncSession")


class _BoundSqlTransport:
    """Runner subset that binds every command to one caller-owned connection."""

    def __init__(self, transport: SqlTaskqTransport, connection: AsyncConnection) -> None:
        self.transport = transport
        self.connection = connection
        self._jobs: dict[UUID, ClaimedJob] = {}
        self._settlements: list[RecordedSettlement] = []

    @property
    def settlements(self) -> tuple[RecordedSettlement, ...]:
        return tuple(self._settlements)

    async def claim(self, *args: Any, **kwargs: Any) -> ClaimResult:
        result = await self.transport.claim(*args, **kwargs, connection=self.connection)
        self._jobs.update({job.job_id: job for job in result.jobs})
        return result

    async def heartbeat(self, *args: Any, **kwargs: Any) -> HeartbeatResult:
        return await self.transport.heartbeat(*args, **kwargs, connection=self.connection)

    def _record(
        self,
        job_id: UUID,
        *,
        command: Literal["complete", "fail", "snooze", "release", "cancel_running"],
        intent: HandlerResult | None,
        result: SettleResult,
        cause: str | None = None,
    ) -> None:
        if result.result not in {
            SettleOutcome.OK,
            SettleOutcome.RETRY_SCHEDULED,
            SettleOutcome.DEAD,
        }:
            return
        claim = self._jobs[job_id]
        self._settlements.append(
            RecordedSettlement(
                job_id=job_id,
                queue=claim.queue,
                job_type=claim.job_type,
                command=command,
                intent=intent,
                outcome=result.result.value,
                cause=cause,
            )
        )

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Followup] | None = None,
    ) -> SettleResult:
        settled = await self.transport.complete(
            job_id,
            attempt_id,
            worker_id,
            result=result,
            stats=stats,
            followups=followups,
            connection=self.connection,
        )
        self._record(
            job_id,
            command="complete",
            intent=Complete(result=dict(result or {}), followups=tuple(followups or ())),
            result=settled,
        )
        return settled

    async def fail(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        settled = await self.transport.fail(
            job_id,
            attempt_id,
            worker_id,
            error,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            progress=progress,
            stats=stats,
            connection=self.connection,
        )
        intent: HandlerResult = (
            Retry(
                after_seconds=retry_after_seconds,
                error=error,
                progress=dict(progress or {}) or None,
            )
            if retryable
            else NonRetryable(error=error, progress=dict(progress or {}) or None)
        )
        self._record(job_id, command="fail", intent=intent, result=settled)
        return settled

    async def snooze(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        delay_seconds: int,
        *,
        reason: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        settled = await self.transport.snooze(
            job_id,
            attempt_id,
            worker_id,
            delay_seconds,
            reason=reason,
            progress=progress,
            connection=self.connection,
        )
        self._record(
            job_id,
            command="snooze",
            intent=Snooze(
                delay_seconds=delay_seconds,
                reason=reason,
                progress=dict(progress or {}) or None,
            ),
            result=settled,
        )
        return settled

    async def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: Literal["released", "worker_shutdown", "no_handler"],
        *,
        delay_seconds: int = 0,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        settled = await self.transport.release(
            job_id,
            attempt_id,
            worker_id,
            cause,
            delay_seconds=delay_seconds,
            progress=progress,
            connection=self.connection,
        )
        self._record(job_id, command="release", intent=None, result=settled, cause=cause)
        return settled

    async def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult:
        settled = await self.transport.cancel_running(
            job_id,
            attempt_id,
            worker_id,
            reason,
            connection=self.connection,
        )
        self._record(
            job_id,
            command="cancel_running",
            intent=Cancel(reason=reason),
            result=settled,
        )
        return settled

    async def aclose(self) -> None:
        return None


async def _run_claim(
    transport: Any,
    registry: TaskRegistry,
    claim: ClaimedJob,
    *,
    worker_id: str,
) -> JobRunReport:
    supervisor = WorkerSupervisor(
        transport,
        registry,
        worker_id,
        options=WorkerOptions(soft_stop_timeout=0),
    )
    try:
        return await supervisor.run_job(claim)
    finally:
        await supervisor.aclose()


async def work(
    connection: AsyncConnection | AsyncSession | None = None,
    *,
    task: Task[Any, Any],
    payload: BaseModel | Mapping[str, object],
    progress: Mapping[str, Any] | None = None,
    unique_mode: Literal["normal", "isolated"] = "normal",
) -> HandlerResult:
    """Execute one registered-style task through the production worker path."""
    if task.handler is None:
        raise TaskqConfigError("work requires a task with a handler")
    if unique_mode not in {"normal", "isolated"}:
        raise TaskqConfigError("unique_mode must be normal or isolated")
    registry = TaskRegistry((task,))
    worker_id = f"taskq-testing-{uuid4()}"
    idempotency_key = str(uuid4()) if unique_mode == "isolated" else None
    if connection is None:
        transport: Any = FakeTaskQClient(queues=(task.queue,))
        facade = TaskQ(transport, registry=registry)
        enqueued = await facade.enqueue(task, payload, idempotency_key=idempotency_key)
        claimed = await transport.claim(task.queue, worker_id, job_id=enqueued.job_id)
        if claimed.state is not ClaimState.CLAIMED:
            raise AssertionError(f"work could not claim synthetic job: {claimed.state.value}")
        claim = claimed.jobs[0].model_copy(update={"progress": dict(progress or {}) or None})
        report = await _run_claim(transport, registry, claim, worker_id=worker_id)
        records = transport.settlements
    else:
        sql_connection = await _connection(connection)
        base = SqlTaskqTransport(sql_connection.engine)
        facade = TaskQ(base, registry=registry)
        enqueued = await facade.enqueue(
            task,
            payload,
            idempotency_key=idempotency_key,
            connection=sql_connection,
        )
        transport = _BoundSqlTransport(base, sql_connection)
        claimed = await transport.claim(task.queue, worker_id, batch=1, job_id=enqueued.job_id)
        if claimed.state is not ClaimState.CLAIMED:
            raise AssertionError(f"work could not claim PostgreSQL job: {claimed.state.value}")
        claim = claimed.jobs[0].model_copy(update={"progress": dict(progress or {}) or None})
        report = await _run_claim(transport, registry, claim, worker_id=worker_id)
        records = transport.settlements
    if report.fatal:
        raise TaskqInternalError(details={"settlement_command": report.settlement_command})
    if not records or records[-1].intent is None:
        raise AssertionError("work did not produce a handler settlement intent")
    return records[-1].intent


class _InlineFakeTaskQClient(FakeTaskQClient):
    def __init__(self, registry: TaskRegistry, *, follow: bool, max_jobs: int) -> None:
        super().__init__(queues=tuple(task.queue for task in registry))
        self.registry = registry
        self.follow = follow
        self.max_jobs = max_jobs
        self.executed = 0

    async def enqueue(self, command: EnqueueCommand) -> EnqueueResult:
        result = await super().enqueue(command)
        if not result.created:
            return result
        await self._execute_job(result.job_id)
        return result

    async def _execute_job(self, job_id: UUID) -> None:
        if self.executed >= self.max_jobs:
            raise AssertionError("inline execution exceeded max_jobs; possible runaway followup")
        job = self._jobs[job_id]
        task = self.registry.resolve(job.command.job_type)
        if task is None or task.handler is None:
            raise TaskqConfigError(
                f"inline task has no registered handler: {job.command.job_type!r}"
            )
        self.executed += 1
        worker_id = f"taskq-inline-{uuid4()}"
        claimed = await self.claim(job.command.queue, worker_id, job_id=job_id)
        if claimed.state is not ClaimState.CLAIMED:
            raise AssertionError(f"inline job was not claimable: {claimed.state.value}")
        before = len(self.settlements)
        report = await _run_claim(self, self.registry, claimed.jobs[0], worker_id=worker_id)
        if report.fatal:
            raise TaskqInternalError(details={"settlement_command": report.settlement_command})
        record = self.settlements[before]
        if self.follow and isinstance(record.intent, Complete):
            for followup in record.intent.followups:
                queue = followup.queue or record.queue
                key = f"chain:{record.job_id}:{followup.step}"
                child_id = self._active_keys.get((queue, key))
                if child_id is None:
                    raise AssertionError("native inline followup was not inserted")
                await self._execute_job(child_id)


def _max_jobs(value: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
        raise TaskqConfigError("max_jobs must be an integer from 1 through 10000")
    return value


@asynccontextmanager
async def inline_mode(
    tq: TaskQ,
    *,
    follow: bool = False,
    max_jobs: int = 100,
) -> AsyncIterator[InlineRecorder]:
    """Temporarily execute newly created enqueues through registered handlers."""
    limit = _max_jobs(max_jobs)
    client = _InlineFakeTaskQClient(tq.registry, follow=follow, max_jobs=limit)
    with tq.replace_client(client):
        yield InlineRecorder(client)


async def require_enqueued(
    source: TaskQ | FakeTaskQClient | AsyncConnection | AsyncSession,
    *,
    job_type: str,
    where: Mapping[str, object] | None = None,
    unique_skipped: bool | None = None,
    enqueue_result: EnqueueResult | None = None,
) -> EnqueuedJob:
    """Require exactly one safe enqueue in a fake ledger or current SQL transaction."""
    if unique_skipped is not None and unique_skipped is not False:
        raise TaskqConfigError("unique_skipped only accepts False as an assertion")
    if unique_skipped is False and (enqueue_result is None or not enqueue_result.created):
        raise AssertionError("expected enqueue result status created")
    fake = source.transport if isinstance(source, TaskQ) else source
    if isinstance(fake, FakeTaskQClient):
        candidates = tuple(
            EnqueuedJob(
                job_id=record.job_id,
                queue=record.queue,
                job_type=record.job_type,
                payload=record.payload,
                headers=record.headers,
                idempotency_key=record.idempotency_key,
                status=fake._jobs[record.job_id].status,
                scheduled_at=fake._jobs[record.job_id].command.scheduled_at,
            )
            for record in fake.enqueues
            if record.status == "created" and record.job_type == job_type
        )
    else:
        if isinstance(source, TaskQ):
            raise TaskqConfigError("SQL require_enqueued needs the caller's connection or session")
        connection = await _connection(source)
        result = await connection.execute(
            text(
                "SELECT id AS job_id, queue, job_type, payload, COALESCE(headers, '{}'::jsonb) "
                "AS headers, idempotency_key, status, scheduled_at "
                "FROM taskq.jobs WHERE job_type = :job_type ORDER BY id"
            ),
            {"job_type": job_type},
        )
        candidates = tuple(EnqueuedJob.model_validate(row) for row in result.mappings())
    matches = tuple(candidate for candidate in candidates if _matches(candidate, where))
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one enqueue for {job_type!r}, found {len(matches)}")
    return matches[0]


def _count_settlement(counts: dict[str, int], record: RecordedSettlement) -> None:
    if record.command == "complete":
        counts["completed"] += 1
    elif record.command == "snooze":
        counts["snoozed"] += 1
    elif record.command == "cancel_running":
        counts["cancelled"] += 1
    elif record.command == "release":
        counts["released"] += 1
    elif record.outcome == "retry_scheduled":
        counts["retried"] += 1
    else:
        counts["failed"] += 1


async def drain(
    tq: TaskQ,
    *,
    queue: str,
    max_jobs: int = 100,
    connection: AsyncConnection | AsyncSession | None = None,
) -> DrainReport:
    """Claim and supervise sequentially until empty, paused, or loudly capped."""
    limit = _max_jobs(max_jobs)
    if connection is not None:
        if not isinstance(tq.transport, SqlTaskqTransport):
            raise TaskqConfigError("connection requires SqlTaskqTransport")
        sql_connection = await _connection(connection)
        transport: Any = _BoundSqlTransport(tq.transport, sql_connection)
    else:
        transport = tq.transport
    worker_id = f"taskq-drain-{uuid4()}"
    counts = {
        "claimed": 0,
        "completed": 0,
        "retried": 0,
        "snoozed": 0,
        "cancelled": 0,
        "released": 0,
        "failed": 0,
    }
    supervisor = WorkerSupervisor(
        transport,
        tq.registry,
        worker_id,
        options=WorkerOptions(soft_stop_timeout=0),
    )
    try:
        while counts["claimed"] < limit:
            claimed = await transport.claim(queue, worker_id, batch=1)
            if claimed.state in {ClaimState.EMPTY, ClaimState.PAUSED}:
                return DrainReport(**counts, capped=False)
            if claimed.state is not ClaimState.CLAIMED:
                raise TaskqInternalError(details={"claim_state": claimed.state.value})
            before = len(transport.settlements) if hasattr(transport, "settlements") else 0
            report = await supervisor.run_job(claimed.jobs[0])
            counts["claimed"] += 1
            if report.fatal:
                raise TaskqInternalError(details={"settlement_command": report.settlement_command})
            records = transport.settlements if hasattr(transport, "settlements") else ()
            if len(records) != before + 1:
                raise TaskqInternalError(details={"testing_ledger": "missing settlement"})
            _count_settlement(counts, records[-1])
        overflow = await transport.claim(queue, worker_id, batch=1)
        if overflow.state is ClaimState.CLAIMED:
            claim = overflow.jobs[0]
            await transport.release(
                claim.job_id, claim.attempt_id, worker_id, "released", delay_seconds=0
            )
            raise AssertionError("drain exceeded max_jobs; possible runaway work")
        if overflow.state not in {ClaimState.EMPTY, ClaimState.PAUSED}:
            raise TaskqInternalError(details={"claim_state": overflow.state.value})
        return DrainReport(**counts, capped=False)
    finally:
        await supervisor.aclose()


__all__ = [
    "DrainReport",
    "EnqueuedJob",
    "FakeTaskQClient",
    "InlineRecorder",
    "RecordedEnqueue",
    "RecordedSettlement",
    "drain",
    "inline_mode",
    "require_enqueued",
    "work",
]
