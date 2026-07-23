"""Closed Protocol-v1 value models shared by every taskq transport.

Model extras are direction-aware. Inbound command values forbid unknown fields
so caller typos fail before crossing a transport boundary. Outbound/result
values ignore unknown fields because ADR-005 permits additive response fields:
an older client must continue decoding a newer compatible producer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from dataclasses import dataclass
from enum import StrEnum
import json
import re
from types import MappingProxyType
from typing import Annotated, Any, Final, Generic, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

PROTOCOL_MAJOR: Final = 1
PROTOCOL_DOCUMENT_REVISION: Final = "1.0.10"
T = TypeVar("T")


def _json_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )


def _bounded_json(value: Any, limit: int, field: str) -> Any:
    if value is not None and _json_size(value) > limit:
        raise ValueError(f"{field} exceeds {limit} UTF-8 bytes")
    return value


class Followup(BaseModel):
    """Closed queue-native child specification emitted only at completion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    job_type: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
    )
    queue: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,57}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = Field(default=None, ge=0, le=1000)
    max_attempts: int | None = Field(default=None, ge=1, le=100)
    lease_seconds: int | None = Field(default=None, ge=15, le=86400)
    scheduled_at: datetime | None = None

    @field_validator("payload")
    @classmethod
    def _payload_bound(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, 65536, "followup payload")

    @field_validator("headers")
    @classmethod
    def _headers_bound(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, 8192, "followup headers")


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


class ProtocolError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    code: TqCode | Literal["AUTH401", "AUTH403"]
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    protocol_version: Literal[1]
    request_id: str
    error: ProtocolError


class CommandEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(frozen=True, extra="ignore")

    protocol_version: Literal[1]
    request_id: str
    outcome: str
    data: T


class EnqueueStatus(StrEnum):
    CREATED = "created"
    EXISTED = "existed"


class JobStatus(StrEnum):
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SettleOutcome(StrEnum):
    OK = "ok"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD = "dead"
    ALREADY_SETTLED = "already_settled"
    SETTLE_CONFLICT = "settle_conflict"
    LOST = "lost"


class QueueControlOutcome(StrEnum):
    PAUSED = "paused"
    ALREADY_PAUSED = "already_paused"
    RESUMED = "resumed"
    ALREADY_RESUMED = "already_resumed"


class ConfigChangeOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class CommandOkOutcome(StrEnum):
    OK = "ok"


class ExpireJobOutcome(StrEnum):
    NOT_RUNNING = "not_running"
    EXPIRED_AND_REAPED = "expired_and_reaped"


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"
    CANCEL_REQUESTED = "cancel_requested"
    ALREADY_TERMINAL = "already_terminal"


class _EnqueueResultBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: EnqueueStatus
    job_id: UUID
    created: bool
    queue: str
    job_type: str
    idempotency_key: str | None = None
    scheduled_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return True


class EnqueueCreatedResult(_EnqueueResultBase):
    status: Literal[EnqueueStatus.CREATED] = EnqueueStatus.CREATED
    created: Literal[True] = True


class EnqueueExistedResult(_EnqueueResultBase):
    status: Literal[EnqueueStatus.EXISTED] = EnqueueStatus.EXISTED
    created: Literal[False] = False


EnqueueResult: TypeAlias = Annotated[
    EnqueueCreatedResult | EnqueueExistedResult,
    Field(discriminator="status"),
]
ENQUEUE_RESULT_ADAPTER: Final[TypeAdapter[EnqueueResult]] = TypeAdapter(EnqueueResult)


class EnqueueCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    workflow_id: UUID | None = None
    step_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    depends_on: tuple[UUID, ...] | None = Field(default=None, max_length=100)
    headers: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _workflow_shape(self) -> EnqueueCommand:
        if (self.workflow_id is None) != (self.step_key is None):
            raise ValueError("workflow_id and step_key must be supplied together")
        if self.workflow_id is None and self.depends_on:
            raise ValueError("depends_on requires workflow_id")
        if self.depends_on is not None and len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must contain distinct job ids")
        if self.workflow_id is not None and self.workflow_id.int == 0:
            raise ValueError("workflow_id must be non-nil")
        if self.depends_on is not None and any(item.int == 0 for item in self.depends_on):
            raise ValueError("depends_on must contain non-nil job ids")
        return self

    @field_validator("step_key")
    @classmethod
    def _step_key_bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 64:
            raise ValueError("step_key exceeds 64 UTF-8 bytes")
        return value


class EnqueueManyItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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


ENQUEUE_MANY_ITEMS_ADAPTER: Final[TypeAdapter[list[EnqueueManyItem]]] = TypeAdapter(
    list[EnqueueManyItem]
)


class EnqueueWireRequest(BaseModel):
    """Canonical path-scoped enqueue body; queue never appears in the body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
    depends_on: tuple[UUID, ...] | None = Field(default=None, max_length=100)
    step_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    workflow_id: UUID | None = None
    headers: dict[str, Any] | None = None

    @field_validator("payload")
    @classmethod
    def _payload_bound(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, 65536, "payload")

    @field_validator("headers")
    @classmethod
    def _headers_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 8192, "headers")

    @model_validator(mode="after")
    def _workflow_shape(self) -> EnqueueWireRequest:
        if (self.workflow_id is None) != (self.step_key is None):
            raise ValueError("workflow_id and step_key must be supplied together")
        if self.workflow_id is None and self.depends_on:
            raise ValueError("depends_on requires workflow_id")
        if self.depends_on is not None and len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must contain distinct job ids")
        if self.workflow_id is not None and self.workflow_id.int == 0:
            raise ValueError("workflow_id must be non-nil")
        if self.depends_on is not None and any(item.int == 0 for item in self.depends_on):
            raise ValueError("depends_on must contain non-nil job ids")
        return self

    @field_validator("step_key")
    @classmethod
    def _step_key_bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 64:
            raise ValueError("step_key exceeds 64 UTF-8 bytes")
        return value


class EnqueueWireData(BaseModel):
    """Exact authoritative single-enqueue response fields (ADR-017)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID


class EnqueueManyWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[EnqueueManyItem, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _body_bound(self) -> EnqueueManyWireRequest:
        _bounded_json(self.model_dump(mode="json"), 4 * 1024 * 1024, "bulk body")
        for item in self.items:
            _bounded_json(item.payload, 65536, "payload")
            _bounded_json(item.headers, 8192, "headers")
        return self


class EnqueueManyWireItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    input_index: int = Field(ge=1)
    job_id: UUID
    outcome: EnqueueStatus


class EnqueueManyWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    items: tuple[EnqueueManyWireItem, ...]


class AdmissionReserveOutcome(StrEnum):
    RESERVED = "reserved"
    PENDING = "pending"
    ADMITTED = "admitted"


class AdmissionFinishOutcome(StrEnum):
    CREATED = "created"
    EXISTED = "existed"


class AdmissionCancelOutcome(StrEnum):
    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    EXPIRED = "expired"
    ALREADY_ADMITTED = "already_admitted"


class AdmissionReserveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=255)
    intent_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    handle: UUID = Field(repr=False, json_schema_extra={"writeOnly": True})
    reservation_ttl_seconds: int = Field(default=300, ge=15, le=3600)
    receipt_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=31_536_000)

    @field_validator("handle")
    @classmethod
    def _handle_non_nil(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("handle must be non-nil")
        return value


class AdmissionJobCommand(BaseModel):
    """Strict finish-time job command; admission and dependency authority are absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_type: str
    payload: dict[str, Any]
    priority: int | None = None
    scheduled_at: datetime | None = None
    concurrency_key: str | None = None
    affinity_key: str | None = None
    max_attempts: int | None = None
    lease_seconds: int | None = None
    backoff_mode: Literal["fixed", "exponential"] | None = None
    backoff_base: int | None = None
    backoff_cap: int | None = None
    headers: dict[str, Any] | None = None

    @field_validator("payload")
    @classmethod
    def _payload_bound(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, 65536, "payload")

    @field_validator("headers")
    @classmethod
    def _headers_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 8192, "headers")


class AdmissionFinishRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=255)
    handle: UUID = Field(repr=False, json_schema_extra={"writeOnly": True})
    job: AdmissionJobCommand
    receipt: dict[str, Any] = Field(default_factory=dict)

    @field_validator("handle")
    @classmethod
    def _handle_non_nil(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("handle must be non-nil")
        return value

    @field_validator("receipt")
    @classmethod
    def _receipt_bound(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, 2048, "receipt")


class AdmissionCancelRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=255)
    handle: UUID = Field(repr=False, json_schema_extra={"writeOnly": True})

    @field_validator("handle")
    @classmethod
    def _handle_non_nil(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("handle must be non-nil")
        return value


class AdmissionReserveWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    handle: UUID | None = Field(default=None, repr=False)
    job_id: UUID | None = None
    reservation_expires_at: datetime | None = None
    retry_after_seconds: int | None = Field(default=None, ge=1, le=3600)
    receipt: dict[str, Any] | None = None
    receipt_expires_at: datetime | None = None


class AdmissionResultWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID
    receipt: dict[str, Any]
    receipt_expires_at: datetime


class AdmissionCancelWireData(BaseModel):
    """Cancel returns admitted data only when finish already won."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID | None = None
    receipt: dict[str, Any] | None = None
    receipt_expires_at: datetime | None = None


class _AdmissionReservationBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    outcome: AdmissionReserveOutcome


class AdmissionReservedResult(_AdmissionReservationBase):
    outcome: Literal[AdmissionReserveOutcome.RESERVED] = AdmissionReserveOutcome.RESERVED
    handle: UUID = Field(repr=False)
    reservation_expires_at: datetime


class AdmissionPendingResult(_AdmissionReservationBase):
    outcome: Literal[AdmissionReserveOutcome.PENDING] = AdmissionReserveOutcome.PENDING
    reservation_expires_at: datetime
    retry_after_seconds: int = Field(ge=1, le=3600)


class AdmissionAdmittedResult(_AdmissionReservationBase):
    outcome: Literal[AdmissionReserveOutcome.ADMITTED] = AdmissionReserveOutcome.ADMITTED
    job_id: UUID
    receipt: dict[str, Any]
    receipt_expires_at: datetime


AdmissionReservationResult: TypeAlias = Annotated[
    AdmissionReservedResult | AdmissionPendingResult | AdmissionAdmittedResult,
    Field(discriminator="outcome"),
]
ADMISSION_RESERVATION_ADAPTER: Final[TypeAdapter[AdmissionReservationResult]] = TypeAdapter(
    AdmissionReservationResult
)


class AdmissionFinishResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    outcome: AdmissionFinishOutcome
    job_id: UUID
    receipt: dict[str, Any]
    receipt_expires_at: datetime


class AdmissionCancelResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    outcome: AdmissionCancelOutcome
    job_id: UUID | None = None
    receipt: dict[str, Any] | None = None
    receipt_expires_at: datetime | None = None

    @model_validator(mode="after")
    def _admitted_data_matches_outcome(self) -> AdmissionCancelResult:
        values = (self.job_id, self.receipt, self.receipt_expires_at)
        if self.outcome is AdmissionCancelOutcome.ALREADY_ADMITTED:
            if any(value is None for value in values):
                raise ValueError("already_admitted requires stored admission data")
        elif any(value is not None for value in values):
            raise ValueError("non-admitted cancellation outcomes carry no data")
        return self


class ClaimWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=200)
    batch: int = Field(default=1, ge=1, le=50)
    job_types: tuple[str, ...] | None = Field(default=None, max_length=20)
    lease_seconds: int | None = Field(default=None, ge=15, le=86400)
    affinity_key: str | None = None
    job_id: UUID | None = None
    wait_seconds: float = Field(default=0, ge=0, le=30)


class AttemptRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: UUID = Field(repr=False, json_schema_extra={"writeOnly": True})
    worker_id: str = Field(min_length=1, max_length=200)


class HeartbeatWireRequest(AttemptRequest):
    lease_seconds: int | None = Field(default=None, ge=15, le=86400)
    progress: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None

    @field_validator("progress")
    @classmethod
    def _progress_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 2048, "progress")


class CompleteWireRequest(AttemptRequest):
    result: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    followups: tuple[Followup, ...] | None = Field(default=None, max_length=20)

    @field_validator("result")
    @classmethod
    def _result_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 8192, "result")


class FailWireRequest(AttemptRequest):
    error: str
    retryable: bool = True
    retry_after_seconds: int | None = Field(default=None, ge=0)
    progress: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None

    @field_validator("progress")
    @classmethod
    def _progress_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 2048, "progress")


class SnoozeWireRequest(AttemptRequest):
    delay_seconds: int = Field(ge=0, le=2592000)
    reason: str | None = None
    progress: dict[str, Any] | None = None

    @field_validator("progress")
    @classmethod
    def _progress_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 2048, "progress")


class ReleaseWireRequest(AttemptRequest):
    cause: Literal["released", "worker_shutdown", "no_handler"]
    delay_seconds: int = Field(default=0, ge=0, le=86400)
    progress: dict[str, Any] | None = None

    @field_validator("progress")
    @classmethod
    def _progress_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 2048, "progress")


class CancelRunningWireRequest(AttemptRequest):
    reason: str


class WorkerPresenceWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=200)
    queues: tuple[str, ...] = Field(min_length=1)
    hostname: str | None = Field(default=None, max_length=200)
    pid: int | None = Field(default=None, gt=0, le=2147483647)
    version: str | None = Field(default=None, max_length=200)
    meta: dict[str, Any] | None = None

    @field_validator("queues")
    @classmethod
    def _distinct_queues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("queues must be distinct")
        return value

    @field_validator("meta")
    @classmethod
    def _meta_bound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value, 8192, "meta")


class HeartbeatWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    cancel_requested: bool
    lease_expires_at: datetime | None


class SettleWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    job_status: JobStatus | None = None
    scheduled_at: datetime | None = None
    prior_verb: str | None = None


class WorkerPresenceWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    shutdown_requested: bool


class EnsureQueueWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: dict[str, Any] = Field(default_factory=dict)


class EmptyWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EnsureQueueWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    profile: QueueProfile


class QueueProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    profile_version: int = Field(ge=1)
    default_priority: int = Field(ge=0, le=1000)
    default_lease_seconds: int = Field(ge=15, le=86400)
    default_max_attempts: int = Field(ge=1, le=100)
    default_backoff_mode: Literal["fixed", "exponential"]
    default_backoff_base: int = Field(ge=1, le=86400)
    default_backoff_cap: int = Field(ge=1, le=86400)
    retention_hours: int = Field(ge=1)
    failed_retention_hours: int = Field(ge=1)
    max_depth: int | None = Field(default=None, ge=1)
    notify_enabled: bool
    paused: bool


class JobListItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID
    job_type: str
    status: JobStatus
    outcome: str | None = None
    priority: int
    attempt_count: int
    failure_count: int
    max_attempts: int
    created_at: datetime
    scheduled_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class JobPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    as_of: datetime
    items: tuple[JobListItem, ...]
    next_after: dict[str, Any] | None = None


class JobPageWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    as_of: datetime
    items: tuple[JobListItem, ...]
    next_cursor: str | None = None


class ReasonWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str | None = None


class CancelWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    job_status: JobStatus


class RedriveWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reset_progress: bool = False


class PurgeWireRequest(ReasonWireRequest):
    limit: int = Field(default=100, ge=1, le=1000)


class ReprioritizeWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    priority: int = Field(ge=0, le=1000)


class ConcurrencyLimitWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_running: int = Field(ge=0)


class ShutdownRequestWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_id: str | None = None
    queue: str | None = None


class CountWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    count: int = Field(ge=0)


class ClaimState(StrEnum):
    CLAIMED = "claimed"
    EMPTY = "empty"
    PAUSED = "paused"
    UNKNOWN_QUEUE = "unknown_queue"
    UNAVAILABLE = "unavailable"


class ClaimResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    state: ClaimState
    jobs: tuple[ClaimedJob, ...] = ()

    @model_validator(mode="after")
    def _jobs_match_state(self) -> ClaimResult:
        if (self.state is ClaimState.CLAIMED) is not bool(self.jobs):
            raise ValueError("claimed state must contain jobs and other states must not")
        return self


class HeartbeatResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool
    cancel_requested: bool
    lease_expires_at: datetime | None


class _SettleResultBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    result: SettleOutcome
    job_status: JobStatus | None
    scheduled_at: datetime | None


class SettleOkResult(_SettleResultBase):
    result: Literal[SettleOutcome.OK] = SettleOutcome.OK


class SettleRetryScheduledResult(_SettleResultBase):
    result: Literal[SettleOutcome.RETRY_SCHEDULED] = SettleOutcome.RETRY_SCHEDULED


class SettleDeadResult(_SettleResultBase):
    result: Literal[SettleOutcome.DEAD] = SettleOutcome.DEAD


class SettleAlreadySettledResult(_SettleResultBase):
    result: Literal[SettleOutcome.ALREADY_SETTLED] = SettleOutcome.ALREADY_SETTLED


class SettleConflictResult(_SettleResultBase):
    result: Literal[SettleOutcome.SETTLE_CONFLICT] = SettleOutcome.SETTLE_CONFLICT


class SettleLostResult(_SettleResultBase):
    result: Literal[SettleOutcome.LOST] = SettleOutcome.LOST


SettleResult: TypeAlias = Annotated[
    SettleOkResult
    | SettleRetryScheduledResult
    | SettleDeadResult
    | SettleAlreadySettledResult
    | SettleConflictResult
    | SettleLostResult,
    Field(discriminator="result"),
]
SETTLE_RESULT_ADAPTER: Final[TypeAdapter[SettleResult]] = TypeAdapter(SettleResult)


class AuthorizationProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID
    queue: str
    job_type: str
    status: JobStatus


class JobDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID
    queue: str
    job_type: str
    status: JobStatus
    outcome: str | None = None
    priority: int
    attempt_count: int
    failure_count: int
    max_attempts: int
    created_at: datetime
    scheduled_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
    error: str | None = None
    result: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


class QueueStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    as_of: datetime
    queue: str
    stats: dict[str, Any]


class QueueStatsWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    items: tuple[QueueStats, ...]


class ContractMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    contract_version: str
    capabilities: dict[str, Any]


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    labels: dict[str, Any]
    value: float


class CancelResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    result: CancelOutcome
    job_status: JobStatus


class EnsureQueueResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    result: ConfigChangeOutcome
    profile: dict[str, Any]


class ExpireWorkerLeasesResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    matched: int
    reaped: int
    skipped: int


class RedriveFailedResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    redriven: int
    skipped: int


class WorkflowKind(StrEnum):
    DAG = "dag"
    BATCH = "batch"


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    outcome: Literal[
        "created",
        "existed",
        "sealed",
        "already_sealed",
        "cancel_requested",
        "already_requested",
        "already_terminal",
    ]
    workflow_id: UUID
    status: WorkflowStatus


class WorkflowAuthorizationProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    workflow_id: UUID
    declared_queues: tuple[str, ...]


class CreateWorkflowWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_key: str = Field(min_length=1, max_length=255)
    kind: WorkflowKind
    params: dict[str, Any] = Field(default_factory=dict)
    declared_queues: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("workflow_key")
    @classmethod
    def _workflow_key_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 255:
            raise ValueError("workflow_key exceeds 255 UTF-8 bytes")
        return value

    @field_validator("params")
    @classmethod
    def _params_bound(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value, 65536, "params")

    @field_validator("declared_queues")
    @classmethod
    def _declared_queue_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("declared_queues must contain distinct queue names")
        if any(re.fullmatch(r"[a-z0-9_]{1,57}", queue) is None for queue in value):
            raise ValueError("declared_queues contains an invalid queue name")
        return value


class WorkflowWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    workflow_id: UUID
    status: WorkflowStatus


class WorkflowCancelWireRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=2048)

    @field_validator("reason")
    @classmethod
    def _reason_bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 2048:
            raise ValueError("reason exceeds 2048 UTF-8 bytes")
        return value


