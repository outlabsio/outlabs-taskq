"""SQLAlchemy asyncio adapter for the complete public contract-0.1 surface."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, TypeVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from taskq.errors import TaskqConfigError, TaskqInternalError, taskq_error_from_exception
from taskq.protocol import (
    AuthorizationProjection,
    COMMAND_SPECS,
    CancelResult,
    ClaimResult,
    CommandOkOutcome,
    ConfigChangeOutcome,
    ContractMeta,
    EnqueueCommand,
    EnqueueManyItem,
    EnqueueResult,
    EnqueueStatus,
    EnsureQueueResult,
    ExpireJobOutcome,
    ExpireWorkerLeasesResult,
    HeartbeatResult,
    JobDetail,
    Metric,
    QueueStats,
    QueueControlOutcome,
    RedriveFailedResult,
    SettleResult,
)

T = TypeVar("T")

METHOD_FUNCTIONS = MappingProxyType(
    {command.value: spec.sql_function for command, spec in COMMAND_SPECS.items()}
)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _json_param(value: Any) -> str | None:
    return None if value is None else json.dumps(value, separators=(",", ":"))


def _nested_mapping(value: Any, fields: Sequence[str]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return dict(zip(fields, value, strict=True))


class SqlTaskqTransport:
    def __init__(self, engine: AsyncEngine, *, owns_engine: bool = False) -> None:
        self._engine = engine
        self._owns_engine = owns_engine
        self._closed = False

    @classmethod
    def from_dsn(cls, dsn: str, **engine_options: Any) -> SqlTaskqTransport:
        url = make_url(dsn)
        if url.drivername == "postgres":
            url = url.set(drivername="postgresql+asyncpg")
        elif url.drivername in {"postgresql", "postgresql+psycopg"}:
            url = url.set(drivername="postgresql+asyncpg")
        elif url.drivername != "postgresql+asyncpg":
            raise TaskqConfigError("SqlTaskqTransport requires a PostgreSQL DSN")
        return cls(create_async_engine(url, **engine_options), owns_engine=True)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_engine:
            await self._engine.dispose()

    async def _run(
        self,
        operation: Callable[[AsyncConnection], Awaitable[T]],
        *,
        connection: AsyncConnection | None = None,
    ) -> T:
        if self._closed:
            raise TaskqConfigError("transport is closed")
        try:
            if connection is not None:
                return await operation(connection)
            async with self._engine.begin() as owned_connection:
                return await operation(owned_connection)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise taskq_error_from_exception(exc) from exc

    @staticmethod
    async def _one(
        connection: AsyncConnection, statement: str, params: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        result = await connection.execute(text(statement), dict(params or {}))
        row = result.mappings().first()
        if row is None:
            raise TaskqInternalError()
        return row

    @staticmethod
    async def _many(
        connection: AsyncConnection, statement: str, params: Mapping[str, Any] | None = None
    ) -> list[Mapping[str, Any]]:
        result = await connection.execute(text(statement), dict(params or {}))
        return list(result.mappings())

    @staticmethod
    async def _scalar(
        connection: AsyncConnection, statement: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        result = await connection.execute(text(statement), dict(params or {}))
        value = result.scalar_one_or_none()
        if value is None:
            raise TaskqInternalError()
        return value

    async def enqueue(
        self, command: EnqueueCommand, *, connection: AsyncConnection | None = None
    ) -> EnqueueResult:
        async def operation(conn: AsyncConnection) -> EnqueueResult:
            row = await self._one(
                conn,
                """SELECT * FROM taskq.enqueue(
                    :queue, :job_type, CAST(:payload AS jsonb), :priority, :scheduled_at,
                    :idempotency_key, :concurrency_key, :affinity_key, :max_attempts,
                    :lease_seconds, :backoff_mode, :backoff_base, :backoff_cap,
                    NULL, NULL, NULL, NULL, CAST(:headers AS jsonb))""",
                {
                    **command.model_dump(exclude={"payload", "headers"}),
                    "payload": _json_param(command.payload),
                    "headers": _json_param(command.headers),
                },
            )
            created = row["created"]
            job_id = row["job_id"]
            if not isinstance(created, bool) or not isinstance(job_id, UUID):
                raise TaskqInternalError()
            return EnqueueResult(
                status=EnqueueStatus.CREATED if created else EnqueueStatus.EXISTED,
                job_id=job_id,
                created=created,
                queue=command.queue,
                job_type=command.job_type,
                idempotency_key=command.idempotency_key,
                scheduled_at=command.scheduled_at,
            )

        return await self._run(operation, connection=connection)

    async def enqueue_many(
        self,
        queue: str,
        items: Sequence[EnqueueManyItem],
        *,
        connection: AsyncConnection | None = None,
    ) -> list[EnqueueResult]:
        frozen_items = tuple(items)

        async def operation(conn: AsyncConnection) -> list[EnqueueResult]:
            rows = await self._many(
                conn,
                "SELECT * FROM taskq.enqueue_many(:queue, CAST(:items AS jsonb))",
                {
                    "queue": queue,
                    "items": _json_param(
                        [item.model_dump(mode="json", exclude_none=True) for item in frozen_items]
                    ),
                },
            )
            by_index: dict[int, Mapping[str, Any]] = {}
            for row in rows:
                index = row["input_index"]
                if not isinstance(index, int) or index in by_index:
                    raise TaskqInternalError()
                by_index[index] = row
            if set(by_index) != set(range(1, len(frozen_items) + 1)):
                raise TaskqInternalError()
            results: list[EnqueueResult] = []
            for index, item in enumerate(frozen_items, 1):
                row = by_index[index]
                try:
                    status = EnqueueStatus(row["outcome"])
                except (TypeError, ValueError) as exc:
                    raise TaskqInternalError(cause=exc) from exc
                job_id = row["job_id"]
                if not isinstance(job_id, UUID):
                    raise TaskqInternalError()
                results.append(
                    EnqueueResult(
                        status=status,
                        job_id=job_id,
                        created=status is EnqueueStatus.CREATED,
                        queue=queue,
                        job_type=item.job_type,
                        idempotency_key=item.idempotency_key,
                        scheduled_at=item.scheduled_at,
                    )
                )
            return results

        return await self._run(operation, connection=connection)

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
        async def operation(conn: AsyncConnection) -> ClaimResult:
            row = await self._one(
                conn,
                "SELECT * FROM taskq.claim_jobs(:queue, :worker_id, :batch, :job_types, "
                ":lease_seconds, :affinity_key, :job_id)",
                {
                    "queue": queue,
                    "worker_id": worker_id,
                    "batch": batch,
                    "job_types": list(job_types) if job_types is not None else None,
                    "lease_seconds": lease_seconds,
                    "affinity_key": affinity_key,
                    "job_id": job_id,
                },
            )
            fields = (
                "job_id",
                "queue",
                "job_type",
                "priority",
                "payload",
                "headers",
                "progress",
                "attempt_id",
                "attempt_number",
                "failure_count",
                "max_attempts",
                "lease_expires_at",
                "workflow_id",
                "step_key",
            )
            decoded_jobs = []
            for value in row["jobs"] or ():
                decoded = _nested_mapping(value, fields)
                decoded["payload"] = _json(decoded["payload"])
                decoded["headers"] = _json(decoded["headers"]) or {}
                decoded["progress"] = _json(decoded["progress"])
                decoded_jobs.append(decoded)
            jobs = tuple(decoded_jobs)
            return ClaimResult.model_validate({"state": row["state"], "jobs": jobs})

        return await self._run(operation)

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
        async def operation(conn: AsyncConnection) -> HeartbeatResult:
            row = await self._one(
                conn,
                "SELECT * FROM taskq.heartbeat(:job_id, :attempt_id, :worker_id, "
                ":lease_seconds, CAST(:progress AS jsonb), CAST(:stats AS jsonb))",
                {
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "worker_id": worker_id,
                    "lease_seconds": lease_seconds,
                    "progress": _json_param(progress),
                    "stats": _json_param(stats),
                },
            )
            return HeartbeatResult.model_validate(row)

        return await self._run(operation)

    async def _settle(self, statement: str, params: Mapping[str, Any]) -> SettleResult:
        async def operation(conn: AsyncConnection) -> SettleResult:
            row = await self._one(conn, statement, params)
            return SettleResult.model_validate(row)

        return await self._run(operation)

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
        return await self._settle(
            "SELECT * FROM taskq.complete_job(:job_id, :attempt_id, :worker_id, "
            "CAST(:result AS jsonb), CAST(:stats AS jsonb), CAST(:followups AS jsonb))",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "result": _json_param(result),
                "stats": _json_param(stats),
                "followups": _json_param(followups),
            },
        )

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
        return await self._settle(
            "SELECT * FROM taskq.fail_job(:job_id, :attempt_id, :worker_id, :error, "
            ":retryable, :retry_after_seconds, CAST(:progress AS jsonb), CAST(:stats AS jsonb))",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "error": error,
                "retryable": retryable,
                "retry_after_seconds": retry_after_seconds,
                "progress": _json_param(progress),
                "stats": _json_param(stats),
            },
        )

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
        return await self._settle(
            "SELECT * FROM taskq.snooze_job(:job_id, :attempt_id, :worker_id, "
            ":delay_seconds, :reason, CAST(:progress AS jsonb))",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "delay_seconds": delay_seconds,
                "reason": reason,
                "progress": _json_param(progress),
            },
        )

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
        return await self._settle(
            "SELECT * FROM taskq.release_job(:job_id, :attempt_id, :worker_id, :cause, "
            ":delay_seconds, CAST(:progress AS jsonb))",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "cause": cause,
                "delay_seconds": delay_seconds,
                "progress": _json_param(progress),
            },
        )

    async def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult:
        return await self._settle(
            "SELECT * FROM taskq.cancel_running_job(:job_id, :attempt_id, :worker_id, :reason)",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "reason": reason,
            },
        )

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
        async def operation(conn: AsyncConnection) -> bool:
            row = await self._one(
                conn,
                "SELECT * FROM taskq.worker_heartbeat(:worker_id, :queues, :hostname, :pid, "
                ":version, CAST(:meta AS jsonb))",
                {
                    "worker_id": worker_id,
                    "queues": list(queues),
                    "hostname": hostname,
                    "pid": pid,
                    "version": version,
                    "meta": _json_param(meta),
                },
            )
            return bool(row["shutdown_requested"])

        return await self._run(operation)

    async def get_authorization_projection(self, job_id: UUID) -> AuthorizationProjection | None:
        async def operation(conn: AsyncConnection) -> AuthorizationProjection | None:
            rows = await self._many(
                conn,
                "SELECT * FROM taskq.get_authorization_projection(:job_id)",
                {"job_id": job_id},
            )
            return AuthorizationProjection.model_validate(rows[0]) if rows else None

        return await self._run(operation)

    async def get_job(
        self,
        job_id: UUID,
        *,
        include_error: bool = False,
        include_result: bool = False,
        include_progress: bool = False,
        include_payload: bool = False,
    ) -> JobDetail | None:
        async def operation(conn: AsyncConnection) -> JobDetail | None:
            rows = await self._many(
                conn,
                "SELECT * FROM taskq.get_job(:job_id, :include_error, :include_result, "
                ":include_progress, :include_payload)",
                {
                    "job_id": job_id,
                    "include_error": include_error,
                    "include_result": include_result,
                    "include_progress": include_progress,
                    "include_payload": include_payload,
                },
            )
            if not rows:
                return None
            data = dict(rows[0])
            for field in ("result", "progress", "payload"):
                data[field] = _json(data[field])
            return JobDetail.model_validate(data)

        return await self._run(operation)

    async def get_queue_stats(self, queue: str | None = None) -> list[QueueStats]:
        async def operation(conn: AsyncConnection) -> list[QueueStats]:
            rows = await self._many(
                conn, "SELECT * FROM taskq.get_queue_stats(:queue)", {"queue": queue}
            )
            return [
                QueueStats.model_validate({**row, "stats": _json(row["stats"])}) for row in rows
            ]

        return await self._run(operation)

    async def get_contract_meta(self) -> ContractMeta:
        async def operation(conn: AsyncConnection) -> ContractMeta:
            row = await self._one(conn, "SELECT * FROM taskq.get_contract_meta()")
            return ContractMeta.model_validate({**row, "capabilities": _json(row["capabilities"])})

        return await self._run(operation)

    async def metrics(self) -> list[Metric]:
        async def operation(conn: AsyncConnection) -> list[Metric]:
            rows = await self._many(conn, "SELECT * FROM taskq.metrics()")
            return [
                Metric(
                    name=row["name"],
                    labels=_json(row["labels"]),
                    value=float(
                        row["value"] if not isinstance(row["value"], Decimal) else row["value"]
                    ),
                )
                for row in rows
            ]

        return await self._run(operation)

    async def ensure_queue(
        self, name: str, profile: Mapping[str, Any] | None = None, actor: str | None = None
    ) -> EnsureQueueResult:
        async def operation(conn: AsyncConnection) -> EnsureQueueResult:
            row = await self._one(
                conn,
                "SELECT * FROM taskq.ensure_queue(:name, CAST(:profile AS jsonb), :actor)",
                {"name": name, "profile": _json_param(profile or {}), "actor": actor},
            )
            return EnsureQueueResult.model_validate({**row, "profile": _json(row["profile"])})

        return await self._run(operation)

    async def _operator_scalar(self, function_call: str, params: Mapping[str, Any]) -> Any:
        async def operation(conn: AsyncConnection) -> Any:
            return await self._scalar(conn, f"SELECT {function_call}", params)

        return await self._run(operation)

    async def pause_queue(
        self, name: str, actor: str, reason: str | None = None
    ) -> QueueControlOutcome:
        return QueueControlOutcome(
            await self._operator_scalar(
                "taskq.pause_queue(:name, :actor, :reason)",
                {"name": name, "actor": actor, "reason": reason},
            )
        )

    async def resume_queue(self, name: str, actor: str) -> QueueControlOutcome:
        return QueueControlOutcome(
            await self._operator_scalar(
                "taskq.resume_queue(:name, :actor)", {"name": name, "actor": actor}
            )
        )

    async def set_concurrency_limit(
        self, key: str, max_running: int, actor: str
    ) -> ConfigChangeOutcome:
        return ConfigChangeOutcome(
            await self._operator_scalar(
                "taskq.set_concurrency_limit(:key, :max_running, :actor)",
                {"key": key, "max_running": max_running, "actor": actor},
            )
        )

    async def request_worker_shutdown(
        self, *, worker_id: str | None, queue: str | None, actor: str
    ) -> int:
        return int(
            await self._operator_scalar(
                "taskq.request_worker_shutdown(:worker_id, :queue, :actor)",
                {"worker_id": worker_id, "queue": queue, "actor": actor},
            )
        )

    async def purge_queued(
        self, queue: str, limit: int, actor: str, reason: str | None = None
    ) -> int:
        return int(
            await self._operator_scalar(
                "taskq.purge_queued(:queue, :limit, :actor, :reason)",
                {"queue": queue, "limit": limit, "actor": actor, "reason": reason},
            )
        )

    async def run_now(self, job_id: UUID, actor: str) -> CommandOkOutcome:
        return CommandOkOutcome(
            await self._operator_scalar(
                "taskq.run_now(:job_id, :actor)", {"job_id": job_id, "actor": actor}
            )
        )

    async def reprioritize(self, job_id: UUID, priority: int, actor: str) -> CommandOkOutcome:
        return CommandOkOutcome(
            await self._operator_scalar(
                "taskq.reprioritize(:job_id, :priority, :actor)",
                {"job_id": job_id, "priority": priority, "actor": actor},
            )
        )

    async def cancel(self, job_id: UUID, actor: str, reason: str | None = None) -> CancelResult:
        async def operation(conn: AsyncConnection) -> CancelResult:
            row = await self._one(
                conn,
                "SELECT * FROM taskq.cancel_job(:job_id, :actor, :reason)",
                {"job_id": job_id, "actor": actor, "reason": reason},
            )
            return CancelResult.model_validate(row)

        return await self._run(operation)

    async def redrive(self, job_id: UUID, actor: str, reset_progress: bool = False) -> bool:
        return bool(
            await self._operator_scalar(
                "taskq.redrive_job(:job_id, :actor, :reset_progress)",
                {"job_id": job_id, "actor": actor, "reset_progress": reset_progress},
            )
        )

    async def redrive_failed(self, queue: str, limit: int, actor: str) -> RedriveFailedResult:
        async def operation(conn: AsyncConnection) -> RedriveFailedResult:
            row = await self._one(
                conn,
                "SELECT * FROM taskq.redrive_failed(:queue, :limit, :actor)",
                {"queue": queue, "limit": limit, "actor": actor},
            )
            return RedriveFailedResult.model_validate(row)

        return await self._run(operation)

    async def expire_job(self, job_id: UUID, actor: str) -> ExpireJobOutcome:
        return ExpireJobOutcome(
            await self._operator_scalar(
                "taskq.expire_job(:job_id, :actor)", {"job_id": job_id, "actor": actor}
            )
        )

    async def expire_worker_leases(self, worker_id: str, actor: str) -> ExpireWorkerLeasesResult:
        value = await self._operator_scalar(
            "taskq.expire_worker_leases(:worker_id, :actor)",
            {"worker_id": worker_id, "actor": actor},
        )
        return ExpireWorkerLeasesResult.model_validate(_json(value))

    async def tick(self, reap_limit: int = 100) -> dict[str, Any]:
        return dict(
            _json(
                await self._operator_scalar("taskq.tick(:reap_limit)", {"reap_limit": reap_limit})
            )
        )

    async def janitor(self) -> dict[str, Any]:
        return dict(_json(await self._operator_scalar("taskq.janitor()", {})))


__all__ = ["METHOD_FUNCTIONS", "SqlTaskqTransport"]
