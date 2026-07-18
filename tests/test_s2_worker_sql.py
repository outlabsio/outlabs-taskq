"""S2-04-AUDIT live-SQL worker conservation and replay evidence."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from taskq import (
    Cancel,
    Complete,
    JobContext,
    Retry,
    Snooze,
    Task,
    TaskRegistry,
    TaskqUnavailableError,
    WorkerOptions,
    WorkerService,
    WorkerServiceOptions,
    WorkerSupervisor,
)
from taskq.protocol import ClaimState, EnqueueCommand, JobStatus, SettleResult
from taskq.sql.transport import SqlTaskqTransport
from taskq.sql.notifications import PostgresNotificationSource

pytestmark = pytest.mark.taskq_sql


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


@pytest.fixture
async def worker_transports(
    sqlalchemy_dsn: str,
) -> AsyncIterator[dict[str, SqlTaskqTransport]]:
    transports: dict[str, SqlTaskqTransport] = {}
    for capability in ("producer", "runner", "observer", "operator"):
        engine = create_async_engine(
            sqlalchemy_dsn,
            connect_args={"server_settings": {"role": f"taskq_{capability}"}},
        )
        transports[capability] = SqlTaskqTransport(engine)
    try:
        yield transports
    finally:
        for transport in transports.values():
            await transport.engine.dispose()


async def _claimed(
    transports: dict[str, SqlTaskqTransport], queue: str, job_type: str
) -> tuple[object, object]:
    await transports["operator"].ensure_queue(queue, actor="worker-audit")
    enqueued = await transports["producer"].enqueue(
        EnqueueCommand(queue=queue, job_type=job_type, payload={"value": 2})
    )
    claimed = await transports["runner"].claim(queue, "worker-audit")
    assert claimed.state is ClaimState.CLAIMED
    assert claimed.jobs[0].job_id == enqueued.job_id
    return enqueued.job_id, claimed.jobs[0]


def _task(job_type: str, handler: object) -> Task[Input, Output]:
    return Task(
        name=job_type,
        queue=job_type.replace(".", "_"),
        input_model=Input,
        output_model=Output,
        handler=handler,  # type: ignore[arg-type]
    )


async def _complete(payload: Input) -> Output:
    return Output(doubled=payload.value * 2)


async def _retry(payload: Input) -> Retry:
    return Retry(error=f"retry {payload.value}", after_seconds=1)


async def _snooze(payload: Input) -> Snooze:
    return Snooze(delay_seconds=1, reason="later")


async def _cancel(payload: Input) -> Cancel:
    return Cancel(reason=f"cancel {payload.value}")


@pytest.mark.parametrize(
    ("job_type", "handler", "status", "failures", "event"),
    [
        ("audit.complete", _complete, JobStatus.SUCCEEDED, 0, "succeeded"),
        ("audit.retry", _retry, JobStatus.QUEUED, 1, "retry_scheduled"),
        ("audit.snooze", _snooze, JobStatus.QUEUED, 0, "snoozed"),
        ("audit.cancel", _cancel, JobStatus.CANCELLED, 0, "cancelled"),
        ("audit.missing", None, JobStatus.QUEUED, 0, "released"),
    ],
)
async def test_real_sql_intents_conserve_budget_attempt_and_events(
    pg: Any,
    worker_transports: dict[str, SqlTaskqTransport],
    job_type: str,
    handler: object | None,
    status: JobStatus,
    failures: int,
    event: str,
) -> None:
    queue = job_type.replace(".", "_")
    job_id, claim = await _claimed(worker_transports, queue, job_type)
    registry = TaskRegistry([] if handler is None else [_task(job_type, handler)])
    supervisor = WorkerSupervisor(worker_transports["runner"], registry, "worker-audit")
    await supervisor.run_job(claim)  # type: ignore[arg-type]
    await supervisor.aclose()

    detail = await worker_transports["observer"].get_job(job_id)  # type: ignore[arg-type]
    assert detail is not None
    assert detail.status is status
    assert detail.attempt_count == 1
    assert detail.failure_count == failures
    events = await pg.fetch(
        "SELECT event_type FROM taskq.job_events WHERE job_id=$1 ORDER BY id", job_id
    )
    assert [row["event_type"] for row in events] == ["enqueued", "claimed", event]


async def test_real_sql_shutdown_release_is_budget_free(
    pg: Any, worker_transports: dict[str, SqlTaskqTransport]
) -> None:
    started = asyncio.Event()

    async def waiting(ctx: JobContext, payload: Input) -> Output:
        started.set()
        await asyncio.Event().wait()
        return Output(doubled=payload.value * 2)

    job_type = "audit.shutdown"
    job_id, claim = await _claimed(worker_transports, "audit_shutdown", job_type)
    supervisor = WorkerSupervisor(
        worker_transports["runner"],
        TaskRegistry([_task(job_type, waiting)]),
        "worker-audit",
    )
    supervisor.start()
    running = supervisor.submit(claim)  # type: ignore[arg-type]
    await started.wait()
    await supervisor.stop(cancel=True)
    assert (await running).settlement_command == "release"
    detail = await worker_transports["observer"].get_job(job_id)  # type: ignore[arg-type]
    assert detail is not None
    assert detail.status is JobStatus.QUEUED
    assert detail.attempt_count == 1 and detail.failure_count == 0
    events = await pg.fetch(
        "SELECT event_type FROM taskq.job_events WHERE job_id=$1 ORDER BY id", job_id
    )
    assert [row["event_type"] for row in events] == ["enqueued", "claimed", "released"]


class _LostCompleteResponse:
    def __init__(self, inner: SqlTaskqTransport) -> None:
        self.inner = inner
        self.complete_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def complete(self, *args: Any, **kwargs: Any) -> SettleResult:
        self.complete_calls += 1
        result = await self.inner.complete(*args, **kwargs)
        if self.complete_calls == 1:
            raise TaskqUnavailableError()
        return result


async def test_real_sql_committed_lost_response_replays_without_handler_rerun(
    pg: Any, worker_transports: dict[str, SqlTaskqTransport]
) -> None:
    calls = 0

    async def counted(payload: Input) -> Complete:
        nonlocal calls
        calls += 1
        return Complete(result={"doubled": payload.value * 2})

    job_type = "audit.response_loss"
    job_id, claim = await _claimed(worker_transports, "audit_response_loss", job_type)
    transport = _LostCompleteResponse(worker_transports["runner"])
    supervisor = WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        TaskRegistry([_task(job_type, counted)]),
        "worker-audit",
        options=WorkerOptions(settle_backoff_base=0.001, settle_backoff_cap=0.001),
    )
    report = await supervisor.run_job(claim)  # type: ignore[arg-type]
    await supervisor.aclose()
    assert report.settlement_outcome == "already_settled"
    assert calls == 1 and transport.complete_calls == 2
    detail = await worker_transports["observer"].get_job(job_id)  # type: ignore[arg-type]
    assert detail is not None and detail.status is JobStatus.SUCCEEDED
    events = await pg.fetch(
        "SELECT event_type FROM taskq.job_events WHERE job_id=$1 ORDER BY id", job_id
    )
    assert [row["event_type"] for row in events] == ["enqueued", "claimed", "succeeded"]


async def test_worker_sql_resources_and_task_ledger_return_to_baseline(
    pg: Any, sqlalchemy_dsn: str
) -> None:
    del pg
    before = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    pool = transport.engine.sync_engine.pool
    supervisor = WorkerSupervisor(transport, TaskRegistry(), "worker-audit")
    await supervisor.aclose()
    await transport.aclose()
    await asyncio.sleep(0)
    assert pool.checkedout() == 0  # type: ignore[attr-defined]
    after = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    assert after == before


async def test_real_service_poll_only_claims_and_settles(
    worker_transports: dict[str, SqlTaskqTransport],
) -> None:
    completed = asyncio.Event()

    async def handler(payload: Input) -> Output:
        completed.set()
        return Output(doubled=payload.value * 2)

    job_type = "service.poll"
    await worker_transports["operator"].ensure_queue("service_poll", actor="audit")
    await worker_transports["producer"].enqueue(
        EnqueueCommand(queue="service_poll", job_type=job_type, payload={"value": 2})
    )
    service = WorkerService(
        worker_transports["runner"],
        TaskRegistry([_task(job_type, handler)]),
        "service-poll",
        options=WorkerServiceOptions(queues=("service_poll",), listen=False, poll_interval=0.1),
    )
    await service.start()
    await asyncio.wait_for(completed.wait(), timeout=5)
    await service.aclose()
    assert service.snapshot().claimed_jobs == service.snapshot().submitted_jobs == 1
    assert service.snapshot().active_slots == 0


async def test_real_notification_wake_and_listener_reconnect(
    pg: Any,
    taskq_dsn: str,
    worker_transports: dict[str, SqlTaskqTransport],
) -> None:
    completed = asyncio.Event()

    async def handler(payload: Input) -> Output:
        completed.set()
        return Output(doubled=payload.value * 2)

    job_type = "service.notify"
    queue = "service_notify"
    await worker_transports["operator"].ensure_queue(queue, actor="audit")
    notifications = PostgresNotificationSource(taskq_dsn)
    service = WorkerService(
        worker_transports["runner"],
        TaskRegistry([_task(job_type, handler)]),
        "service-notify",
        options=WorkerServiceOptions(queues=(queue,), poll_interval=30, listener_backoff_base=0.01),
        notifications=notifications,
    )
    await service.start()
    connection = notifications._connection
    assert connection is not None
    assert await pg.fetchval("SELECT pg_terminate_backend($1)", connection.get_server_pid())
    for _ in range(200):
        if service.snapshot().listener_reconnects == 1:
            break
        await asyncio.sleep(0.01)
    assert service.snapshot().listener_reconnects == 1
    await worker_transports["producer"].enqueue(
        EnqueueCommand(queue=queue, job_type=job_type, payload={"value": 2})
    )
    await asyncio.wait_for(completed.wait(), timeout=5)
    await service.aclose()
    assert notifications._connection is None


async def test_real_remote_shutdown_prevents_first_claim(
    worker_transports: dict[str, SqlTaskqTransport],
) -> None:
    worker_id = "service-drained"
    queue = "service_drained"
    await worker_transports["operator"].ensure_queue(queue, actor="audit")
    await worker_transports["producer"].enqueue(
        EnqueueCommand(queue=queue, job_type="service.drained", payload={"value": 2})
    )
    await worker_transports["runner"].worker_heartbeat(worker_id, [queue])
    assert (
        await worker_transports["operator"].request_worker_shutdown(
            worker_id=worker_id, queue=None, actor="audit"
        )
        == 1
    )
    service = WorkerService(
        worker_transports["runner"],
        TaskRegistry([_task("service.drained", _complete)]),
        worker_id,
        options=WorkerServiceOptions(queues=(queue,), listen=False),
    )
    await service.start()
    await asyncio.wait_for(service._stopped.wait(), timeout=5)
    assert service.snapshot().claim_sweeps == 0


async def test_real_hot_queues_each_receive_capacity(
    worker_transports: dict[str, SqlTaskqTransport],
) -> None:
    seen: set[str] = set()
    both = asyncio.Event()

    async def handler(context: JobContext, payload: Input) -> Output:
        seen.add(context.queue)
        if len(seen) == 2:
            both.set()
        return Output(doubled=payload.value * 2)

    tasks: list[Task[Input, Output]] = []
    for queue in ("service_fair_a", "service_fair_b"):
        await worker_transports["operator"].ensure_queue(queue, actor="audit")
        job_type = f"{queue}.work"
        tasks.append(
            Task(
                name=job_type,
                queue=queue,
                input_model=Input,
                output_model=Output,
                handler=handler,
            )
        )
        await worker_transports["producer"].enqueue(
            EnqueueCommand(queue=queue, job_type=job_type, payload={"value": 2})
        )
    service = WorkerService(
        worker_transports["runner"],
        TaskRegistry(tasks),
        "service-fair",
        options=WorkerServiceOptions(queues=("service_fair_a", "service_fair_b"), listen=False),
        supervisor_options=WorkerOptions(concurrency=2),
    )
    await service.start()
    await asyncio.wait_for(both.wait(), timeout=5)
    await service.aclose()
    assert seen == {"service_fair_a", "service_fair_b"}
