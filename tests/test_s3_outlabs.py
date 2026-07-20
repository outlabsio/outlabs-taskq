"""S3-04 real-package adapter, catalog, and provisioning vectors."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib.metadata
import logging
import os
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import asyncpg
from fastapi import HTTPException
import pytest
from sqlalchemy.engine import make_url
from starlette.requests import Request

from outlabs_auth import (
    AuthConfig,
    AuthDeps,
    EnterpriseRBAC,
    PermissionService,
    ServiceTokenService,
    SimpleRBAC,
)
from outlabs_auth.core.exceptions import InvalidInputError
from outlabs_auth.models.sql.enums import APIKeyKind, IntegrationPrincipalScopeKind
from outlabs_auth.services.api_key_policy import APIKeyPolicyService
from taskq.http.outlabs import (
    OutlabsQueueAuthorizer,
    ProvisioningReport,
    ensure_queue,
    ensure_queue_with_auth,
    provision_taskq_auth,
    taskq_permission_catalog,
)
from taskq.cli import _asyncpg_dsn, _print_auth_report, main
from taskq.protocol import TaskqAction


class _Credentials:
    async def get_credentials(self) -> str:
        return "test"


class _Backend:
    name = "scripted"
    transport = _Credentials()

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def has_credentials(self, request: Request) -> bool:
        return True

    async def authenticate(self, request: Request, **kwargs: Any) -> dict[str, Any]:
        return self.result


class _DepsProxy:
    def __init__(self, deps: AuthDeps) -> None:
        self._deps = deps
        self.permission_builds = 0

    def require_auth(self) -> Any:
        return self._deps.require_auth()

    def require_permission(self, *names: str, require_all: bool) -> Any:
        self.permission_builds += 1
        return self._deps.require_permission(*names, require_all=require_all)


class _OwnedSession:
    def __init__(self, closed: list[object]) -> None:
        self.closed = closed

    async def close(self) -> None:
        self.closed.append(self)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/taskq/v1/queues/emails/claims",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )


def _api_key_request(api_key: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/taskq/v1/queues/tools/stats",
            "headers": [(b"x-api-key", api_key.encode("ascii"))],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )


def _real_auth(
    permissions: list[str], session_dependency: Any
) -> tuple[OutlabsQueueAuthorizer, _DepsProxy]:
    config = AuthConfig(secret_key="x" * 32)
    backend = _Backend(
        {
            "source": "service_token",
            "service_id": "fleet-a",
            "service_name": "Fleet A",
            "metadata": {"permissions": permissions},
        }
    )
    deps = AuthDeps(
        [backend],
        get_session=session_dependency,
        permission_service=PermissionService(config),
        service_token_service=ServiceTokenService(config),
    )
    proxy = _DepsProxy(deps)
    return OutlabsQueueAuthorizer(
        auth=SimpleNamespace(deps=proxy), session_dependency=session_dependency
    ), proxy


async def test_real_permission_dependency_queue_global_denial_actor_and_cache() -> None:
    finalized: list[object] = []

    async def sessions() -> Any:
        marker = object()
        try:
            yield _OwnedSession([])
        finally:
            finalized.append(marker)

    authorizer, deps = _real_auth(["taskq_emails:run", "taskq:read"], sessions)
    context = await authorizer.authorize(_request(), TaskqAction.RUN, "emails")
    assert context.actor == "fleet-a"
    assert "taskq_emails:run" not in repr(context)
    with pytest.raises(HTTPException) as denied:
        await authorizer.authorize(_request(), TaskqAction.RUN, "tools")
    assert denied.value.status_code == 403
    await authorizer.authorize(_request(), TaskqAction.READ, "tools")
    with pytest.raises(HTTPException) as non_global:
        await authorizer.authorize(_request(), TaskqAction.RUN, None)
    assert non_global.value.status_code == 403

    request = _request()
    await asyncio.gather(
        *(authorizer.authorize(request, TaskqAction.RUN, "emails") for _ in range(8))
    )
    assert deps.permission_builds == 4  # queue/global tuples each build exactly once
    assert len(finalized) == 24  # two dependency scopes per authorize attempt


async def test_real_dependency_legacy_candidate_and_awaitable_session() -> None:
    closed: list[object] = []

    async def session() -> _OwnedSession:
        return _OwnedSession(closed)

    authorizer, _ = _real_auth(["job:write"], session)
    authorizer = OutlabsQueueAuthorizer(
        auth=authorizer._auth,
        session_dependency=session,
        extra_candidates={TaskqAction.RUN: ("job:write",)},
    )
    await authorizer.authorize(_request(), TaskqAction.RUN, "emails")
    assert len(closed) == 2


@pytest.mark.parametrize(
    ("status", "reason"),
    [(429, "auth_rate_limited"), (503, "auth_infrastructure_unavailable")],
)
async def test_adapter_sanitizes_real_dependency_http_failures(status: int, reason: str) -> None:
    async def checker(request: Request, session: Any) -> Any:
        raise HTTPException(
            status_code=status,
            detail={"token": "must-not-escape"},
            headers={"Retry-After": "11"},
        )

    deps = SimpleNamespace(require_auth=lambda: checker)
    auth = SimpleNamespace(deps=deps)

    async def session() -> _OwnedSession:
        return _OwnedSession([])

    authorizer = OutlabsQueueAuthorizer(auth=auth, session_dependency=session)
    with pytest.raises(HTTPException) as raised:
        await authorizer.authenticate(_request())
    assert raised.value.status_code == status
    assert raised.value.detail == {"reason": reason}
    assert raised.value.headers == {"Retry-After": "11"}


def test_catalog_is_sorted_exact_validated_and_rejects_noncanonical_queue() -> None:
    assert importlib.metadata.version("outlabs-auth") == "0.1.0a24"
    catalog = taskq_permission_catalog(["tools", "emails", "emails"])
    names = tuple(item.name for item in catalog)
    assert len(names) == 15
    assert names == tuple(sorted(names))
    assert {"taskq:admin", "taskq_emails:run", "taskq_tools:enqueue"} <= set(names)
    for bad in ("MyQueue", "my-queue", "", "q" * 58):
        with pytest.raises(ValueError, match="rejected, not normalized"):
            taskq_permission_catalog([bad])


class _PermissionService:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    async def get_permission_by_name(self, session: Any, name: str) -> Any:
        return self.rows.get(name)

    async def create_permission(self, session: Any, *, name: str, **kwargs: Any) -> Any:
        row = SimpleNamespace(name=name)
        self.rows[name] = row
        return row


class _RoleService:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}
        self.fail_create: str | None = None

    async def get_role_by_name(self, session: Any, name: str) -> Any:
        return self.rows.get(name)

    async def get_role_by_id(self, session: Any, role_id: Any, **kwargs: Any) -> Any:
        return next(role for role in self.rows.values() if role.id == role_id)

    async def create_role(
        self,
        session: Any,
        *,
        name: str,
        permission_names: list[str],
        is_system_role: bool,
        is_global: bool,
        **kwargs: Any,
    ) -> Any:
        if name == self.fail_create:
            raise RuntimeError("scripted secret must never escape")
        assert is_system_role is False
        role = SimpleNamespace(
            id=uuid4(),
            name=name,
            permissions=[SimpleNamespace(name=value) for value in permission_names],
            is_system_role=is_system_role,
            is_global=is_global,
        )
        self.rows[name] = role
        return role

    async def update_role(
        self,
        session: Any,
        role_id: Any,
        *,
        permission_names: list[str],
        is_global: bool,
        update_permissions: bool,
        **kwargs: Any,
    ) -> Any:
        assert update_permissions is True
        role = await self.get_role_by_id(session, role_id)
        role.permissions = [SimpleNamespace(name=value) for value in permission_names]
        role.is_global = is_global
        return role


class _Session:
    def __init__(self) -> None:
        self.nested = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.nested_errors = 0

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        self.nested += 1
        try:
            yield self
        except Exception:
            self.nested_errors += 1
            raise


def _provision_auth() -> Any:
    return SimpleNamespace(permission_service=_PermissionService(), role_service=_RoleService())


async def test_provision_report_apply_idempotency_and_public_role_shape() -> None:
    auth = _provision_auth()
    session = _Session()
    report = await provision_taskq_auth(auth, session, queues=["emails"], mode="report")
    assert len(report.created) == 14
    assert not auth.permission_service.rows and not auth.role_service.rows

    applied = await provision_taskq_auth(auth, session, queues=["emails"], mode="apply")
    assert applied.created == report.created
    assert session.nested == 1
    assert set(auth.role_service.rows) == {
        "taskq-producer",
        "taskq-worker",
        "taskq-operator",
        "taskq-admin",
    }
    assert {
        permission.name for permission in auth.role_service.rows["taskq-admin"].permissions
    } == {
        "taskq:enqueue",
        "taskq:run",
        "taskq:read",
        "taskq:control",
        "taskq:admin",
    }
    second = await provision_taskq_auth(auth, session, queues=["emails"], mode="apply")
    assert not second.created and len(second.existing) == 14
    assert session.nested == 2
    assert session.commit_calls == session.rollback_calls == session.close_calls == 0


async def test_provision_conflict_reconcile_savepoint_and_failure() -> None:
    auth = _provision_auth()
    session = _Session()
    await provision_taskq_auth(auth, session, queues=["emails"], mode="apply")
    role = auth.role_service.rows["taskq-worker"]
    role.permissions = []
    conflict = await provision_taskq_auth(auth, session, queues=["emails"], mode="apply")
    assert conflict.conflicting == ("role:taskq-worker",)
    assert session.nested == 1

    reconciled = await provision_taskq_auth(
        auth, session, queues=["emails"], mode="apply", reconcile=True
    )
    assert reconciled.changed == ("role:taskq-worker",)
    assert {permission.name for permission in role.permissions} == {"taskq:read", "taskq:run"}
    assert session.nested == 2

    failing = _provision_auth()
    failing.role_service.fail_create = "taskq-producer"
    with pytest.raises(RuntimeError, match="scripted secret"):
        await provision_taskq_auth(failing, session, queues=["tools"], mode="apply")
    assert session.nested_errors == 1
    assert session.commit_calls == session.rollback_calls == 0

    role.is_system_role = True
    role.permissions = []
    protected = await provision_taskq_auth(
        auth, session, queues=["emails"], mode="apply", reconcile=True
    )
    assert protected.conflicting == ("role:taskq-worker",)


@pytest.mark.taskq_sql
@pytest.mark.filterwarnings("ignore:No path_separator found in configuration:DeprecationWarning")
async def test_real_public_services_first_apply_and_reconcile(taskq_dsn: str) -> None:
    schema = "taskq_s3_auth_test"
    plain_dsn = taskq_dsn.replace("postgresql+asyncpg://", "postgresql://")
    _, separator, suffix = plain_dsn.partition("://")
    engine_dsn = "postgresql+asyncpg" + separator + suffix
    admin = await asyncpg.connect(plain_dsn)
    await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    await admin.close()
    auth = SimpleRBAC(
        database_url=engine_dsn,
        database_schema=schema,
        secret_key="x" * 32,
        auto_migrate=True,
    )
    root = logging.getLogger()
    root_state = (root.level, tuple(root.handlers))
    logger_state = {
        name: (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    try:
        await auth.initialize()
        async with auth.get_session() as session:
            first = await provision_taskq_auth(auth, session, queues=["emails"], mode="apply")
            assert len(first.created) == 14 and first.ok
            await session.commit()

        async with auth.get_session() as session:
            worker = await auth.role_service.get_role_by_name(session, "taskq-worker")
            assert worker is not None and worker.is_system_role is False
            await auth.role_service.update_role(
                session,
                worker.id,
                permission_names=["taskq:read"],
                update_permissions=True,
            )
            await session.commit()

        async with auth.get_session() as session:
            conflict = await provision_taskq_auth(auth, session, queues=["emails"], mode="report")
            assert conflict.conflicting == ("role:taskq-worker",)
            converged = await provision_taskq_auth(
                auth,
                session,
                queues=["emails"],
                mode="apply",
                reconcile=True,
            )
            assert converged.changed == ("role:taskq-worker",)
            await session.commit()

        async with auth.get_session() as session:
            final = await provision_taskq_auth(auth, session, queues=["emails"], mode="report")
            assert not final.created and not final.changed and not final.conflicting
            assert len(final.existing) == 14
    finally:
        await auth.shutdown()
        root.setLevel(root_state[0])
        root.handlers[:] = root_state[1]
        for name, state in logger_state.items():
            logger = logging.getLogger(name)
            logger.disabled, logger.level, logger.propagate = state[:3]
            logger.handlers[:] = state[3]
        admin = await asyncpg.connect(plain_dsn)
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()


async def test_real_api_key_policy_enterprise_denies_run_simple_documents_residual() -> None:
    owner = SimpleNamespace(
        id=uuid4(),
        root_entity_id=None,
        is_superuser=False,
        can_authenticate=lambda: True,
    )
    enterprise = APIKeyPolicyService(AuthConfig(secret_key="x" * 32, enable_entity_hierarchy=True))
    with pytest.raises(InvalidInputError) as denied:
        await enterprise.validate_create(
            object(),
            actor_user_id=owner.id,
            owner=owner,
            key_kind=APIKeyKind.PERSONAL,
            scopes=["taskq_emails:run"],
            entity_id=None,
            inherit_from_tree=False,
        )
    assert denied.value.details["policy_reason"] == "scope_not_allowed_for_personal"

    simple = APIKeyPolicyService(AuthConfig(secret_key="x" * 32))
    await simple.validate_create(
        object(),
        actor_user_id=owner.id,
        owner=owner,
        key_kind=APIKeyKind.PERSONAL,
        scopes=["taskq_emails:run"],
        entity_id=None,
        inherit_from_tree=False,
    )
    config = AuthConfig(secret_key="x" * 32)
    assert {"run", "read", "control"} <= set(config.api_key_system_allowed_action_prefixes)
    assert {"enqueue", "admin"}.isdisjoint(config.api_key_system_allowed_action_prefixes)


@pytest.mark.taskq_sql
@pytest.mark.filterwarnings("ignore:No path_separator found in configuration:DeprecationWarning")
@pytest.mark.filterwarnings("ignore:Call to deprecated setex.*:DeprecationWarning")
@pytest.mark.filterwarnings("ignore:Call to deprecated close.*:DeprecationWarning")
async def test_preinitialized_enterprise_system_key_binds_after_startup(
    taskq_dsn: str,
) -> None:
    """Regression for S4-CQ-04's real host mount-before-initialize ordering."""

    schema = "taskq_s4_cq04_auth_test"
    plain_dsn = taskq_dsn.replace("postgresql+asyncpg://", "postgresql://")
    _, separator, suffix = plain_dsn.partition("://")
    engine_dsn = "postgresql+asyncpg" + separator + suffix
    redis_url = os.environ.get("TASKQ_TEST_REDIS_URL", "redis://localhost:6379/15")
    admin = await asyncpg.connect(plain_dsn)
    await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    await admin.close()
    auth = EnterpriseRBAC(
        database_url=engine_dsn,
        database_schema=schema,
        secret_key="x" * 32,
        auto_migrate=True,
        redis_url=redis_url,
        redis_key_prefix=f"taskq:test:cq04:{uuid4().hex}",
    )
    root = logging.getLogger()
    root_state = (root.level, tuple(root.handlers))
    logger_state = {
        name: (logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    auth.prime_fastapi_routing()
    authorizer = OutlabsQueueAuthorizer(
        auth=auth,
        session_dependency=auth.get_session,
    )
    assert auth.deps.services.get("api_key_service") is not auth.api_key_service

    try:
        await auth.initialize()
        assert auth.redis_client is not None and auth.redis_client.is_available
        async with auth.get_session() as session:
            async with session.begin():
                report = await provision_taskq_auth(
                    auth,
                    session,
                    queues=["tools"],
                    mode="apply",
                )
                assert report.ok
                actor = await auth.user_service.create_user(
                    session,
                    "taskq-cq04-admin@example.test",
                    "Password!123",
                    is_superuser=True,
                )
                principal = await auth.integration_principal_service.create_principal(
                    session,
                    name="taskq-cq04-reader",
                    description="S4-CQ-04 regression principal",
                    scope_kind=IntegrationPrincipalScopeKind.PLATFORM_GLOBAL,
                    anchor_entity_id=None,
                    inherit_from_tree=False,
                    allowed_scopes=["taskq_tools:read"],
                    created_by_user_id=actor.id,
                )
                raw_key, _ = await auth.api_key_service.create_api_key(
                    session,
                    integration_principal_id=principal.id,
                    name="taskq-cq04-reader-key",
                    scopes=["taskq_tools:read"],
                    key_kind=APIKeyKind.SYSTEM_INTEGRATION,
                    actor_user_id=actor.id,
                )

        request = _api_key_request(raw_key)
        context = await authorizer.authenticate(request)
        fingerprint = context.principal.fingerprint
        await authorizer.authorize_context(request, context, TaskqAction.READ, "tools")
        assert context.principal.fingerprint == fingerprint

        with pytest.raises(HTTPException) as denied:
            await authorizer.authorize_context(request, context, TaskqAction.RUN, "tools")
        assert denied.value.status_code == 403
        assert denied.value.detail == "authorization failed"
    finally:
        await auth.shutdown()
        root.setLevel(root_state[0])
        root.handlers[:] = root_state[1]
        for name, state in logger_state.items():
            logger = logging.getLogger(name)
            logger.disabled, logger.level, logger.propagate = state[:3]
            logger.handlers[:] = state[3]
        admin = await asyncpg.connect(plain_dsn)
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()


async def test_ensure_queue_composition_reports_partial_failure_without_secret() -> None:
    class Operator:
        async def ensure_queue(self, queue: str, profile: dict[str, Any], actor: str) -> str:
            return f"created:{queue}"

    plain = await ensure_queue(Operator(), "emails")
    assert plain.queue_result == "created:emails" and plain.auth_report is None
    result = await ensure_queue_with_auth(
        Operator(), SimpleNamespace(), object(), "emails", mode="apply"
    )
    assert result.queue_result == "created:emails"
    assert result.auth_report is None
    assert result.auth_error == "TypeError"
    assert "secret" not in repr(result)


def test_provisioning_report_repr_is_secret_free() -> None:
    report = ProvisioningReport(mode="report", created=("permission:taskq:run",))
    assert "super-secret-value" not in repr(report)


def test_cli_report_is_deterministic_and_prints_wildcard_policy(capsys: Any) -> None:
    report = ProvisioningReport(
        mode="report",
        created=("permission:taskq:run",),
        conflicting=("role:taskq-worker",),
    )
    _print_auth_report(report)
    output = capsys.readouterr().out
    assert "created: 1\n  - permission:taskq:run" in output
    assert "conflicting: 1\n  - role:taskq-worker" in output
    assert "wildcard scopes are not supported" in output
    assert "super-secret-value" not in output


def test_auth_cli_renders_the_real_password_for_the_owned_connection() -> None:
    rendered = _asyncpg_dsn(
        make_url("postgresql://installer:p%40ss%2Fword@db.example.test/taskq?ssl=require")
    )

    assert rendered == (
        "postgresql+asyncpg://installer:p%40ss%2Fword@db.example.test/taskq?ssl=require"
    )
    assert "***" not in rendered


def test_auth_cli_dispatches_lazily_without_printing_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    async def run(args: Any) -> ProvisioningReport:
        assert args.queues == "emails,tools"
        return ProvisioningReport(mode="report", existing=("permission:taskq:read",))

    monkeypatch.setattr("taskq.cli._run_auth_sync", run)
    main(["auth", "sync-permissions", "--queues", "emails,tools"])
    output = capsys.readouterr().out
    assert "mode: report" in output
    assert "permission:taskq:read" in output
    assert "super-secret-value" not in output
