"""Protocol-1.0.12 schedule facade, client, authorization, and CQ-05 evidence."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
import httpx
import pytest

from taskq import TaskqConflictError
from taskq.http import (
    AsyncTaskqHttpClient,
    AuthContext,
    ClaimWaitHub,
    TaskqFacadeTransports,
    callable_auth,
    create_taskq_app,
    no_auth_for_tests,
)
from taskq.protocol import TaskqAction
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


def _resources(transport: SqlTaskqTransport, *, enabled: bool) -> TaskqFacadeTransports:
    return TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
        schedule_enabled=enabled,
    )


async def _queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'schedule-surface')",
        name,
    )


def _definition(queue: str, *, paused: bool = False) -> dict[str, Any]:
    return {
        "target": {
            "kind": "job",
            "queue": queue,
            "job_type": "tests.schedule",
            "payload": {"stable": True},
        },
        "recurrence": {"kind": "interval", "interval_seconds": 3600},
        "catchup_policy": "fire_once",
        "max_catchup": 1,
        "paused": paused,
    }


async def test_schedule_routes_are_absent_until_gate_and_openapi_is_exact(
    sqlalchemy_dsn: str,
) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        disabled = create_taskq_app(
            _resources(transport, enabled=False),
            authorizer=no_auth_for_tests(),
            operator_transport=transport,
            operator_authorizer=no_auth_for_tests(),
        )
        assert not any("/schedules/" in path for path in disabled.openapi()["paths"])

        enabled = create_taskq_app(
            _resources(transport, enabled=True),
            authorizer=no_auth_for_tests(),
            operator_transport=transport,
            operator_authorizer=no_auth_for_tests(),
        )
        paths = enabled.openapi()["paths"]
        assert {path for path in paths if "/schedules/" in path} == {"/v1/schedules/{name}"}
        route = paths["/v1/schedules/{name}"]
        assert set(route) >= {"get", "put", "delete"}
        request = route["put"]["requestBody"]["content"]["application/json"]["schema"]
        assert set(request["properties"]) == {
            "target",
            "recurrence",
            "catchup_policy",
            "max_catchup",
            "paused",
        }
        response = route["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "schedule_id" not in str(response)
        assert "maintenance" not in str(response)
    finally:
        await transport.aclose()


async def test_schedule_http_lifecycle_etags_clients_and_raw_state(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "schedule_surface"
    await _queue(operator, queue)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, enabled=True),
            authorizer=no_auth_for_tests(),
            operator_transport=transport,
            operator_authorizer=no_auth_for_tests(),
        )
    )
    raw = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client = AsyncTaskqHttpClient("http://test", bearer_token="test", client=raw)
    try:
        created = await client.put_schedule("surface.hourly", _definition(queue))
        assert created.outcome == "created"
        assert created.profile.version == 1
        assert created.profile.target.queue == queue
        assert created.profile.target.priority is None

        profile = await client.get_schedule("surface.hourly")
        assert profile == created.profile
        replay = await client.put_schedule("surface.hourly", _definition(queue))
        assert replay.outcome == "unchanged"

        updated = await client.put_schedule(
            "surface.hourly",
            _definition(queue, paused=True),
            expected_version=profile.version,
        )
        assert updated.outcome == "updated"
        assert updated.profile.state == "paused"
        assert updated.profile.version == 2

        with pytest.raises(TaskqConflictError) as stale:
            await client.put_schedule(
                "surface.hourly",
                _definition(queue),
                expected_version=1,
            )
        assert stale.value.details == {
            "reason": "schedule_version_conflict",
            "current_version": 2,
        }

        retired = await client.retire_schedule("surface.hourly", 2)
        assert retired.outcome == "retired"
        assert retired.profile.state == "retired"
        assert retired.profile.version == 3
        repeated = await client.retire_schedule("surface.hourly", retired.profile.version)
        assert repeated.outcome == "already_retired"
        row = await pg.fetchrow(
            "SELECT state, version, target->>'queue' AS queue "
            "FROM taskq.schedules WHERE name='surface.hourly'"
        )
        assert row is not None
        assert tuple(row.values()) == ("retired", 3, queue)
    finally:
        await client.aclose()
        await raw.aclose()
        await transport.aclose()


async def test_reserved_janitor_name_is_uniform_tq422_before_lookup_body_or_sql(
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, enabled=True),
            authorizer=no_auth_for_tests(),
            operator_transport=transport,
            operator_authorizer=no_auth_for_tests(),
        )
    )
    before = await pg.fetchrow(
        "SELECT count(*) AS schedules, "
        "(SELECT count(*) FROM taskq.jobs) AS jobs, "
        "(SELECT count(*) FROM taskq.job_events) AS events, "
        "max(updated_at) AS updated_at FROM taskq.schedules"
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = (
                await client.get("/taskq/v1/schedules/taskq-janitor-daily"),
                await client.put(
                    "/taskq/v1/schedules/taskq-janitor-daily",
                    content=b'{"malformed":',
                ),
                await client.delete("/taskq/v1/schedules/taskq-janitor-daily"),
            )
        for response in responses:
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "TQ422"
            assert response.json()["error"]["details"] == {"field": "name"}
        after = await pg.fetchrow(
            "SELECT count(*) AS schedules, "
            "(SELECT count(*) FROM taskq.jobs) AS jobs, "
            "(SELECT count(*) FROM taskq.job_events) AS events, "
            "max(updated_at) AS updated_at FROM taskq.schedules"
        )
        assert after == before
    finally:
        await transport.aclose()


async def test_existing_schedule_authorizes_old_then_new_queue_before_decode_or_write(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    await _queue(operator, "schedule_old")
    await _queue(operator, "schedule_new")
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    await transport.put_schedule("surface.auth", _definition("schedule_old"), "setup")
    calls: list[tuple[TaskqAction, str | None]] = []

    async def authenticate(_: Any) -> AuthContext:
        return AuthContext(actor="schedule-actor", principal="principal")

    async def authorize(
        _: Any,
        __: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        calls.append((action, queue))
        if queue == "schedule_new":
            raise HTTPException(status_code=403)

    auth = callable_auth(authenticate, authorize)
    app = _mounted(
        create_taskq_app(
            _resources(transport, enabled=True),
            authorizer=auth,
            operator_transport=transport,
            operator_authorizer=auth,
        )
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            malformed = await client.put(
                "/taskq/v1/schedules/surface.auth",
                content=b'{"target":',
            )
            assert malformed.status_code == 422
            assert calls == [(TaskqAction.CONTROL, "schedule_old")]

            calls.clear()
            denied = await client.put(
                "/taskq/v1/schedules/surface.auth",
                headers={"If-Match": '"taskq-schedule-1"'},
                json=_definition("schedule_new"),
            )
            assert denied.status_code == 403
            assert calls == [
                (TaskqAction.CONTROL, "schedule_old"),
                (TaskqAction.CONTROL, "schedule_new"),
            ]
        assert (
            await pg.fetchval(
                "SELECT target->>'queue' FROM taskq.schedules WHERE name='surface.auth'"
            )
            == "schedule_old"
        )
    finally:
        await transport.aclose()
