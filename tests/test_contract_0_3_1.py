"""SQL 0.3.1 bounded CLI read model and HTTP parity vectors."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from taskq.http import (
    AsyncTaskqHttpClient,
    AuthContext,
    ClaimWaitHub,
    TaskqFacadeTransports,
    callable_auth,
    create_taskq_app,
    no_auth_for_tests,
)
from taskq.protocol import EnqueueCommand, JobStatus, PROTOCOL_DOCUMENT_REVISION, ScheduleState
from taskq.sql.manifest import CONTRACT_VERSION, META_SEEDS
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


def _resources(transport: SqlTaskqTransport) -> TaskqFacadeTransports:
    return TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
        workflow_producer=transport,
        workflow_authorization=transport,
        workflow_enabled=True,
        workflow_read_enabled=True,
        schedule_enabled=True,
        worker_presence_enabled=True,
    )


def _schedule(queue: str, *, paused: bool = False) -> dict[str, object]:
    return {
        "target": {
            "kind": "job",
            "queue": queue,
            "job_type": "tests.schedule",
            "payload": {},
        },
        "recurrence": {"kind": "interval", "interval_seconds": 3600},
        "catchup_policy": "fire_once",
        "max_catchup": 1,
        "paused": paused,
    }


async def _queue(transport: SqlTaskqTransport, name: str) -> None:
    await transport.ensure_queue(name, actor="cli-read-model-test")


async def _enqueue(transport: SqlTaskqTransport, queue: str, **values: object):
    return await transport.enqueue(
        EnqueueCommand(
            queue=queue,
            job_type="tests.read_model",
            payload={},
            idempotency_key=f"read-model:{uuid4()}",
            **values,
        )
    )


def test_0_3_1_machine_identity_and_capabilities_are_exact() -> None:
    assert CONTRACT_VERSION == "0.6.2"
    assert PROTOCOL_DOCUMENT_REVISION == "1.0.17"
    assert {
        "operator_schedule_list",
        "queue_counters",
        "read_model_job_events",
        "read_model_job_views_v2",
        "read_model_workflow_list",
    } <= set(json.loads(META_SEEDS["capabilities"])["active"])


async def test_all_finite_job_views_and_keyset_pages(pg: object, sqlalchemy_dsn: str) -> None:
    del pg
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        for view in (
            "ready",
            "scheduled",
            "blocked",
            "running",
            "cancel_requested",
            "failed",
            "finished",
        ):
            await _queue(transport, f"cli_view_{view}")

        ready_ids = [(await _enqueue(transport, "cli_view_ready")).job_id for _ in range(3)]
        first = await transport.list_jobs("cli_view_ready", "ready", limit=2)
        second = await transport.list_jobs(
            "cli_view_ready", "ready", limit=2, after=first.next_after
        )
        assert len(first.items) == 2 and len(second.items) == 1
        assert {item.job_id for item in (*first.items, *second.items)} == set(ready_ids)
        assert first.next_after is not None and second.next_after is None

        await _enqueue(
            transport,
            "cli_view_scheduled",
            scheduled_at=datetime.now(UTC) + timedelta(days=1),
        )

        blocked_workflow = await transport.create_workflow(
            f"blocked-{uuid4()}",
            "dag",
            params={},
            declared_queues=("cli_view_blocked",),
            actor="cli-read-model-test",
        )
        parent = await _enqueue(
            transport,
            "cli_view_blocked",
            workflow_id=blocked_workflow.workflow_id,
            step_key="parent",
        )
        await _enqueue(
            transport,
            "cli_view_blocked",
            workflow_id=blocked_workflow.workflow_id,
            step_key="child",
            depends_on=(parent.job_id,),
        )

        running = await _enqueue(transport, "cli_view_running")
        claimed = await transport.claim("cli_view_running", "cli-running")
        assert claimed.jobs[0].job_id == running.job_id

        cancelling = await _enqueue(transport, "cli_view_cancel_requested")
        claimed_cancel = await transport.claim("cli_view_cancel_requested", "cli-cancel")
        assert claimed_cancel.jobs[0].job_id == cancelling.job_id
        await transport.cancel(cancelling.job_id, "cli-read-model-test", "stop")

        failed = await _enqueue(transport, "cli_view_failed")
        claimed_failed = await transport.claim("cli_view_failed", "cli-failed")
        await transport.fail(
            failed.job_id,
            claimed_failed.jobs[0].attempt_id,
            "cli-failed",
            "expected failure",
            retryable=False,
        )

        finished = await _enqueue(transport, "cli_view_finished")
        await transport.cancel(finished.job_id, "cli-read-model-test", "finished view")

        expected = {
            "scheduled": JobStatus.QUEUED,
            "blocked": JobStatus.BLOCKED,
            "running": JobStatus.RUNNING,
            "cancel_requested": JobStatus.RUNNING,
            "failed": JobStatus.FAILED,
            "finished": JobStatus.CANCELLED,
        }
        for view, status in expected.items():
            page = await transport.list_jobs(f"cli_view_{view}", view, limit=10)
            assert len(page.items) == 1
            assert page.items[0].status is status
    finally:
        await transport.aclose()


async def test_missing_queue_profile_is_a_typed_absence(sqlalchemy_dsn: str) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        assert await transport.get_queue_profile("cli_missing_queue") is None
    finally:
        await transport.aclose()


async def test_job_events_are_attempt_fence_free_and_details_are_opt_in(
    pg: object,
    sqlalchemy_dsn: str,
) -> None:
    del pg
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        await _queue(transport, "cli_event_view")
        job = await _enqueue(transport, "cli_event_view")
        summary = await transport.list_job_events(job.job_id, limit=1)
        details = await transport.list_job_events(job.job_id, limit=1, include_details=True)
        assert len(summary.items) == len(details.items) == 1
        assert summary.items[0].message is None and summary.items[0].data is None
        assert details.items[0].data is not None
        assert "attempt_id" not in details.items[0].model_dump(mode="json")
        assert "attempt_id" not in str(details.items[0].model_dump(mode="json"))
    finally:
        await transport.aclose()


async def test_global_workflow_and_schedule_lists_are_bounded_and_stable(
    pg: object,
    sqlalchemy_dsn: str,
) -> None:
    del pg
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        await _queue(transport, "cli_global_lists")
        running = await transport.create_workflow(
            f"running-{uuid4()}",
            "dag",
            params={},
            declared_queues=("cli_global_lists",),
            actor="cli-read-model-test",
        )
        finished = await transport.create_workflow(
            f"finished-{uuid4()}",
            "dag",
            params={},
            declared_queues=("cli_global_lists",),
            actor="cli-read-model-test",
        )
        await transport.seal_workflow(finished.workflow_id, "cli-read-model-test")
        running_page = await transport.list_workflows("running", limit=100)
        finished_page = await transport.list_workflows("finished", limit=100)
        assert running.workflow_id in {item.workflow_id for item in running_page.items}
        assert finished.workflow_id in {item.workflow_id for item in finished_page.items}

        for name, paused in (
            ("cli-list-active-a", False),
            ("cli-list-active-b", False),
            ("cli-list-paused", True),
            ("cli-list-retired", False),
        ):
            await transport.put_schedule(
                name, _schedule("cli_global_lists", paused=paused), "cli-read-model-test"
            )
        retired = await transport.get_schedule("cli-list-retired")
        await transport.retire_schedule("cli-list-retired", retired.version, "cli-read-model-test")

        active_first = await transport.list_schedules("active", limit=1)
        active_second = await transport.list_schedules(
            "active", limit=10, after=active_first.next_after
        )
        active_names = [item.name for item in (*active_first.items, *active_second.items)]
        assert active_names == sorted(active_names)
        assert {"cli-list-active-a", "cli-list-active-b"} <= set(active_names)
        assert any(
            item.name == "cli-list-paused"
            for item in (await transport.list_schedules("paused", limit=100)).items
        )
        retired_page = await transport.list_schedules("retired", limit=100)
        assert any(item.name == "cli-list-retired" for item in retired_page.items)
        assert all(item.state is ScheduleState.RETIRED for item in retired_page.items)
    finally:
        await transport.aclose()


async def test_real_asgi_http_matches_sql_read_model_and_target_diagnostics(
    pg: object,
    sqlalchemy_dsn: str,
) -> None:
    del pg
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        await _queue(transport, "cli_http_parity")
        job = await _enqueue(transport, "cli_http_parity")
        workflow = await transport.create_workflow(
            f"http-parity-{uuid4()}",
            "dag",
            params={},
            declared_queues=("cli_http_parity",),
            actor="cli-http-parity",
        )
        await transport.put_schedule(
            "cli-http-parity", _schedule("cli_http_parity"), "cli-http-parity"
        )

        app = _mounted(
            create_taskq_app(
                _resources(transport),
                authorizer=no_auth_for_tests(),
                operator_transport=transport,
                operator_authorizer=no_auth_for_tests(),
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as raw:
            client = AsyncTaskqHttpClient(
                "http://test",
                header_name="X-Test-Key",
                header_value="test",
                client=raw,
                max_retries=0,
            )
            target = await client.get_target_identity()
            health = await client.get_scheduler_health()
            sql_target = await transport.get_target_identity()
            assert target.model_dump(exclude={"bound_at", "bound_by"}) == sql_target.model_dump(
                exclude={"bound_at", "bound_by"}
            )
            assert target.bound_at is None and target.bound_by is None
            sql_health = await transport.get_scheduler_health()
            assert health.model_dump(exclude={"database_time"}) == sql_health.model_dump(
                exclude={"database_time"}
            )

            sql_jobs = await transport.list_jobs("cli_http_parity", "ready", limit=10)
            http_jobs = await client.list_jobs("cli_http_parity", "ready", limit=10)
            assert http_jobs.items == sql_jobs.items

            sql_events = await transport.list_job_events(job.job_id, limit=10)
            http_events = await client.list_job_events(job.job_id, limit=10)
            assert http_events.items == sql_events.items

            http_workflows = await client.list_workflows("running", limit=100)
            assert workflow.workflow_id in {item.workflow_id for item in http_workflows.items}
            http_schedules = await client.list_schedules("active", limit=100)
            assert "cli-http-parity" in {item.name for item in http_schedules.items}
    finally:
        await transport.aclose()


async def test_authorization_precedes_cursor_decoding_on_new_http_lists(
    pg: object,
    sqlalchemy_dsn: str,
) -> None:
    del pg
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        await _queue(transport, "cli_auth_before_cursor")
        job = await _enqueue(transport, "cli_auth_before_cursor")

        async def authenticate(_request: object) -> AuthContext:
            return AuthContext(actor="denied", principal="denied")

        async def deny(*_args: object, **_kwargs: object) -> None:
            raise HTTPException(status_code=403)

        denied = callable_auth(authenticate, deny)
        app = _mounted(
            create_taskq_app(
                _resources(transport),
                authorizer=denied,
                operator_transport=transport,
                operator_authorizer=denied,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = [
                await client.get(f"/taskq/v1/jobs/{job.job_id}/events?cursor=not-base64"),
                await client.get("/taskq/v1/workflows?view=running&cursor=not-base64"),
                await client.get("/taskq/v1/schedules?view=active&cursor=not-base64"),
            ]
        assert [response.status_code for response in responses] == [403, 403, 403]
        assert all(response.json()["error"]["code"] == "AUTH403" for response in responses)
    finally:
        await transport.aclose()
