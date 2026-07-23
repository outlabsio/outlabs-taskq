"""Protocol-1.0.10 workflow facade, client, replay, and authorization evidence."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4
import asyncpg
from fastapi import FastAPI, HTTPException
import httpx
import pytest

from taskq.http import (
    AsyncTaskqHttpClient,
    AuthContext,
    ClaimWaitHub,
    TaskqFacadeTransports,
    callable_auth,
    create_taskq_app,
    no_auth_for_tests,
)
from taskq.protocol import EnqueueCommand, TaskqAction
from taskq.sql.transport import SqlTaskqTransport
from taskq.testing import FakeTaskQClient

pytestmark = pytest.mark.taskq_sql


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


def _resources(
    transport: SqlTaskqTransport,
    *,
    workflow_enabled: bool,
    workflow_read_enabled: bool = False,
) -> TaskqFacadeTransports:
    return TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
        workflow_producer=transport if workflow_enabled else None,
        workflow_authorization=transport if workflow_enabled or workflow_read_enabled else None,
        workflow_enabled=workflow_enabled,
        workflow_read_enabled=workflow_read_enabled,
    )


async def _make_queues(operator: asyncpg.Connection, *queues: str) -> None:
    for queue in queues:
        row = await operator.fetchrow(
            "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'workflow-surface')",
            queue,
        )
        assert row is not None


class _DropFirstCreateResponse(httpx.AsyncBaseTransport):
    def __init__(self, app: FastAPI) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.bodies: dict[str, list[bytes]] = {}
        self.dropped: set[str] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        path = request.url.path
        target = (
            "create"
            if path.endswith("/workflows")
            else "member"
            if path.endswith("/jobs")
            else "seal"
            if path.endswith("/seal")
            else None
        )
        if target is not None:
            self.bodies.setdefault(target, []).append(body)
        response = await self.inner.handle_async_request(request)
        if target is not None and target not in self.dropped:
            self.dropped.add(target)
            await response.aread()
            await response.aclose()
            raise httpx.ReadError("committed response lost", request=request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


async def test_workflow_routes_are_absent_until_runtime_gate_and_openapi_is_exact(
    sqlalchemy_dsn: str,
) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        disabled = create_taskq_app(
            _resources(transport, workflow_enabled=False), authorizer=no_auth_for_tests()
        )
        assert not any("/workflows" in path for path in disabled.openapi()["paths"])

        enabled = create_taskq_app(
            _resources(transport, workflow_enabled=True, workflow_read_enabled=True),
            authorizer=no_auth_for_tests(),
            operator_transport=transport,
            operator_authorizer=no_auth_for_tests(),
        )
        schema = enabled.openapi()
        assert {path for path in schema["paths"] if "/workflows" in path} == {
            "/v1/workflows",
            "/v1/workflows/{id}/seal",
            "/v1/workflows/{id}/cancel",
            "/v1/workflows/{workflow_id}",
        }
        create_schema = schema["paths"]["/v1/workflows"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert set(create_schema["properties"]) == {
            "workflow_key",
            "kind",
            "params",
            "declared_queues",
        }
        cancel_schema = schema["paths"]["/v1/workflows/{id}/cancel"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]
        assert set(cancel_schema["properties"]) == {"reason"}
        assert "actor" not in json.dumps(schema)
    finally:
        await transport.aclose()


async def test_http_workflow_replay_dependency_promotion_and_raw_state(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    await _make_queues(operator, "workflow_one", "workflow_two")
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, workflow_enabled=True, workflow_read_enabled=True),
            authorizer=no_auth_for_tests(),
            operator_transport=transport,
            operator_authorizer=no_auth_for_tests(),
        )
    )
    dropping = _DropFirstCreateResponse(app)
    raw = httpx.AsyncClient(transport=dropping, base_url="http://test")
    client = AsyncTaskqHttpClient(
        "http://test", bearer_token="test-only", client=raw, max_retries=1
    )
    try:
        workflow = await client.create_workflow(
            "surface-replay",
            "dag",
            params={"stable": [1, 2]},
            declared_queues=("workflow_two", "workflow_one"),
            actor="must-not-cross-wire",
        )
        assert workflow.outcome == "existed"
        assert len(dropping.bodies["create"]) == 2
        assert dropping.bodies["create"][0] == dropping.bodies["create"][1]
        assert b"must-not-cross-wire" not in dropping.bodies["create"][0]

        parent = await client.enqueue(
            EnqueueCommand(
                queue="workflow_one",
                job_type="tests.parent",
                payload={},
                workflow_id=workflow.workflow_id,
                step_key="parent",
            )
        )
        assert parent.status.value == "existed"
        assert dropping.bodies["member"][0] == dropping.bodies["member"][1]
        child = await client.enqueue(
            EnqueueCommand(
                queue="workflow_two",
                job_type="tests.child",
                payload={},
                workflow_id=workflow.workflow_id,
                step_key="child",
                depends_on=(parent.job_id,),
            )
        )
        assert (
            await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", child.job_id)
            == "blocked"
        )
        claim = await transport.claim("workflow_one", "workflow-worker", job_id=parent.job_id)
        attempt = claim.jobs[0]
        await transport.complete(parent.job_id, attempt.attempt_id, "workflow-worker")
        assert (
            await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", child.job_id) == "queued"
        )
        sealed = await client.seal_workflow(workflow.workflow_id, "ignored")
        assert sealed.outcome == "already_sealed"
        assert dropping.bodies["seal"][0] == dropping.bodies["seal"][1] == b"{}"
        rows = await pg.fetch(
            "SELECT step_key, status FROM taskq.jobs WHERE workflow_id=$1 ORDER BY step_key",
            workflow.workflow_id,
        )
        assert [tuple(row.values()) for row in rows] == [
            ("child", "queued"),
            ("parent", "succeeded"),
        ]
        cancelled = await client.cancel_workflow(
            workflow.workflow_id, "must-not-cross-wire", "surface stop"
        )
        replayed_cancel = await client.cancel_workflow(
            workflow.workflow_id, "must-not-cross-wire", "surface stop"
        )
        assert (cancelled.outcome, replayed_cancel.outcome) == (
            "cancel_requested",
            "already_terminal",
        )
        assert cancelled.status.value == replayed_cancel.status.value == "cancelled"
    finally:
        await client.aclose()
        await raw.aclose()
        await transport.aclose()


async def test_workflow_authorization_precedes_graph_access_and_rejection_writes_nothing(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    await _make_queues(operator, "workflow_allowed", "workflow_denied")
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    calls: list[tuple[TaskqAction, str | None]] = []

    async def authenticate(_: Any) -> AuthContext:
        return AuthContext(actor="auth-actor", principal="principal")

    async def authorize(
        _: Any,
        __: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        calls.append((action, queue))
        if queue == "workflow_denied":
            raise HTTPException(status_code=403)

    authorizer = callable_auth(authenticate, authorize)
    app = _mounted(
        create_taskq_app(
            _resources(transport, workflow_enabled=True, workflow_read_enabled=True),
            authorizer=authorizer,
            operator_transport=transport,
            operator_authorizer=authorizer,
            not_found_on_forbidden=True,
        )
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied_create = await client.post(
                "/taskq/v1/workflows",
                json={
                    "workflow_key": "denied",
                    "kind": "dag",
                    "declared_queues": ["workflow_allowed", "workflow_denied"],
                },
            )
            assert denied_create.status_code == 403
            assert await pg.fetchval("SELECT count(*) FROM taskq.workflows") == 0

            workflow = await transport.create_workflow(
                "projection-denial",
                "dag",
                declared_queues=("workflow_allowed", "workflow_denied"),
                actor="setup",
            )
            calls.clear()
            denied_member = await client.post(
                "/taskq/v1/queues/workflow_allowed/jobs",
                json={
                    "job_type": "tests.denied",
                    "payload": {},
                    "workflow_id": str(workflow.workflow_id),
                    "step_key": "member",
                },
            )
            assert denied_member.status_code == 404
            assert calls == [
                (TaskqAction.ENQUEUE, "workflow_allowed"),
                (TaskqAction.ENQUEUE, "workflow_allowed"),
                (TaskqAction.ENQUEUE, "workflow_denied"),
            ]
            assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0

            calls.clear()
            path_denied_before_decode = await client.post(
                "/taskq/v1/queues/workflow_denied/jobs",
                content=b'{"workflow_id":',
            )
            assert path_denied_before_decode.status_code == 403
            assert calls == [(TaskqAction.ENQUEUE, "workflow_denied")]
            assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0

            denied_seal = await client.post(
                f"/taskq/v1/workflows/{workflow.workflow_id}/seal",
                json={},
            )
            unknown_seal = await client.post(f"/taskq/v1/workflows/{uuid4()}/seal", json={})
            assert denied_seal.status_code == unknown_seal.status_code == 404
            assert denied_seal.json()["error"] == unknown_seal.json()["error"]

            calls.clear()
            denied_cancel = await client.post(
                f"/taskq/v1/workflows/{workflow.workflow_id}/cancel",
                json={"reason": "bounded"},
            )
            assert denied_cancel.status_code == 404
            assert calls == [
                (TaskqAction.CONTROL, "workflow_allowed"),
                (TaskqAction.CONTROL, "workflow_denied"),
            ]
            assert (
                await pg.fetchval(
                    "SELECT cancel_requested_at FROM taskq.workflows WHERE id=$1",
                    workflow.workflow_id,
                )
                is None
            )
    finally:
        await transport.aclose()


async def test_workflow_page_authorizes_all_queues_before_cursor_and_has_sql_http_parity(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queues = ("workflow_page_a", "workflow_page_b")
    await _make_queues(operator, *queues)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    workflow = await transport.create_workflow(
        "workflow-page",
        "dag",
        declared_queues=queues,
        actor="setup",
    )
    members = [
        await transport.enqueue(
            EnqueueCommand(
                queue=queue,
                job_type="tests.workflow_page",
                payload={},
                workflow_id=workflow.workflow_id,
                step_key=f"step-{index}",
            )
        )
        for index, queue in enumerate(queues)
    ]
    calls: list[tuple[TaskqAction, str | None]] = []

    async def authenticate(_: Any) -> AuthContext:
        return AuthContext(actor="reader", principal="reader")

    async def deny_second(
        _: Any,
        __: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        calls.append((action, queue))
        if queue == queues[1]:
            raise HTTPException(status_code=403)

    denied_app = _mounted(
        create_taskq_app(
            _resources(transport, workflow_enabled=True, workflow_read_enabled=True),
            authorizer=callable_auth(authenticate, deny_second),
            not_found_on_forbidden=True,
        )
    )
    allowed_app = _mounted(
        create_taskq_app(
            _resources(transport, workflow_enabled=True, workflow_read_enabled=True),
            authorizer=no_auth_for_tests(),
        )
    )
    denied_raw = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=denied_app), base_url="http://test"
    )
    allowed_raw = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=allowed_app), base_url="http://test"
    )
    client = AsyncTaskqHttpClient("http://test", bearer_token="test", client=allowed_raw)
    try:
        denied = await denied_raw.get(
            f"/taskq/v1/workflows/{workflow.workflow_id}?cursor=not-base64!"
        )
        missing = await denied_raw.get(f"/taskq/v1/workflows/{uuid4()}?cursor=not-base64!")
        assert denied.status_code == missing.status_code == 404
        assert calls == [
            (TaskqAction.READ, queues[0]),
            (TaskqAction.READ, queues[1]),
        ]

        sql_page = await transport.get_workflow_page(workflow.workflow_id, limit=1)
        raw_page = await allowed_raw.get(f"/taskq/v1/workflows/{workflow.workflow_id}?limit=1")
        assert raw_page.status_code == 200
        wire = raw_page.json()["data"]
        assert set(wire) == {"as_of", "profile", "counts", "items", "next_cursor"}
        assert set(wire["profile"]) == {
            "workflow_id",
            "kind",
            "status",
            "sealed",
            "cancel_requested",
            "declared_queues",
            "created_at",
            "updated_at",
            "finished_at",
        }
        assert set(wire["counts"]) == {
            "blocked",
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        }
        assert set(wire["items"][0]) == {
            "job_id",
            "queue",
            "job_type",
            "step_key",
            "status",
            "outcome",
            "pending_deps",
            "attempt_count",
            "failure_count",
            "created_at",
            "scheduled_at",
            "started_at",
            "finished_at",
            "updated_at",
        }
        encoded_wire = json.dumps(wire)
        assert all(
            field not in encoded_wire
            for field in (
                "workflow_key",
                "params",
                "payload",
                "headers",
                "result",
                "progress",
                "error",
                "attempt_id",
                "fence",
                "worker_id",
            )
        )
        first = await client.get_workflow_page(workflow.workflow_id, limit=1)
        assert first.profile == sql_page.profile
        assert first.counts == sql_page.counts
        assert first.items == sql_page.items
        assert first.next_cursor is not None

        second = await client.get_workflow_page(
            workflow.workflow_id,
            limit=1,
            cursor=first.next_cursor,
        )
        projected_ids = [first.items[0].job_id, second.items[0].job_id]
        assert projected_ids == sorted(member.job_id for member in members)
        assert second.next_cursor is None
        raw_ids = await pg.fetch(
            "SELECT id FROM taskq.jobs WHERE workflow_id=$1 ORDER BY id",
            workflow.workflow_id,
        )
        assert projected_ids == [row["id"] for row in raw_ids]
    finally:
        await client.aclose()
        await denied_raw.aclose()
        await allowed_raw.aclose()
        await transport.aclose()


async def test_sql_http_and_fake_workflow_surfaces_have_state_parity(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queues = ("parity_sql_a", "parity_sql_b", "parity_http_a", "parity_http_b")
    await _make_queues(operator, *queues)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, workflow_enabled=True),
            authorizer=no_auth_for_tests(),
        )
    )
    raw = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    http = AsyncTaskqHttpClient("http://test", bearer_token="test", client=raw)
    fake = FakeTaskQClient(queues=("parity_fake_a", "parity_fake_b"))
    try:
        sql_workflow = await transport.create_workflow(
            "parity-sql",
            "dag",
            declared_queues=("parity_sql_a", "parity_sql_b"),
            actor="sql",
        )
        http_workflow = await http.create_workflow(
            "parity-http",
            "dag",
            declared_queues=("parity_http_a", "parity_http_b"),
            actor="ignored",
        )
        fake_workflow = await fake.create_workflow(
            "parity-fake",
            "dag",
            declared_queues=("parity_fake_a", "parity_fake_b"),
            actor="fake",
        )
        assert {
            sql_workflow.outcome,
            http_workflow.outcome,
            fake_workflow.outcome,
        } == {"created"}

        async def pair(client: Any, workflow_id: Any, first: str, second: str) -> tuple[Any, Any]:
            parent = await client.enqueue(
                EnqueueCommand(
                    queue=first,
                    job_type="tests.parent",
                    payload={"same": True},
                    workflow_id=workflow_id,
                    step_key="parent",
                )
            )
            child = await client.enqueue(
                EnqueueCommand(
                    queue=second,
                    job_type="tests.child",
                    payload={"same": True},
                    workflow_id=workflow_id,
                    step_key="child",
                    depends_on=(parent.job_id,),
                )
            )
            return parent, child

        sql_parent, sql_child = await pair(
            transport, sql_workflow.workflow_id, "parity_sql_a", "parity_sql_b"
        )
        http_parent, http_child = await pair(
            http, http_workflow.workflow_id, "parity_http_a", "parity_http_b"
        )
        fake_parent, fake_child = await pair(
            fake, fake_workflow.workflow_id, "parity_fake_a", "parity_fake_b"
        )
        assert {
            sql_parent.status.value,
            http_parent.status.value,
            fake_parent.status.value,
            sql_child.status.value,
            http_child.status.value,
            fake_child.status.value,
        } == {"created"}
        raw_statuses = await pg.fetch(
            "SELECT workflow_id, step_key, status FROM taskq.jobs "
            "WHERE workflow_id=ANY($1::uuid[]) ORDER BY workflow_id, step_key",
            [sql_workflow.workflow_id, http_workflow.workflow_id],
        )
        assert [row["status"] for row in raw_statuses] == [
            "blocked",
            "queued",
            "blocked",
            "queued",
        ]
        assert fake._jobs[fake_parent.job_id].status.value == "queued"
        assert fake._jobs[fake_child.job_id].status.value == "blocked"
    finally:
        await http.aclose()
        await raw.aclose()
        await transport.aclose()
