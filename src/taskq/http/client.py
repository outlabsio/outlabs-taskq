"""Generated-metadata Protocol-v1 synchronous and asynchronous HTTP clients."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import os
import random
import re
import threading
import time
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, SecretStr, ValidationError
from pydantic_core import to_jsonable_python

from taskq.errors import TaskqConfigError, TaskqInternalError, taskq_error_from_code
from taskq.protocol import (
    CLAIM_BATCH_ADAPTER,
    ENQUEUE_RESULT_ADAPTER,
    SETTLE_RESULT_ADAPTER,
    HTTP_COMMAND_SPECS,
    ClaimResult,
    ClaimState,
    ClaimWireData,
    CancelResult,
    CommandEnvelope,
    CommandName,
    CommandOkOutcome,
    ConfigChangeOutcome,
    ContractMeta,
    EnqueueCommand,
    EnqueueManyItem,
    EnqueueManyWireData,
    EnqueueResult,
    EnqueueWireData,
    ErrorEnvelope,
    EnsureQueueResult,
    EnsureQueueWireData,
    ExpireJobOutcome,
    ExpireWorkerLeasesResult,
    HeartbeatResult,
    HeartbeatWireData,
    HttpCommandName,
    HttpCommandSpec,
    HttpSurface,
    JobDetail,
    PROTOCOL_MAJOR,
    RetryClass,
    QueueControlOutcome,
    QueueStatsWireData,
    SettleResult,
    SettleWireData,
    TqCode,
    TQ_ERROR_REGISTRY,
    WorkerPresenceWireData,
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PATH_PARAMETER_RE = re.compile(r"{([a-z_]+)}")
_RETRYABLE_HTTP_CODES = frozenset({TqCode.BACKPRESSURE, TqCode.INTERNAL, TqCode.UNAVAILABLE})


class TaskqAuthenticationError(PermissionError):
    code = "AUTH401"


class TaskqAuthorizationError(PermissionError):
    code = "AUTH403"


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TaskqConfigError("base_url must be an absolute http(s) URL")
    if parsed.query or parsed.fragment:
        raise TaskqConfigError("base_url cannot contain query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _credential_headers(
    *,
    bearer_token: str | SecretStr | None,
    header_name: str | None,
    header_value: str | SecretStr | None,
    auth: httpx.Auth | None,
) -> tuple[dict[str, str], httpx.Auth | None]:
    bearer = bearer_token is not None
    static_header = header_name is not None or header_value is not None
    supplied = int(bearer) + int(static_header) + int(auth is not None)
    if supplied != 1:
        raise TaskqConfigError("configure exactly one credential source")
    if static_header and (not header_name or header_value is None):
        raise TaskqConfigError("custom credential requires header_name and header_value")
    headers: dict[str, str] = {}
    if bearer_token is not None:
        secret = bearer_token if isinstance(bearer_token, SecretStr) else SecretStr(bearer_token)
        headers["Authorization"] = f"Bearer {secret.get_secret_value()}"
    elif header_name is not None and header_value is not None:
        if not re.fullmatch(r"[A-Za-z0-9-]+", header_name):
            raise TaskqConfigError("credential header name is invalid")
        secret = header_value if isinstance(header_value, SecretStr) else SecretStr(header_value)
        headers[header_name] = secret.get_secret_value()
    return headers, auth


def _request_id(provider: Callable[[], str] | None) -> str:
    value = provider() if provider is not None else str(uuid4())
    if not isinstance(value, str) or _REQUEST_ID_RE.fullmatch(value) is None:
        raise TaskqConfigError("request-id provider returned an invalid value")
    return value


def _jsonable(value: BaseModel | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return to_jsonable_python({str(key): item for key, item in value.items() if item is not None})


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After")
    if value is None:
        return 0.0
    try:
        return max(0.0, min(float(value), 30.0))
    except ValueError:
        return 0.0


def _retry_delay(response: httpx.Response) -> float:
    retry_after = _retry_after_seconds(response)
    if retry_after == 0:
        return 0.0
    return retry_after + random.uniform(0, min(retry_after * 0.1, 0.25))


def _can_retry(spec: HttpCommandSpec, body: Mapping[str, Any] | None) -> bool:
    if spec.retry_class is RetryClass.SAFE_IDEMPOTENT:
        return True
    if spec.retry_class is RetryClass.KEYED_ENQUEUE:
        return bool(body and body.get("idempotency_key"))
    if spec.retry_class is RetryClass.KEYED_BATCH:
        items = body.get("items") if body else None
        return bool(
            isinstance(items, list)
            and items
            and all(isinstance(item, dict) and item.get("idempotency_key") for item in items)
        )
    return False


def _format_path(spec: HttpCommandSpec, path_params: Mapping[str, Any] | None) -> str:
    supplied = dict(path_params or {})
    path = spec.path
    for name in _PATH_PARAMETER_RE.findall(path):
        if name not in supplied:
            raise TaskqConfigError(f"missing path parameter: {name}")
        value = str(supplied.pop(name))
        if "/" in value or not value:
            raise TaskqConfigError(f"invalid path parameter: {name}")
        path = path.replace("{" + name + "}", value)
    if supplied:
        raise TaskqConfigError("unexpected path parameter")
    return path


def _decode_domain(spec: HttpCommandSpec, outcome: str, data: dict[str, Any]) -> Any:
    """Normalize a generated command response to its core SQL-domain value."""

    command = spec.sql_command
    if command is CommandName.ENSURE_QUEUE:
        wire = EnsureQueueWireData.model_validate(data)
        return EnsureQueueResult(
            result=ConfigChangeOutcome(outcome), profile=wire.profile.model_dump(mode="json")
        )
    if command in {CommandName.PAUSE_QUEUE, CommandName.RESUME_QUEUE}:
        return QueueControlOutcome(outcome)
    if command is CommandName.CANCEL:
        return CancelResult(result=outcome, job_status=data["job_status"])
    if command is CommandName.REDRIVE:
        return outcome == "redriven"
    if command is CommandName.EXPIRE_JOB:
        return ExpireJobOutcome(outcome)
    if command is CommandName.EXPIRE_WORKER_LEASES:
        return ExpireWorkerLeasesResult.model_validate(data)
    if command in {CommandName.PURGE_QUEUED, CommandName.REQUEST_WORKER_SHUTDOWN}:
        return int(data["count"])
    if command in {CommandName.RUN_NOW, CommandName.REPRIORITIZE}:
        return CommandOkOutcome(outcome)
    if command is CommandName.SET_CONCURRENCY_LIMIT:
        return ConfigChangeOutcome(outcome)
    if command is CommandName.GET_QUEUE_STATS:
        return list(QueueStatsWireData.model_validate(data).items)
    if spec.data_model is not None:
        return spec.data_model.model_validate(data)
    return data


def _decode_envelope(
    response: httpx.Response,
    *,
    spec: HttpCommandSpec,
    sent_request_id: str,
) -> tuple[str, dict[str, Any], str]:
    if response.headers.get("Taskq-Protocol-Version") != str(PROTOCOL_MAJOR):
        raise TaskqInternalError(details={"reason": "missing_or_invalid_protocol_header"})
    try:
        raw = response.json()
    except ValueError as exc:
        raise TaskqInternalError(details={"reason": "invalid_json_envelope"}, cause=exc) from exc

    if response.status_code >= 400 and "error" in raw:
        try:
            envelope = ErrorEnvelope.model_validate(raw)
        except ValidationError as exc:
            raise TaskqInternalError(
                details={"reason": "invalid_error_envelope"}, cause=exc
            ) from exc
        if (
            envelope.request_id != response.headers.get("Taskq-Request-Id")
            or envelope.request_id != sent_request_id
        ):
            raise TaskqInternalError(details={"reason": "request_id_mismatch"})
        if envelope.error.code == "AUTH401":
            raise TaskqAuthenticationError("taskq authentication failed")
        if envelope.error.code == "AUTH403":
            raise TaskqAuthorizationError("taskq authorization failed")
        expected_error_status = TQ_ERROR_REGISTRY[envelope.error.code].http_status
        if (
            response.status_code != expected_error_status
            or envelope.error.retryable != TQ_ERROR_REGISTRY[envelope.error.code].retryable
        ):
            raise TaskqInternalError(details={"reason": "error_status_mismatch"})
        raise taskq_error_from_code(envelope.error.code, details=envelope.error.details)

    try:
        envelope = CommandEnvelope[dict[str, Any]].model_validate(raw)
    except ValidationError as exc:
        raise TaskqInternalError(details={"reason": "invalid_command_envelope"}, cause=exc) from exc
    echoed = response.headers.get("Taskq-Request-Id")
    if envelope.request_id != echoed or envelope.request_id != sent_request_id:
        raise TaskqInternalError(details={"reason": "request_id_mismatch"})
    expected_status = spec.outcomes.get(envelope.outcome)
    if expected_status is None or expected_status != response.status_code:
        raise TaskqInternalError(details={"reason": "unexpected_outcome_or_status"})
    return envelope.outcome, envelope.data, envelope.request_id


class _ClientConfig:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | SecretStr | None,
        header_name: str | None,
        header_value: str | SecretStr | None,
        auth: httpx.Auth | None,
        request_id_provider: Callable[[], str] | None,
        max_retries: int,
    ) -> None:
        if max_retries < 0 or max_retries > 10:
            raise TaskqConfigError("max_retries must be between 0 and 10")
        self.base_url = _normalize_base_url(base_url)
        self.credential_headers, self.auth = _credential_headers(
            bearer_token=bearer_token,
            header_name=header_name,
            header_value=header_value,
            auth=auth,
        )
        self.request_id_provider = request_id_provider
        self.max_retries = max_retries
        self.created_pid = os.getpid()

    def headers(self) -> tuple[str, dict[str, str]]:
        if os.getpid() != self.created_pid:
            raise TaskqConfigError("HTTP client cannot be reused across a process fork")
        request_id = _request_id(self.request_id_provider)
        return request_id, {
            **self.credential_headers,
            "Taskq-Protocol-Version": str(PROTOCOL_MAJOR),
            "Taskq-Request-Id": request_id,
        }


class AsyncTaskqHttpClient:
    """Async Producer/Runner transport plus generated Protocol-v1 command access."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | SecretStr | None = None,
        header_name: str | None = None,
        header_value: str | SecretStr | None = None,
        auth: httpx.Auth | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | float = 30.0,
        claim_wait_seconds: float = 25.0,
        request_id_provider: Callable[[], str] | None = None,
        max_retries: int = 2,
    ) -> None:
        if not 0 <= claim_wait_seconds <= 30:
            raise TaskqConfigError("claim_wait_seconds must be between 0 and 30")
        read_timeout = httpx.Timeout(timeout).read
        if read_timeout is not None and read_timeout <= claim_wait_seconds:
            raise TaskqConfigError("HTTP read timeout must exceed claim_wait_seconds")
        self._config = _ClientConfig(
            base_url,
            bearer_token=bearer_token,
            header_name=header_name,
            header_value=header_value,
            auth=auth,
            request_id_provider=request_id_provider,
            max_retries=max_retries,
        )
        self._client = client
        self._owned = client is None
        self._timeout = timeout
        self._claim_wait_seconds = claim_wait_seconds
        self._closed = False
        self._compatible = False
        self._meta: ContractMeta | None = None
        self._start_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            f"AsyncTaskqHttpClient(base_url={self._config.base_url!r}, "
            f"owned={self._owned!r}, closed={self._closed!r})"
        )

    def _http(self) -> httpx.AsyncClient:
        if self._closed:
            raise TaskqConfigError("HTTP client is closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._timeout,
                auth=self._config.auth,
            )
        return self._client

    async def _request(
        self,
        name: HttpCommandName,
        *,
        path_params: Mapping[str, Any] | None = None,
        body: BaseModel | Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> tuple[str, dict[str, Any], str]:
        spec = HTTP_COMMAND_SPECS[name]
        if spec.surface is HttpSurface.DEFERRED:
            raise TaskqConfigError("deferred command has no official client method")
        path = _format_path(spec, path_params)
        encoded = _jsonable(body)
        if spec.request_model is not None:
            encoded = spec.request_model.model_validate(encoded or {}).model_dump(
                mode="json", exclude_none=True
            )
        retry = _can_retry(spec, encoded)
        for attempt in range(self._config.max_retries + 1):
            request_id, headers = self._config.headers()
            headers.update(extra_headers or {})
            try:
                response = await self._http().request(
                    spec.method,
                    path,
                    json=encoded if spec.method != "GET" else None,
                    params=query if spec.method == "GET" else None,
                    headers=headers,
                    auth=self._config.auth,
                )
                try:
                    return _decode_envelope(response, spec=spec, sent_request_id=request_id)
                except Exception as exc:
                    code = getattr(exc, "code", None)
                    if (
                        not retry
                        or attempt >= self._config.max_retries
                        or code not in _RETRYABLE_HTTP_CODES
                    ):
                        raise
                    await asyncio.sleep(_retry_delay(response))
            except httpx.TransportError:
                if not retry or attempt >= self._config.max_retries:
                    raise
                await asyncio.sleep(0)
        raise AssertionError("unreachable")

    async def start(self) -> ContractMeta:
        async with self._start_lock:
            if self._compatible:
                assert self._meta is not None
                return self._meta
            meta = await self.get_contract_meta()
            self._meta = meta
            self._compatible = True
            return meta

    async def ensure_compatible(self) -> ContractMeta:
        return await self.start()

    async def get_contract_meta(self) -> ContractMeta:
        _, data, _ = await self._request(HttpCommandName.META)
        protocol_min = int(data.get("protocol_min", PROTOCOL_MAJOR))
        protocol_max = int(data.get("protocol_max", PROTOCOL_MAJOR))
        if not protocol_min <= PROTOCOL_MAJOR <= protocol_max:
            raise taskq_error_from_code(TqCode.VERSION, details={"protocol": PROTOCOL_MAJOR})
        return ContractMeta.model_validate(data)

    async def enqueue(self, command: EnqueueCommand) -> EnqueueResult:
        body = command.model_dump(mode="json", exclude={"queue"}, exclude_none=True)
        outcome, data, _ = await self._request(
            HttpCommandName.ENQUEUE,
            path_params={"queue": command.queue},
            body=body,
        )
        wire = EnqueueWireData.model_validate(data)
        return ENQUEUE_RESULT_ADAPTER.validate_python(
            {
                **command.model_dump(mode="json"),
                "status": outcome,
                "created": outcome == "created",
                "job_id": wire.job_id,
            }
        )

    async def enqueue_many(
        self, queue: str, items: Sequence[EnqueueManyItem]
    ) -> list[EnqueueResult]:
        body = {"items": [item.model_dump(mode="json", exclude_none=True) for item in items]}
        _, data, _ = await self._request(
            HttpCommandName.ENQUEUE_MANY,
            path_params={"queue": queue},
            body=body,
        )
        wire = EnqueueManyWireData.model_validate(data)
        if len(wire.items) != len(items):
            raise TaskqInternalError(details={"reason": "bulk_result_count_mismatch"})
        results: list[EnqueueResult] = []
        for index, result in enumerate(wire.items, start=1):
            if result.input_index != index:
                raise TaskqInternalError(details={"reason": "bulk_result_order_mismatch"})
            item = items[index - 1]
            results.append(
                ENQUEUE_RESULT_ADAPTER.validate_python(
                    {
                        **item.model_dump(),
                        "queue": queue,
                        "status": result.outcome,
                        "created": result.outcome == "created",
                        "job_id": result.job_id,
                    }
                )
            )
        return results

    async def claim(
        self,
        queue: str,
        worker_id: str,
        *,
        batch: int = 1,
        job_types: Sequence[str] | None = None,
        lease_seconds: int | None = None,
        affinity_key: str | None = None,
        job_id: UUID | None = None,
    ) -> ClaimResult:
        await self.start()
        outcome, data, _ = await self._request(
            HttpCommandName.CLAIM,
            path_params={"queue": queue},
            body={
                "worker_id": worker_id,
                "batch": batch,
                "job_types": list(job_types) if job_types is not None else None,
                "lease_seconds": lease_seconds,
                "affinity_key": affinity_key,
                "job_id": str(job_id) if job_id is not None else None,
                "wait_seconds": self._claim_wait_seconds,
            },
        )
        wire = ClaimWireData.model_validate(data)
        state = ClaimState.EMPTY if outcome == "timeout" else ClaimState(outcome)
        return CLAIM_BATCH_ADAPTER.validate_python(
            {"state": state, "jobs": [job.to_core() for job in wire.jobs]}
        )

    async def heartbeat(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> HeartbeatResult:
        outcome, data, _ = await self._request(
            HttpCommandName.HEARTBEAT,
            path_params={"job_id": job_id},
            body={
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "progress": dict(progress) if progress is not None else None,
                "stats": dict(stats) if stats is not None else None,
            },
        )
        wire = HeartbeatWireData.model_validate(data)
        return HeartbeatResult(
            ok=outcome == "ok",
            cancel_requested=wire.cancel_requested,
            lease_expires_at=wire.lease_expires_at,
        )

    async def _settle(
        self,
        name: HttpCommandName,
        job_id: UUID,
        body: Mapping[str, Any],
    ) -> SettleResult:
        outcome, data, _ = await self._request(name, path_params={"job_id": job_id}, body=body)
        wire = SettleWireData.model_validate(data)
        return SETTLE_RESULT_ADAPTER.validate_python(
            {"result": outcome, "job_status": wire.job_status, "scheduled_at": wire.scheduled_at}
        )

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Mapping[str, Any]] | None = None,
    ) -> SettleResult:
        return await self._settle(
            HttpCommandName.COMPLETE,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "result": dict(result) if result is not None else None,
                "stats": dict(stats) if stats is not None else None,
                "followups": [dict(item) for item in followups] if followups is not None else None,
            },
        )

    async def fail(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return await self._settle(
            HttpCommandName.FAIL,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "error": error,
                "retryable": retryable,
                "retry_after_seconds": retry_after_seconds,
                "progress": dict(progress) if progress is not None else None,
                "stats": dict(stats) if stats is not None else None,
            },
        )

    async def snooze(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        delay_seconds: int,
        *,
        reason: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return await self._settle(
            HttpCommandName.SNOOZE,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "delay_seconds": delay_seconds,
                "reason": reason,
                "progress": dict(progress) if progress is not None else None,
            },
        )

    async def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: Literal["released", "worker_shutdown", "no_handler"],
        *,
        delay_seconds: int = 0,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return await self._settle(
            HttpCommandName.RELEASE,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "cause": cause,
                "delay_seconds": delay_seconds,
                "progress": dict(progress) if progress is not None else None,
            },
        )

    async def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult:
        return await self._settle(
            HttpCommandName.CANCEL_RUNNING,
            job_id,
            {"attempt_id": str(attempt_id), "worker_id": worker_id, "reason": reason},
        )

    async def worker_heartbeat(
        self,
        worker_id: str,
        queues: Sequence[str],
        *,
        hostname: str | None = None,
        pid: int | None = None,
        version: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> bool:
        _, data, _ = await self._request(
            HttpCommandName.WORKER_HEARTBEAT,
            body={
                "worker_id": worker_id,
                "queues": list(queues),
                "hostname": hostname,
                "pid": pid,
                "version": version,
                "meta": dict(meta) if meta is not None else None,
            },
        )
        return WorkerPresenceWireData.model_validate(data).shutdown_requested

    async def get_job(
        self,
        job_id: UUID,
        *,
        include_error: bool = False,
        include_result: bool = False,
        include_progress: bool = False,
        include_payload: bool = False,
    ) -> JobDetail:
        _, data, _ = await self._request(
            HttpCommandName.GET_JOB,
            path_params={"job_id": job_id},
            query={
                "include_error": include_error,
                "include_result": include_result,
                "include_progress": include_progress,
                "include_payload": include_payload,
            },
        )
        if "id" in data and "job_id" not in data:
            data = {**data, "job_id": data["id"]}
        return JobDetail.model_validate(data)

    async def metrics(self) -> str:
        spec = HTTP_COMMAND_SPECS[HttpCommandName.METRICS]
        request_id, headers = self._config.headers()
        response = await self._http().request(spec.method, spec.path, headers=headers)
        if response.status_code != 200:
            _decode_envelope(response, spec=spec, sent_request_id=request_id)
        if (
            response.headers.get("Taskq-Protocol-Version") != str(PROTOCOL_MAJOR)
            or response.headers.get("Taskq-Request-Id") != request_id
        ):
            raise TaskqInternalError(details={"reason": "invalid_metrics_headers"})
        return response.text

    async def command(
        self,
        name: HttpCommandName,
        *,
        path_params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        outcome, data, _ = await self._request(
            name, path_params=path_params, body=body, query=query
        )
        return _decode_domain(HTTP_COMMAND_SPECS[name], outcome, data)

    async def ensure_queue(
        self, queue: str, profile: Mapping[str, Any], *, expected_version: int | None = None
    ) -> EnsureQueueResult:
        headers = (
            {"If-Match": f'"taskq-profile-{expected_version}"'}
            if expected_version is not None
            else None
        )
        outcome, data, _ = await self._request(
            HttpCommandName.ENSURE_QUEUE,
            path_params={"queue": queue},
            body={"profile": dict(profile)},
            extra_headers=headers,
        )
        return _decode_domain(HTTP_COMMAND_SPECS[HttpCommandName.ENSURE_QUEUE], outcome, data)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned and self._client is not None:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncTaskqHttpClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class TaskqHttpClient:
    """Thread-safe synchronous Protocol-v1 client generated from the same metadata."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | SecretStr | None = None,
        header_name: str | None = None,
        header_value: str | SecretStr | None = None,
        auth: httpx.Auth | None = None,
        client: httpx.Client | None = None,
        timeout: httpx.Timeout | float = 30.0,
        request_id_provider: Callable[[], str] | None = None,
        max_retries: int = 2,
    ) -> None:
        self._config = _ClientConfig(
            base_url,
            bearer_token=bearer_token,
            header_name=header_name,
            header_value=header_value,
            auth=auth,
            request_id_provider=request_id_provider,
            max_retries=max_retries,
        )
        self._client = client
        self._owned = client is None
        self._timeout = timeout
        self._closed = False
        self._compatible = False
        self._meta: ContractMeta | None = None
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return (
            f"TaskqHttpClient(base_url={self._config.base_url!r}, "
            f"owned={self._owned!r}, closed={self._closed!r})"
        )

    def _http(self) -> httpx.Client:
        if self._closed:
            raise TaskqConfigError("HTTP client is closed")
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._config.base_url,
                timeout=self._timeout,
                auth=self._config.auth,
            )
        return self._client

    def _request(
        self,
        name: HttpCommandName,
        *,
        path_params: Mapping[str, Any] | None = None,
        body: BaseModel | Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> tuple[str, dict[str, Any], str]:
        spec = HTTP_COMMAND_SPECS[name]
        if spec.surface is HttpSurface.DEFERRED:
            raise TaskqConfigError("deferred command has no official client method")
        path = _format_path(spec, path_params)
        encoded = _jsonable(body)
        if spec.request_model is not None:
            encoded = spec.request_model.model_validate(encoded or {}).model_dump(
                mode="json", exclude_none=True
            )
        retry = _can_retry(spec, encoded)
        for attempt in range(self._config.max_retries + 1):
            with self._lock:
                request_id, headers = self._config.headers()
                headers.update(extra_headers or {})
            try:
                response = self._http().request(
                    spec.method,
                    path,
                    json=encoded if spec.method != "GET" else None,
                    params=query if spec.method == "GET" else None,
                    headers=headers,
                    auth=self._config.auth,
                )
                try:
                    return _decode_envelope(response, spec=spec, sent_request_id=request_id)
                except Exception as exc:
                    code = getattr(exc, "code", None)
                    if (
                        not retry
                        or attempt >= self._config.max_retries
                        or code not in _RETRYABLE_HTTP_CODES
                    ):
                        raise
                    time.sleep(_retry_delay(response))
            except httpx.TransportError:
                if not retry or attempt >= self._config.max_retries:
                    raise
                time.sleep(0)
        raise AssertionError("unreachable")

    def start(self) -> ContractMeta:
        with self._lock:
            if self._compatible:
                assert self._meta is not None
                return self._meta
            meta = self.get_contract_meta()
            self._meta = meta
            self._compatible = True
            return meta

    def ensure_compatible(self) -> ContractMeta:
        return self.start()

    def get_contract_meta(self) -> ContractMeta:
        _, data, _ = self._request(HttpCommandName.META)
        protocol_min = int(data.get("protocol_min", PROTOCOL_MAJOR))
        protocol_max = int(data.get("protocol_max", PROTOCOL_MAJOR))
        if not protocol_min <= PROTOCOL_MAJOR <= protocol_max:
            raise taskq_error_from_code(TqCode.VERSION, details={"protocol": PROTOCOL_MAJOR})
        return ContractMeta.model_validate(data)

    def enqueue(self, command: EnqueueCommand) -> EnqueueResult:
        body = command.model_dump(mode="json", exclude={"queue"}, exclude_none=True)
        outcome, data, _ = self._request(
            HttpCommandName.ENQUEUE,
            path_params={"queue": command.queue},
            body=body,
        )
        wire = EnqueueWireData.model_validate(data)
        return ENQUEUE_RESULT_ADAPTER.validate_python(
            {
                **command.model_dump(mode="json"),
                "status": outcome,
                "created": outcome == "created",
                "job_id": wire.job_id,
            }
        )

    def enqueue_many(self, queue: str, items: Sequence[EnqueueManyItem]) -> list[EnqueueResult]:
        body = {"items": [item.model_dump(mode="json", exclude_none=True) for item in items]}
        _, data, _ = self._request(
            HttpCommandName.ENQUEUE_MANY,
            path_params={"queue": queue},
            body=body,
        )
        wire = EnqueueManyWireData.model_validate(data)
        if len(wire.items) != len(items):
            raise TaskqInternalError(details={"reason": "bulk_result_count_mismatch"})
        results: list[EnqueueResult] = []
        for index, result in enumerate(wire.items, start=1):
            if result.input_index != index:
                raise TaskqInternalError(details={"reason": "bulk_result_order_mismatch"})
            item = items[index - 1]
            results.append(
                ENQUEUE_RESULT_ADAPTER.validate_python(
                    {
                        **item.model_dump(),
                        "queue": queue,
                        "status": result.outcome,
                        "created": result.outcome == "created",
                        "job_id": result.job_id,
                    }
                )
            )
        return results

    def claim(
        self,
        queue: str,
        worker_id: str,
        *,
        batch: int = 1,
        job_types: Sequence[str] | None = None,
        lease_seconds: int | None = None,
        affinity_key: str | None = None,
        job_id: UUID | None = None,
        wait_seconds: float = 0,
    ) -> ClaimResult:
        self.start()
        outcome, data, _ = self._request(
            HttpCommandName.CLAIM,
            path_params={"queue": queue},
            body={
                "worker_id": worker_id,
                "batch": batch,
                "job_types": list(job_types) if job_types is not None else None,
                "lease_seconds": lease_seconds,
                "affinity_key": affinity_key,
                "job_id": str(job_id) if job_id is not None else None,
                "wait_seconds": wait_seconds,
            },
        )
        wire = ClaimWireData.model_validate(data)
        state = ClaimState.EMPTY if outcome == "timeout" else ClaimState(outcome)
        return CLAIM_BATCH_ADAPTER.validate_python(
            {"state": state, "jobs": [job.to_core() for job in wire.jobs]}
        )

    def heartbeat(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> HeartbeatResult:
        outcome, data, _ = self._request(
            HttpCommandName.HEARTBEAT,
            path_params={"job_id": job_id},
            body={
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "progress": dict(progress) if progress is not None else None,
                "stats": dict(stats) if stats is not None else None,
            },
        )
        wire = HeartbeatWireData.model_validate(data)
        return HeartbeatResult(
            ok=outcome == "ok",
            cancel_requested=wire.cancel_requested,
            lease_expires_at=wire.lease_expires_at,
        )

    def _settle(
        self,
        name: HttpCommandName,
        job_id: UUID,
        body: Mapping[str, Any],
    ) -> SettleResult:
        outcome, data, _ = self._request(name, path_params={"job_id": job_id}, body=body)
        wire = SettleWireData.model_validate(data)
        return SETTLE_RESULT_ADAPTER.validate_python(
            {"result": outcome, "job_status": wire.job_status, "scheduled_at": wire.scheduled_at}
        )

    def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Mapping[str, Any]] | None = None,
    ) -> SettleResult:
        return self._settle(
            HttpCommandName.COMPLETE,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "result": dict(result) if result is not None else None,
                "stats": dict(stats) if stats is not None else None,
                "followups": [dict(item) for item in followups] if followups is not None else None,
            },
        )

    def fail(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return self._settle(
            HttpCommandName.FAIL,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "error": error,
                "retryable": retryable,
                "retry_after_seconds": retry_after_seconds,
                "progress": dict(progress) if progress is not None else None,
                "stats": dict(stats) if stats is not None else None,
            },
        )

    def snooze(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        delay_seconds: int,
        *,
        reason: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return self._settle(
            HttpCommandName.SNOOZE,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "delay_seconds": delay_seconds,
                "reason": reason,
                "progress": dict(progress) if progress is not None else None,
            },
        )

    def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: Literal["released", "worker_shutdown", "no_handler"],
        *,
        delay_seconds: int = 0,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return self._settle(
            HttpCommandName.RELEASE,
            job_id,
            {
                "attempt_id": str(attempt_id),
                "worker_id": worker_id,
                "cause": cause,
                "delay_seconds": delay_seconds,
                "progress": dict(progress) if progress is not None else None,
            },
        )

    def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult:
        return self._settle(
            HttpCommandName.CANCEL_RUNNING,
            job_id,
            {"attempt_id": str(attempt_id), "worker_id": worker_id, "reason": reason},
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        queues: Sequence[str],
        *,
        hostname: str | None = None,
        pid: int | None = None,
        version: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> bool:
        _, data, _ = self._request(
            HttpCommandName.WORKER_HEARTBEAT,
            body={
                "worker_id": worker_id,
                "queues": list(queues),
                "hostname": hostname,
                "pid": pid,
                "version": version,
                "meta": dict(meta) if meta is not None else None,
            },
        )
        return WorkerPresenceWireData.model_validate(data).shutdown_requested

    def get_job(
        self,
        job_id: UUID,
        *,
        include_error: bool = False,
        include_result: bool = False,
        include_progress: bool = False,
        include_payload: bool = False,
    ) -> JobDetail:
        _, data, _ = self._request(
            HttpCommandName.GET_JOB,
            path_params={"job_id": job_id},
            query={
                "include_error": include_error,
                "include_result": include_result,
                "include_progress": include_progress,
                "include_payload": include_payload,
            },
        )
        if "id" in data and "job_id" not in data:
            data = {**data, "job_id": data["id"]}
        return JobDetail.model_validate(data)

    def command(
        self,
        name: HttpCommandName,
        *,
        path_params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        outcome, data, _ = self._request(name, path_params=path_params, body=body, query=query)
        return _decode_domain(HTTP_COMMAND_SPECS[name], outcome, data)

    def ensure_queue(
        self, queue: str, profile: Mapping[str, Any], *, expected_version: int | None = None
    ) -> EnsureQueueResult:
        headers = (
            {"If-Match": f'"taskq-profile-{expected_version}"'}
            if expected_version is not None
            else None
        )
        outcome, data, _ = self._request(
            HttpCommandName.ENSURE_QUEUE,
            path_params={"queue": queue},
            body={"profile": dict(profile)},
            extra_headers=headers,
        )
        return _decode_domain(HTTP_COMMAND_SPECS[HttpCommandName.ENSURE_QUEUE], outcome, data)

    def metrics(self) -> str:
        spec = HTTP_COMMAND_SPECS[HttpCommandName.METRICS]
        with self._lock:
            request_id, headers = self._config.headers()
        response = self._http().request(spec.method, spec.path, headers=headers)
        if response.status_code != 200:
            _decode_envelope(response, spec=spec, sent_request_id=request_id)
        if (
            response.headers.get("Taskq-Protocol-Version") != str(PROTOCOL_MAJOR)
            or response.headers.get("Taskq-Request-Id") != request_id
        ):
            raise TaskqInternalError(details={"reason": "invalid_metrics_headers"})
        return response.text

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned and self._client is not None:
            self._client.close()

    def __enter__(self) -> TaskqHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _install_generated_methods() -> None:
    """Expose every active/gated command without a second handwritten route table."""

    for command_name, spec in HTTP_COMMAND_SPECS.items():
        if spec.surface is HttpSurface.DEFERRED:
            continue
        method_name = command_name.value
        if not hasattr(AsyncTaskqHttpClient, method_name):

            async def async_method(
                self: AsyncTaskqHttpClient,
                _name: HttpCommandName = command_name,
                **kwargs: Any,
            ) -> Any:
                path_params = {
                    name: kwargs.pop(name)
                    for name in _PATH_PARAMETER_RE.findall(HTTP_COMMAND_SPECS[_name].path)
                }
                return await self.command(
                    _name,
                    path_params=path_params,
                    query=kwargs if HTTP_COMMAND_SPECS[_name].method == "GET" else None,
                    body=kwargs if HTTP_COMMAND_SPECS[_name].method != "GET" else None,
                )

            async_method.__name__ = method_name
            setattr(AsyncTaskqHttpClient, method_name, async_method)

        if not hasattr(TaskqHttpClient, method_name):

            def sync_method(
                self: TaskqHttpClient,
                _name: HttpCommandName = command_name,
                **kwargs: Any,
            ) -> Any:
                path_params = {
                    name: kwargs.pop(name)
                    for name in _PATH_PARAMETER_RE.findall(HTTP_COMMAND_SPECS[_name].path)
                }
                return self.command(
                    _name,
                    path_params=path_params,
                    query=kwargs if HTTP_COMMAND_SPECS[_name].method == "GET" else None,
                    body=kwargs if HTTP_COMMAND_SPECS[_name].method != "GET" else None,
                )

            sync_method.__name__ = method_name
            setattr(TaskqHttpClient, method_name, sync_method)


_install_generated_methods()


__all__ = [
    "AsyncTaskqHttpClient",
    "TaskqAuthenticationError",
    "TaskqAuthorizationError",
    "TaskqHttpClient",
]
