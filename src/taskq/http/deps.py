"""Generic queue authorization adapters with no OutLabs dependency."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from inspect import isawaitable
import secrets
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr

from taskq.protocol import TaskqAction


class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    actor: str
    principal: object


@runtime_checkable
class QueueAuthorizer(Protocol):
    async def authenticate(self, request: Request) -> AuthContext: ...

    async def authorize_context(
        self,
        request: Request,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None: ...

    async def authorize(
        self, request: Request, action: TaskqAction, queue: str | None
    ) -> AuthContext: ...


class _QueueBlindAuthorizer:
    async def authorize_context(
        self,
        request: Request,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        return None

    async def authorize(
        self, request: Request, action: TaskqAction, queue: str | None
    ) -> AuthContext:
        context = await self.authenticate(request)
        await self.authorize_context(request, context, action, queue)
        return context


class StaticApiKeyAuthorizer(_QueueBlindAuthorizer):
    def __init__(
        self, value: str | SecretStr, *, header: str = "X-API-Key", actor: str = "static-key"
    ):
        self._value = value if isinstance(value, SecretStr) else SecretStr(value)
        self._header = header
        self._actor = actor

    def __repr__(self) -> str:
        return f"StaticApiKeyAuthorizer(header={self._header!r}, actor={self._actor!r})"

    async def authenticate(self, request: Request) -> AuthContext:
        supplied = request.headers.get(self._header, "")
        if not secrets.compare_digest(supplied, self._value.get_secret_value()):
            raise HTTPException(status_code=401, detail="authentication required")
        return AuthContext(actor=self._actor, principal=self._actor)


class BearerTokenAuthorizer(_QueueBlindAuthorizer):
    def __init__(self, token: str | SecretStr, *, actor: str = "bearer-token"):
        self._token = token if isinstance(token, SecretStr) else SecretStr(token)
        self._actor = actor

    def __repr__(self) -> str:
        return f"BearerTokenAuthorizer(actor={self._actor!r})"

    async def authenticate(self, request: Request) -> AuthContext:
        expected = f"Bearer {self._token.get_secret_value()}"
        if not secrets.compare_digest(request.headers.get("Authorization", ""), expected):
            raise HTTPException(status_code=401, detail="authentication required")
        return AuthContext(actor=self._actor, principal=self._actor)


AuthenticationCallable = Callable[[Request], AuthContext | Awaitable[AuthContext]]
AuthorizationCallable = Callable[
    [Request, AuthContext, TaskqAction, str | None], None | Awaitable[None]
]


async def _await_if_needed(value: Any) -> Any:
    return await value if isawaitable(value) else value


class CallableAuthorizer:
    def __init__(
        self,
        authenticate: AuthenticationCallable,
        authorize: AuthorizationCallable | None = None,
    ) -> None:
        self._authenticate = authenticate
        self._authorize = authorize

    async def authenticate(self, request: Request) -> AuthContext:
        return AuthContext.model_validate(await _await_if_needed(self._authenticate(request)))

    async def authorize_context(
        self,
        request: Request,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        if self._authorize is not None:
            await _await_if_needed(self._authorize(request, context, action, queue))

    async def authorize(
        self, request: Request, action: TaskqAction, queue: str | None
    ) -> AuthContext:
        context = await self.authenticate(request)
        await self.authorize_context(request, context, action, queue)
        return context


class LegacyTaskqAuthorizer(CallableAuthorizer):
    """Queue-blind read/write/operator compatibility shim."""

    def __init__(
        self,
        authenticate: AuthenticationCallable,
        checks: Mapping[str, AuthorizationCallable],
    ) -> None:
        self._checks = dict(checks)
        super().__init__(authenticate, self._legacy_check)

    async def _legacy_check(
        self,
        request: Request,
        context: AuthContext,
        action: TaskqAction,
        queue: str | None,
    ) -> None:
        family = {
            TaskqAction.READ: "read",
            TaskqAction.ENQUEUE: "write",
            TaskqAction.RUN: "write",
            TaskqAction.CONTROL: "operator",
            TaskqAction.ADMIN: "operator",
        }[action]
        checker = self._checks.get(family)
        if checker is None:
            raise HTTPException(status_code=403, detail="permission denied")
        await _await_if_needed(checker(request, context, action, queue))


class NoAuthForTests(_QueueBlindAuthorizer):
    async def authenticate(self, request: Request) -> AuthContext:
        return AuthContext(actor="test", principal="explicit-test-no-auth")


def static_api_key_auth(
    value: str | SecretStr, *, header: str = "X-API-Key", actor: str = "static-key"
) -> QueueAuthorizer:
    return StaticApiKeyAuthorizer(value, header=header, actor=actor)


def bearer_token_auth(token: str | SecretStr, *, actor: str = "bearer-token") -> QueueAuthorizer:
    return BearerTokenAuthorizer(token, actor=actor)


def callable_auth(
    authenticate: AuthenticationCallable,
    authorize: AuthorizationCallable | None = None,
) -> QueueAuthorizer:
    return CallableAuthorizer(authenticate, authorize)


def legacy_taskq_auth(
    authenticate: AuthenticationCallable,
    *,
    read: AuthorizationCallable,
    write: AuthorizationCallable,
    operator: AuthorizationCallable,
) -> QueueAuthorizer:
    return LegacyTaskqAuthorizer(authenticate, {"read": read, "write": write, "operator": operator})


def no_auth_for_tests() -> QueueAuthorizer:
    return NoAuthForTests()


async def authenticate_request(authorizer: QueueAuthorizer, request: Request) -> AuthContext:
    authenticate = getattr(authorizer, "authenticate", None)
    if authenticate is None:
        raise TypeError("QueueAuthorizer must support the facade authentication phase")
    return AuthContext.model_validate(await authenticate(request))


async def authorize_context(
    authorizer: QueueAuthorizer,
    request: Request,
    context: AuthContext,
    action: TaskqAction,
    queue: str | None,
) -> None:
    phased = getattr(authorizer, "authorize_context", None)
    if phased is None:
        raise TypeError("QueueAuthorizer must support the facade authorization phase")
    await phased(request, context, action, queue)


__all__ = [
    "AuthContext",
    "QueueAuthorizer",
    "bearer_token_auth",
    "callable_auth",
    "legacy_taskq_auth",
    "no_auth_for_tests",
    "static_api_key_auth",
]
