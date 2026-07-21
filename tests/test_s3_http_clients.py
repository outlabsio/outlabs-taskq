"""S3-01 generated-client conformance and ownership vectors."""

from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from taskq.errors import TaskqCapabilityError, TaskqConfigError
from taskq.http import AsyncTaskqHttpClient, TaskqHttpClient
from taskq.protocol import (
    ClaimState,
    EnqueueCommand,
    EnqueueStatus,
    HttpCommandName,
)
from taskq.transport import ProducerTransport, RunnerTransport


def _response(
    request: httpx.Request,
    *,
    outcome: str = "ok",
    data: dict[str, object] | None = None,
    status: int = 200,
) -> httpx.Response:
    request_id = request.headers["Taskq-Request-Id"]
    return httpx.Response(
        status,
        headers={
            "Taskq-Protocol-Version": "1",
            "Taskq-Request-Id": request_id,
        },
        json={
            "protocol_version": 1,
            "request_id": request_id,
            "outcome": outcome,
            "data": data or {},
        },
    )


def _error(
    request: httpx.Request,
    code: str,
    *,
    status: int,
    retryable: bool = False,
) -> httpx.Response:
    request_id = request.headers["Taskq-Request-Id"]
    return httpx.Response(
        status,
        headers={
            "Taskq-Protocol-Version": "1",
            "Taskq-Request-Id": request_id,
        },
        json={
            "protocol_version": 1,
            "request_id": request_id,
            "error": {
                "code": code,
                "message": "safe",
                "retryable": retryable,
                "details": {},
            },
        },
    )


def _enqueue_command(*, idempotency_key: str | None = "key-1") -> EnqueueCommand:
    return EnqueueCommand(
        queue="emails",
        job_type="send",
        payload={"at": datetime(2026, 7, 19, tzinfo=UTC).isoformat()},
        scheduled_at=datetime(2026, 7, 19, tzinfo=UTC),
        idempotency_key=idempotency_key,
    )


def test_construction_is_side_effect_free_and_repr_is_secret_safe() -> None:
    client = AsyncTaskqHttpClient("https://example.test/base/", bearer_token="do-not-print")
    rendered = repr(client)
    assert "do-not-print" not in rendered
    assert "https://example.test/base" in rendered
    assert client._client is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"bearer_token": "one", "header_name": "X-Key", "header_value": "two"},
        {"header_name": "X-Key"},
    ],
)
def test_exactly_one_credential_source(kwargs: dict[str, str]) -> None:
    with pytest.raises(TaskqConfigError, match="credential"):
        TaskqHttpClient("https://example.test", **kwargs)


def test_claim_read_timeout_must_exceed_long_poll() -> None:
    with pytest.raises(TaskqConfigError, match="read timeout"):
        AsyncTaskqHttpClient(
            "https://example.test",
            bearer_token="secret",
            timeout=25,
            claim_wait_seconds=25,
        )


async def test_async_client_is_a_producer_and_runner_transport() -> None:
    client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret")
    assert isinstance(client, ProducerTransport)
    assert isinstance(client, RunnerTransport)
    await client.aclose()


async def test_owned_client_closes_and_borrowed_cancellation_propagates() -> None:
    owned = AsyncTaskqHttpClient("https://example.test", bearer_token="secret")
    raw_owned = owned._http()
    await owned.aclose()
    assert raw_owned.is_closed

    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        return _response(request)

    borrowed = httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret", client=borrowed)
    pending = asyncio.create_task(client.enqueue(_enqueue_command()))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await client.aclose()
    assert not borrowed.is_closed
    await borrowed.aclose()


async def test_enqueue_uses_exact_path_body_and_typed_result() -> None:
    seen: list[httpx.Request] = []
    job_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(request, outcome="created", data={"job_id": str(job_id)}, status=201)

    borrowed = httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret", client=borrowed)
    result = await client.enqueue(_enqueue_command())
    assert result.status is EnqueueStatus.CREATED
    assert result.job_id == job_id
    assert seen[0].url.path == "/taskq/v1/queues/emails/jobs"
    body = json.loads(seen[0].content)
    assert "queue" not in body
    assert body["scheduled_at"] == "2026-07-19T00:00:00Z"
    await client.aclose()
    assert not borrowed.is_closed
    await borrowed.aclose()


async def test_published_queue_profile_envelope_decodes_versioned_profile() -> None:
    profile = {
        "name": "emails",
        "profile_version": 7,
        "default_priority": 100,
        "default_lease_seconds": 300,
        "default_max_attempts": 5,
        "default_backoff_mode": "exponential",
        "default_backoff_base": 30,
        "default_backoff_cap": 3600,
        "retention_hours": 48,
        "failed_retention_hours": 336,
        "max_depth": None,
        "notify_enabled": True,
        "paused": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/taskq/v1/queues/emails"
        assert request.headers["If-Match"] == '"taskq-profile-6"'
        assert json.loads(request.content) == {"profile": {"default_priority": 100}}
        return _response(request, outcome="updated", data={"profile": profile})

    borrowed = httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret", client=borrowed)
    result = await client.ensure_queue("emails", {"default_priority": 100}, expected_version=6)
    assert result.profile == profile
    await client.aclose()
    await borrowed.aclose()


async def test_keyed_enqueue_retries_with_a_fresh_request_id() -> None:
    request_ids: list[str] = []
    job_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        request_ids.append(request.headers["Taskq-Request-Id"])
        if len(request_ids) == 1:
            return _error(request, "TQ503", status=503, retryable=True)
        return _response(request, outcome="existed", data={"job_id": str(job_id)})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="https://example.test", transport=transport) as raw:
        client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret", client=raw)
        result = await client.enqueue(_enqueue_command())
    assert result.status is EnqueueStatus.EXISTED
    assert len(request_ids) == 2
    assert len(set(request_ids)) == 2


