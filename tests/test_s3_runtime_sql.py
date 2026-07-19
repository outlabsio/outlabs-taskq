"""Live PostgreSQL and mounted-ASGI evidence for the S3-03 runtime."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from taskq import Task, TaskRegistry, WorkerOptions, WorkerService, WorkerServiceOptions
from taskq.errors import TaskqUnavailableError
from taskq.http import (
    AsyncTaskqHttpClient,
    EmbeddedWorkerOptions,
    TaskqRuntime,
    TaskqRuntimeOptions,
    create_taskq_app,
    no_auth_for_tests,
    taskq_lifespan,
)

pytestmark = pytest.mark.taskq_sql


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


async def _wait_for_status(pg: asyncpg.Connection, job_id: str, status: str) -> None:
    for _ in range(500):
        if await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1::uuid", job_id) == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not reach {status}")


def _mounted(runtime: TaskqRuntime) -> FastAPI:
    host = FastAPI(lifespan=taskq_lifespan(runtime))
    host.mount(
        "/taskq",
        create_taskq_app(runtime, authorizer=no_auth_for_tests(), poll_interval=0.1),
    )
    return host


async def test_live_runtime_embedded_worker_uses_ordinary_presence_and_settlement(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    taskq_dsn: str,
) -> None:
    queue = "s3_runtime_embedded"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 's3-test')", queue)
    handled = asyncio.Event()

    async def handler(payload: Input) -> Output:
        handled.set()
        return Output(doubled=payload.value * 2)

    registry = TaskRegistry(
        [
            Task(
                name="runtime.double",
                queue=queue,
                input_model=Input,
                output_model=Output,
                handler=handler,
            )
        ]
    )
    runtime = TaskqRuntime.from_dsn(
        taskq_dsn,
        registry=registry,
        options=TaskqRuntimeOptions(
            housekeeper_enabled=True,
            long_poll_listener_enabled=True,
            embedded_worker=EmbeddedWorkerOptions(
                queues=(queue,),
                acknowledge_process_multiplication=True,
                listen=True,
                poll_interval=0.1,
            ),
            request_pool_max=3,
            housekeeper_pool_max=1,
            embedded_worker_pool_max=2,
            expected_asgi_processes=1,
            database_connection_ceiling=20,
            database_connection_reserve=2,
        ),
    )
    app = _mounted(runtime)
    headers = {"Taskq-Protocol-Version": "1"}
    async with app.router.lifespan_context(app):
        assert runtime.ready
        snapshot = runtime.snapshot()
        assert snapshot.total_handler_capacity == 1
        assert snapshot.total_pool_capacity == 6
        assert snapshot.total_listener_connections == 2
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/taskq/v1/queues/{queue}/jobs",
                headers=headers,
                json={"job_type": "runtime.double", "payload": {"value": 3}},
            )
            assert response.status_code == 201
            job_id = response.json()["data"]["job_id"]
            await asyncio.wait_for(handled.wait(), timeout=5)
            await _wait_for_status(pg, job_id, "succeeded")
            hidden_tick = await client.post("/taskq/v1/tick", headers=headers)
            assert hidden_tick.status_code == 404
        presence = await pg.fetchrow(
            "SELECT worker_id, queues FROM taskq.workers "
            "WHERE worker_id LIKE 'api:%' ORDER BY last_seen_at DESC LIMIT 1"
        )
        assert presence is not None
        assert presence["queues"] == [queue]
    assert runtime.state.value == "stopped"


async def test_live_runtime_long_poll_listener_wakes_without_spanning_connection(
    operator: asyncpg.Connection,
    taskq_dsn: str,
) -> None:
    queue = "s3_runtime_long_poll"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 's3-test')", queue)
    runtime = TaskqRuntime.from_dsn(
        taskq_dsn,
        options=TaskqRuntimeOptions(
            housekeeper_enabled=False,
            long_poll_listener_enabled=True,
            request_pool_max=2,
            expected_asgi_processes=1,
            database_connection_ceiling=10,
        ),
    )
    app = _mounted(runtime)
    headers = {"Taskq-Protocol-Version": "1"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            claim = asyncio.create_task(
                client.post(
                    f"/taskq/v1/queues/{queue}/claims",
                    headers=headers,
                    json={"worker_id": "remote-runtime", "wait_seconds": 5},
                )
            )
            for _ in range(100):
                listener = runtime.notification_listener
                if listener is not None and queue in listener._queues:
                    break
                await asyncio.sleep(0.01)
            enqueued = await client.post(
                f"/taskq/v1/queues/{queue}/jobs",
                headers=headers,
                json={"job_type": "runtime.remote", "payload": {}},
            )
            assert enqueued.status_code == 201
            claimed = await asyncio.wait_for(claim, timeout=2)
            assert claimed.status_code == 200
            assert claimed.json()["outcome"] == "claimed"
            pool = runtime.facade_transports.producer.engine.sync_engine.pool  # type: ignore[attr-defined]
            assert pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_live_duplicate_housekeepers_are_advisory_lock_safe(taskq_dsn: str) -> None:
    options = TaskqRuntimeOptions(
        housekeeper_enabled=True,
        long_poll_listener_enabled=False,
        request_pool_max=1,
        housekeeper_pool_max=1,
        expected_asgi_processes=2,
        database_connection_ceiling=10,
    )
    first = TaskqRuntime.from_dsn(taskq_dsn, options=options)
    second = TaskqRuntime.from_dsn(taskq_dsn, options=options)
    try:
        await asyncio.gather(first.start(), second.start())
        assert first.ready and second.ready
        assert first.snapshot().housekeeper_failures == 0
        assert second.snapshot().housekeeper_failures == 0
    finally:
        await asyncio.gather(first.stop(), second.stop())


async def test_live_http_worker_reuses_supervisor_and_honors_remote_drain(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "s3_runtime_http_worker"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 's3-test')", queue)
    handled = asyncio.Event()
    handler_calls = 0

    async def handler(payload: Input) -> Output:
        nonlocal handler_calls
        handler_calls += 1
        handled.set()
        return Output(doubled=payload.value * 2)

    class LostCompleteResponse:
        def __init__(self, inner: AsyncTaskqHttpClient) -> None:
            self.inner = inner
            self.complete_calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            self.complete_calls += 1
            result = await self.inner.complete(*args, **kwargs)
            if self.complete_calls == 1:
                raise TaskqUnavailableError()
            return result

    registry = TaskRegistry(
        [
            Task(
                name="runtime.remote_worker",
                queue=queue,
                input_model=Input,
                output_model=Output,
                handler=handler,
            )
        ]
    )
    facade_transport = TaskqRuntime.from_dsn(
        sqlalchemy_dsn,
        options=TaskqRuntimeOptions(
            housekeeper_enabled=False,
            long_poll_listener_enabled=False,
        ),
    )
    taskq_app = FastAPI()
    taskq_app.mount(
        "/taskq",
        create_taskq_app(facade_transport, authorizer=no_auth_for_tests(), poll_interval=0.1),
    )
    borrowed = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=taskq_app), base_url="http://test"
    )
    remote = AsyncTaskqHttpClient(
        "http://test/taskq",
        client=borrowed,
        bearer_token="test-worker",
        claim_wait_seconds=0.2,
        timeout=1,
    )
    lost_response = LostCompleteResponse(remote)
    service = WorkerService(
        lost_response,  # type: ignore[arg-type]
        registry,
        "remote-runtime-worker",
        options=WorkerServiceOptions(
            queues=(queue,),
            listen=False,
            poll_interval=1,
            cancel_inflight_claim_on_stop=True,
        ),
        supervisor_options=WorkerOptions(
            concurrency=1,
            soft_stop_timeout=1,
            settle_backoff_base=0.01,
            settle_backoff_cap=0.01,
        ),
    )
    try:
        await remote.start()
        await service.start()
        response = await borrowed.post(
            f"/taskq/v1/queues/{queue}/jobs",
            headers={"Taskq-Protocol-Version": "1"},
            json={"job_type": "runtime.remote_worker", "payload": {"value": 7}},
        )
        assert response.status_code == 201
        job_id = response.json()["data"]["job_id"]
        for _ in range(500):
            if handled.is_set() or service.snapshot().fatal:
                break
            await asyncio.sleep(0.01)
        assert not service.snapshot().fatal, repr(service._fatal_error)
        assert handled.is_set()
        await _wait_for_status(pg, job_id, "succeeded")
        for _ in range(100):
            if lost_response.complete_calls == 2:
                break
            await asyncio.sleep(0.01)
        assert handler_calls == 1
        assert lost_response.complete_calls == 2
        assert (
            await operator.fetchval(
                "SELECT taskq.request_worker_shutdown($1, NULL, 's3-test')",
                "remote-runtime-worker",
            )
            == 1
        )
        assert await service._write_presence() is True
        await asyncio.wait_for(service.stop(), timeout=2)
        assert service.snapshot().submitted_jobs == 1
    finally:
        await service.aclose()
        await remote.aclose()
        await borrowed.aclose()
        await facade_transport.stop()


async def test_live_asgi_runtime_exits_process_before_abandoned_sync_can_be_released(
    operator: asyncpg.Connection,
    taskq_dsn: str,
) -> None:
    queue = "s3_runtime_unsafe_sync"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 's3-test')", queue)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    exits: list[tuple[int, bool]] = []

    def handler(payload: Input) -> Output:
        started.set()
        release.wait(timeout=5)
        finished.set()
        return Output(doubled=payload.value * 2)

    def process_exit(code: int) -> None:
        exits.append((code, finished.is_set()))
        release.set()  # Simulate termination only after recording the safety boundary.

    registry = TaskRegistry(
        [
            Task(
                name="runtime.blocking",
                queue=queue,
                input_model=Input,
                output_model=Output,
                handler=handler,
            )
        ]
    )
    runtime = TaskqRuntime.from_dsn(
        taskq_dsn,
        registry=registry,
        process_exit=process_exit,
        options=TaskqRuntimeOptions(
            housekeeper_enabled=False,
            long_poll_listener_enabled=False,
            embedded_worker=EmbeddedWorkerOptions(
                queues=(queue,),
                acknowledge_process_multiplication=True,
                listen=False,
                poll_interval=0.1,
            ),
            soft_stop_timeout=0.05,
            request_pool_max=2,
            embedded_worker_pool_max=2,
            expected_asgi_processes=1,
            database_connection_ceiling=10,
        ),
    )
    app = _mounted(runtime)
    headers = {"Taskq-Protocol-Version": "1"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/taskq/v1/queues/{queue}/jobs",
                headers=headers,
                json={"job_type": "runtime.blocking", "payload": {"value": 4}},
            )
            assert response.status_code == 201
            for _ in range(500):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
    assert exits == [(3, False)]
    assert runtime.state.value == "failed"
    assert finished.wait(timeout=1)
