"""Closed Protocol-v1 value models shared by every taskq transport."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TqCode(StrEnum):
    NOT_FOUND = "TQ001"
    CONFLICT = "TQ409"
    VALIDATION = "TQ422"
    VERSION = "TQ426"
    BACKPRESSURE = "TQ429"
    INTERNAL = "TQ500"
    CAPABILITY = "TQ501"
    UNAVAILABLE = "TQ503"


class TqErrorSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    http_status: int
    retryable: bool
    category: str


TQ_ERROR_REGISTRY: Final = MappingProxyType(
    {
        TqCode.NOT_FOUND: TqErrorSpec(
            http_status=404, retryable=False, category="resource not found"
        ),
        TqCode.CONFLICT: TqErrorSpec(
            http_status=409, retryable=False, category="durable state conflict"
        ),
        TqCode.VALIDATION: TqErrorSpec(
            http_status=422, retryable=False, category="invalid command"
        ),
        TqCode.VERSION: TqErrorSpec(
            http_status=426, retryable=False, category="unsupported version"
        ),
        TqCode.BACKPRESSURE: TqErrorSpec(
            http_status=429, retryable=True, category="queue backpressure"
        ),
        TqCode.INTERNAL: TqErrorSpec(
            http_status=500, retryable=True, category="internal taskq failure"
        ),
        TqCode.CAPABILITY: TqErrorSpec(
            http_status=501, retryable=False, category="capability not active"
        ),
        TqCode.UNAVAILABLE: TqErrorSpec(
            http_status=503, retryable=True, category="taskq unavailable"
        ),
    }
)


class EnqueueStatus(StrEnum):
    CREATED = "created"
    EXISTED = "existed"


class EnqueueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: EnqueueStatus
    job_id: UUID
    created: bool
    queue: str
    job_type: str
    idempotency_key: str | None = None
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def _created_matches_status(self) -> EnqueueResult:
        if self.created is not (self.status is EnqueueStatus.CREATED):
            raise ValueError("created must be true exactly when status is created")
        return self

    @property
    def ok(self) -> bool:
        return True


class EnqueueCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    queue: str
    job_type: str
    payload: dict[str, Any]
    priority: int | None = None
    scheduled_at: datetime | None = None
    idempotency_key: str | None = None
    concurrency_key: str | None = None
    affinity_key: str | None = None
    max_attempts: int | None = None
    lease_seconds: int | None = None
    backoff_mode: Literal["fixed", "exponential"] | None = None
    backoff_base: int | None = None
    backoff_cap: int | None = None
    headers: dict[str, Any] | None = None


class EnqueueManyItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = None
    scheduled_at: datetime | None = None
    idempotency_key: str | None = None
    concurrency_key: str | None = None
    affinity_key: str | None = None
    max_attempts: int | None = None
    lease_seconds: int | None = None
    backoff_mode: Literal["fixed", "exponential"] | None = None
    backoff_base: int | None = None
    backoff_cap: int | None = None
    headers: dict[str, Any] | None = None


class ClaimState(StrEnum):
    CLAIMED = "claimed"
    EMPTY = "empty"
    PAUSED = "paused"
    UNKNOWN_QUEUE = "unknown_queue"
    UNAVAILABLE = "unavailable"


class ClaimResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ClaimState
    jobs: tuple[ClaimedJob, ...] = ()

    @model_validator(mode="after")
    def _jobs_match_state(self) -> ClaimResult:
        if (self.state is ClaimState.CLAIMED) is not bool(self.jobs):
            raise ValueError("claimed state must contain jobs and other states must not")
        return self


class HeartbeatResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    cancel_requested: bool
    lease_expires_at: datetime | None


class SettleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: str
    job_status: str | None
    scheduled_at: datetime | None


class AuthorizationProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    queue: str
    job_type: str
    status: str


class JobDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    queue: str
    job_type: str
    status: str
    outcome: str | None
    priority: int
    attempt_count: int
    failure_count: int
    max_attempts: int
    created_at: datetime
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    error: str | None
    result: dict[str, Any] | None
    progress: dict[str, Any] | None
    payload: dict[str, Any] | None


class QueueStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    queue: str
    stats: dict[str, Any]


class ContractMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str
    capabilities: dict[str, Any]


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    labels: dict[str, Any]
    value: float


class CancelResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: Literal["cancelled", "cancel_requested", "already_terminal"]
    job_status: str


class EnsureQueueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: Literal["created", "updated", "unchanged"]
    profile: dict[str, Any]


class ExpireWorkerLeasesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    matched: int
    reaped: int
    skipped: int


class RedriveFailedResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    redriven: int
    skipped: int


class ClaimedJob(BaseModel):
    """Runner projection; the attempt fence is intentionally non-serializable."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    queue: str
    job_type: str
    priority: int
    payload: dict[str, Any]
    headers: dict[str, Any]
    progress: dict[str, Any] | None
    attempt_id: UUID = Field(exclude=True, repr=False)
    attempt_number: int
    failure_count: int
    max_attempts: int
    lease_expires_at: datetime
    workflow_id: UUID | None = None
    step_key: str | None = None


ClaimResult.model_rebuild()


__all__ = [
    "AuthorizationProjection",
    "CancelResult",
    "ClaimedJob",
    "ClaimResult",
    "ClaimState",
    "ContractMeta",
    "EnqueueCommand",
    "EnqueueManyItem",
    "EnqueueResult",
    "EnqueueStatus",
    "EnsureQueueResult",
    "ExpireWorkerLeasesResult",
    "HeartbeatResult",
    "JobDetail",
    "Metric",
    "QueueStats",
    "RedriveFailedResult",
    "SettleResult",
    "TQ_ERROR_REGISTRY",
    "TqCode",
    "TqErrorSpec",
]
