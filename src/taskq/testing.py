"""Consumer test helpers; fake behavior is not PostgreSQL protocol proof."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from taskq.client import TaskQ
from taskq.errors import TaskqConfigError, TaskqInternalError
from taskq.execution import Cancel, Complete, HandlerResult, NonRetryable, Retry, Snooze
from taskq.protocol import (
    ClaimedJob,
    ClaimResult,
    ClaimState,
    EnqueueCommand,
    EnqueueCreatedResult,
    EnqueueExistedResult,
    EnqueueManyItem,
    EnqueueResult,
    HeartbeatResult,
    JobStatus,
    SettleAlreadySettledResult,
    SettleConflictResult,
    SettleDeadResult,
    SettleLostResult,
    SettleOkResult,
    SettleOutcome,
    SettleResult,
    SettleRetryScheduledResult,
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


class RecordedEnqueue(_TestingModel):
    job_id: UUID
    queue: str
    job_type: str
    payload: dict[str, Any]
    headers: dict[str, Any]
    idempotency_key: str | None = None
    status: Literal["created", "existed"]


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


_SAFE_FIELDS = {
    "job_id",
    "queue",
    "job_type",
    "idempotency_key",
    "status",
    "scheduled_at",
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


def _matches(
    record: RecordedEnqueue | EnqueuedJob, where: Mapping[str, object] | None
) -> bool:
    return where is None or all(_value_at(record, path) == expected for path, expected in where.items())


class FakeTaskQClient:
    """Typed unit-test double for producer and runner paths only."""

    _SUPPORTED = {
        "enqueue",
        "enqueue_many",
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

    def __init__(self, *, queues: Sequence[str] = ()) -> None:
        self._known_queues = set(queues)
        self._jobs: dict[UUID, _FakeJob] = {}
        self._order: list[UUID] = []
        self._active_keys: dict[tuple[str, str], UUID] = {}
        self._enqueues: list[RecordedEnqueue] = []
        self._settlements: list[RecordedSettlement] = []
        self._closed = False

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
        )

    async def enqueue(self, command: EnqueueCommand) -> EnqueueResult:
        self._ensure_open()
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
            )
        )
        return result

    async def enqueue_many(
        self, queue: str, items: Sequence[EnqueueManyItem]
    ) -> list[EnqueueResult]:
        return [
            await self.enqueue(EnqueueCommand(queue=queue, **item.model_dump())) for item in items
        ]

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
        now = datetime.now(UTC)
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
        return HeartbeatResult(
            ok=self._attempt(job_id, attempt_id, worker_id) is not None,
            cancel_requested=False,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=duration),
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

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Mapping[str, Any]] | None = None,
    ) -> SettleResult:
        self._ensure_open()
        job, replay = self._replay_or_lost(job_id, attempt_id, worker_id, "complete")
        if replay is not None:
            return replay
        assert job is not None
        self._finish(
            job,
            command="complete",
            status=JobStatus.SUCCEEDED,
            intent=Complete(result=dict(result or {}), followups=tuple(followups or ())),
            outcome="ok",
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
            scheduled = datetime.now(UTC) + timedelta(seconds=retry_after_seconds or 0)
            intent: HandlerResult = Retry(
                after_seconds=retry_after_seconds, error=error, progress=dict(progress or {}) or None
            )
            self._finish(
                job,
                command="fail",
                status=JobStatus.QUEUED,
                intent=intent,
                outcome="retry_scheduled",
                scheduled_at=scheduled,
            )
            return SettleRetryScheduledResult(
                job_status=JobStatus.QUEUED, scheduled_at=scheduled
            )
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
        scheduled = datetime.now(UTC) + timedelta(seconds=delay_seconds)
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
        scheduled = datetime.now(UTC) + timedelta(seconds=delay_seconds)
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
        followups: Sequence[Mapping[str, Any]] | None = None,
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
        if self.executed >= self.max_jobs:
            raise AssertionError("inline execution exceeded max_jobs; possible runaway followup")
        task = self.registry.resolve(command.job_type)
        if task is None or task.handler is None:
            raise TaskqConfigError(f"inline task has no registered handler: {command.job_type!r}")
        self.executed += 1
        worker_id = f"taskq-inline-{uuid4()}"
        claimed = await self.claim(command.queue, worker_id, job_id=result.job_id)
        if claimed.state is not ClaimState.CLAIMED:
            raise AssertionError(f"inline job was not claimable: {claimed.state.value}")
        before = len(self.settlements)
        report = await _run_claim(self, self.registry, claimed.jobs[0], worker_id=worker_id)
        if report.fatal:
            raise TaskqInternalError(details={"settlement_command": report.settlement_command})
        record = self.settlements[before]
        if self.follow and isinstance(record.intent, Complete):
            for followup in record.intent.followups:
                allowed = {"job_type", "payload", "idempotency_key"}
                if set(followup) - allowed:
                    raise TaskqConfigError("inline followup contains unsupported fields")
                job_type = followup.get("job_type")
                if not isinstance(job_type, str):
                    raise TaskqConfigError("inline followup requires job_type")
                follow_task = self.registry.resolve(job_type)
                if follow_task is None:
                    raise TaskqConfigError(f"inline followup task is not registered: {job_type!r}")
                await TaskQ(self, registry=self.registry).enqueue(
                    follow_task,
                    followup.get("payload", {}),
                    idempotency_key=followup.get("idempotency_key"),
                )
        return result


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
        raise AssertionError(
            f"expected exactly one enqueue for {job_type!r}, found {len(matches)}"
        )
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
