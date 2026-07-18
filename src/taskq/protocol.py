"""Closed Protocol-v1 value models shared by every taskq transport."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
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

    result: SettleOutcome
    job_status: JobStatus | None
    scheduled_at: datetime | None


class AuthorizationProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    queue: str
    job_type: str
    status: JobStatus


class JobDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    queue: str
    job_type: str
    status: JobStatus
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

    result: CancelOutcome
    job_status: JobStatus


class EnsureQueueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: ConfigChangeOutcome
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


class CommandName(StrEnum):
    ENQUEUE = "enqueue"
    ENQUEUE_MANY = "enqueue_many"
    CLAIM = "claim"
    HEARTBEAT = "heartbeat"
    COMPLETE = "complete"
    FAIL = "fail"
    SNOOZE = "snooze"
    RELEASE = "release"
    CANCEL_RUNNING = "cancel_running"
    WORKER_HEARTBEAT = "worker_heartbeat"
    GET_AUTHORIZATION_PROJECTION = "get_authorization_projection"
    GET_JOB = "get_job"
    GET_QUEUE_STATS = "get_queue_stats"
    GET_CONTRACT_META = "get_contract_meta"
    METRICS = "metrics"
    ENSURE_QUEUE = "ensure_queue"
    PAUSE_QUEUE = "pause_queue"
    RESUME_QUEUE = "resume_queue"
    SET_CONCURRENCY_LIMIT = "set_concurrency_limit"
    REQUEST_WORKER_SHUTDOWN = "request_worker_shutdown"
    PURGE_QUEUED = "purge_queued"
    RUN_NOW = "run_now"
    REPRIORITIZE = "reprioritize"
    CANCEL = "cancel"
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
        CommandName.ENQUEUE: _spec(
            "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)",
            _PRODUCER,
            tuple(item.value for item in EnqueueStatus),
            (
                TqCode.NOT_FOUND,
                TqCode.VALIDATION,
                TqCode.BACKPRESSURE,
                TqCode.INTERNAL,
                TqCode.CAPABILITY,
            ),
        ),
        CommandName.ENQUEUE_MANY: _spec(
            "taskq.enqueue_many(text,jsonb)",
            _PRODUCER,
            tuple(item.value for item in EnqueueStatus),
            (TqCode.NOT_FOUND, TqCode.VALIDATION, TqCode.BACKPRESSURE, TqCode.INTERNAL),
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
        CommandName.GET_JOB: _spec(
            "taskq.get_job(uuid,boolean,boolean,boolean,boolean)",
            _OBSERVER,
            ("ok", "missing"),
        ),
        CommandName.GET_QUEUE_STATS: _spec("taskq.get_queue_stats(text)", _OBSERVER, ("ok",)),
        CommandName.GET_CONTRACT_META: _spec("taskq.get_contract_meta()", _OBSERVER, ("ok",)),
        CommandName.METRICS: _spec("taskq.metrics()", _OBSERVER, ("ok",)),
        CommandName.ENSURE_QUEUE: _spec(
            "taskq.ensure_queue(text,jsonb,text)",
            _OPERATOR,
            tuple(item.value for item in ConfigChangeOutcome),
            (TqCode.VALIDATION,),
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
    lease_seconds: int = Field(ge=15, le=86400)


ClaimResult.model_rebuild()


__all__ = [
    "AuthorizationProjection",
    "COMMAND_SPECS",
    "CancelOutcome",
    "CancelResult",
    "CapabilityRole",
    "ClaimedJob",
    "ClaimResult",
    "ClaimState",
    "CommandName",
    "CommandOkOutcome",
    "CommandSpec",
    "ConfigChangeOutcome",
    "ContractMeta",
    "EnqueueCommand",
    "EnqueueManyItem",
    "EnqueueResult",
    "EnqueueStatus",
    "EnsureQueueResult",
    "ExpireJobOutcome",
    "ExpireWorkerLeasesResult",
    "HeartbeatResult",
    "JobDetail",
    "JobStatus",
    "Metric",
    "QueueControlOutcome",
    "QueueStats",
    "RedriveFailedResult",
    "ReplayRule",
    "SettleOutcome",
    "SettleResult",
    "TQ_ERROR_REGISTRY",
    "TqCode",
    "TqErrorSpec",
]
