"""Generated FastAPI Protocol-v1 facade with complete envelope ownership."""

from __future__ import annotations

import copy
import base64
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python
from starlette.exceptions import HTTPException as StarletteHTTPException

from taskq.errors import (
    TaskqCapabilityError,
    TaskqConfigError,
    TaskqConflictError,
    TaskqError,
    TaskqInternalError,
    TaskqNotFoundError,
    TaskqUnavailableError,
    TaskqValidationError,
    TaskqVersionError,
)
from taskq.http.deps import (
    AuthContext,
    QueueAuthorizer,
    authenticate_request,
    authorize_context,
)
from taskq.http.hub import ClaimWaitHub
from taskq.protocol import (
    HTTP_COMMAND_SPECS,
    TQ_ERROR_REGISTRY,
    CancelRunningWireRequest,
    ClaimResult,
    ClaimedJobWire,
    ClaimState,
    ClaimWireData,
    ClaimWireRequest,
    CommandName,
    CompleteWireRequest,
    ConcurrencyLimitWireRequest,
    EnqueueCommand,
    EnqueueManyWireRequest,
    EnqueueWireData,
    EnqueueWireRequest,
    EnsureQueueWireRequest,
    FailWireRequest,
    HeartbeatWireRequest,
    HttpCommandName,
    HttpCommandSpec,
    HttpSurface,
    PROTOCOL_MAJOR,
    PurgeWireRequest,
    QueueProfile,
    QueueSource,
    ReasonWireRequest,
    RedriveWireRequest,
    ReleaseWireRequest,
    ReprioritizeWireRequest,
    ShutdownRequestWireRequest,
    SnoozeWireRequest,
    TaskqAction,
    TqCode,
    WorkerPresenceWireRequest,
)
from taskq.transport import (
    AuthorizationLookupTransport,
    ObserverTransport,
    OperatorTransport,
    ProducerTransport,
    RunnerTransport,
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PROFILE_ETAG_RE = re.compile(r'^"taskq-profile-([1-9][0-9]*)"$')
_MAX_BODY_BYTES = 4 * 1024 * 1024
_OPERATOR_COMMANDS = frozenset(
    name
    for name, spec in HTTP_COMMAND_SPECS.items()
    if spec.sql_command is not None
    and spec.sql_command
    in {
        CommandName.ENSURE_QUEUE,
        CommandName.PAUSE_QUEUE,
        CommandName.RESUME_QUEUE,
        CommandName.CANCEL,
        CommandName.REDRIVE,
        CommandName.EXPIRE_JOB,
        CommandName.EXPIRE_WORKER_LEASES,
        CommandName.PURGE_QUEUED,
        CommandName.RUN_NOW,
        CommandName.REPRIORITIZE,
        CommandName.SET_CONCURRENCY_LIMIT,
        CommandName.REQUEST_WORKER_SHUTDOWN,
    }
)


@dataclass(frozen=True, slots=True)
class TaskqFacadeTransports:
    producer: ProducerTransport
    runner: RunnerTransport
    observer: ObserverTransport
    authorization: AuthorizationLookupTransport
    claim_wait_hub: ClaimWaitHub


def _selected_request_id(request: Request) -> tuple[str, bool]:
    supplied = request.headers.get("Taskq-Request-Id")
    if supplied is not None and _REQUEST_ID_RE.fullmatch(supplied) is not None:
        return supplied, False
    return str(uuid4()), supplied is not None


def _request_id(request: Request) -> str:
    return str(request.state.taskq_request_id)


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    blocked = {"attempt_id", "fence", "payload", "headers", "progress", "result", "sql"}
    result: dict[str, Any] = {}
    for key, value in details.items():
        normalized = str(key).lower()
        if normalized in blocked:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            rendered = value[:256] if isinstance(value, str) else value
            result[str(key)[:64]] = rendered
        if len(result) >= 16:
            break
    return result


def _error_response(
    request: Request,
    code: TqCode | str,
    status_code: int,
    *,
    details: Mapping[str, Any] | None = None,
    message: str = "request failed",
    retryable: bool | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    if isinstance(code, TqCode):
        owned_retryable = TQ_ERROR_REGISTRY[code].retryable
    else:
        owned_retryable = False if retryable is None else retryable
    body = {
        "protocol_version": PROTOCOL_MAJOR,
        "request_id": _request_id(request),
        "error": {
            "code": code,
            "message": message[:200],
            "retryable": owned_retryable,
            "details": _safe_details(details),
        },
    }
    return JSONResponse(body, status_code=status_code, headers=dict(headers or {}))


def _command_response(
    request: Request,
    spec: HttpCommandSpec,
    outcome: str,
    data: BaseModel | Mapping[str, Any] | Sequence[Any] | None,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    if outcome not in spec.outcomes:
        raise TaskqInternalError(details={"reason": "unregistered_http_outcome"})
    if isinstance(data, BaseModel):
        encoded: Any = data.model_dump(mode="json", exclude_none=True)
    else:
        encoded = data if data is not None else {}
    encoded = to_jsonable_python(encoded)
    return JSONResponse(
        {
            "protocol_version": PROTOCOL_MAJOR,
            "request_id": _request_id(request),
            "outcome": outcome,
            "data": encoded,
        },
        status_code=spec.outcomes[outcome],
        headers=dict(headers or {}),
    )


def _internal_path(path: str) -> str:
    if not path.startswith("/taskq"):
        raise TaskqConfigError("HTTP metadata path must start with /taskq")
    return path.removeprefix("/taskq") or "/"


def _parse_bool(request: Request, name: str) -> bool:
    value = request.query_params.get(name)
    if value is None:
        return False
    if value.lower() in {"1", "true"}:
        return True
    if value.lower() in {"0", "false"}:
        return False
    raise TaskqValidationError(details={"field": name})


def _profile_etag(version: int) -> str:
    return f'"taskq-profile-{version}"'


def _parse_if_match(value: str | None) -> int | None:
    if value is None:
        return None
    match = _PROFILE_ETAG_RE.fullmatch(value)
    if match is None:
        raise TaskqValidationError(details={"header": "If-Match"})
    return int(match.group(1))


def _parse_page_limit(value: str | None) -> int:
    if value is None:
        return 50
    try:
        limit = int(value)
    except ValueError as exc:
        raise TaskqValidationError(details={"field": "limit"}, cause=exc) from exc
    if not 1 <= limit <= 100:
        raise TaskqValidationError(details={"field": "limit"})
    return limit


def _decode_cursor(value: str | None, *, queue: str, view: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if len(value) > 1366 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise TaskqValidationError(details={"field": "cursor"})
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskqValidationError(details={"field": "cursor"}, cause=exc) from exc
    if (
        len(raw) > 1024
        or not isinstance(decoded, dict)
        or decoded.get("v") != 1
        or decoded.get("queue") != queue
        or decoded.get("view") != view
    ):
        raise TaskqValidationError(details={"field": "cursor"})
    return decoded


def _encode_cursor(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    raw = json.dumps({"v": 1, **value}, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _path_uuid(request: Request, name: str = "job_id") -> UUID:
    try:
        return UUID(str(request.path_params[name]))
    except (TypeError, ValueError) as exc:
        raise TaskqValidationError(details={"field": name}, cause=exc) from exc


def _wire_job(job: Any) -> ClaimedJobWire:
    return ClaimedJobWire.model_validate(
        {**job.model_dump(mode="json"), "attempt_id": job.attempt_id}
    )


def _metrics_text(metrics: Sequence[Any]) -> str:
    lines: list[str] = []
    for metric in metrics:
        labels = ""
        if metric.labels:
            pairs = []
            for key, value in sorted(metric.labels.items()):
                escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                pairs.append(f'{key}="{escaped}"')
            labels = "{" + ",".join(pairs) + "}"
        lines.append(f"{metric.name}{labels} {metric.value}")
    return "\n".join(lines) + ("\n" if lines else "")


def _inline_model_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: Any) -> Any:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.rsplit("/", 1)[-1]
                return resolve(copy.deepcopy(definitions[name]))
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(schema)


class _FacadeDispatcher:
    def __init__(
        self,
        resources: TaskqFacadeTransports,
        *,
        authorizer: QueueAuthorizer,
        operator_transport: OperatorTransport | None,
        operator_authorizer: QueueAuthorizer | None,
        not_found_on_forbidden: bool,
        meta_public: bool,
        metrics_authorizer: QueueAuthorizer | None,
        poll_interval: float,
    ) -> None:
        self.resources = resources
        self.authorizer = authorizer
        self.operator_transport = operator_transport
        self.operator_authorizer = operator_authorizer
        self.not_found_on_forbidden = not_found_on_forbidden
        self.meta_public = meta_public
        self.metrics_authorizer = metrics_authorizer or authorizer
        self.poll_interval = poll_interval

    def _authorizer(self, name: HttpCommandName) -> QueueAuthorizer:
        if name is HttpCommandName.METRICS:
            return self.metrics_authorizer
        if name in _OPERATOR_COMMANDS:
            assert self.operator_authorizer is not None
            return self.operator_authorizer
        return self.authorizer

    async def handle(self, request: Request, name: HttpCommandName) -> Response:
        spec = HTTP_COMMAND_SPECS[name]
        public_meta = name is HttpCommandName.META and self.meta_public
        authorizer = self._authorizer(name)
        context = (
            AuthContext(actor="public-meta", principal="deployment-policy")
            if public_meta
            else await authenticate_request(authorizer, request)
        )

        self._validate_headers(request)
        body = await self._body(request, spec)
        self._validate_query(request, name)
        queue = await self._authorize(request, name, spec, body, context, authorizer, public_meta)

        if spec.surface is not HttpSurface.ACTIVE:
            raise TaskqCapabilityError(details={"capability": name.value})
        return await self._execute(request, name, spec, body, context, queue)

    def _validate_headers(self, request: Request) -> None:
        protocol = request.headers.get("Taskq-Protocol-Version")
        if protocol is not None and protocol != str(PROTOCOL_MAJOR):
            raise TaskqVersionError(details={"protocol": protocol[:32]})
        if request.state.taskq_invalid_request_id:
            raise TaskqValidationError(details={"header": "Taskq-Request-Id"})

    @staticmethod
    def _validate_query(request: Request, name: HttpCommandName) -> None:
        allowed = (
            {"include_error", "include_result", "include_progress", "include_payload"}
            if name is HttpCommandName.GET_JOB
            else {"queue", "view", "limit", "cursor"}
            if name is HttpCommandName.LIST_JOBS
            else set()
        )
        if set(request.query_params) - allowed or any(
            len(request.query_params.getlist(key)) != 1 for key in request.query_params
        ):
            raise TaskqValidationError(details={"reason": "unexpected_query_parameter"})

    async def _body(self, request: Request, spec: HttpCommandSpec) -> BaseModel | None:
        if request.method == "GET":
            if await request.body():
                raise TaskqValidationError(details={"reason": "body_not_allowed"})
            return None
        raw = await request.body()
        if len(raw) > _MAX_BODY_BYTES:
            raise TaskqValidationError(details={"reason": "request_body_too_large"})
        if not raw:
            value: Any = {}
        else:
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TaskqValidationError(details={"reason": "invalid_json"}, cause=exc) from exc
        if not isinstance(value, dict):
            raise TaskqValidationError(details={"reason": "body_must_be_object"})
        if spec.request_model is None:
            if value:
                raise TaskqValidationError(details={"reason": "body_not_allowed"})
            return None
        try:
            return spec.request_model.model_validate(value)
        except ValidationError as exc:
            raise TaskqValidationError(details={"reason": "invalid_command"}, cause=exc) from exc

    async def _authorize(
        self,
        request: Request,
        name: HttpCommandName,
        spec: HttpCommandSpec,
        body: BaseModel | None,
        context: AuthContext,
        authorizer: QueueAuthorizer,
        public_meta: bool,
    ) -> str | None:
        if public_meta or spec.action is None:
            if name is HttpCommandName.METRICS:
                await self._check_authorization(
                    authorizer, request, context, TaskqAction.READ, None
                )
            return None
        if spec.queue_source is QueueSource.PATH:
            queue = str(request.path_params["queue"])
            await self._check_authorization(authorizer, request, context, spec.action, queue)
            return queue
        if spec.queue_source is QueueSource.QUERY:
            queue = request.query_params.get("queue")
            if queue is None:
                raise TaskqValidationError(details={"field": "queue"})
            await self._check_authorization(authorizer, request, context, spec.action, queue)
            return queue
        if spec.queue_source is QueueSource.DECLARED_QUEUES:
            assert isinstance(body, WorkerPresenceWireRequest)
            for queue in body.queues:
                await self._check_authorization(authorizer, request, context, spec.action, queue)
            return None
        if spec.queue_source is QueueSource.JOB_LOOKUP:
            job_id = _path_uuid(request)
            projection = await self.resources.authorization.get_authorization_projection(job_id)
            if projection is None:
                raise TaskqNotFoundError()
            await self._check_authorization(
                authorizer,
                request,
                context,
                spec.action,
                projection.queue,
                hide_forbidden=self.not_found_on_forbidden,
            )
            return projection.queue
        await self._check_authorization(authorizer, request, context, spec.action, None)
        return None

    @staticmethod
    async def _check_authorization(
        authorizer: QueueAuthorizer,
        request: Request,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
        *,
        hide_forbidden: bool = False,
    ) -> None:
        try:
            await authorize_context(authorizer, request, context, action, queue)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            if hide_forbidden:
                raise TaskqNotFoundError() from None
            request.state.taskq_denied_queue = queue
            raise

    async def _claim(
        self,
        request: Request,
        queue: str,
        body: ClaimWireRequest,
    ) -> tuple[str, ClaimWireData]:
        if body.wait_seconds > 0:
            await self.resources.claim_wait_hub.prepare_queue(queue)
        deadline = time.monotonic() + body.wait_seconds
        while True:
            generation = self.resources.claim_wait_hub.generation
            result = await self.resources.runner.claim(
                queue,
                body.worker_id,
                batch=body.batch,
                job_types=body.job_types,
                lease_seconds=body.lease_seconds,
                affinity_key=body.affinity_key,
                job_id=body.job_id,
            )
            outcome = self._claim_outcome(result)
            if outcome != "empty" or body.wait_seconds == 0:
                return outcome, ClaimWireData(jobs=tuple(_wire_job(job) for job in result.jobs))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout", ClaimWireData(jobs=())

            async with await self.resources.claim_wait_hub.subscribe(generation) as subscription:
                # Immediate authoritative recheck closes notify-between-claim-and-subscribe.
                result = await self.resources.runner.claim(
                    queue,
                    body.worker_id,
                    batch=body.batch,
                    job_types=body.job_types,
                    lease_seconds=body.lease_seconds,
                    affinity_key=body.affinity_key,
                    job_id=body.job_id,
                )
                outcome = self._claim_outcome(result)
                if outcome != "empty":
                    return outcome, ClaimWireData(jobs=tuple(_wire_job(job) for job in result.jobs))
                if await request.is_disconnected():
                    raise TaskqUnavailableError(details={"reason": "claim_client_disconnected"})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout", ClaimWireData(jobs=())
                await subscription.wait(min(remaining, self.poll_interval))

    @staticmethod
    def _claim_outcome(result: ClaimResult) -> str:
        if result.state is ClaimState.UNKNOWN_QUEUE:
            raise TaskqNotFoundError()
        return result.state.value

    async def _execute(
        self,
        request: Request,
        name: HttpCommandName,
        spec: HttpCommandSpec,
        body: BaseModel | None,
        context: AuthContext,
        queue: str | None,
    ) -> Response:
        r = self.resources
        operator = self.operator_transport

        if name is HttpCommandName.META:
            return _command_response(request, spec, "ok", await r.observer.get_contract_meta())
        if name is HttpCommandName.ENQUEUE:
            assert queue is not None and isinstance(body, EnqueueWireRequest)
            command = EnqueueCommand(queue=queue, **body.model_dump())
            result = await r.producer.enqueue(command)
            return _command_response(
                request, spec, result.status.value, EnqueueWireData(job_id=result.job_id)
            )
        if name is HttpCommandName.ENQUEUE_MANY:
            assert queue is not None and isinstance(body, EnqueueManyWireRequest)
            results = await r.producer.enqueue_many(queue, body.items)
            data = {
                "items": [
                    {
                        "input_index": index,
                        "job_id": result.job_id,
                        "outcome": result.status,
                    }
                    for index, result in enumerate(results, 1)
                ]
            }
            return _command_response(request, spec, "ok", data)
        if name is HttpCommandName.CLAIM:
            assert queue is not None and isinstance(body, ClaimWireRequest)
            outcome, data = await self._claim(request, queue, body)
            return _command_response(request, spec, outcome, data)
        if name is HttpCommandName.HEARTBEAT:
            assert isinstance(body, HeartbeatWireRequest)
            result = await r.runner.heartbeat(
                _path_uuid(request),
                body.attempt_id,
                body.worker_id,
                lease_seconds=body.lease_seconds,
                progress=body.progress,
                stats=body.stats,
            )
            outcome = "ok" if result.ok else "lost"
            return _command_response(
                request,
                spec,
                outcome,
                {
                    "cancel_requested": result.cancel_requested,
                    "lease_expires_at": result.lease_expires_at,
                },
            )
        if name in {
            HttpCommandName.COMPLETE,
            HttpCommandName.FAIL,
            HttpCommandName.RELEASE,
            HttpCommandName.SNOOZE,
            HttpCommandName.CANCEL_RUNNING,
        }:
            result = await self._settle(name, _path_uuid(request), body)
            return _command_response(
                request,
                spec,
                result.result.value,
                {
                    "job_status": result.job_status,
                    "scheduled_at": result.scheduled_at,
                },
            )
        if name is HttpCommandName.WORKER_HEARTBEAT:
            assert isinstance(body, WorkerPresenceWireRequest)
            shutdown = await r.runner.worker_heartbeat(
                body.worker_id,
                body.queues,
                hostname=body.hostname,
                pid=body.pid,
                version=body.version,
                meta=body.meta,
            )
            outcome = "shutdown_requested" if shutdown else "continue"
            return _command_response(request, spec, outcome, {"shutdown_requested": shutdown})
        if name is HttpCommandName.GET_JOB:
            job_id = _path_uuid(request)
            result = await r.observer.get_job(
                job_id,
                include_error=_parse_bool(request, "include_error"),
                include_result=_parse_bool(request, "include_result"),
                include_progress=_parse_bool(request, "include_progress"),
                include_payload=_parse_bool(request, "include_payload"),
            )
            if result is None:
                raise TaskqNotFoundError()
            return _command_response(request, spec, "ok", result)
        if name is HttpCommandName.GET_QUEUE:
            assert queue is not None
            profile = await r.observer.get_queue_profile(queue)
            if profile is None:
                raise TaskqNotFoundError()
            return _command_response(
                request,
                spec,
                "ok",
                profile,
                headers={"ETag": _profile_etag(profile.profile_version)},
            )
        if name is HttpCommandName.LIST_JOBS:
            assert queue is not None
            view = request.query_params.get("view")
            if view not in {"ready", "running", "finished"}:
                raise TaskqValidationError(details={"field": "view"})
            limit = _parse_page_limit(request.query_params.get("limit"))
            after = _decode_cursor(request.query_params.get("cursor"), queue=queue, view=view)
            try:
                page = await r.observer.list_jobs(queue, view, limit=limit, after=after)
            except TaskqCapabilityError as exc:
                raise TaskqCapabilityError(
                    details={"reason": "read_model_view_inactive", "view": view}, cause=exc
                ) from exc
            return _command_response(
                request,
                spec,
                "ok",
                {
                    "as_of": page.as_of,
                    "items": page.items,
                    "next_cursor": _encode_cursor(page.next_after),
                },
            )
        if name in {HttpCommandName.GET_QUEUE_STATS, HttpCommandName.LIST_QUEUE_STATS}:
            # Empty is the honest snapshot-lag/unknown configuration posture in 0.1.
            items = await r.observer.get_queue_stats(queue)
            return _command_response(request, spec, "ok", {"items": items})
        if name is HttpCommandName.METRICS:
            return PlainTextResponse(_metrics_text(await r.observer.metrics()))

        assert operator is not None
        return await self._execute_operator(request, name, spec, body, context, operator)

    async def _settle(self, name: HttpCommandName, job_id: UUID, body: BaseModel | None) -> Any:
        runner = self.resources.runner
        if name is HttpCommandName.COMPLETE:
            assert isinstance(body, CompleteWireRequest)
            return await runner.complete(
                job_id,
                body.attempt_id,
                body.worker_id,
                result=body.result,
                stats=body.stats,
                followups=body.followups,
            )
        if name is HttpCommandName.FAIL:
            assert isinstance(body, FailWireRequest)
            return await runner.fail(
                job_id,
                body.attempt_id,
                body.worker_id,
                body.error,
                retryable=body.retryable,
                retry_after_seconds=body.retry_after_seconds,
                progress=body.progress,
                stats=body.stats,
            )
        if name is HttpCommandName.RELEASE:
            assert isinstance(body, ReleaseWireRequest)
            return await runner.release(
                job_id,
                body.attempt_id,
                body.worker_id,
                body.cause,
                delay_seconds=body.delay_seconds,
                progress=body.progress,
            )
        if name is HttpCommandName.SNOOZE:
            assert isinstance(body, SnoozeWireRequest)
            return await runner.snooze(
                job_id,
                body.attempt_id,
                body.worker_id,
                body.delay_seconds,
                reason=body.reason,
                progress=body.progress,
            )
        assert isinstance(body, CancelRunningWireRequest)
        return await runner.cancel_running(job_id, body.attempt_id, body.worker_id, body.reason)

    async def _execute_operator(
        self,
        request: Request,
        name: HttpCommandName,
        spec: HttpCommandSpec,
        body: BaseModel | None,
        context: AuthContext,
        operator: OperatorTransport,
    ) -> Response:
        path = request.path_params
        actor = context.actor
        if name is HttpCommandName.ENSURE_QUEUE:
            assert isinstance(body, EnsureQueueWireRequest)
            expected_version = _parse_if_match(request.headers.get("If-Match"))
            if expected_version is None:
                result = await operator.ensure_queue(str(path["queue"]), body.profile, actor)
                profile = QueueProfile.model_validate(result.profile)
                return _command_response(
                    request,
                    spec,
                    result.result.value,
                    {"profile": profile},
                    headers={"ETag": _profile_etag(profile.profile_version)},
                )
            result, profile, current_version = await operator.update_queue_profile(
                str(path["queue"]), body.profile, actor, expected_version
            )
            if result == "missing":
                raise TaskqNotFoundError()
            if result == "profile_version_conflict":
                assert current_version is not None
                raise TaskqConflictError(
                    details={
                        "reason": "profile_version_conflict",
                        "current_version": current_version,
                    }
                )
            assert profile is not None
            return _command_response(
                request,
                spec,
                result,
                {"profile": profile},
                headers={"ETag": _profile_etag(profile.profile_version)},
            )
        if name is HttpCommandName.PAUSE_QUEUE:
            assert isinstance(body, ReasonWireRequest)
            result = await operator.pause_queue(str(path["queue"]), actor, body.reason)
            return _command_response(request, spec, result.value, {})
        if name is HttpCommandName.RESUME_QUEUE:
            result = await operator.resume_queue(str(path["queue"]), actor)
            return _command_response(request, spec, result.value, {})
        if name is HttpCommandName.CANCEL:
            assert isinstance(body, ReasonWireRequest)
            result = await operator.cancel(_path_uuid(request), actor, body.reason)
            return _command_response(
                request, spec, result.result.value, {"job_status": result.job_status}
            )
        if name is HttpCommandName.REDRIVE:
            assert isinstance(body, RedriveWireRequest)
            redriven = await operator.redrive(_path_uuid(request), actor, body.reset_progress)
            if not redriven:
                raise TaskqNotFoundError()
            return _command_response(request, spec, "redriven", {})
        if name is HttpCommandName.EXPIRE_JOB:
            result = await operator.expire_job(_path_uuid(request), actor)
            return _command_response(request, spec, result.value, {})
        if name is HttpCommandName.EXPIRE_WORKER_LEASES:
            result = await operator.expire_worker_leases(str(path["worker_id"]), actor)
            return _command_response(request, spec, "ok", result)
        if name is HttpCommandName.PURGE_QUEUED:
            assert isinstance(body, PurgeWireRequest)
            count = await operator.purge_queued(str(path["queue"]), body.limit, actor, body.reason)
            return _command_response(request, spec, "ok", {"count": count})
        if name is HttpCommandName.RUN_NOW:
            result = await operator.run_now(_path_uuid(request), actor)
            return _command_response(request, spec, result.value, {})
        if name is HttpCommandName.REPRIORITIZE:
            assert isinstance(body, ReprioritizeWireRequest)
            result = await operator.reprioritize(_path_uuid(request), body.priority, actor)
            return _command_response(request, spec, result.value, {})
        if name is HttpCommandName.SET_CONCURRENCY_LIMIT:
            assert isinstance(body, ConcurrencyLimitWireRequest)
            result = await operator.set_concurrency_limit(str(path["key"]), body.max_running, actor)
            return _command_response(request, spec, result.value, {})
        assert name is HttpCommandName.REQUEST_WORKER_SHUTDOWN
        assert isinstance(body, ShutdownRequestWireRequest)
        count = await operator.request_worker_shutdown(
            worker_id=body.worker_id, queue=body.queue, actor=actor
        )
        return _command_response(request, spec, "accepted", {"count": count})


def _openapi_extra(spec: HttpCommandSpec) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "x-taskq-action": spec.action.value if spec.action is not None else None,
        "x-taskq-queue-source": spec.queue_source.value,
        "x-taskq-retry-class": spec.retry_class.value,
        "parameters": [
            {
                "in": "header",
                "name": "Taskq-Protocol-Version",
                "required": False,
                "schema": {"type": "string"},
            },
            {
                "in": "header",
                "name": "Taskq-Request-Id",
                "required": False,
                "schema": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9._:-]+$",
                },
            },
        ],
    }
    if spec.request_model is not None:
        extra["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": _inline_model_schema(spec.request_model)}},
        }
    responses: dict[str, Any] = {}
    if spec.enveloped:
        by_status: dict[int, list[str]] = {}
        for outcome, status in spec.outcomes.items():
            by_status.setdefault(status, []).append(outcome)
        for status, outcomes in by_status.items():
            data_schema = (
                _inline_model_schema(spec.data_model)
                if spec.data_model is not None
                else {"type": "object", "additionalProperties": True}
            )
            responses[str(status)] = {
                "description": "Taskq command result",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "protocol_version",
                                "request_id",
                                "outcome",
                                "data",
                            ],
                            "properties": {
                                "protocol_version": {"const": PROTOCOL_MAJOR},
                                "request_id": {"type": "string"},
                                "outcome": {"enum": sorted(outcomes)},
                                "data": data_schema,
                            },
                        }
                    }
                },
            }
        error_statuses = {TQ_ERROR_REGISTRY[code].http_status for code in spec.errors} | (
            {401, 403} if spec.action is not None else set()
        )
        for status in error_statuses:
            responses.setdefault(
                str(status),
                {
                    "description": "Taskq error envelope",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["protocol_version", "request_id", "error"],
                                "properties": {
                                    "protocol_version": {"const": PROTOCOL_MAJOR},
                                    "request_id": {"type": "string"},
                                    "error": {"type": "object"},
                                },
                            }
                        }
                    },
                },
            )
    else:
        responses["200"] = {
            "description": "Prometheus text exposition",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        }
    extra["responses"] = responses
    return extra


