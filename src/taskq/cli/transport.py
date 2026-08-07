"""Uniform CLI adapter over the typed SQL and HTTP transports."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal
from uuid import UUID

from taskq.errors import TaskqCapabilityError, TaskqConfigError, TaskqNotFoundError
from taskq.protocol import (
    EnqueueCommand,
    EnqueueManyItem,
    HttpCommandName,
    ScheduleDefinition,
    ScheduleState,
    WorkflowKind,
)
from taskq.sql.transport import SqlTaskqTransport

from .models import ResolvedConnection


class CliTransport:
    def __init__(self, connection: ResolvedConnection, transport: Any) -> None:
        self.connection = connection
        self.transport = transport

    @property
    def mode(self) -> Literal["sql", "http"]:
        return self.connection.transport

    def _actor(self) -> str:
        if self.mode == "http":
            return "authenticated-http-principal"
        if not self.connection.actor:
            raise TaskqConfigError(
                "direct-SQL mutation requires --actor, TASKQ_ACTOR, or context actor"
            )
        return self.connection.actor

    async def target(self) -> Any:
        if self.mode == "sql":
            return await self.transport.get_target_identity()
        name = getattr(HttpCommandName, "TARGET", None)
        if name is None:
            raise TaskqCapabilityError(details={"capability": "target_attestation_http"})
        return await self.transport.command(name)

    async def queue_health(self, queue: str | None = None) -> Any:
        if self.mode == "sql":
            return await self.transport.get_queue_health(queue)
        raise TaskqCapabilityError(details={"capability": "queue_health_http"})

    async def queue_audit(self, queue: str, limit: int, before_id: int | None) -> Any:
        if self.mode == "sql":
            return await self.transport.list_queue_audit(queue, limit, before_id)
        raise TaskqCapabilityError(details={"capability": "queue_audit_http"})

    async def scheduler_health(self) -> Any:
        if self.mode == "sql":
            return await self.transport.get_scheduler_health()
        name = getattr(HttpCommandName, "SCHEDULER_HEALTH", None)
        if name is None:
            raise TaskqCapabilityError(details={"capability": "scheduler_health_http"})
        return await self.transport.command(name)

    async def meta(self) -> Any:
        return await self.transport.get_contract_meta()

    async def queue_stats(self, queue: str | None = None) -> Any:
        if self.mode == "sql":
            return await self.transport.get_queue_stats(queue)
        name = HttpCommandName.GET_QUEUE_STATS if queue else HttpCommandName.LIST_QUEUE_STATS
        return await self.transport.command(name, path_params={"queue": queue} if queue else None)

    async def queue_show(self, queue: str) -> Any:
        if self.mode == "sql":
            result = await self.transport.get_queue_profile(queue)
            if result is None:
                raise TaskqNotFoundError(details={"resource": "queue"})
            return result
        return await self.transport.command(HttpCommandName.GET_QUEUE, path_params={"queue": queue})

    async def queue_ensure(self, queue: str, profile: dict[str, Any]) -> Any:
        if self.mode == "sql":
            return await self.transport.ensure_queue(queue, profile, self._actor())
        return await self.transport.ensure_queue(queue, profile)

    async def queue_update(self, queue: str, profile: dict[str, Any], expected_version: int) -> Any:
        if self.mode == "sql":
            outcome, result, version = await self.transport.update_queue_profile(
                queue, profile, self._actor(), expected_version
            )
            return {"outcome": outcome, "profile": result, "version": version}
        return await self.transport.ensure_queue(queue, profile, expected_version=expected_version)

    async def queue_pause(self, queue: str, reason: str | None) -> Any:
        if self.mode == "sql":
            return {"outcome": await self.transport.pause_queue(queue, self._actor(), reason)}
        return {
            "outcome": await self.transport.command(
                HttpCommandName.PAUSE_QUEUE,
                path_params={"queue": queue},
                body={"reason": reason},
            )
        }

    async def queue_resume(self, queue: str) -> Any:
        if self.mode == "sql":
            return {"outcome": await self.transport.resume_queue(queue, self._actor())}
        return {
            "outcome": await self.transport.command(
                HttpCommandName.RESUME_QUEUE, path_params={"queue": queue}, body={}
            )
        }

    async def queue_purge(self, queue: str, limit: int, reason: str | None) -> Any:
        if self.mode == "sql":
            count = await self.transport.purge_queued(queue, limit, self._actor(), reason)
        else:
            count = await self.transport.command(
                HttpCommandName.PURGE_QUEUED,
                path_params={"queue": queue},
                body={"limit": limit, "reason": reason},
            )
        return {"count": count}

    async def queue_redrive_failed(self, queue: str, limit: int) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(details={"transport": "http", "command": "redrive_failed"})
        return await self.transport.redrive_failed(queue, limit, self._actor())

    async def queue_set_breaker(
        self, queue: str, threshold: int | None, cooldown_seconds: int, half_open_successes: int
    ) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(
                details={"transport": "http", "command": "set_breaker_config"}
            )
        return {
            "outcome": await self.transport.set_breaker_config(
                queue, threshold, cooldown_seconds, half_open_successes, self._actor()
            )
        }

    async def queue_trip_breaker(self, queue: str) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(details={"transport": "http", "command": "trip_breaker"})
        return {"outcome": await self.transport.trip_breaker(queue, self._actor())}

    async def queue_close_breaker(self, queue: str) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(
                details={"transport": "http", "command": "force_close_breaker"}
            )
        return {"outcome": await self.transport.force_close_breaker(queue, self._actor())}

    async def queue_set_breaker_rate(
        self, queue: str, failure_ratio: float | None, window_seconds: int, min_volume: int
    ) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(details={"transport": "http", "command": "set_breaker_rate"})
        return {
            "outcome": await self.transport.set_breaker_rate(
                queue, failure_ratio, window_seconds, min_volume, self._actor()
            )
        }

    async def queue_set_breaker_latency(
        self, queue: str, threshold_ms: int | None, window_seconds: int, min_volume: int
    ) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(
                details={"transport": "http", "command": "set_breaker_latency"}
            )
        return {
            "outcome": await self.transport.set_breaker_latency(
                queue, threshold_ms, window_seconds, min_volume, self._actor()
            )
        }

    async def queue_set_aging(self, queue: str, aging_seconds: int | None) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(
                details={"transport": "http", "command": "set_priority_aging"}
            )
        return {
            "outcome": await self.transport.set_priority_aging(queue, aging_seconds, self._actor())
        }

    async def queue_set_flow_limit(self, key: str, rate_per_minute: int, burst: int | None) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(details={"transport": "http", "command": "set_flow_limit"})
        return {
            "outcome": await self.transport.set_flow_limit(
                key, rate_per_minute, burst, self._actor()
            )
        }

    async def schedule_set_smear(self, name: str, smear_seconds: int | None) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(
                details={"transport": "http", "command": "set_schedule_smear"}
            )
        return {
            "outcome": await self.transport.set_schedule_smear(name, smear_seconds, self._actor())
        }

    async def job_list(self, queue: str, view: str, limit: int, cursor: Any) -> Any:
        if self.mode == "sql":
            return await self.transport.list_jobs(queue, view, limit=limit, after=cursor)
        return await self.transport.command(
            HttpCommandName.LIST_JOBS,
            query={"queue": queue, "view": view, "limit": limit, "cursor": cursor},
        )

    async def job_show(self, job_id: UUID, **includes: bool) -> Any:
        result = await self.transport.get_job(job_id, **includes)
        if result is None:
            raise TaskqNotFoundError(details={"resource": "job"})
        return result

    async def job_events(
        self, job_id: UUID, *, limit: int, cursor: Any, include_details: bool
    ) -> Any:
        if self.mode == "sql":
            method = getattr(self.transport, "list_job_events", None)
            if method is None:
                raise TaskqCapabilityError(details={"capability": "read_model_job_events"})
            return await method(job_id, limit=limit, after=cursor, include_details=include_details)
        name = getattr(HttpCommandName, "LIST_JOB_EVENTS", None)
        if name is None:
            raise TaskqCapabilityError(details={"capability": "read_model_job_events"})
        return await self.transport.command(
            name,
            path_params={"job_id": job_id},
            query={"limit": limit, "cursor": cursor, "include_details": include_details},
        )

    async def enqueue(self, command: EnqueueCommand) -> Any:
        return await self.transport.enqueue(command)

    async def enqueue_many(self, queue: str, items: list[EnqueueManyItem]) -> Any:
        return await self.transport.enqueue_many(queue, items)

    async def job_control(self, action: str, job_id: UUID, **values: Any) -> Any:
        actor = self._actor()
        if self.mode == "sql":
            if action == "cancel":
                return await self.transport.cancel(job_id, actor, values.get("reason"))
            if action == "redrive":
                return {
                    "redriven": await self.transport.redrive(
                        job_id, actor, values.get("reset_progress", False)
                    )
                }
            if action == "run_now":
                return {"outcome": await self.transport.run_now(job_id, actor)}
            if action == "reprioritize":
                return {
                    "outcome": await self.transport.reprioritize(job_id, values["priority"], actor)
                }
            if action == "expire":
                return {"outcome": await self.transport.expire_job(job_id, actor)}
        names = {
            "cancel": HttpCommandName.CANCEL,
            "redrive": HttpCommandName.REDRIVE,
            "run_now": HttpCommandName.RUN_NOW,
            "reprioritize": HttpCommandName.REPRIORITIZE,
            "expire": HttpCommandName.EXPIRE_JOB,
        }
        bodies = {
            "cancel": {"reason": values.get("reason")},
            "redrive": {"reset_progress": values.get("reset_progress", False)},
            "run_now": {},
            "reprioritize": {"priority": values["priority"]},
            "expire": {},
        }
        return await self.transport.command(
            names[action], path_params={"job_id": job_id}, body=bodies[action]
        )

    async def workers(self, limit: int, cursor: Any) -> Any:
        if self.mode == "sql":
            kwargs = cursor or {}
            return await self.transport.list_worker_presence(
                limit=limit,
                after_last_seen_at=kwargs.get("last_seen_at"),
                after_worker_id=kwargs.get("worker_id"),
            )
        return await self.transport.command(
            HttpCommandName.LIST_WORKERS, query={"limit": limit, "cursor": cursor}
        )

    async def worker_shutdown(self, worker_id: str | None, queue: str | None) -> Any:
        if self.mode == "sql":
            count = await self.transport.request_worker_shutdown(
                worker_id=worker_id, queue=queue, actor=self._actor()
            )
        else:
            count = await self.transport.command(
                HttpCommandName.REQUEST_WORKER_SHUTDOWN,
                body={"worker_id": worker_id, "queue": queue},
            )
        return {"count": count}

    async def worker_expire(self, worker_id: str) -> Any:
        if self.mode == "sql":
            return await self.transport.expire_worker_leases(worker_id, self._actor())
        return await self.transport.command(
            HttpCommandName.EXPIRE_WORKER_LEASES,
            path_params={"worker_id": worker_id},
            body={},
        )

    async def workflow_list(self, view: str, limit: int, cursor: Any) -> Any:
        if self.mode == "sql":
            method = getattr(self.transport, "list_workflows", None)
            if method is None:
                raise TaskqCapabilityError(details={"capability": "read_model_workflow_list"})
            return await method(view, limit=limit, after=cursor)
        name = getattr(HttpCommandName, "LIST_WORKFLOWS", None)
        if name is None:
            raise TaskqCapabilityError(details={"capability": "read_model_workflow_list"})
        return await self.transport.command(
            name, query={"view": view, "limit": limit, "cursor": cursor}
        )

    async def workflow_show(self, workflow_id: UUID, limit: int = 50, cursor: Any = None) -> Any:
        if self.mode == "sql":
            return await self.transport.get_workflow_page(workflow_id, limit=limit, after=cursor)
        return await self.transport.get_workflow_page(workflow_id, limit=limit, cursor=cursor)

    async def workflow_create(
        self,
        workflow_key: str,
        kind: str,
        params: dict[str, Any],
        declared_queues: tuple[str, ...],
        member_limit: int | None,
        continuation_policy_hash: str | None,
    ) -> Any:
        return await self.transport.create_workflow(
            workflow_key,
            WorkflowKind(kind),
            params=params,
            declared_queues=declared_queues,
            actor=self._actor(),
            member_limit=member_limit,
            continuation_policy_hash=continuation_policy_hash,
        )

    async def workflow_seal(self, workflow_id: UUID) -> Any:
        return await self.transport.seal_workflow(workflow_id, self._actor())

    async def workflow_cancel(self, workflow_id: UUID, reason: str | None) -> Any:
        return await self.transport.cancel_workflow(workflow_id, self._actor(), reason)

    async def schedule_list(self, view: str, limit: int, cursor: Any) -> Any:
        if self.mode == "sql":
            method = getattr(self.transport, "list_schedules", None)
            if method is None:
                raise TaskqCapabilityError(details={"capability": "operator_schedule_list"})
            return await method(view, limit=limit, after=cursor)
        name = getattr(HttpCommandName, "LIST_SCHEDULES", None)
        if name is None:
            raise TaskqCapabilityError(details={"capability": "operator_schedule_list"})
        return await self.transport.command(
            name, query={"view": view, "limit": limit, "cursor": cursor}
        )

    async def schedule_show(self, name: str) -> Any:
        return await self.transport.get_schedule(name)

    async def schedule_state(self, name: str, state: ScheduleState, expected_version: int) -> Any:
        profile = await self.schedule_show(name)
        definition = ScheduleDefinition(
            target=profile.target,
            recurrence=profile.recurrence,
            catchup_policy=profile.catchup_policy,
            max_catchup=profile.max_catchup,
            paused=state is ScheduleState.PAUSED,
        )
        if self.mode == "sql":
            return await self.transport.put_schedule(
                name, definition, self._actor(), expected_version=expected_version
            )
        return await self.transport.put_schedule(
            name, definition, expected_version=expected_version
        )

    async def schedule_retire(self, name: str, expected_version: int) -> Any:
        if self.mode == "sql":
            return await self.transport.retire_schedule(name, expected_version, self._actor())
        return await self.transport.retire_schedule(name, expected_version)

    async def tick(self, reap_limit: int) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(details={"transport": "http", "command": "tick"})
        return await self.transport.tick(reap_limit)

    async def janitor(self) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(details={"transport": "http", "command": "janitor"})
        return await self.transport.janitor()

    async def prune_queue_audit(self, older_than_hours: int) -> Any:
        if self.mode != "sql":
            raise TaskqCapabilityError(
                details={"transport": "http", "command": "prune_queue_audit"}
            )
        return await self.transport.prune_queue_audit(older_than_hours)

    async def metrics(self) -> Any:
        return await self.transport.metrics()


@asynccontextmanager
async def open_cli_transport(connection: ResolvedConnection) -> AsyncIterator[CliTransport]:
    if connection.transport == "sql":
        if connection.dsn is None:
            raise TaskqConfigError("SQL connection has no DSN")
        transport = SqlTaskqTransport.from_dsn(
            connection.dsn.get_secret_value(),
            expected_environment=connection.expected_environment,
            expected_installation_id=connection.expected_installation_id,
            allow_production=connection.expected_environment == "production",
        )
    else:
        try:
            from taskq.http.client import AsyncTaskqHttpClient
        except ModuleNotFoundError:
            raise TaskqConfigError(
                "HTTP CLI commands require the package extra: outlabs-taskq[http]"
            ) from None
        transport = AsyncTaskqHttpClient(
            connection.base_url or "",
            bearer_token=connection.bearer_token,
            header_name=connection.header_name,
            header_value=connection.header_value,
        )
        await transport.start()
    try:
        yield CliTransport(connection, transport)
    finally:
        await transport.aclose()


__all__ = ["CliTransport", "open_cli_transport"]
