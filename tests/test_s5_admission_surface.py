"""S5-AR-02 typed SQL/HTTP admission parity, replay, and authorization vectors."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import asyncpg
from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError
import pytest

from taskq import TaskQ
from taskq.errors import (
    TaskqBackpressureError,
    TaskqConflictError,
    TaskqNotFoundError,
    TaskqUnavailableError,
)
from taskq.http import (
    AsyncTaskqHttpClient,
    AuthContext,
    ClaimWaitHub,
    TaskqFacadeTransports,
    callable_auth,
    create_taskq_app,
    no_auth_for_tests,
    static_api_key_auth,
)
from taskq.protocol import (
    AdmissionAdmittedResult,
    AdmissionCancelOutcome,
    AdmissionFinishOutcome,
    AdmissionFinishRequest,
    AdmissionJobCommand,
    AdmissionReserveOutcome,
    AdmissionReservedResult,
    EnqueueCommand,
    TaskqAction,
)
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql

_INTENT = "c" * 64


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


def _resources(transport: SqlTaskqTransport, *, admission_enabled: bool) -> TaskqFacadeTransports:
    return TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
        admission_enabled=admission_enabled,
    )


async def _make_queue(operator: asyncpg.Connection, queue: str) -> None:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'admission-surface')", queue
    )
    assert row is not None


class _DropFirstCommittedFinish(httpx.AsyncBaseTransport):
    """Discard one committed ASGI response, then let the official client replay."""

    def __init__(self, app: FastAPI) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.dropped = False
        self.finish_bodies: list[bytes] = []
        self.finish_request_ids: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/admissions/finish"):
            body = await request.aread()
            self.finish_bodies.append(body)
            self.finish_request_ids.append(request.headers["Taskq-Request-Id"])
        response = await self.inner.handle_async_request(request)
        if request.url.path.endswith("/admissions/finish") and not self.dropped:
            self.dropped = True
            await response.aread()
            await response.aclose()
            raise httpx.ReadError("simulated committed-response loss", request=request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def test_finish_job_model_excludes_competing_authority_and_hides_handle() -> None:
    handle = uuid4()
    request = AdmissionFinishRequest(
        idempotency_key="stable",
        handle=handle,
        job={"job_type": "test.admission", "payload": {"value": 1}},
        receipt={"external_id": "safe"},
    )
    assert str(handle) not in repr(request)
    for forbidden in ("idempotency_key", "queue", "workflow_id", "depends_on", "parent_id"):
        with pytest.raises(ValidationError):
            AdmissionJobCommand.model_validate(
                {"job_type": "test.admission", "payload": {}, forbidden: "forbidden"}
            )
    with pytest.raises(ValidationError):
        AdmissionFinishRequest(
            idempotency_key="stable",
            handle=handle,
            job={"job_type": "test.admission", "payload": {}},
            receipt={"too_large": "x" * 2048},
        )


async def test_direct_sql_transport_returns_typed_durable_results(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "admission_sql_transport"
    await _make_queue(operator, queue)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        reserved = await transport.reserve_admission(queue, "sql-key", _INTENT)
        assert isinstance(reserved, AdmissionReservedResult)
        assert reserved.outcome is AdmissionReserveOutcome.RESERVED
        finished = await transport.finish_admission(
            queue,
            "sql-key",
            reserved.handle,
            {"job_type": "test.sql", "payload": {"planned": 1}},
            {"plan_id": "sql-plan"},
        )
        assert finished.outcome is AdmissionFinishOutcome.CREATED
        admitted = await transport.reserve_admission(queue, "sql-key", _INTENT)
        assert isinstance(admitted, AdmissionAdmittedResult)
        assert admitted.job_id == finished.job_id
        assert admitted.receipt == {"plan_id": "sql-plan"}
        row = await pg.fetchrow(
            "SELECT a.state, a.job_id, j.admission_id = a.id AS linked "
            "FROM taskq.admissions a JOIN taskq.jobs j ON j.id=a.job_id "
            "WHERE a.queue=$1 AND a.idempotency_key=$2",
            queue,
            "sql-key",
        )
        assert row is not None and dict(row) == {
            "state": "admitted",
            "job_id": finished.job_id,
            "linked": True,
        }
        facade = TaskQ(transport)
        facade_reserved = await facade.reserve_admission(queue, "facade-key", "f" * 64)
        assert isinstance(facade_reserved, AdmissionReservedResult)
        facade_cancelled = await facade.cancel_admission(
            queue, "facade-key", facade_reserved.handle
        )
        assert facade_cancelled.outcome.value == "cancelled"
    finally:
        await transport.aclose()


async def test_http_finish_response_loss_replays_same_handle_and_one_job(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "admission_http_replay"
    key = "http-key"
    handle = uuid4()
    await _make_queue(operator, queue)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, admission_enabled=True), authorizer=no_auth_for_tests()
        )
    )
    dropping = _DropFirstCommittedFinish(app)
    raw_client = httpx.AsyncClient(transport=dropping, base_url="http://test")
    client = AsyncTaskqHttpClient(
        "http://test", bearer_token="test-only", client=raw_client, max_retries=1
    )
    try:
        reserved = await client.reserve_admission(queue, key, _INTENT, handle=handle)
        assert isinstance(reserved, AdmissionReservedResult)
        assert reserved.handle == handle

        finished = await client.finish_admission(
            queue,
            key,
            handle,
            {"job_type": "test.http", "payload": {"planned": [1, 2]}},
            {"plan_id": "http-plan", "count": 2},
        )
        assert finished.outcome is AdmissionFinishOutcome.EXISTED
        assert finished.receipt == {"plan_id": "http-plan", "count": 2}
        assert dropping.dropped
        assert len(dropping.finish_bodies) == 2
        assert dropping.finish_bodies[0] == dropping.finish_bodies[1]
        assert len(set(dropping.finish_request_ids)) == 2
        sent = json.loads(dropping.finish_bodies[0])
        assert sent["handle"] == str(handle)

        admitted = await client.reserve_admission(queue, key, _INTENT)
        assert isinstance(admitted, AdmissionAdmittedResult)
        assert admitted.job_id == finished.job_id
        assert admitted.receipt == finished.receipt
        with pytest.raises(TaskqConflictError) as mismatch:
            await client.reserve_admission(queue, key, "d" * 64)
        assert mismatch.value.details == {"reason": "idempotency_mismatch"}
        assert key not in str(mismatch.value)
        assert _INTENT not in str(mismatch.value)
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions WHERE queue=$1", queue) == 1
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE queue=$1", queue) == 1
    finally:
        await client.aclose()
        await raw_client.aclose()
        await transport.aclose()


async def test_sql_and_http_admission_surfaces_have_typed_state_parity(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    sql_queue = "admission_parity_sql"
    http_queue = "admission_parity_http"
    for queue in (sql_queue, http_queue):
        await _make_queue(operator, queue)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, admission_enabled=True), authorizer=no_auth_for_tests()
        )
    )
    raw_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client = AsyncTaskqHttpClient("http://test", bearer_token="test-only", client=raw_client)
    try:
        job = {"job_type": "test.parity", "payload": {"stable": [1, 2, 3]}}
        receipt = {"planner": "v1", "count": 3}
        handles = (uuid4(), uuid4())
        sql_reserved = await transport.reserve_admission(
            sql_queue, "parity-key", _INTENT, handle=handles[0]
        )
        http_reserved = await client.reserve_admission(
            http_queue, "parity-key", _INTENT, handle=handles[1]
        )
        assert sql_reserved.outcome == http_reserved.outcome == AdmissionReserveOutcome.RESERVED

        sql_finished = await transport.finish_admission(
            sql_queue, "parity-key", handles[0], job, receipt
        )
        http_finished = await client.finish_admission(
            http_queue, "parity-key", handles[1], job, receipt
        )
        assert sql_finished.outcome == http_finished.outcome == AdmissionFinishOutcome.CREATED
        assert sql_finished.receipt == http_finished.receipt == receipt

        sql_admitted = await transport.reserve_admission(
            sql_queue, "parity-key", _INTENT, handle=uuid4()
        )
        http_admitted = await client.reserve_admission(
            http_queue, "parity-key", _INTENT, handle=uuid4()
        )
        assert isinstance(sql_admitted, AdmissionAdmittedResult)
        assert isinstance(http_admitted, AdmissionAdmittedResult)
        assert sql_admitted.receipt == http_admitted.receipt == receipt
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM taskq.admissions WHERE queue=ANY($1::text[])",
                [sql_queue, http_queue],
            )
            == 2
        )
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM taskq.jobs WHERE queue=ANY($1::text[])",
                [sql_queue, http_queue],
            )
            == 2
        )

        reserved_for_cancel = await client.reserve_admission(
            http_queue, "cancel-key", "e" * 64, handle=handles[1]
        )
        assert isinstance(reserved_for_cancel, AdmissionReservedResult)
        first_cancel = await client.cancel_admission(http_queue, "cancel-key", handles[1])
        second_cancel = await client.cancel_admission(http_queue, "cancel-key", handles[1])
        assert first_cancel.outcome.value == "cancelled"
        assert second_cancel.outcome.value == "already_cancelled"
    finally:
        await client.aclose()
        await raw_client.aclose()
        await transport.aclose()


async def test_http_outcome_and_safe_error_matrix(
    pg: asyncpg.Connection,
    stateful_time_travel: None,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "admission_http_matrix"
    await _make_queue(operator, queue)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    app = _mounted(
        create_taskq_app(
            _resources(transport, admission_enabled=True), authorizer=no_auth_for_tests()
        )
    )
    raw_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client = AsyncTaskqHttpClient("http://test", bearer_token="test-only", client=raw_client)
    owner_handle = uuid4()
    contender_handle = uuid4()
    try:
        with pytest.raises(TaskqNotFoundError):
            await client.reserve_admission("missing_queue", "key", _INTENT)

        reserved = await client.reserve_admission(queue, "matrix", _INTENT, handle=owner_handle)
        assert reserved.outcome is AdmissionReserveOutcome.RESERVED
        pending = await client.reserve_admission(queue, "matrix", _INTENT, handle=contender_handle)
        assert pending.outcome is AdmissionReserveOutcome.PENDING

        with pytest.raises(TaskqConflictError) as wrong_handle:
            await client.cancel_admission(queue, "matrix", contender_handle)
        assert wrong_handle.value.details == {"reason": "reservation_conflict"}

        cancelled = await client.cancel_admission(queue, "matrix", owner_handle)
        cancelled_replay = await client.cancel_admission(queue, "matrix", owner_handle)
        assert cancelled.outcome is AdmissionCancelOutcome.CANCELLED
        assert cancelled_replay.outcome is AdmissionCancelOutcome.ALREADY_CANCELLED
        with pytest.raises(TaskqConflictError) as cancelled_finish:
            await client.finish_admission(
                queue,
                "matrix",
                owner_handle,
                {"job_type": "test.cancelled", "payload": {}},
            )
        assert cancelled_finish.value.details == {"reason": "reservation_cancelled"}

        current = await client.reserve_admission(queue, "matrix", "d" * 64, handle=contender_handle)
        assert current.outcome is AdmissionReserveOutcome.RESERVED
        created = await client.finish_admission(
            queue,
            "matrix",
            contender_handle,
            {"job_type": "test.matrix", "payload": {"version": 1}},
            {"plan": "stable"},
        )
        with pytest.raises(TaskqConflictError) as changed_finish:
            await client.finish_admission(
                queue,
                "matrix",
                contender_handle,
                {"job_type": "test.matrix", "payload": {"version": 2}},
                {"plan": "stable"},
            )
        assert changed_finish.value.details == {"reason": "finish_mismatch"}

        with pytest.raises(TaskqConflictError) as changed_intent:
            await client.reserve_admission(queue, "matrix", "e" * 64)
        assert changed_intent.value.details == {"reason": "idempotency_mismatch"}

        admitted_cancel = await client.cancel_admission(queue, "matrix", contender_handle)
        assert admitted_cancel.outcome is AdmissionCancelOutcome.ALREADY_ADMITTED
        assert admitted_cancel.job_id == created.job_id
        assert admitted_cancel.receipt == {"plan": "stable"}

        with pytest.raises(TaskqNotFoundError):
            await client.finish_admission(
                queue,
                "missing-admission",
                uuid4(),
                {"job_type": "test.missing", "payload": {}},
            )

        expired = await client.reserve_admission(
            queue,
            "expired",
            "f" * 64,
            handle=owner_handle,
            reservation_ttl_seconds=15,
        )
        assert expired.outcome is AdmissionReserveOutcome.RESERVED
        assert await pg.fetchval(
            "SELECT taskq_test.rewind_admission($1,$2,interval '1 hour')",
            queue,
            "expired",
        )
        expired_cancel = await client.cancel_admission(queue, "expired", owner_handle)
        assert expired_cancel.outcome is AdmissionCancelOutcome.EXPIRED

        expired_finish = await client.reserve_admission(
            queue,
            "expired-finish",
            "1" * 64,
            handle=owner_handle,
            reservation_ttl_seconds=15,
        )
        assert expired_finish.outcome is AdmissionReserveOutcome.RESERVED
        assert await pg.fetchval(
            "SELECT taskq_test.rewind_admission($1,$2,interval '1 hour')",
            queue,
            "expired-finish",
        )
        with pytest.raises(TaskqConflictError) as expired_error:
            await client.finish_admission(
                queue,
                "expired-finish",
                owner_handle,
                {"job_type": "test.expired", "payload": {}},
            )
        assert expired_error.value.details == {"reason": "reservation_expired"}

        cross_writer = await client.reserve_admission(
            queue, "cross-writer", "2" * 64, handle=owner_handle
        )
        assert cross_writer.outcome is AdmissionReserveOutcome.RESERVED
        created_by_client = await client.finish_admission(
            queue,
            "cross-writer",
            owner_handle,
            {"job_type": "test.cross-writer", "payload": {}},
            {"style": "omitted"},
        )
        assert created_by_client.outcome is AdmissionFinishOutcome.CREATED
        explicit_null_job = {
            "job_type": "test.cross-writer",
            "payload": {},
            "priority": None,
        }
        with pytest.raises(asyncpg.PostgresError) as null_style_mismatch:
            await producer.fetchrow(
                "SELECT * FROM taskq.finish_admission($1,$2,$3,$4::jsonb,$5::jsonb)",
                queue,
                "cross-writer",
                owner_handle,
                json.dumps(explicit_null_job),
                json.dumps({"style": "omitted"}),
            )
        assert null_style_mismatch.value.sqlstate == "TQ409"
        assert json.loads(null_style_mismatch.value.detail) == {"reason": "finish_mismatch"}
    finally:
        await client.aclose()
        await raw_client.aclose()
        await transport.aclose()


async def test_finish_backpressure_rolls_back_without_partial_job_or_receipt(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "admission_backpressure"
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{\"max_depth\":1}'::jsonb, 'admission')",
        queue,
    )
    assert row is not None
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        await transport.enqueue(EnqueueCommand(queue=queue, job_type="test.capacity", payload={}))
        reserved = await transport.reserve_admission(queue, "blocked", _INTENT)
        assert isinstance(reserved, AdmissionReservedResult)
        with pytest.raises(TaskqBackpressureError):
            await transport.finish_admission(
                queue,
                "blocked",
                reserved.handle,
                {"job_type": "test.blocked", "payload": {}},
                {"must_not_commit": True},
            )
        admission = await pg.fetchrow(
            "SELECT state, job_id, finish_hash, receipt FROM taskq.admissions "
            "WHERE queue=$1 AND idempotency_key='blocked'",
            queue,
        )
        assert admission is not None and dict(admission) == {
            "state": "reserved",
            "job_id": None,
            "finish_hash": None,
            "receipt": None,
        }
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs WHERE queue=$1", queue) == 1
    finally:
        await transport.aclose()


async def test_admission_routes_are_mount_gated_and_authorize_before_body_decode(
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    disabled = create_taskq_app(
        _resources(transport, admission_enabled=False), authorizer=no_auth_for_tests()
    )
    enabled_subapp = create_taskq_app(
        _resources(transport, admission_enabled=True),
        authorizer=static_api_key_auth("correct"),
    )
    enabled = _mounted(enabled_subapp)
    try:
        assert not any("admissions" in path for path in disabled.openapi()["paths"])
        assert {
            "/v1/queues/{queue}/admissions/reserve",
            "/v1/queues/{queue}/admissions/finish",
            "/v1/queues/{queue}/admissions/cancel",
        } <= set(enabled_subapp.openapi()["paths"])
        openapi = enabled_subapp.openapi()
        for suffix in ("reserve", "finish", "cancel"):
            request_schema = openapi["paths"][f"/v1/queues/{{queue}}/admissions/{suffix}"]["post"][
                "requestBody"
            ]["content"]["application/json"]["schema"]
            assert request_schema["properties"]["handle"]["writeOnly"] is True
        cancel_response = openapi["paths"]["/v1/queues/{queue}/admissions/cancel"]["post"][
            "responses"
        ]["200"]["content"]["application/json"]["schema"]
        assert set(cancel_response["properties"]["data"]["properties"]) == {
            "job_id",
            "receipt",
            "receipt_expires_at",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=enabled), base_url="http://test"
        ) as client:
            response = await client.post(
                "/taskq/v1/queues/private/admissions/reserve",
                headers={
                    "X-API-Key": "wrong",
                    "Taskq-Request-Id": "invalid value",
                },
                content=b'{"handle":"sensitive-bad-body",',
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH401"
        assert "sensitive-bad-body" not in response.text
        assert "invalid value" not in response.text
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 0
    finally:
        await transport.aclose()


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(429, TaskqBackpressureError), (503, TaskqUnavailableError)],
)
async def test_admission_auth_dependency_failure_is_typed_and_fail_closed(
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
    status: int,
    error_type: type[Exception],
) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)

    async def authenticate(request: Any) -> AuthContext:
        raise HTTPException(
            status_code=status,
            detail={"reason": "auth_dependency", "credential": "must-not-leak"},
            headers={"Retry-After": "2"},
        )

    app = _mounted(
        create_taskq_app(
            _resources(transport, admission_enabled=True),
            authorizer=callable_auth(authenticate),
        )
    )
    raw_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client = AsyncTaskqHttpClient(
        "http://test", bearer_token="test-only", client=raw_client, max_retries=0
    )
    try:
        with pytest.raises(error_type):
            await client.reserve_admission("private", "key", _INTENT)
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 0
    finally:
        await client.aclose()
        await raw_client.aclose()
        await transport.aclose()


async def test_queue_scoped_authorization_denial_never_calls_admission_sql(
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    observed: list[tuple[TaskqAction, str | None]] = []

    async def authenticate(request: Any) -> AuthContext:
        return AuthContext(actor="denied", principal="denied")

    async def deny(
        request: Any,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        observed.append((action, queue))
        raise HTTPException(status_code=403)

    app = _mounted(
        create_taskq_app(
            _resources(transport, admission_enabled=True),
            authorizer=callable_auth(authenticate, deny),
        )
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/taskq/v1/queues/secret/admissions/reserve",
                headers={"Taskq-Protocol-Version": "1"},
                content=b'{"handle":"not-even-decoded",',
            )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH403"
        assert "not-even-decoded" not in response.text
        assert observed == [(TaskqAction.ENQUEUE, "secret")]
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 0
    finally:
        await transport.aclose()