class CommandName(StrEnum):
    RESERVE_ADMISSION = "reserve_admission"
    FINISH_ADMISSION = "finish_admission"
    CANCEL_ADMISSION = "cancel_admission"
    ENQUEUE = "enqueue"
    ENQUEUE_MANY = "enqueue_many"
    CREATE_WORKFLOW = "create_workflow"
    SEAL_WORKFLOW = "seal_workflow"
    CLAIM = "claim"
    HEARTBEAT = "heartbeat"
    COMPLETE = "complete"
    FAIL = "fail"
    SNOOZE = "snooze"
    RELEASE = "release"
    CANCEL_RUNNING = "cancel_running"
    WORKER_HEARTBEAT = "worker_heartbeat"
    GET_AUTHORIZATION_PROJECTION = "get_authorization_projection"
    GET_WORKFLOW_AUTHORIZATION_PROJECTION = "get_workflow_authorization_projection"
    GET_JOB = "get_job"
    GET_QUEUE_STATS = "get_queue_stats"
    GET_QUEUE_PROFILE = "get_queue_profile"
    LIST_JOBS = "list_jobs"
    GET_CONTRACT_META = "get_contract_meta"
    METRICS = "metrics"
    ENSURE_QUEUE = "ensure_queue"
    UPDATE_QUEUE_PROFILE = "update_queue_profile"
    PAUSE_QUEUE = "pause_queue"
    RESUME_QUEUE = "resume_queue"
    SET_CONCURRENCY_LIMIT = "set_concurrency_limit"
    REQUEST_WORKER_SHUTDOWN = "request_worker_shutdown"
    PURGE_QUEUED = "purge_queued"
    RUN_NOW = "run_now"
    REPRIORITIZE = "reprioritize"
    CANCEL = "cancel"
    CANCEL_WORKFLOW = "cancel_workflow"
    REDRIVE = "redrive"
    REDRIVE_FAILED = "redrive_failed"
    EXPIRE_JOB = "expire_job"
    EXPIRE_WORKER_LEASES = "expire_worker_leases"
    TICK = "tick"
    JANITOR = "janitor"


class CapabilityRole(StrEnum):
    PRODUCER = "taskq_producer"
    RUNNER = "taskq_runner"
    OBSERVER = "taskq_observer"
    OPERATOR = "taskq_operator"
    HOUSEKEEPER = "taskq_housekeeper"


class ReplayRule(StrEnum):
    FENCED = "verb-aware attempt replay"
    STATE_DERIVED = "state-derived idempotency or documented repeat"


class HttpCommandName(StrEnum):
    META = "meta"
    ENSURE_QUEUE = "ensure_queue"
    ENQUEUE = "enqueue"
    ENQUEUE_MANY = "enqueue_many"
    RESERVE_ADMISSION = "reserve_admission"
    FINISH_ADMISSION = "finish_admission"
    CANCEL_ADMISSION = "cancel_admission"
    CREATE_WORKFLOW = "create_workflow"
    SEAL_WORKFLOW = "seal_workflow"
    CANCEL_WORKFLOW = "cancel_workflow"
    CLAIM = "claim"
    HEARTBEAT = "heartbeat"
    COMPLETE = "complete"
    FAIL = "fail"
    RELEASE = "release"
    SNOOZE = "snooze"
    CANCEL_RUNNING = "cancel_running"
    WORKER_HEARTBEAT = "worker_heartbeat"
    GET_JOB = "get_job"
    GET_QUEUE_STATS = "get_queue_stats"
    LIST_QUEUE_STATS = "list_queue_stats"
    METRICS = "metrics"
    PAUSE_QUEUE = "pause_queue"
    RESUME_QUEUE = "resume_queue"
    CANCEL = "cancel"
    REDRIVE = "redrive"
    EXPIRE_JOB = "expire_job"
    EXPIRE_WORKER_LEASES = "expire_worker_leases"
    PURGE_QUEUED = "purge_queued"
    RUN_NOW = "run_now"
    REPRIORITIZE = "reprioritize"
    SET_CONCURRENCY_LIMIT = "set_concurrency_limit"
    REQUEST_WORKER_SHUTDOWN = "request_worker_shutdown"
    LIST_WORKERS = "list_workers"
    GET_QUEUE = "get_queue"
    LIST_JOBS = "list_jobs"


class TaskqAction(StrEnum):
    ENQUEUE = "enqueue"
    RUN = "run"
    READ = "read"
    CONTROL = "control"
    ADMIN = "admin"


class QueueSource(StrEnum):
    PATH = "path"
    JOB_LOOKUP = "job_lookup"
    WORKFLOW_LOOKUP = "workflow_lookup"
    DECLARED_QUEUES = "declared_queues"
    GLOBAL = "global"
    DEPLOYMENT = "deployment_policy"
    QUERY = "query"


class HttpSurface(StrEnum):
    ACTIVE = "active"
    GATED = "gated"
    DEFERRED = "deferred"


class RetryClass(StrEnum):
    NEVER = "never"
    KEYED_ENQUEUE = "keyed_enqueue"
    KEYED_BATCH = "keyed_batch"
    SAFE_IDEMPOTENT = "safe_idempotent"
    WORKER_SETTLEMENT = "worker_settlement"


@dataclass(frozen=True, slots=True)
class HttpCommandSpec:
    method: Literal["GET", "POST", "PUT"]
    path: str
    action: TaskqAction | None
    queue_source: QueueSource
    surface: HttpSurface
    outcomes: Mapping[str, int]
    retry_class: RetryClass
    sql_command: CommandName | None
    request_model: type[BaseModel] | None = None
    data_model: type[BaseModel] | None = None
    enveloped: bool = True

    @property
    def active(self) -> bool:
        return self.surface is HttpSurface.ACTIVE

    @property
    def errors(self) -> frozenset[TqCode]:
        if self.surface is not HttpSurface.ACTIVE:
            return frozenset({TqCode.CAPABILITY})
        command_errors = (
            COMMAND_SPECS[self.sql_command].errors if self.sql_command is not None else frozenset()
        )
        return frozenset(command_errors | {TqCode.VERSION, TqCode.INTERNAL, TqCode.UNAVAILABLE})


@dataclass(frozen=True, slots=True)
class CommandSpec:
    sql_function: str
    capability: CapabilityRole
    outcomes: frozenset[str]
    errors: frozenset[TqCode]
    replay_rule: ReplayRule = ReplayRule.STATE_DERIVED

    @property
    def retryable_errors(self) -> frozenset[TqCode]:
        return frozenset(code for code in self.errors if TQ_ERROR_REGISTRY[code].retryable)


def _spec(
    sql_function: str,
    capability: CapabilityRole,
    outcomes: tuple[str, ...],
    errors: tuple[TqCode, ...] = (),
    replay_rule: ReplayRule = ReplayRule.STATE_DERIVED,
) -> CommandSpec:
    return CommandSpec(
        sql_function=sql_function,
        capability=capability,
        outcomes=frozenset(outcomes),
        errors=frozenset(errors),
        replay_rule=replay_rule,
    )


_FENCED = ReplayRule.FENCED
_PRODUCER = CapabilityRole.PRODUCER
_RUNNER = CapabilityRole.RUNNER
_OBSERVER = CapabilityRole.OBSERVER
_OPERATOR = CapabilityRole.OPERATOR
_HOUSEKEEPER = CapabilityRole.HOUSEKEEPER