def create_taskq_app(
    resources: TaskqFacadeTransports | object,
    *,
    authorizer: QueueAuthorizer,
    operator_transport: OperatorTransport | None = None,
    operator_authorizer: QueueAuthorizer | None = None,
    not_found_on_forbidden: bool = False,
    meta_public: bool = False,
    metrics_authorizer: QueueAuthorizer | None = None,
    poll_interval: float = 1.0,
) -> FastAPI:
    """Construct a lifespan-free sub-application without starting any resource."""

    if authorizer is None:
        raise TaskqConfigError("authorizer is required")
    for candidate in (authorizer, operator_authorizer, metrics_authorizer):
        if candidate is None:
            continue
        if not callable(getattr(candidate, "authenticate", None)) or not callable(
            getattr(candidate, "authorize_context", None)
        ):
            raise TaskqConfigError(
                "authorizers must implement separate authenticate and authorize_context phases"
            )
    if (operator_transport is None) is not (operator_authorizer is None):
        raise TaskqConfigError(
            "operator_transport and operator_authorizer must be configured together"
        )
    if poll_interval <= 0 or poll_interval > 30:
        raise TaskqConfigError("poll_interval must be greater than zero and at most 30 seconds")

    resources = getattr(resources, "facade_transports", resources)
    if not isinstance(resources, TaskqFacadeTransports):
        raise TaskqConfigError("facade resources are not configured")

    app = FastAPI(lifespan=None, openapi_url="/openapi.json", docs_url=None, redoc_url=None)
    dispatcher = _FacadeDispatcher(
        resources,
        authorizer=authorizer,
        operator_transport=operator_transport,
        operator_authorizer=operator_authorizer,
        not_found_on_forbidden=not_found_on_forbidden,
        meta_public=meta_public,
        metrics_authorizer=metrics_authorizer,
        poll_interval=poll_interval,
    )

    @app.middleware("http")
    async def protocol_boundary(request: Request, call_next: Any) -> Response:
        request_id, invalid = _selected_request_id(request)
        request.state.taskq_request_id = request_id
        request.state.taskq_invalid_request_id = invalid
        try:
            response = await call_next(request)
        except Exception as exc:
            handler = app.exception_handlers.get(type(exc)) or app.exception_handlers[Exception]
            response = await handler(request, exc)
        response.headers["Taskq-Protocol-Version"] = str(PROTOCOL_MAJOR)
        response.headers["Taskq-Request-Id"] = request_id
        return response

    @app.exception_handler(TaskqError)
    async def taskq_error(request: Request, exc: TaskqError) -> JSONResponse:
        return _error_response(
            request,
            exc.code,
            TQ_ERROR_REGISTRY[exc.code].http_status,
            details=exc.details,
            message=TQ_ERROR_REGISTRY[exc.code].category,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 401:
            return _error_response(request, "AUTH401", 401, message="authentication failed")
        if exc.status_code == 403:
            queue = getattr(request.state, "taskq_denied_queue", None)
            return _error_response(
                request,
                "AUTH403",
                403,
                message="authorization failed",
                details={"queue": queue} if queue is not None else None,
            )
        if exc.status_code in {429, 503}:
            detail = exc.detail if isinstance(exc.detail, Mapping) else {}
            reason = str(detail.get("reason") or "auth_dependency_failure")
            retry_after = (exc.headers or {}).get("Retry-After")
            headers = {"Retry-After": retry_after} if retry_after else None
            code = TqCode.BACKPRESSURE if exc.status_code == 429 else TqCode.UNAVAILABLE
            return _error_response(
                request,
                code,
                exc.status_code,
                message=(
                    "authorization rate limited"
                    if exc.status_code == 429
                    else "authorization dependency unavailable"
                ),
                details={"reason": reason},
                headers=headers,
            )
        return _error_response(request, TqCode.VALIDATION, 422)

    @app.exception_handler(StarletteHTTPException)
    async def starlette_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(request, TqCode.NOT_FOUND, 404, message="resource not found")
        return _error_response(request, TqCode.VALIDATION, 422, message="invalid route or method")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(request, TqCode.VALIDATION, 422, message="invalid request")

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, exc: Exception) -> JSONResponse:
        return _error_response(request, TqCode.INTERNAL, 500, message="internal taskq failure")

    for name, spec in HTTP_COMMAND_SPECS.items():
        if name in _OPERATOR_COMMANDS and operator_transport is None:
            continue

        async def generated_endpoint(request: Request, _name: HttpCommandName = name) -> Response:
            return await dispatcher.handle(request, _name)

        generated_endpoint.__name__ = f"taskq_{name.value}"
        generated_endpoint.__doc__ = f"Generated Protocol-v1 command: {name.value}."
        app.add_api_route(
            _internal_path(spec.path),
            generated_endpoint,
            methods=[spec.method],
            name=name.value,
            operation_id=f"taskq_{name.value}",
            include_in_schema=spec.surface is not HttpSurface.DEFERRED,
            openapi_extra=_openapi_extra(spec),
        )

    original_openapi = app.openapi

    def taskq_openapi() -> dict[str, Any]:
        schema = original_openapi()
        schema["servers"] = [{"url": "/taskq"}]
        return schema

    app.openapi = taskq_openapi
    return app


def merge_taskq_openapi(
    host_schema: Mapping[str, Any], taskq_schema: Mapping[str, Any], *, mount: str = "/taskq"
) -> dict[str, Any]:
    """Return a deterministic host schema copy with Taskq paths explicitly prefixed."""

    merged = copy.deepcopy(dict(host_schema))
    paths = merged.setdefault("paths", {})
    for path, operation in sorted(taskq_schema.get("paths", {}).items()):
        target = mount.rstrip("/") + path
        if target in paths:
            raise TaskqConfigError(f"OpenAPI path collision: {target}")
        paths[target] = copy.deepcopy(operation)
    components = merged.setdefault("components", {})
    for section, values in taskq_schema.get("components", {}).items():
        target = components.setdefault(section, {})
        for name, value in values.items():
            if name in target and target[name] != value:
                raise TaskqConfigError(f"OpenAPI component collision: {section}.{name}")
            target[name] = copy.deepcopy(value)
    return merged


__all__ = ["TaskqFacadeTransports", "create_taskq_app", "merge_taskq_openapi"]