async def test_unkeyed_enqueue_and_settlement_are_never_inner_retried() -> None:
    counts = {"enqueue": 0, "complete": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            counts["enqueue"] += 1
            return _error(request, "TQ503", status=503, retryable=True)
        counts["complete"] += 1
        return _error(request, "TQ503", status=503, retryable=True)

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as raw:
        client = AsyncTaskqHttpClient(
            "https://example.test", bearer_token="secret", client=raw, max_retries=3
        )
        with pytest.raises(Exception) as enqueue_error:
            await client.enqueue(_enqueue_command(idempotency_key=None))
        with pytest.raises(Exception) as settle_error:
            await client.complete(uuid4(), uuid4(), "worker-1")
    assert getattr(enqueue_error.value, "code", None) == "TQ503"
    assert getattr(settle_error.value, "code", None) == "TQ503"
    assert counts == {"enqueue": 1, "complete": 1}


async def test_claim_negotiates_once_and_timeout_normalizes_to_empty() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/meta"):
            return _response(
                request,
                data={"contract_version": "0.1.2", "capabilities": {}},
            )
        return _response(request, outcome="timeout", data={"jobs": []})

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as raw:
        client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret", client=raw)
        first = await client.claim("emails", "worker-1")
        second = await client.claim("emails", "worker-1")
    assert first.state is second.state is ClaimState.EMPTY
    assert calls.count("/taskq/v1/meta") == 1


async def test_claim_transport_failure_is_never_replayed_after_negotiation() -> None:
    claim_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal claim_calls
        if request.url.path.endswith("/meta"):
            return _response(
                request,
                data={"contract_version": "0.1.2", "capabilities": {}},
            )
        claim_calls += 1
        raise httpx.ReadError("response lost", request=request)

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as raw:
        client = AsyncTaskqHttpClient(
            "https://example.test", bearer_token="secret", client=raw, max_retries=3
        )
        with pytest.raises(httpx.ReadError):
            await client.claim("emails", "worker-1")
    assert claim_calls == 1


async def test_gated_and_read_model_methods_are_generated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _error(request, "TQ501", status=501)

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as raw:
        client = AsyncTaskqHttpClient("https://example.test", bearer_token="secret", client=raw)
        with pytest.raises(TaskqCapabilityError):
            await client.list_workers()
    assert hasattr(AsyncTaskqHttpClient, HttpCommandName.LIST_WORKERS.value)
    assert hasattr(AsyncTaskqHttpClient, HttpCommandName.GET_QUEUE.value)
    assert hasattr(AsyncTaskqHttpClient, HttpCommandName.LIST_JOBS.value)
    assert hasattr(TaskqHttpClient, HttpCommandName.GET_QUEUE.value)
    assert hasattr(TaskqHttpClient, HttpCommandName.LIST_JOBS.value)


def test_sync_client_is_thread_safe_at_request_id_boundary() -> None:
    provided: list[str] = []
    job_id = uuid4()

    def provider() -> str:
        value = f"request-{len(provided) + 1}"
        provided.append(value)
        return value

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, outcome="created", data={"job_id": str(job_id)}, status=201)

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as raw:
        client = TaskqHttpClient(
            "https://example.test",
            bearer_token="secret",
            client=raw,
            request_id_provider=provider,
        )
        result = client.enqueue(_enqueue_command())
    assert result.job_id == job_id
    assert provided == ["request-1"]


def test_sync_request_id_provider_is_serialized_across_threads() -> None:
    counter = 0
    observed: list[str] = []

    def provider() -> str:
        nonlocal counter
        counter += 1
        return f"thread-{counter}"

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.headers["Taskq-Request-Id"])
        return _response(request, data={"contract_version": "0.1.2", "capabilities": {}})

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as raw:
        client = TaskqHttpClient(
            "https://example.test",
            bearer_token="secret",
            client=raw,
            request_id_provider=provider,
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            metas = list(executor.map(lambda _: client.get_contract_meta(), range(32)))
    assert all(meta.contract_version == "0.1.2" for meta in metas)
    assert len(observed) == len(set(observed)) == 32


def test_process_fork_guard_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TaskqHttpClient("https://example.test", bearer_token="secret")
    monkeypatch.setattr("taskq.http.client.os.getpid", lambda: client._config.created_pid + 1)
    with pytest.raises(TaskqConfigError, match="process fork"):
        client.enqueue(_enqueue_command())