# Single Python source for Protocol-v1 command names, SQL identities, capability
# roles, closed outcomes, public TQ errors, and replay metadata. Parity tests
# audit this independent projection against the Tier-0-derived SQL manifest.
COMMAND_SPECS: Final = MappingProxyType(
    {
        CommandName.RESERVE_ADMISSION: _spec(
            "taskq.reserve_admission(text,text,text,uuid,integer,integer)",
            _PRODUCER,
            tuple(item.value for item in AdmissionReserveOutcome),
            (TqCode.NOT_FOUND, TqCode.CONFLICT, TqCode.VALIDATION),
        ),
        CommandName.FINISH_ADMISSION: _spec(
            "taskq.finish_admission(text,text,uuid,jsonb,jsonb)",
            _PRODUCER,
            tuple(item.value for item in AdmissionFinishOutcome),
            (
                TqCode.NOT_FOUND,
                TqCode.CONFLICT,
                TqCode.VALIDATION,
                TqCode.BACKPRESSURE,
                TqCode.INTERNAL,
            ),
        ),
        CommandName.CANCEL_ADMISSION: _spec(
            "taskq.cancel_admission(text,text,uuid)",
            _PRODUCER,
            tuple(item.value for item in AdmissionCancelOutcome),
            (TqCode.NOT_FOUND, TqCode.CONFLICT, TqCode.VALIDATION),
        ),
        CommandName.ENQUEUE: _spec(
            "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)",
            _PRODUCER,
            tuple(item.value for item in EnqueueStatus),
            (
                TqCode.NOT_FOUND,
                TqCode.CONFLICT,
                TqCode.VALIDATION,
                TqCode.BACKPRESSURE,
                TqCode.INTERNAL,
            ),
        ),
        CommandName.ENQUEUE_MANY: _spec(
            "taskq.enqueue_many(text,jsonb)",
            _PRODUCER,
            tuple(item.value for item in EnqueueStatus),
            (TqCode.NOT_FOUND, TqCode.VALIDATION, TqCode.BACKPRESSURE, TqCode.INTERNAL),
        ),
        CommandName.CREATE_WORKFLOW: _spec(
            "taskq.create_workflow(text,text,jsonb,text[],text)",
            _PRODUCER,
            ("created", "existed"),
            (TqCode.NOT_FOUND, TqCode.CONFLICT, TqCode.VALIDATION),
        ),
        CommandName.SEAL_WORKFLOW: _spec(
            "taskq.seal_workflow(uuid,text)",
            _PRODUCER,
            ("sealed", "already_sealed"),
            (TqCode.NOT_FOUND,),
        ),
        CommandName.CLAIM: _spec(
            "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)",
            _RUNNER,
            tuple(item.value for item in ClaimState),
            (TqCode.VALIDATION,),
        ),
        CommandName.HEARTBEAT: _spec(
            "taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb)",
            _RUNNER,
            ("ok", "lost"),
            (TqCode.VALIDATION,),
        ),
        CommandName.COMPLETE: _spec(
            "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)",
            _RUNNER,
            ("ok", "already_settled", "settle_conflict", "lost"),
            (TqCode.VALIDATION, TqCode.CAPABILITY),
            _FENCED,
        ),
        CommandName.FAIL: _spec(
            "taskq.fail_job(uuid,uuid,text,text,boolean,integer,jsonb,jsonb)",
            _RUNNER,
            ("ok", "retry_scheduled", "dead", "already_settled", "settle_conflict", "lost"),
            (TqCode.VALIDATION,),
            _FENCED,
        ),
        CommandName.SNOOZE: _spec(
            "taskq.snooze_job(uuid,uuid,text,integer,text,jsonb)",
            _RUNNER,
            ("ok", "already_settled", "settle_conflict", "lost"),
            (TqCode.VALIDATION,),
            _FENCED,
        ),
        CommandName.RELEASE: _spec(
            "taskq.release_job(uuid,uuid,text,text,integer,jsonb)",
            _RUNNER,
            ("ok", "already_settled", "settle_conflict", "lost"),
            (TqCode.VALIDATION,),
            _FENCED,
        ),
        CommandName.CANCEL_RUNNING: _spec(
            "taskq.cancel_running_job(uuid,uuid,text,text)",
            _RUNNER,
            ("ok", "already_settled", "settle_conflict", "lost"),
            replay_rule=_FENCED,
        ),
        CommandName.WORKER_HEARTBEAT: _spec(
            "taskq.worker_heartbeat(text,text[],text,integer,text,jsonb)",
            _RUNNER,
            ("continue", "shutdown_requested"),
            (TqCode.VALIDATION,),
        ),
        CommandName.GET_AUTHORIZATION_PROJECTION: _spec(
            "taskq.get_authorization_projection(uuid)", _OBSERVER, ("ok", "missing")
        ),
        CommandName.GET_WORKFLOW_AUTHORIZATION_PROJECTION: _spec(
            "taskq.get_workflow_authorization_projection(uuid)",
            _OBSERVER,
            ("ok",),
            (TqCode.NOT_FOUND,),
        ),
        CommandName.GET_JOB: _spec(
            "taskq.get_job(uuid,boolean,boolean,boolean,boolean)",
            _OBSERVER,
            ("ok", "missing"),
        ),
        CommandName.GET_QUEUE_STATS: _spec("taskq.get_queue_stats(text)", _OBSERVER, ("ok",)),
        CommandName.GET_QUEUE_PROFILE: _spec("taskq.get_queue_profile(text)", _OBSERVER, ("ok",)),
        CommandName.LIST_JOBS: _spec(
            "taskq.list_jobs(text,text,integer,jsonb)",
            _OBSERVER,
            ("ok",),
            (TqCode.NOT_FOUND, TqCode.VALIDATION, TqCode.CAPABILITY),
        ),
        CommandName.GET_CONTRACT_META: _spec("taskq.get_contract_meta()", _OBSERVER, ("ok",)),
        CommandName.METRICS: _spec("taskq.metrics()", _OBSERVER, ("ok",)),
        CommandName.ENSURE_QUEUE: _spec(
            "taskq.ensure_queue(text,jsonb,text)",
            _OPERATOR,
            tuple(item.value for item in ConfigChangeOutcome),
            (TqCode.VALIDATION,),
        ),
        CommandName.UPDATE_QUEUE_PROFILE: _spec(
            "taskq.update_queue_profile(text,jsonb,text,bigint)",
            _OPERATOR,
            ("updated", "profile_version_conflict"),
            (TqCode.NOT_FOUND, TqCode.VALIDATION),
        ),
        CommandName.PAUSE_QUEUE: _spec(
            "taskq.pause_queue(text,text,text)",
            _OPERATOR,
            ("paused", "already_paused"),
            (TqCode.NOT_FOUND,),
        ),
        CommandName.RESUME_QUEUE: _spec(
            "taskq.resume_queue(text,text)",
            _OPERATOR,
            ("resumed", "already_resumed"),
            (TqCode.NOT_FOUND,),
        ),
        CommandName.SET_CONCURRENCY_LIMIT: _spec(
            "taskq.set_concurrency_limit(text,integer,text)",
            _OPERATOR,
            tuple(item.value for item in ConfigChangeOutcome),
            (TqCode.VALIDATION,),
        ),
        CommandName.REQUEST_WORKER_SHUTDOWN: _spec(
            "taskq.request_worker_shutdown(text,text,text)", _OPERATOR, ("ok",)
        ),
        CommandName.PURGE_QUEUED: _spec(
            "taskq.purge_queued(text,integer,text,text)",
            _OPERATOR,
            ("ok",),
            (TqCode.NOT_FOUND, TqCode.VALIDATION),
        ),
        CommandName.RUN_NOW: _spec(
            "taskq.run_now(uuid,text)",
            _OPERATOR,
            ("ok",),
            (TqCode.NOT_FOUND, TqCode.CONFLICT),
        ),
        CommandName.REPRIORITIZE: _spec(
            "taskq.reprioritize(uuid,smallint,text)",
            _OPERATOR,
            ("ok",),
            (TqCode.NOT_FOUND, TqCode.CONFLICT, TqCode.VALIDATION),
        ),
        CommandName.CANCEL: _spec(
            "taskq.cancel_job(uuid,text,text)",
            _OPERATOR,
            tuple(item.value for item in CancelOutcome),
            (TqCode.NOT_FOUND,),
        ),
        CommandName.CANCEL_WORKFLOW: _spec(
            "taskq.cancel_workflow(uuid,text,text)",
            _OPERATOR,
            ("cancel_requested", "already_requested", "already_terminal"),
            (TqCode.NOT_FOUND, TqCode.VALIDATION),
        ),
        CommandName.REDRIVE: _spec(
            "taskq.redrive_job(uuid,text,boolean)",
            _OPERATOR,
            ("redriven",),
            (TqCode.NOT_FOUND, TqCode.CONFLICT),
        ),
        CommandName.REDRIVE_FAILED: _spec(
            "taskq.redrive_failed(text,integer,text)",
            _OPERATOR,
            ("ok",),
            (TqCode.VALIDATION,),
        ),
        CommandName.EXPIRE_JOB: _spec(
            "taskq.expire_job(uuid,text)",
            _OPERATOR,
            tuple(item.value for item in ExpireJobOutcome),
            (TqCode.NOT_FOUND,),
        ),
        CommandName.EXPIRE_WORKER_LEASES: _spec(
            "taskq.expire_worker_leases(text,text)", _OPERATOR, ("ok",)
        ),
        CommandName.TICK: _spec("taskq.tick(integer)", _HOUSEKEEPER, ("ok",), (TqCode.VALIDATION,)),
        CommandName.JANITOR: _spec("taskq.janitor()", _HOUSEKEEPER, ("ok",)),
    }
)


