"""S3-02 mounted-facade authorization, envelope, and pool-split vectors."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from taskq.errors import TaskqCapabilityError
from taskq.http import (
    AuthContext,
    ClaimWaitHub,
    TaskqFacadeTransports,
    bearer_token_auth,
    callable_auth,
    create_taskq_app,
    legacy_taskq_auth,
    merge_taskq_openapi,
    no_auth_for_tests,
    static_api_key_auth,
)
from taskq.protocol import (
    AuthorizationProjection,
    ClaimResult,
    ClaimState,
    ClaimedJob,
    ConfigChangeOutcome,
    ContractMeta,
    EnqueueCreatedResult,
    EnsureQueueResult,
    JobPage,
    JobStatus,
    Metric,
    QueueProfile,
    SettleOkResult,
    TaskqAction,
)


class FacadeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.claims: list[ClaimResult] = []
        self.projections: dict[UUID, AuthorizationProjection] = {}
        self.shutdown_requested = False
        self.inactive_views: set[str] = set()

    async def enqueue(self, command: Any) -> EnqueueCreatedResult:
        self.calls.append(("enqueue", command))
        return EnqueueCreatedResult(
            job_id=uuid4(),
            queue=command.queue,
            job_type=command.job_type,
            created=True,
            scheduled_at=command.scheduled_at,
            idempotency_key=command.idempotency_key,
        )

    async def enqueue_many(self, queue: str, items: Any) -> list[EnqueueCreatedResult]:
        self.calls.append(("enqueue_many", (queue, items)))
        return [
            EnqueueCreatedResult(
                job_id=uuid4(),
                queue=queue,
                job_type=item.job_type,
                created=True,
            )
            for item in items
        ]

    async def claim(self, queue: str, worker_id: str, **kwargs: Any) -> ClaimResult:
        self.calls.append(("claim", (queue, worker_id, kwargs)))
        if self.claims:
            return self.claims.pop(0)
        return ClaimResult(state=ClaimState.EMPTY)

    async def heartbeat(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not expected")

    async def complete(self, *args: Any, **kwargs: Any) -> SettleOkResult:
        self.calls.append(("complete", (args, kwargs)))
        return SettleOkResult(result="ok", job_status=JobStatus.SUCCEEDED, scheduled_at=None)

    async def fail(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not expected")

    async def release(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not expected")

    async def snooze(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not expected")

    async def cancel_running(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not expected")

    async def worker_heartbeat(self, worker_id: str, queues: Any, **kwargs: Any) -> bool:
        self.calls.append(("worker_heartbeat", (worker_id, tuple(queues), kwargs)))
        return self.shutdown_requested

    async def get_authorization_projection(self, job_id: UUID) -> AuthorizationProjection | None:
        self.calls.append(("projection", job_id))
        return self.projections.get(job_id)

    async def get_job(self, job_id: UUID, **kwargs: Any) -> None:
        self.calls.append(("get_job", (job_id, kwargs)))
        return None

    async def get_queue_stats(self, queue: str | None = None) -> list[Any]:
        self.calls.append(("stats", queue))
        return []

    async def get_queue_profile(self, queue: str) -> QueueProfile | None:
        self.calls.append(("profile", queue))
        return _profile(queue)

    async def list_jobs(self, queue: str, view: str, **kwargs: Any) -> JobPage:
        self.calls.append(("list_jobs", (queue, view, kwargs)))
        if view in self.inactive_views:
            raise TaskqCapabilityError()
        return JobPage(as_of=datetime.now(UTC), items=())

    async def get_contract_meta(self) -> ContractMeta:
        self.calls.append(("meta", None))
        return ContractMeta(contract_version="0.1.2", capabilities={})

    async def metrics(self) -> list[Metric]:
        self.calls.append(("metrics", None))
        return [Metric(name="taskq_ready", labels={"mode": "test"}, value=1)]

    async def aclose(self) -> None:
        raise AssertionError("facade cannot close borrowed transports")


class OperatorFake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.missing_profiles: set[str] = set()

    async def ensure_queue(
        self, name: str, profile: dict[str, Any] | None = None, actor: str | None = None
    ) -> EnsureQueueResult:
        self.calls.append(("ensure_queue", (name, profile, actor)))
        return EnsureQueueResult(result=ConfigChangeOutcome.CREATED, profile=_profile(name).model_dump())

    async def update_queue_profile(
        self, name: str, profile: dict[str, Any], actor: str, expected_version: int
    ) -> tuple[str, QueueProfile | None, int | None]:
        self.calls.append(("update_queue_profile", (name, profile, actor, expected_version)))
        if name in self.missing_profiles:
            return "missing", None, None
        updated = _profile(name).model_copy(update={"profile_version": expected_version + 1})
        return "updated", updated, updated.profile_version


def _profile(name: str) -> QueueProfile:
    return QueueProfile(
        name=name, profile_version=1, default_priority=0, default_lease_seconds=60,
        default_max_attempts=1, default_backoff_mode="fixed", default_backoff_base=1,
        default_backoff_cap=1, retention_hours=24, failed_retention_hours=168,
        max_depth=1000, notify_enabled=True, paused=False,
    )


def _resources(
    transport: FacadeTransport, hub: ClaimWaitHub | None = None
) -> TaskqFacadeTransports:
    return TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=hub or ClaimWaitHub(),
    )


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


def _headers(**extra: str) -> dict[str, str]:
    return {"Taskq-Protocol-Version": "1", **extra}


async def test_bad_credentials_win_over_invalid_request_id_and_body() -> None:
    transport = FacadeTransport()
    app = _mounted(
        create_taskq_app(_resources(transport), authorizer=static_api_key_auth("correct"))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/taskq/v1/queues/emails/jobs",
            headers={
                "X-API-Key": "wrong",
                "Taskq-Request-Id": "invalid value with spaces",
            },
            content=b'{"attempt_id":"live-fence","payload":',
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH401"
    assert response.headers["Taskq-Request-Id"] == response.json()["request_id"]
    assert "invalid value" not in response.text
    assert "live-fence" not in response.text
    assert not transport.calls


@pytest.mark.parametrize(
    ("status", "reason", "code"),
    [
        (429, "auth_rate_limited", "TQ429"),
        (503, "auth_infrastructure_unavailable", "TQ503"),
    ],
)
async def test_auth_dependency_failures_have_typed_retryable_envelopes(
    status: int, reason: str, code: str
) -> None:
    transport = FacadeTransport()

    async def authenticate(request: Any) -> AuthContext:
        raise HTTPException(
            status_code=status,
            detail={"reason": reason, "secret": "must-not-escape"},
            headers={"Retry-After": "7"},
        )

    app = _mounted(create_taskq_app(_resources(transport), authorizer=callable_auth(authenticate)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/taskq/v1/meta", headers=_headers())
    body = response.json()
    assert response.status_code == status
    assert response.headers["Retry-After"] == "7"
    assert body["error"] == {
        "code": code,
        "message": (
            "authorization rate limited"
            if status == 429
            else "authorization dependency unavailable"
        ),
        "retryable": True,
        "details": {"reason": reason},
    }
    assert "must-not-escape" not in response.text


async def test_enqueue_authorizes_path_and_returns_exact_manifest_backed_data() -> None:
    transport = FacadeTransport()
    authorized: list[tuple[TaskqAction, str | None]] = []

    async def authenticate(request: Any) -> AuthContext:
        return AuthContext(actor="producer", principal={"id": 1})

    async def authorize(
        request: Any,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        authorized.append((action, queue))

    app = _mounted(
        create_taskq_app(_resources(transport), authorizer=callable_auth(authenticate, authorize))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/taskq/v1/queues/emails/jobs",
            headers=_headers(**{"Taskq-Request-Id": "enqueue-1"}),
            json={"job_type": "mail.send", "payload": {"to": "safe@example.test"}},
        )
    assert response.status_code == 201
    assert response.json()["outcome"] == "created"
    assert set(response.json()["data"]) == {"job_id"}
    assert authorized == [(TaskqAction.ENQUEUE, "emails")]
    assert transport.calls[0][0] == "enqueue"


async def test_job_lookup_authorizes_authoritative_queue_and_hides_denial() -> None:
    job_id = uuid4()
    transport = FacadeTransport()
    transport.projections[job_id] = AuthorizationProjection(
        job_id=job_id, queue="secret", job_type="tests.echo", status=JobStatus.RUNNING
    )

    async def authenticate(request: Any) -> AuthContext:
        return AuthContext(actor="worker", principal="worker")

    async def deny(
        request: Any,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        assert queue == "secret"
        raise HTTPException(status_code=403)

    app = _mounted(
        create_taskq_app(
            _resources(transport),
            authorizer=callable_auth(authenticate, deny),
            not_found_on_forbidden=True,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        hidden = await client.post(
            f"/taskq/v1/jobs/{job_id}/complete",
            headers=_headers(**{"Taskq-Request-Id": "hidden"}),
            json={"attempt_id": str(uuid4()), "worker_id": "worker-1"},
        )
        missing = await client.post(
            f"/taskq/v1/jobs/{uuid4()}/complete",
            headers=_headers(**{"Taskq-Request-Id": "missing"}),
            json={"attempt_id": str(uuid4()), "worker_id": "worker-1"},
        )
    assert hidden.status_code == missing.status_code == 404
    hidden_body = hidden.json()
    missing_body = missing.json()
    hidden_body.pop("request_id")
    missing_body.pop("request_id")
    assert hidden_body == missing_body
    assert all(name != "complete" for name, _ in transport.calls)


async def test_worker_presence_preflights_every_queue_before_one_call() -> None:
    transport = FacadeTransport()
    checked: list[str | None] = []

    async def authenticate(request: Any) -> AuthContext:
        return AuthContext(actor="fleet", principal="fleet")

    async def authorize(
        request: Any,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        checked.append(queue)
        if queue == "tools":
            raise HTTPException(status_code=403)

    app = _mounted(
        create_taskq_app(_resources(transport), authorizer=callable_auth(authenticate, authorize))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post(
            "/taskq/v1/workers/heartbeat",
            headers=_headers(),
            json={"worker_id": "worker-1", "queues": ["emails", "tools"]},
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"queue": "tools"}
    assert checked == ["emails", "tools"]
    assert all(name != "worker_heartbeat" for name, _ in transport.calls)


async def test_worker_presence_returns_both_typed_outcomes_from_one_presence_call() -> None:
    transport = FacadeTransport()
    app = _mounted(create_taskq_app(_resources(transport), authorizer=no_auth_for_tests()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        continued = await client.post(
            "/taskq/v1/workers/heartbeat",
            headers=_headers(),
            json={"worker_id": "worker-1", "queues": ["emails"]},
        )
        transport.shutdown_requested = True
        shutdown = await client.post(
            "/taskq/v1/workers/heartbeat",
            headers=_headers(),
            json={"worker_id": "worker-1", "queues": ["emails"]},
        )
    assert continued.json()["outcome"] == "continue"
    assert shutdown.json()["outcome"] == "shutdown_requested"
    assert [name for name, _ in transport.calls] == [
        "worker_heartbeat",
        "worker_heartbeat",
    ]


async def test_operator_routes_require_both_configs_and_never_fallback() -> None:
    transport = FacadeTransport()
    with pytest.raises(ValueError, match="configured together"):
        create_taskq_app(
            _resources(transport),
            authorizer=no_auth_for_tests(),
            operator_transport=OperatorFake(),  # type: ignore[arg-type]
        )

    app = _mounted(create_taskq_app(_resources(transport), authorizer=no_auth_for_tests()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        absent = await client.put(
            "/taskq/v1/queues/emails", headers=_headers(), json={"profile": {}}
        )
    assert absent.status_code == 422
    assert absent.json()["error"]["code"] == "TQ422"

    operator = OperatorFake()
    configured = _mounted(
        create_taskq_app(
            _resources(transport),
            authorizer=no_auth_for_tests(),
            operator_transport=operator,  # type: ignore[arg-type]
            operator_authorizer=no_auth_for_tests(),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=configured), base_url="http://test"
    ) as client:
        created = await client.put(
            "/taskq/v1/queues/emails",
            headers=_headers(),
            json={"profile": {"default_priority": 5}},
        )
    assert created.status_code == 201
    assert operator.calls == [("ensure_queue", ("emails", {"default_priority": 5}, "test"))]


async def test_queue_profile_get_and_conditional_put_use_etag_envelope() -> None:
    transport = FacadeTransport()
    operator = OperatorFake()
    app = _mounted(
        create_taskq_app(
            _resources(transport),
            authorizer=no_auth_for_tests(),
            operator_transport=operator,  # type: ignore[arg-type]
            operator_authorizer=no_auth_for_tests(),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        got = await client.get("/taskq/v1/queues/emails", headers=_headers())
        updated = await client.put(
            "/taskq/v1/queues/emails",
            headers=_headers(**{"If-Match": '"taskq-profile-1"'}),
            json={"profile": {"default_priority": 5}},
        )
        malformed = await client.put(
            "/taskq/v1/queues/emails",
            headers=_headers(**{"If-Match": "*"}),
            json={"profile": {}},
        )
    assert got.headers["etag"] == '"taskq-profile-1"'
    assert set(got.json()["data"]) == set(_profile("emails").model_dump(mode="json"))
    assert updated.headers["etag"] == '"taskq-profile-2"'
    assert updated.json()["data"]["profile"]["profile_version"] == 2
    assert malformed.status_code == 422
    assert operator.calls[-1] == (
        "update_queue_profile",
        ("emails", {"default_priority": 5}, "test", 1),
    )


async def test_conditional_queue_update_missing_and_inactive_view_are_typed() -> None:
    transport = FacadeTransport()
    transport.inactive_views.add("running")
    operator = OperatorFake()
    operator.missing_profiles.add("missing")
    app = _mounted(
        create_taskq_app(
            _resources(transport),
            authorizer=no_auth_for_tests(),
            operator_transport=operator,  # type: ignore[arg-type]
            operator_authorizer=no_auth_for_tests(),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.put(
            "/taskq/v1/queues/missing",
            headers=_headers(**{"If-Match": '"taskq-profile-1"'}),
            json={"profile": {}},
        )
        inactive = await client.get(
            "/taskq/v1/jobs?queue=emails&view=running", headers=_headers()
        )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TQ001"
    assert inactive.status_code == 501
    assert inactive.json()["error"]["details"] == {
        "reason": "read_model_view_inactive",
        "view": "running",
    }


async def test_bearer_and_legacy_adapters_preserve_simple_mounts() -> None:
    transport = FacadeTransport()
    bearer_app = _mounted(
        create_taskq_app(_resources(transport), authorizer=bearer_token_auth("fleet-token"))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bearer_app), base_url="http://test"
    ) as client:
        denied = await client.get("/taskq/v1/meta", headers=_headers())
        allowed = await client.get(
            "/taskq/v1/meta",
            headers=_headers(**{"Authorization": "Bearer fleet-token"}),
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200

    families: list[str] = []

    async def authenticate(request: Any) -> AuthContext:
        return AuthContext(actor="legacy", principal="legacy")

    def check(family: str) -> Any:
        async def checker(*args: Any) -> None:
            families.append(family)

        return checker

    legacy = legacy_taskq_auth(
        authenticate, read=check("read"), write=check("write"), operator=check("operator")
    )
    operator = OperatorFake()
    legacy_app = _mounted(
        create_taskq_app(
            _resources(transport),
            authorizer=legacy,
            operator_transport=operator,  # type: ignore[arg-type]
            operator_authorizer=legacy,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=legacy_app), base_url="http://test"
    ) as client:
        await client.get("/taskq/v1/meta", headers=_headers())
        await client.post(
            "/taskq/v1/queues/emails/jobs",
            headers=_headers(),
            json={"job_type": "tests.echo", "payload": {}},
        )
        await client.put("/taskq/v1/queues/emails", headers=_headers(), json={"profile": {}})
    assert families == ["read", "write", "operator"]


async def test_hidden_and_gated_routes_return_typed_capability_envelopes() -> None:
    app = _mounted(create_taskq_app(_resources(FacadeTransport()), authorizer=no_auth_for_tests()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = [
            await client.get("/taskq/v1/queues/emails", headers=_headers()),
            await client.get("/taskq/v1/jobs", headers=_headers()),
            await client.get("/taskq/v1/workers", headers=_headers()),
        ]
    assert responses[0].status_code == 200
    assert responses[1].status_code == 422
    assert responses[2].status_code == 501
    assert responses[1].json()["error"]["code"] == "TQ422"
    assert responses[2].json()["error"]["code"] == "TQ501"


async def test_unknown_wrong_method_validation_and_metrics_are_owned() -> None:
    transport = FacadeTransport()
    app = _mounted(create_taskq_app(_resources(transport), authorizer=no_auth_for_tests()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unknown = await client.get("/taskq/v1/does-not-exist", headers=_headers())
        wrong = await client.get("/taskq/v1/queues/emails/jobs", headers=_headers())
        invalid = await client.post(
            "/taskq/v1/jobs/not-a-uuid/complete",
            headers=_headers(),
            json={"attempt_id": str(uuid4()), "worker_id": "worker-1"},
        )
        metrics = await client.get("/taskq/metrics", headers=_headers())
    assert unknown.json()["error"]["code"] == "TQ001"
    assert wrong.json()["error"]["code"] == "TQ422"
    assert invalid.json()["error"]["code"] == "TQ422"
    assert "attempt_id" not in invalid.text
    assert metrics.text == 'taskq_ready{mode="test"} 1.0\n'
    assert metrics.headers["Taskq-Protocol-Version"] == "1"


async def test_raising_authorizer_is_an_opaque_tq500() -> None:
    async def explode(request: Any) -> AuthContext:
        raise RuntimeError("secret-token live-fence SQL SELECT")

    app = _mounted(
        create_taskq_app(_resources(FacadeTransport()), authorizer=callable_auth(explode))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/taskq/v1/meta", headers=_headers())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "TQ500"
    assert "secret-token" not in response.text
    assert "live-fence" not in response.text


async def test_stats_empty_is_typed_ok_for_snapshot_lag_or_unknown_queue() -> None:
    app = _mounted(create_taskq_app(_resources(FacadeTransport()), authorizer=no_auth_for_tests()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/taskq/v1/stats/queues/not-yet-snapshotted", headers=_headers()
        )
    assert response.status_code == 200
    assert response.json()["data"] == {"items": []}


async def test_metrics_defaults_to_primary_authorizer_global_read() -> None:
    checked: list[tuple[TaskqAction, str | None]] = []

    async def authenticate(request: Any) -> AuthContext:
        return AuthContext(actor="scraper", principal="scraper")

    async def authorize(
        request: Any,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        checked.append((action, queue))

    app = _mounted(
        create_taskq_app(
            _resources(FacadeTransport()),
            authorizer=callable_auth(authenticate, authorize),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/taskq/metrics", headers=_headers())
    assert response.status_code == 200
    assert checked == [(TaskqAction.READ, None)]


async def test_openapi_exposes_generated_surface_but_hides_deferred_routes() -> None:
    app = create_taskq_app(_resources(FacadeTransport()), authorizer=no_auth_for_tests())
    schema = app.openapi()
    assert schema["servers"] == [{"url": "/taskq"}]
    assert "/v1/workers" in schema["paths"]
    assert "/v1/queues/{queue}" in {
        path for path, item in schema["paths"].items() if "get" in item
    }
    assert "/v1/jobs" in schema["paths"]
    complete = schema["paths"]["/v1/jobs/{job_id}/complete"]["post"]
    attempt = complete["requestBody"]["content"]["application/json"]["schema"]["properties"][
        "attempt_id"
    ]
    assert attempt["writeOnly"] is True
    response_fence_paths = [
        path
        for path, operations in schema["paths"].items()
        if "attempt_id"
        in json.dumps(next(iter(operations.values())).get("responses", {}), sort_keys=True)
    ]
    assert response_fence_paths == ["/v1/queues/{queue}/claims"]
    host_schema = {"openapi": "3.1.0", "paths": {"/health": {"get": {}}}}
    merged = merge_taskq_openapi(host_schema, schema)
    assert "/taskq/v1/workers" in merged["paths"]
    assert "/health" in merged["paths"]
    assert "/taskq/v1/workers" not in host_schema["paths"]


def _claimed() -> ClaimResult:
    return ClaimResult(
        state=ClaimState.CLAIMED,
        jobs=(
            ClaimedJob(
                job_id=uuid4(),
                queue="emails",
                job_type="tests.echo",
                priority=100,
                payload={},
                headers={},
                progress=None,
                attempt_id=uuid4(),
                attempt_number=1,
                failure_count=0,
                max_attempts=3,
                lease_expires_at=datetime.now(UTC),
                lease_seconds=30,
            ),
        ),
    )


async def test_long_poll_subscribe_recheck_notify_and_cleanup() -> None:
    transport = FacadeTransport()
    hub = ClaimWaitHub()
    transport.claims = [ClaimResult(state=ClaimState.EMPTY), ClaimResult(state=ClaimState.EMPTY)]
    app = _mounted(
        create_taskq_app(
            _resources(transport, hub),
            authorizer=no_auth_for_tests(),
            poll_interval=0.05,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        request = asyncio.create_task(
            client.post(
                "/taskq/v1/queues/emails/claims",
                headers=_headers(),
                json={"worker_id": "worker-1", "wait_seconds": 1},
            )
        )
        while hub.subscriber_count == 0:
            await asyncio.sleep(0)
        transport.claims.append(_claimed())
        await hub.notify()
        response = await request
    assert response.status_code == 200
    assert response.json()["outcome"] == "claimed"
    assert "attempt_id" in response.json()["data"]["jobs"][0]
    assert hub.subscriber_count == 0


async def test_long_poll_cancellation_removes_waiter_without_closing_resources() -> None:
    transport = FacadeTransport()
    hub = ClaimWaitHub()
    app = _mounted(
        create_taskq_app(
            _resources(transport, hub),
            authorizer=no_auth_for_tests(),
            poll_interval=1,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(
            client.post(
                "/taskq/v1/queues/emails/claims",
                headers=_headers(),
                json={"worker_id": "worker-1", "wait_seconds": 10},
            )
        )
        while hub.subscriber_count == 0:
            await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert hub.subscriber_count == 0
    assert not hub.closed


async def test_long_poll_missed_hint_future_poll_timeout_and_shutdown_orders() -> None:
    transport = FacadeTransport()
    hub = ClaimWaitHub()
    transport.claims = [
        ClaimResult(state=ClaimState.EMPTY),
        ClaimResult(state=ClaimState.EMPTY),
        _claimed(),
    ]
    app = _mounted(
        create_taskq_app(
            _resources(transport, hub),
            authorizer=no_auth_for_tests(),
            poll_interval=0.01,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        future_due = await client.post(
            "/taskq/v1/queues/emails/claims",
            headers=_headers(),
            json={"worker_id": "worker-1", "wait_seconds": 0.2},
        )
        timeout = await client.post(
            "/taskq/v1/queues/emails/claims",
            headers=_headers(),
            json={"worker_id": "worker-1", "wait_seconds": 0.02},
        )
        draining = asyncio.create_task(
            client.post(
                "/taskq/v1/queues/emails/claims",
                headers=_headers(),
                json={"worker_id": "worker-1", "wait_seconds": 1},
            )
        )
        while hub.subscriber_count == 0:
            await asyncio.sleep(0)
        await hub.shutdown()
        stopped = await draining
    assert future_due.json()["outcome"] == "claimed"
    assert timeout.json()["outcome"] == "timeout"
    assert stopped.status_code == 503
    assert stopped.json()["error"]["code"] == "TQ503"
    assert hub.subscriber_count == 0


async def test_hub_generation_closes_notify_before_subscribe_and_shutdown_races() -> None:
    hub = ClaimWaitHub()
    observed = hub.generation
    await hub.notify()
    async with await hub.subscribe(observed) as subscription:
        assert await subscription.wait(0) is True
    waiter = await hub.subscribe(hub.generation)
    pending = asyncio.create_task(waiter.wait(1))
    await hub.shutdown()
    assert await pending is True
    await waiter.aclose()
    assert hub.subscriber_count == 0
