"""Closed Protocol-v1 value models shared by every taskq transport."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final
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


__all__ = [
    "ClaimedJob",
    "EnqueueResult",
    "EnqueueStatus",
    "TQ_ERROR_REGISTRY",
    "TqCode",
    "TqErrorSpec",
]