class ClaimedJob(BaseModel):
    """Runner projection; the attempt fence is intentionally non-serializable."""

    model_config = ConfigDict(frozen=True, extra="ignore")

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
    lease_seconds: int = Field(ge=15, le=86400)


ClaimResult.model_rebuild()
CLAIM_BATCH_ADAPTER: Final[TypeAdapter[ClaimResult]] = TypeAdapter(ClaimResult)


class ClaimedJobWire(BaseModel):
    """Claim-only wire projection; this is the sole response model carrying a fence."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: UUID
    queue: str
    job_type: str
    priority: int
    payload: dict[str, Any]
    headers: dict[str, Any]
    progress: dict[str, Any] | None = None
    attempt_id: UUID = Field(repr=False)
    attempt_number: int
    failure_count: int
    max_attempts: int
    lease_expires_at: datetime
    workflow_id: UUID | None = None
    step_key: str | None = None
    lease_seconds: int = Field(ge=15, le=86400)

    def to_core(self) -> ClaimedJob:
        return ClaimedJob.model_validate(self.model_dump())


class ClaimWireData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    jobs: tuple[ClaimedJobWire, ...] = ()
    elapsed_seconds: float | None = None
    deadline: datetime | None = None


def _status_map(**outcomes: int) -> Mapping[str, int]:
    return MappingProxyType(dict(outcomes))


def _http(
    method: Literal["GET", "POST", "PUT"],
    path: str,
    action: TaskqAction | None,
    queue_source: QueueSource,
    outcomes: Mapping[str, int],
    retry_class: RetryClass,
    sql_command: CommandName | None,
    *,
    surface: HttpSurface = HttpSurface.ACTIVE,
    request_model: type[BaseModel] | None = None,
    data_model: type[BaseModel] | None = None,
    enveloped: bool = True,
) -> HttpCommandSpec:
    return HttpCommandSpec(
        method=method,
        path=path,
        action=action,
        queue_source=queue_source,
        surface=surface,
        outcomes=MappingProxyType(dict(outcomes)),
        retry_class=retry_class,
        sql_command=sql_command,
        request_model=request_model,
        data_model=data_model,
        enveloped=enveloped,
    )


_RUN = TaskqAction.RUN
_READ = TaskqAction.READ
_CONTROL = TaskqAction.CONTROL
_ADMIN = TaskqAction.ADMIN
_PATH = QueueSource.PATH
_LOOKUP = QueueSource.JOB_LOOKUP
_GLOBAL = QueueSource.GLOBAL
_QUERY = QueueSource.QUERY
_SAFE = RetryClass.SAFE_IDEMPOTENT
_NEVER = RetryClass.NEVER
_SETTLE = RetryClass.WORKER_SETTLEMENT

# Human-maintained protocol metadata. Tests derive the expected identities and
# mappings independently from Tier 0 and never import this table for expected values.
HTTP_COMMAND_SPECS: Final = MappingProxyType(
    {
        HttpCommandName.META: _http(
            "GET",
            "/taskq/v1/meta",
            _READ,
            QueueSource.DEPLOYMENT,
            _status_map(ok=200),
            _SAFE,
            CommandName.GET_CONTRACT_META,
            data_model=ContractMeta,
        ),
        HttpCommandName.ENSURE_QUEUE: _http(
            "PUT",
            "/taskq/v1/queues/{queue}",
            _ADMIN,
            _PATH,
            _status_map(created=201, updated=200, unchanged=200),
            _SAFE,
            CommandName.ENSURE_QUEUE,
            request_model=EnsureQueueWireRequest,
            data_model=EnsureQueueWireData,
        ),
        HttpCommandName.ENQUEUE: _http(
            "POST",
            "/taskq/v1/queues/{queue}/jobs",
            TaskqAction.ENQUEUE,
            _PATH,
            _status_map(created=201, existed=200),
            RetryClass.KEYED_ENQUEUE,
            CommandName.ENQUEUE,
            request_model=EnqueueWireRequest,
            data_model=EnqueueWireData,
        ),
        HttpCommandName.ENQUEUE_MANY: _http(
            "POST",
            "/taskq/v1/queues/{queue}/jobs/batch",
            TaskqAction.ENQUEUE,
            _PATH,
            _status_map(ok=200),
            RetryClass.KEYED_BATCH,
            CommandName.ENQUEUE_MANY,
            request_model=EnqueueManyWireRequest,
            data_model=EnqueueManyWireData,
        ),
        HttpCommandName.RESERVE_ADMISSION: _http(
            "POST",
            "/taskq/v1/queues/{queue}/admissions/reserve",
            TaskqAction.ENQUEUE,
            _PATH,
            _status_map(reserved=200, pending=202, admitted=200),
            _SAFE,
            CommandName.RESERVE_ADMISSION,
            request_model=AdmissionReserveRequest,
            data_model=AdmissionReserveWireData,
        ),
        HttpCommandName.FINISH_ADMISSION: _http(
            "POST",
            "/taskq/v1/queues/{queue}/admissions/finish",
            TaskqAction.ENQUEUE,
            _PATH,
            _status_map(created=201, existed=200),
            _SAFE,
            CommandName.FINISH_ADMISSION,
            request_model=AdmissionFinishRequest,
            data_model=AdmissionResultWireData,
        ),
        HttpCommandName.CANCEL_ADMISSION: _http(
            "POST",
            "/taskq/v1/queues/{queue}/admissions/cancel",
            TaskqAction.ENQUEUE,
            _PATH,
            _status_map(cancelled=200, already_cancelled=200, expired=200, already_admitted=200),
            _SAFE,
            CommandName.CANCEL_ADMISSION,
            request_model=AdmissionCancelRequest,
            data_model=AdmissionCancelWireData,
        ),
        HttpCommandName.CREATE_WORKFLOW: _http(
            "POST",
            "/taskq/v1/workflows",
            TaskqAction.ENQUEUE,
            QueueSource.DECLARED_QUEUES,
            _status_map(created=201, existed=200),
            _SAFE,
            CommandName.CREATE_WORKFLOW,
            request_model=CreateWorkflowWireRequest,
            data_model=WorkflowWireData,
        ),
        HttpCommandName.SEAL_WORKFLOW: _http(
            "POST",
            "/taskq/v1/workflows/{id}/seal",
            TaskqAction.ENQUEUE,
            QueueSource.WORKFLOW_LOOKUP,
            _status_map(sealed=200, already_sealed=200),
            _SAFE,
            CommandName.SEAL_WORKFLOW,
            request_model=EmptyWireRequest,
            data_model=WorkflowWireData,
        ),
        HttpCommandName.CANCEL_WORKFLOW: _http(
            "POST",
            "/taskq/v1/workflows/{id}/cancel",
            _CONTROL,
            QueueSource.WORKFLOW_LOOKUP,
            _status_map(cancel_requested=202, already_requested=202, already_terminal=200),
            _SAFE,
            CommandName.CANCEL_WORKFLOW,
            request_model=WorkflowCancelWireRequest,
            data_model=WorkflowWireData,
        ),
        HttpCommandName.CLAIM: _http(
            "POST",
            "/taskq/v1/queues/{queue}/claims",
            _RUN,
            _PATH,
            _status_map(claimed=200, empty=200, timeout=200, paused=200, unavailable=200),
            _NEVER,
            CommandName.CLAIM,
            request_model=ClaimWireRequest,
            data_model=ClaimWireData,
        ),
        HttpCommandName.HEARTBEAT: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/heartbeat",
            _RUN,
            _LOOKUP,
            _status_map(ok=200, lost=409),
            _SAFE,
            CommandName.HEARTBEAT,
            request_model=HeartbeatWireRequest,
            data_model=HeartbeatWireData,
        ),
        HttpCommandName.COMPLETE: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/complete",
            _RUN,
            _LOOKUP,
            _status_map(ok=200, already_settled=200, settle_conflict=409, lost=409),
            _SETTLE,
            CommandName.COMPLETE,
            request_model=CompleteWireRequest,
            data_model=SettleWireData,
        ),
        HttpCommandName.FAIL: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/fail",
            _RUN,
            _LOOKUP,
            _status_map(
                retry_scheduled=200, dead=200, already_settled=200, settle_conflict=409, lost=409
            ),
            _SETTLE,
            CommandName.FAIL,
            request_model=FailWireRequest,
            data_model=SettleWireData,
        ),
        HttpCommandName.RELEASE: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/release",
            _RUN,
            _LOOKUP,
            _status_map(ok=200, already_settled=200, settle_conflict=409, lost=409),
            _SETTLE,
            CommandName.RELEASE,
            request_model=ReleaseWireRequest,
            data_model=SettleWireData,
        ),
        HttpCommandName.SNOOZE: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/snooze",
            _RUN,
            _LOOKUP,
            _status_map(ok=200, already_settled=200, settle_conflict=409, lost=409),
            _SETTLE,
            CommandName.SNOOZE,
            request_model=SnoozeWireRequest,
            data_model=SettleWireData,
        ),
        HttpCommandName.CANCEL_RUNNING: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/cancel-running",
            _RUN,
            _LOOKUP,
            _status_map(ok=200, already_settled=200, settle_conflict=409, lost=409),
            _SETTLE,
            CommandName.CANCEL_RUNNING,
            request_model=CancelRunningWireRequest,
            data_model=SettleWireData,
        ),
        HttpCommandName.WORKER_HEARTBEAT: _http(
            "POST",
            "/taskq/v1/workers/heartbeat",
            _RUN,
            QueueSource.DECLARED_QUEUES,
            _status_map(**{"continue": 200, "shutdown_requested": 200}),
            _SAFE,
            CommandName.WORKER_HEARTBEAT,
            request_model=WorkerPresenceWireRequest,
            data_model=WorkerPresenceWireData,
        ),
        HttpCommandName.GET_JOB: _http(
            "GET",
            "/taskq/v1/jobs/{job_id}",
            _READ,
            _LOOKUP,
            _status_map(ok=200),
            _SAFE,
            CommandName.GET_JOB,
            data_model=JobDetail,
        ),
        HttpCommandName.GET_QUEUE_STATS: _http(
            "GET",
            "/taskq/v1/stats/queues/{queue}",
            _READ,
            _PATH,
            _status_map(ok=200),
            _SAFE,
            CommandName.GET_QUEUE_STATS,
            data_model=QueueStatsWireData,
        ),
        HttpCommandName.LIST_QUEUE_STATS: _http(
            "GET",
            "/taskq/v1/stats/queues",
            _READ,
            _GLOBAL,
            _status_map(ok=200),
            _SAFE,
            CommandName.GET_QUEUE_STATS,
            data_model=QueueStatsWireData,
        ),
        HttpCommandName.METRICS: _http(
            "GET",
            "/taskq/metrics",
            None,
            QueueSource.DEPLOYMENT,
            _status_map(ok=200),
            _SAFE,
            CommandName.METRICS,
            enveloped=False,
        ),
        HttpCommandName.PAUSE_QUEUE: _http(
            "POST",
            "/taskq/v1/queues/{queue}/pause",
            _CONTROL,
            _PATH,
            _status_map(paused=200, already_paused=200),
            _SAFE,
            CommandName.PAUSE_QUEUE,
            request_model=ReasonWireRequest,
        ),
        HttpCommandName.RESUME_QUEUE: _http(
            "POST",
            "/taskq/v1/queues/{queue}/resume",
            _CONTROL,
            _PATH,
            _status_map(resumed=200, already_resumed=200),
            _SAFE,
            CommandName.RESUME_QUEUE,
            request_model=EmptyWireRequest,
        ),
        HttpCommandName.CANCEL: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/cancel",
            _CONTROL,
            _LOOKUP,
            _status_map(cancelled=200, cancel_requested=202, already_terminal=200),
            _SAFE,
            CommandName.CANCEL,
            request_model=ReasonWireRequest,
            data_model=CancelWireData,
        ),
        HttpCommandName.REDRIVE: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/redrive",
            _CONTROL,
            _LOOKUP,
            _status_map(redriven=200),
            _SAFE,
            CommandName.REDRIVE,
            request_model=RedriveWireRequest,
        ),
        HttpCommandName.EXPIRE_JOB: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/expire",
            _CONTROL,
            _LOOKUP,
            _status_map(expired_and_reaped=200, not_running=409),
            _SAFE,
            CommandName.EXPIRE_JOB,
            request_model=EmptyWireRequest,
        ),
        HttpCommandName.EXPIRE_WORKER_LEASES: _http(
            "POST",
            "/taskq/v1/workers/{worker_id}/expire-leases",
            _CONTROL,
            _GLOBAL,
            _status_map(ok=200),
            _SAFE,
            CommandName.EXPIRE_WORKER_LEASES,
            request_model=EmptyWireRequest,
            data_model=ExpireWorkerLeasesResult,
        ),
        HttpCommandName.PURGE_QUEUED: _http(
            "POST",
            "/taskq/v1/queues/{queue}/purge",
            _CONTROL,
            _PATH,
            _status_map(ok=200),
            _SAFE,
            CommandName.PURGE_QUEUED,
            request_model=PurgeWireRequest,
            data_model=CountWireData,
        ),
        HttpCommandName.RUN_NOW: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/run-now",
            _CONTROL,
            _LOOKUP,
            _status_map(ok=200),
            _SAFE,
            CommandName.RUN_NOW,
            request_model=EmptyWireRequest,
        ),
        HttpCommandName.REPRIORITIZE: _http(
            "POST",
            "/taskq/v1/jobs/{job_id}/reprioritize",
            _CONTROL,
            _LOOKUP,
            _status_map(ok=200),
            _SAFE,
            CommandName.REPRIORITIZE,
            request_model=ReprioritizeWireRequest,
        ),
        HttpCommandName.SET_CONCURRENCY_LIMIT: _http(
            "PUT",
            "/taskq/v1/concurrency-limits/{key}",
            _ADMIN,
            _GLOBAL,
            _status_map(created=201, updated=200, unchanged=200),
            _SAFE,
            CommandName.SET_CONCURRENCY_LIMIT,
            request_model=ConcurrencyLimitWireRequest,
        ),
        HttpCommandName.REQUEST_WORKER_SHUTDOWN: _http(
            "POST",
            "/taskq/v1/workers/shutdown-requests",
            _CONTROL,
            _GLOBAL,
            _status_map(accepted=202),
            _SAFE,
            CommandName.REQUEST_WORKER_SHUTDOWN,
            request_model=ShutdownRequestWireRequest,
            data_model=CountWireData,
        ),
        HttpCommandName.LIST_WORKERS: _http(
            "GET", "/taskq/v1/workers", _READ, _GLOBAL, {}, _NEVER, None, surface=HttpSurface.GATED
        ),
        HttpCommandName.GET_QUEUE: _http(
            "GET",
            "/taskq/v1/queues/{queue}",
            _READ,
            _PATH,
            _status_map(ok=200),
            _SAFE,
            CommandName.GET_QUEUE_PROFILE,
            data_model=QueueProfile,
        ),
        HttpCommandName.LIST_JOBS: _http(
            "GET",
            "/taskq/v1/jobs",
            _READ,
            _QUERY,
            _status_map(ok=200),
            _SAFE,
            CommandName.LIST_JOBS,
            data_model=JobPageWireData,
        ),
    }
)


__all__ = [
    "ADMISSION_RESERVATION_ADAPTER",
    "AdmissionAdmittedResult",
    "AdmissionCancelOutcome",
    "AdmissionCancelRequest",
    "AdmissionCancelResult",
    "AdmissionCancelWireData",
    "AdmissionFinishOutcome",
    "AdmissionFinishRequest",
    "AdmissionFinishResult",
    "AdmissionJobCommand",
    "AdmissionPendingResult",
    "AdmissionReservationResult",
    "AdmissionReserveOutcome",
    "AdmissionReserveRequest",
    "AdmissionReserveWireData",
    "AdmissionReservedResult",
    "AdmissionResultWireData",
    "AttemptRequest",
    "AuthorizationProjection",
    "COMMAND_SPECS",
    "CLAIM_BATCH_ADAPTER",
    "CancelRunningWireRequest",
    "CancelWireData",
    "CancelOutcome",
    "CancelResult",
    "CapabilityRole",
    "ClaimedJob",
    "ClaimResult",
    "ClaimState",
    "ClaimWireData",
    "ClaimWireRequest",
    "ClaimedJobWire",
    "CommandEnvelope",
    "CommandName",
    "CommandOkOutcome",
    "CommandSpec",
    "ConfigChangeOutcome",
    "ConcurrencyLimitWireRequest",
    "CountWireData",
    "ContractMeta",
    "CompleteWireRequest",
    "CreateWorkflowWireRequest",
    "ENQUEUE_MANY_ITEMS_ADAPTER",
    "ENQUEUE_RESULT_ADAPTER",
    "EnqueueCommand",
    "EmptyWireRequest",
    "EnqueueCreatedResult",
    "EnqueueExistedResult",
    "EnqueueManyItem",
    "EnqueueManyWireData",
    "EnqueueManyWireItem",
    "EnqueueManyWireRequest",
    "EnqueueResult",
    "EnqueueStatus",
    "EnqueueWireData",
    "EnqueueWireRequest",
    "ErrorEnvelope",
    "EnsureQueueResult",
    "EnsureQueueWireData",
    "EnsureQueueWireRequest",
    "ExpireJobOutcome",
    "ExpireWorkerLeasesResult",
    "Followup",
    "HeartbeatResult",
    "HeartbeatWireData",
    "HeartbeatWireRequest",
    "HTTP_COMMAND_SPECS",
    "HttpCommandName",
    "HttpCommandSpec",
    "HttpSurface",
    "JobDetail",
    "JobStatus",
    "Metric",
    "QueueControlOutcome",
    "QueueSource",
    "QueueStats",
    "QueueStatsWireData",
    "RedriveFailedResult",
    "ReplayRule",
    "ReleaseWireRequest",
    "ReasonWireRequest",
    "RedriveWireRequest",
    "ReprioritizeWireRequest",
    "RetryClass",
    "SETTLE_RESULT_ADAPTER",
    "SettleAlreadySettledResult",
    "SettleConflictResult",
    "SettleDeadResult",
    "SettleLostResult",
    "SettleOkResult",
    "SettleOutcome",
    "SettleResult",
    "SettleRetryScheduledResult",
    "SettleWireData",
    "SnoozeWireRequest",
    "ShutdownRequestWireRequest",
    "PurgeWireRequest",
    "FailWireRequest",
    "PROTOCOL_DOCUMENT_REVISION",
    "PROTOCOL_MAJOR",
    "ProtocolError",
    "TaskqAction",
    "TQ_ERROR_REGISTRY",
    "TqCode",
    "TqErrorSpec",
    "WorkerPresenceWireData",
    "WorkerPresenceWireRequest",
    "WorkflowCancelWireRequest",
    "WorkflowAuthorizationProjection",
    "WorkflowKind",
    "WorkflowResult",
    "WorkflowStatus",
    "WorkflowWireData",
]
