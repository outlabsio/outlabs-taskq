"""Capability-sized transport interfaces for Protocol v1 / contract 0.1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, TypeVar, cast, runtime_checkable
from uuid import UUID

from taskq.protocol import (
    AdmissionCancelResult,
    AdmissionFinishResult,
    AdmissionJobCommand,
    AdmissionReservationResult,
    AuthorizationProjection,
    CancelResult,
    ClaimResult,
    CommandOkOutcome,
    ConfigChangeOutcome,
    ContractMeta,
    EnqueueCommand,
    EnqueueManyItem,
    EnqueueResult,
    EnsureQueueResult,
    ExpireJobOutcome,
    ExpireWorkerLeasesResult,
    Followup,
    HeartbeatResult,
    JobDetail,
    JobPage,
    Metric,
    QueueControlOutcome,
    QueueStats,
    QueueProfile,
    RedriveFailedResult,
    SettleResult,
    WorkflowAuthorizationProjection,
    WorkflowKind,
    WorkflowResult,
)

TTransport = TypeVar("TTransport")


class _NonOwningTransportView:
    """Capability view whose close is deliberately a no-op."""

    def __init__(self, transport: object) -> None:
        self._transport = transport

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)

    async def aclose(self) -> None:
        return None


def non_owning_transport_view(transport: TTransport) -> TTransport:
    """Return a delegating view that cannot close the underlying transport."""

    return cast(TTransport, _NonOwningTransportView(transport))


class ClosableTransport(Protocol):
    async def aclose(self) -> None: ...


@runtime_checkable
class ProducerTransport(ClosableTransport, Protocol):
    async def reserve_admission(
        self,
        queue: str,
        idempotency_key: str,
        intent_hash: str,
        *,
        handle: UUID | None = None,
        reservation_ttl_seconds: int = 300,
        receipt_ttl_seconds: int = 2_592_000,
    ) -> AdmissionReservationResult: ...

    async def finish_admission(
        self,
        queue: str,
        idempotency_key: str,
        handle: UUID,
        job: AdmissionJobCommand | Mapping[str, Any],
        receipt: Mapping[str, Any] | None = None,
    ) -> AdmissionFinishResult: ...

    async def cancel_admission(
        self, queue: str, idempotency_key: str, handle: UUID
    ) -> AdmissionCancelResult: ...

    async def enqueue(self, command: EnqueueCommand) -> EnqueueResult: ...

    async def enqueue_many(
        self, queue: str, items: Sequence[EnqueueManyItem]
    ) -> list[EnqueueResult]: ...


@runtime_checkable
class RunnerTransport(ClosableTransport, Protocol):
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
    ) -> ClaimResult: ...

    async def heartbeat(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> HeartbeatResult: ...

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Followup] | None = None,
    ) -> SettleResult: ...

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
    ) -> SettleResult: ...

    async def snooze(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        delay_seconds: int,
        *,
        reason: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult: ...

    async def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: Literal["released", "worker_shutdown", "no_handler"],
        *,
        delay_seconds: int = 0,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult: ...

    async def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult: ...

    async def worker_heartbeat(
        self,
        worker_id: str,
        queues: Sequence[str],
        *,
        hostname: str | None = None,
        pid: int | None = None,
        version: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> bool: ...


@runtime_checkable
class ObserverTransport(ClosableTransport, Protocol):
    async def list_jobs(
        self, queue: str, view: str, *, limit: int = 50, after: Mapping[str, Any] | None = None
    ) -> JobPage: ...

    async def get_queue_profile(self, queue: str) -> QueueProfile | None: ...

    async def get_job(
        self,
        job_id: UUID,
        *,
        include_error: bool = False,
        include_result: bool = False,
        include_progress: bool = False,
        include_payload: bool = False,
    ) -> JobDetail | None: ...

    async def get_queue_stats(self, queue: str | None = None) -> list[QueueStats]: ...

    async def get_contract_meta(self) -> ContractMeta: ...

    async def metrics(self) -> list[Metric]: ...


@runtime_checkable
class AuthorizationLookupTransport(ClosableTransport, Protocol):
    """Facade-internal observer projection; never an HTTP client command."""

    async def get_authorization_projection(
        self, job_id: UUID
    ) -> AuthorizationProjection | None: ...


@runtime_checkable
class OperatorTransport(ClosableTransport, Protocol):
    async def ensure_queue(
        self, name: str, profile: Mapping[str, Any] | None = None, actor: str | None = None
    ) -> EnsureQueueResult: ...

    async def update_queue_profile(
        self, name: str, profile: Mapping[str, Any], actor: str, expected_version: int
    ) -> tuple[str, QueueProfile | None, int | None]: ...

    async def pause_queue(
        self, name: str, actor: str, reason: str | None = None
    ) -> QueueControlOutcome: ...

    async def resume_queue(self, name: str, actor: str) -> QueueControlOutcome: ...

    async def set_concurrency_limit(
        self, key: str, max_running: int, actor: str
    ) -> ConfigChangeOutcome: ...

    async def request_worker_shutdown(
        self, *, worker_id: str | None, queue: str | None, actor: str
    ) -> int: ...

    async def purge_queued(
        self, queue: str, limit: int, actor: str, reason: str | None = None
    ) -> int: ...

    async def run_now(self, job_id: UUID, actor: str) -> CommandOkOutcome: ...

    async def reprioritize(self, job_id: UUID, priority: int, actor: str) -> CommandOkOutcome: ...

    async def cancel(self, job_id: UUID, actor: str, reason: str | None = None) -> CancelResult: ...

    async def redrive(self, job_id: UUID, actor: str, reset_progress: bool = False) -> bool: ...

    async def redrive_failed(self, queue: str, limit: int, actor: str) -> RedriveFailedResult: ...

    async def expire_job(self, job_id: UUID, actor: str) -> ExpireJobOutcome: ...

    async def expire_worker_leases(
        self, worker_id: str, actor: str
    ) -> ExpireWorkerLeasesResult: ...


@runtime_checkable
class HousekeeperTransport(ClosableTransport, Protocol):
    async def tick(self, reap_limit: int = 100) -> dict[str, Any]: ...

    async def janitor(self) -> dict[str, Any]: ...


@runtime_checkable
class WorkflowProducerTransport(ClosableTransport, Protocol):
    async def create_workflow(
        self,
        workflow_key: str,
        kind: WorkflowKind | Literal["dag", "batch"],
        *,
        params: Mapping[str, Any] | None = None,
        declared_queues: Sequence[str],
        actor: str,
    ) -> WorkflowResult: ...

    async def seal_workflow(self, workflow_id: UUID, actor: str) -> WorkflowResult: ...


@runtime_checkable
class WorkflowAuthorizationLookupTransport(ClosableTransport, Protocol):
    async def get_workflow_authorization_projection(
        self, workflow_id: UUID
    ) -> WorkflowAuthorizationProjection: ...


@runtime_checkable
class WorkflowOperatorTransport(ClosableTransport, Protocol):
    async def cancel_workflow(
        self, workflow_id: UUID, actor: str, reason: str | None = None
    ) -> WorkflowResult: ...


@runtime_checkable
class TaskqTransport(
    ProducerTransport,
    RunnerTransport,
    ObserverTransport,
    AuthorizationLookupTransport,
    OperatorTransport,
    HousekeeperTransport,
    WorkflowProducerTransport,
    WorkflowAuthorizationLookupTransport,
    WorkflowOperatorTransport,
    Protocol,
):
    """Complete direct-SQL transport intersection retained for compatibility."""


__all__ = [
    "AuthorizationLookupTransport",
    "ClosableTransport",
    "HousekeeperTransport",
    "ObserverTransport",
    "OperatorTransport",
    "ProducerTransport",
    "RunnerTransport",
    "TaskqTransport",
    "WorkflowAuthorizationLookupTransport",
    "WorkflowOperatorTransport",
    "WorkflowProducerTransport",
    "non_owning_transport_view",
]
