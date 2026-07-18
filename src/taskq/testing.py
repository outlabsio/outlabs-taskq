"""Consumer test helpers; fake behavior is not PostgreSQL protocol proof."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from taskq.errors import TaskqConfigError
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
    SettleResult,
    SettleRetryScheduledResult,
)


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


__all__ = [
    "DrainReport",
    "EnqueuedJob",
    "FakeTaskQClient",
    "InlineRecorder",
    "RecordedEnqueue",
    "RecordedSettlement",
]
