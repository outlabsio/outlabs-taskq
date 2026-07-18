"""Typed application facade and transactional enqueue compilation."""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from taskq.errors import TaskqConfigError
from taskq.protocol import (
    ENQUEUE_MANY_ITEMS_ADAPTER,
    EnqueueCommand,
    EnqueueResult,
)
from taskq.registry import InT, OutT, RetryStrategy, Task, TaskRegistry
from taskq.sql.transport import SqlTaskqTransport
from taskq.transport import TaskqTransport


class TaskQ:
    def __init__(
        self,
        transport: TaskqTransport,
        *,
        registry: TaskRegistry | None = None,
        validate_job_types: bool = True,
    ) -> None:
        self.transport = transport
        self.registry = registry or TaskRegistry()
        self.validate_job_types = validate_job_types
        self._replacement_active = False

    @contextmanager
    def replace_client(self, client: TaskqTransport) -> Iterator[TaskqTransport]:
        """Temporarily replace this facade's transport for a test scope."""
        if self._replacement_active:
            raise TaskqConfigError("TaskQ client replacement cannot be nested")
        previous = self.transport
        self._replacement_active = True
        self.transport = client
        try:
            yield client
        finally:
            self.transport = previous
            self._replacement_active = False

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        registry: TaskRegistry | None = None,
        validate_job_types: bool = True,
        **engine_options: Any,
    ) -> TaskQ:
        return cls(
            SqlTaskqTransport.from_dsn(dsn, **engine_options),
            registry=registry,
            validate_job_types=validate_job_types,
        )

    @staticmethod
    def _retry_fields(task: Task[Any, Any]) -> dict[str, Any]:
        retry = task.retry
        if retry is False:
            return {"max_attempts": 1}
        if retry is True:
            return {}
        if type(retry) is int:
            return {"max_attempts": retry}
        if isinstance(retry, RetryStrategy):
            return {
                "max_attempts": retry.max_attempts,
                "backoff_mode": retry.mode,
                "backoff_base": retry.base_seconds,
                "backoff_cap": retry.cap_seconds,
            }
        raise TaskqConfigError("unsupported retry value")

    def _command(
        self,
        task: Task[InT, OutT],
        payload: InT | Mapping[str, object],
        *,
        idempotency_key: str | None,
        scheduled_at: datetime | None,
        priority: int | None,
        lease_seconds: int | None,
        concurrency_key: str | None,
        affinity_key: str | None,
        max_attempts: int | None,
        backoff_mode: str | None,
        backoff_base: int | None,
        backoff_cap: int | None,
        headers: Mapping[str, Any] | None,
    ) -> EnqueueCommand:
        registered = self.registry.require(task)
        fields = self._retry_fields(registered)
        fields.update(
            {
                "priority": registered.priority,
                "lease_seconds": registered.lease_seconds,
            }
        )
        overrides = {
            "priority": priority,
            "lease_seconds": lease_seconds,
            "max_attempts": max_attempts,
            "backoff_mode": backoff_mode,
            "backoff_base": backoff_base,
            "backoff_cap": backoff_cap,
        }
        fields.update({key: value for key, value in overrides.items() if value is not None})
        return EnqueueCommand(
            queue=registered.queue,
            job_type=registered.name,
            payload=registered.validate_payload(payload),
            idempotency_key=idempotency_key,
            scheduled_at=scheduled_at,
            concurrency_key=concurrency_key,
            affinity_key=affinity_key,
            headers=dict(headers) if headers is not None else None,
            **fields,
        )

    @staticmethod
    def _supplied_sql_object(
        session: AsyncSession | None, connection: AsyncConnection | None
    ) -> AsyncSession | AsyncConnection | None:
        if session is not None and connection is not None:
            raise TaskqConfigError("pass session or connection, not both")
        return session if session is not None else connection

    async def _sql_connection(self, supplied: AsyncSession | AsyncConnection) -> AsyncConnection:
        if not isinstance(self.transport, SqlTaskqTransport):
            raise TaskqConfigError("session/connection requires SqlTaskqTransport")
        if isinstance(supplied, AsyncSession):
            return await supplied.connection()
        if isinstance(supplied, AsyncConnection):
            return supplied
        raise TaskqConfigError("unsupported SQLAlchemy transaction object")

    async def enqueue(
        self,
        task: Task[InT, OutT],
        payload: InT | Mapping[str, object],
        *,
        idempotency_key: str | None = None,
        scheduled_at: datetime | None = None,
        priority: int | None = None,
        lease_seconds: int | None = None,
        concurrency_key: str | None = None,
        affinity_key: str | None = None,
        max_attempts: int | None = None,
        backoff_mode: str | None = None,
        backoff_base: int | None = None,
        backoff_cap: int | None = None,
        headers: Mapping[str, Any] | None = None,
        session: AsyncSession | None = None,
        connection: AsyncConnection | None = None,
    ) -> EnqueueResult:
        command = self._command(
            task,
            payload,
            idempotency_key=idempotency_key,
            scheduled_at=scheduled_at,
            priority=priority,
            lease_seconds=lease_seconds,
            concurrency_key=concurrency_key,
            affinity_key=affinity_key,
            max_attempts=max_attempts,
            backoff_mode=backoff_mode,
            backoff_base=backoff_base,
            backoff_cap=backoff_cap,
            headers=headers,
        )
        supplied = self._supplied_sql_object(session, connection)
        if supplied is None:
            return await self.transport.enqueue(command)
        sql_connection = await self._sql_connection(supplied)
        assert isinstance(self.transport, SqlTaskqTransport)
        return await self.transport.enqueue(command, connection=sql_connection)

    async def enqueue_many(
        self,
        task: Task[InT, OutT],
        payloads: Sequence[InT | Mapping[str, object]],
        *,
        idempotency_keys: Sequence[str | None] | None = None,
        scheduled_at: datetime | None = None,
        priority: int | None = None,
        lease_seconds: int | None = None,
        session: AsyncSession | None = None,
        connection: AsyncConnection | None = None,
    ) -> list[EnqueueResult]:
        registered = self.registry.require(task)
        keys = tuple(idempotency_keys) if idempotency_keys is not None else (None,) * len(payloads)
        if len(keys) != len(payloads):
            raise TaskqConfigError("idempotency_keys must match payload count")
        retry = self._retry_fields(registered)
        items = ENQUEUE_MANY_ITEMS_ADAPTER.validate_python(
            [
                {
                    "job_type": registered.name,
                    "payload": registered.validate_payload(payload),
                    "idempotency_key": keys[index],
                    "scheduled_at": scheduled_at,
                    "priority": priority if priority is not None else registered.priority,
                    "lease_seconds": (
                        lease_seconds if lease_seconds is not None else registered.lease_seconds
                    ),
                    **retry,
                }
                for index, payload in enumerate(payloads)
            ]
        )
        supplied = self._supplied_sql_object(session, connection)
        if supplied is None:
            return await self.transport.enqueue_many(registered.queue, items)
        sql_connection = await self._sql_connection(supplied)
        assert isinstance(self.transport, SqlTaskqTransport)
        return await self.transport.enqueue_many(registered.queue, items, connection=sql_connection)

    async def enqueue_raw(
        self,
        *,
        queue: str,
        job_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> EnqueueResult:
        if self.validate_job_types:
            raise TaskqConfigError("raw enqueue requires validate_job_types=False")
        return await self.transport.enqueue(
            EnqueueCommand(
                queue=queue,
                job_type=job_type,
                payload=dict(payload),
                idempotency_key=idempotency_key,
            )
        )

    async def aclose(self) -> None:
        await self.transport.aclose()


__all__ = ["TaskQ"]
