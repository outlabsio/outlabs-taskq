"""Optional OutLabs authorization adapter and explicit IAM provisioning.

Importing this module performs no I/O and never initializes or mutates an
OutlabsAuth installation. Install ``outlabs-taskq[outlabs]`` to use it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import inspect
import re
from types import MappingProxyType
from typing import Any, Literal

from fastapi import HTTPException, Request

try:
    from outlabs_auth import PermissionSeed, seed_system_records
    from outlabs_auth.utils.validation import validate_permission_name
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "taskq.http.outlabs requires the OutLabs extra: install 'outlabs-taskq[outlabs]'"
    ) from None

from taskq.http.deps import AuthContext
from taskq.protocol import TaskqAction

_QUEUE_RE = re.compile(r"^[a-z0-9_]{1,57}$")
_ACTIONS = tuple(action.value for action in TaskqAction)
_ACTOR_BYTES = 200
_ROLE_DESCRIPTION = "Managed explicitly by outlabs-taskq provisioning."


@dataclass(frozen=True, repr=False)
class _OutlabsPrincipal:
    value: Mapping[str, Any]
    fingerprint: tuple[str, str]

    def __repr__(self) -> str:
        return f"_OutlabsPrincipal(source={self.fingerprint[0]!r}, subject=<redacted>)"


@dataclass(frozen=True)
class ProvisioningReport:
    """Deterministic, secret-free IAM provisioning diff."""

    mode: Literal["report", "apply"]
    created: tuple[str, ...] = ()
    existing: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    conflicting: tuple[str, ...] = ()
    policy_notes: tuple[str, ...] = (
        "API keys must enumerate exact scopes; wildcard scopes are not supported.",
        "System API keys using enqueue/admin must allow those action prefixes in host config.",
        "SimpleRBAC worker denial is a host role/grant invariant; prefer service tokens.",
    )

    @property
    def ok(self) -> bool:
        return not self.conflicting


@dataclass(frozen=True)
class EnsureQueueAuthResult:
    """Honest result for the non-atomic SQL-queue plus IAM composition."""

    queue_result: Any
    auth_report: ProvisioningReport | None
    auth_error: str | None = None


@dataclass(frozen=True)
class _RoleSpec:
    name: str
    display_name: str
    permissions: tuple[str, ...]


def _canonical_queue(queue: str) -> str:
    if not isinstance(queue, str) or _QUEUE_RE.fullmatch(queue) is None:
        raise ValueError("queue must match [a-z0-9_]{1,57}; values are rejected, not normalized")
    return queue


def _validated_permission(name: str) -> str:
    validated = validate_permission_name(name)
    if validated != name:
        raise ValueError("permission names must already be canonical")
    return validated


def taskq_permission_catalog(queues: Sequence[str]) -> tuple[PermissionSeed, ...]:
    """Build the deterministic five-global plus five-per-queue catalog."""

    canonical_queues = tuple(sorted({_canonical_queue(queue) for queue in queues}))
    names = {f"taskq:{action}" for action in _ACTIONS}
    names.update(f"taskq_{queue}:{action}" for queue in canonical_queues for action in _ACTIONS)
    return tuple(
        PermissionSeed(
            name=_validated_permission(name),
            display_name="Taskq " + name.replace("taskq_", "").replace(":", " ").title(),
            description=f"Taskq facade permission {name}.",
        )
        for name in sorted(names)
    )


def _bounded_actor(value: str) -> str:
    raw = value.strip()
    if not raw:
        raw = "unknown"
    encoded = raw.encode("utf-8")
    if len(encoded) <= _ACTOR_BYTES:
        return raw
    suffix = hashlib.sha256(encoded).hexdigest()[:16]
    prefix = encoded[: _ACTOR_BYTES - len(suffix) - 1].decode("utf-8", "ignore")
    return f"{prefix}:{suffix}"


def _principal_fingerprint(value: Mapping[str, Any]) -> tuple[str, str]:
    source = str(value.get("source") or "unknown")
    if source == "service_token":
        subject = value.get("service_id")
    elif value.get("integration_principal_id"):
        subject = value.get("integration_principal_id")
    else:
        subject = value.get("user_id")
    if subject is None:
        api_key = value.get("api_key")
        subject = getattr(api_key, "id", None) or source
    return source, str(subject)


def _default_actor(value: Mapping[str, Any]) -> str:
    source = str(value.get("source") or "unknown")
    if source == "service_token":
        return _bounded_actor(str(value.get("service_id") or "service"))
    if source == "api_key":
        api_key = value.get("api_key")
        name = getattr(api_key, "name", None)
        principal = value.get("integration_principal")
        name = name or getattr(principal, "name", None) or value.get("integration_principal_id")
        return _bounded_actor(str(name or "api-key"))
    user = value.get("user")
    email = getattr(user, "email", None)
    return _bounded_actor(f"operator:{email or value.get('user_id') or source}")


def _call_provider(provider: Callable[..., Any], request: Request) -> Any:
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return provider(request)
    try:
        signature.bind(request)
    except TypeError:
        return provider()
    return provider(request)


async def _close_session(session: Any) -> None:
    close = getattr(session, "aclose", None) or getattr(session, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result


@asynccontextmanager
async def _resolved_session(
    provider: Callable[..., Any], request: Request
) -> AsyncGenerator[Any, None]:
    value = _call_provider(provider, request)
    if inspect.isawaitable(value):
        value = await value
    if hasattr(value, "__aenter__") and hasattr(value, "__aexit__"):
        async with value as session:
            yield session
        return
    if inspect.isasyncgen(value):
        try:
            session = await anext(value)
        except StopAsyncIteration as exc:
            raise RuntimeError("OutLabs session dependency yielded no session") from exc
        try:
            yield session
        finally:
            await value.aclose()
        return
    try:
        yield value
    finally:
        await _close_session(value)


class OutlabsQueueAuthorizer:
    """QueueAuthorizer backed by OutlabsAuth's supported dependency surface."""

    def __init__(
        self,
        *,
        auth: Any,
        session_dependency: Callable[..., Any],
        actor_from_principal: Callable[[Mapping[str, Any]], str] | None = None,
        extra_candidates: Mapping[TaskqAction | str, Sequence[str]] | None = None,
    ) -> None:
        deps = getattr(auth, "deps", None)
        if deps is None:
            raise TypeError("auth must be initialized and expose its public deps API")
        self._auth = auth
        self._session_dependency = session_dependency
        self._actor_from_principal = actor_from_principal or _default_actor
        self._extra_candidates: dict[str, tuple[str, ...]] = {}
        for action, names in (extra_candidates or {}).items():
            key = action.value if isinstance(action, TaskqAction) else str(action)
            if key not in _ACTIONS:
                raise ValueError(f"unknown taskq action: {key}")
            self._extra_candidates[key] = tuple(_validated_permission(name) for name in names)
        # Hosts mount the facade before their lifespan initializes OutlabsAuth.
        # Building the dependency here would freeze the pre-initialization service
        # graph (including an unavailable Redis-backed API-key service). Bind it
        # lazily on the first served request, after application startup completes.
        self._authenticate_checker: Callable[..., Any] | None = None
        self._authenticate_checker_lock = asyncio.Lock()
        self._checkers: dict[tuple[str, ...], Callable[..., Awaitable[Mapping[str, Any]]]] = {}
        self._checker_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            "OutlabsQueueAuthorizer("
            f"cached_checkers={len(self._checkers)}, legacy_actions={tuple(sorted(self._extra_candidates))})"
        )

    async def _auth_checker(self) -> Callable[..., Any]:
        checker = self._authenticate_checker
        if checker is not None:
            return checker
        async with self._authenticate_checker_lock:
            checker = self._authenticate_checker
            if checker is None:
                checker = self._auth.deps.require_auth()
                if "session" not in inspect.signature(checker).parameters:
                    raise TypeError("OutLabs auth dependencies must expose the session parameter")
                self._authenticate_checker = checker
        return checker

    async def _checker(self, names: tuple[str, ...]) -> Callable[..., Any]:
        checker = self._checkers.get(names)
        if checker is not None:
            return checker
        async with self._checker_lock:
            checker = self._checkers.get(names)
            if checker is None:
                checker = self._auth.deps.require_permission(*names, require_all=False)
                if "session" not in inspect.signature(checker).parameters:
                    raise TypeError(
                        "OutLabs permission dependency must expose the session parameter"
                    )
                self._checkers[names] = checker
        return checker

    def _names(self, action: TaskqAction, queue: str | None) -> tuple[str, ...]:
        action_name = action.value
        generated = (
            (f"taskq_{_canonical_queue(queue)}:{action_name}", f"taskq:{action_name}")
            if queue is not None
            else (f"taskq:{action_name}",)
        )
        return tuple(
            dict.fromkeys(
                _validated_permission(name)
                for name in (*generated, *self._extra_candidates.get(action_name, ()))
            )
        )

    @staticmethod
    def _normalize_http_exception(exc: HTTPException) -> HTTPException:
        headers = None
        retry_after = (exc.headers or {}).get("Retry-After")
        if retry_after and retry_after.isdigit():
            headers = {"Retry-After": retry_after}
        if exc.status_code == 429:
            return HTTPException(
                status_code=429,
                detail={"reason": "auth_rate_limited"},
                headers=headers,
            )
        if exc.status_code == 503:
            return HTTPException(
                status_code=503,
                detail={"reason": "auth_infrastructure_unavailable"},
                headers=headers,
            )
        if exc.status_code in {401, 403}:
            return HTTPException(status_code=exc.status_code, detail="authorization failed")
        return HTTPException(status_code=503, detail={"reason": "auth_dependency_failure"})

    def _context(self, result: Mapping[str, Any]) -> AuthContext:
        copied = MappingProxyType(dict(result))
        actor = _bounded_actor(self._actor_from_principal(copied))
        return AuthContext(
            actor=actor,
            principal=_OutlabsPrincipal(copied, _principal_fingerprint(copied)),
        )

    async def authenticate(self, request: Request) -> AuthContext:
        checker = await self._auth_checker()
        try:
            async with _resolved_session(self._session_dependency, request) as session:
                result = await checker(request=request, session=session)
        except HTTPException as exc:
            raise self._normalize_http_exception(exc) from None
        if not isinstance(result, Mapping):
            raise HTTPException(status_code=503, detail={"reason": "auth_dependency_failure"})
        return self._context(result)

    async def authorize_context(
        self,
        request: Request,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        if not isinstance(context.principal, _OutlabsPrincipal):
            raise HTTPException(status_code=401, detail="authentication required")
        checker = await self._checker(self._names(action, queue))
        try:
            async with _resolved_session(self._session_dependency, request) as session:
                result = await checker(request=request, session=session)
        except HTTPException as exc:
            raise self._normalize_http_exception(exc) from None
        if not isinstance(result, Mapping):
            raise HTTPException(status_code=503, detail={"reason": "auth_dependency_failure"})
        if _principal_fingerprint(result) != context.principal.fingerprint:
            raise HTTPException(status_code=401, detail="authentication changed during request")

    async def authorize(
        self, request: Request, action: TaskqAction, queue: str | None
    ) -> AuthContext:
        context = await self.authenticate(request)
        await self.authorize_context(request, context, action, queue)
        return context


def _role_specs(
    queues: Sequence[str], *, prefix: str, per_queue_roles: bool
) -> tuple[_RoleSpec, ...]:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", prefix):
        raise ValueError("role_prefix must contain lowercase letters, numbers, or hyphens")
    specs = [
        _RoleSpec(f"{prefix}producer", "Taskq Producer", ("taskq:enqueue", "taskq:read")),
        _RoleSpec(f"{prefix}worker", "Taskq Worker", ("taskq:read", "taskq:run")),
        _RoleSpec(f"{prefix}operator", "Taskq Operator", ("taskq:control", "taskq:read")),
        _RoleSpec(
            f"{prefix}admin",
            "Taskq Admin",
            tuple(f"taskq:{action}" for action in _ACTIONS),
        ),
    ]
    if per_queue_roles:
        specs.extend(
            _RoleSpec(
                f"{prefix}worker-{queue}",
                f"Taskq {queue} Worker",
                (f"taskq_{queue}:read", f"taskq_{queue}:run"),
            )
            for queue in sorted({_canonical_queue(queue) for queue in queues})
        )
    return tuple(sorted(specs, key=lambda item: item.name))


async def _loaded_role(role_service: Any, session: Any, name: str) -> Any:
    role = await role_service.get_role_by_name(session, name)
    if role is None:
        return None
    loader = getattr(role_service, "get_role_by_id", None)
    if loader is not None:
        role = await loader(session, role.id, load_permissions=True)
    return role


def _permission_names(role: Any) -> tuple[str, ...]:
    return tuple(sorted(permission.name for permission in (getattr(role, "permissions", ()) or ())))


def _role_status(role: Any) -> str:
    status = getattr(role, "status", "active")
    return str(getattr(status, "value", status))


async def provision_taskq_auth(
    auth: Any,
    session: Any,
    *,
    queues: Sequence[str],
    roles: Literal["standard"] | None = "standard",
    role_prefix: str = "taskq-",
    mode: Literal["report", "apply"] = "report",
    reconcile: bool = False,
    per_queue_roles: bool = False,
) -> ProvisioningReport:
    """Report or apply taskq IAM rows without owning the caller transaction."""

    if mode not in {"report", "apply"}:
        raise ValueError("mode must be report or apply")
    if roles not in {None, "standard"}:
        raise ValueError("roles must be None or standard")
    permission_service = getattr(auth, "permission_service", None)
    role_service = getattr(auth, "role_service", None)
    if permission_service is None or (roles is not None and role_service is None):
        raise TypeError("initialized auth must expose public permission and role services")

    catalog = taskq_permission_catalog(queues)
    role_specs = _role_specs(queues, prefix=role_prefix, per_queue_roles=per_queue_roles)
    created: list[str] = []
    existing: list[str] = []
    changed: list[str] = []
    conflicting: list[str] = []

    for permission in catalog:
        marker = f"permission:{permission.name}"
        if await permission_service.get_permission_by_name(session, permission.name) is None:
            created.append(marker)
        else:
            existing.append(marker)

    role_state: dict[str, Any] = {}
    for spec in role_specs:
        marker = f"role:{spec.name}"
        role = await _loaded_role(role_service, session, spec.name)
        role_state[spec.name] = role
        if role is None:
            created.append(marker)
            continue
        exact = (
            not bool(getattr(role, "is_system_role", False))
            and bool(getattr(role, "is_global", False))
            and getattr(role, "display_name", spec.display_name) == spec.display_name
            and getattr(role, "description", _ROLE_DESCRIPTION) == _ROLE_DESCRIPTION
            and _role_status(role) == "active"
            and _permission_names(role) == tuple(sorted(spec.permissions))
        )
        if exact:
            existing.append(marker)
        elif reconcile and not bool(getattr(role, "is_system_role", False)):
            changed.append(marker)
        else:
            conflicting.append(marker)

    report = ProvisioningReport(
        mode=mode,
        created=tuple(sorted(created)),
        existing=tuple(sorted(existing)),
        changed=tuple(sorted(changed)),
        conflicting=tuple(sorted(conflicting)),
    )
    if mode == "report" or report.conflicting:
        return report

    async with session.begin_nested():
        await seed_system_records(
            session,
            permission_service=permission_service,
            include_permissions=True,
            include_config=False,
            permission_catalog=catalog,
        )
        for spec in role_specs:
            role = role_state[spec.name]
            if role is None:
                await role_service.create_role(
                    session,
                    name=spec.name,
                    display_name=spec.display_name,
                    description=_ROLE_DESCRIPTION,
                    permission_names=list(spec.permissions),
                    is_global=True,
                    is_system_role=False,
                )
            elif f"role:{spec.name}" in report.changed:
                await role_service.update_role(
                    session,
                    role.id,
                    display_name=spec.display_name,
                    description=_ROLE_DESCRIPTION,
                    is_global=True,
                    status="active",
                    permission_names=list(spec.permissions),
                    update_permissions=True,
                )
    return report


async def ensure_queue(
    operator: Any,
    queue: str,
    *,
    profile: Mapping[str, Any] | None = None,
    actor: str = "taskq-bootstrap",
    provision_auth: bool = False,
    auth: Any | None = None,
    session: Any | None = None,
    **provision_options: Any,
) -> EnsureQueueAuthResult:
    """Ensure SQL queue configuration and optionally provision IAM explicitly."""

    canonical = _canonical_queue(queue)
    queue_result = await operator.ensure_queue(canonical, dict(profile or {}), actor)
    if not provision_auth:
        return EnsureQueueAuthResult(queue_result=queue_result, auth_report=None)
    if auth is None or session is None:
        return EnsureQueueAuthResult(
            queue_result=queue_result,
            auth_report=None,
            auth_error="TaskqAuthConfigurationError",
        )
    try:
        auth_report = await provision_taskq_auth(
            auth, session, queues=(canonical,), **provision_options
        )
    except Exception as exc:
        return EnsureQueueAuthResult(
            queue_result=queue_result,
            auth_report=None,
            auth_error=type(exc).__name__,
        )
    return EnsureQueueAuthResult(queue_result=queue_result, auth_report=auth_report)


async def ensure_queue_with_auth(
    operator: Any,
    auth: Any,
    session: Any,
    queue: str,
    *,
    profile: Mapping[str, Any] | None = None,
    actor: str = "taskq-bootstrap",
    **provision_options: Any,
) -> EnsureQueueAuthResult:
    """Explicit convenience spelling for ``ensure_queue(provision_auth=True)``."""

    return await ensure_queue(
        operator,
        queue,
        profile=profile,
        actor=actor,
        provision_auth=True,
        auth=auth,
        session=session,
        **provision_options,
    )


__all__ = [
    "EnsureQueueAuthResult",
    "OutlabsQueueAuthorizer",
    "ProvisioningReport",
    "ensure_queue",
    "ensure_queue_with_auth",
    "provision_taskq_auth",
    "taskq_permission_catalog",
]
