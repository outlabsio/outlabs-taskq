"""Internal runtime adapters retained behind the resource-oriented Click CLI.

These helpers own process lifecycles and installer/auth adapters; public
argument parsing, context resolution, safety, and rendering live in
``taskq.cli.app``. A bare PostgreSQL DSN uses the bundled asyncpg driver, while
an explicit synchronous SQLAlchemy driver uses that installed driver.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import logging
import os
import secrets
import signal
import socket
import sys
from collections.abc import Callable
from typing import Any, NoReturn
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from taskq.errors import (
    TaskqCapabilityError,
    TaskqConfigError,
    TaskqUnavailableError,
    TaskqValidationError,
    TaskqVersionError,
)
from taskq.registry import TaskRegistry
from taskq.settings import WorkerSettings
from taskq.sql import VerifyReport, _migrate_impl, verify, verify_sync
from taskq.sql.notifications import PostgresNotificationSource
from taskq.sql.transport import SqlTaskqTransport
from taskq.worker import WorkerOptions, WorkerService, WorkerServiceOptions

_DSN_HELP = (
    "PostgreSQL DSN; omit to read TASKQ_DSN. Bare DSNs use the bundled asyncpg "
    "driver; an explicit postgresql+<driver> DSN selects that installed driver."
)


def _normalized_url(dsn: str) -> URL:
    url = make_url(dsn)
    if url.drivername == "postgres":  # legacy alias SQLAlchemy no longer accepts
        url = url.set(drivername="postgresql")
    return url


def _is_asyncpg_url(url: URL) -> bool:
    return url.drivername == "postgresql" or url.drivername.endswith("+asyncpg")


def _asyncpg_dsn(url: URL) -> str:
    """Render a real asyncpg DSN without SQLAlchemy's display-only redaction."""
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


def _dsn_contains_password(dsn: str) -> bool:
    """Best-effort credential detection used only for a secret-free argv warning."""
    try:
        return _normalized_url(dsn).password is not None
    except Exception:
        return False


def _warn_command_line_credentials(*environment_names: str) -> None:
    preferred = ", ".join(environment_names)
    print(
        "warning: a credential supplied on the command line may be visible to other "
        f"processes; prefer {preferred}",
        file=sys.stderr,
    )


def _warn_for_argv_credentials(args: argparse.Namespace) -> None:
    if args.command in {"migrate", "verify"}:
        if args.dsn is not None and _dsn_contains_password(args.dsn):
            _warn_command_line_credentials("TASKQ_DSN")
        return
    if args.command == "worker":
        preferred: list[str] = []
        if args.dsn is not None and _dsn_contains_password(args.dsn):
            preferred.append("TASKQ_DSN")
        if args.http_bearer_token is not None:
            preferred.append("TASKQ_HTTP_BEARER_TOKEN")
        if args.http_header_value is not None:
            preferred.append("TASKQ_HTTP_HEADER_VALUE")
        if preferred:
            _warn_command_line_credentials(*preferred)
        return
    if args.command == "auth" and args.dsn is not None and _dsn_contains_password(args.dsn):
        _warn_command_line_credentials("TASKQ_AUTH_DSN")
        return
    if args.command == "target" and args.dsn is not None and _dsn_contains_password(args.dsn):
        _warn_command_line_credentials("TASKQ_DSN")
        return
    if args.command in {"scheduler", "schedule"}:
        if args.dsn is not None and _dsn_contains_password(args.dsn):
            _warn_command_line_credentials("TASKQ_DSN")


def _required_dsn(
    parser: argparse.ArgumentParser,
    argument: str | None,
    *,
    command: str,
) -> str:
    dsn = argument or os.environ.get("TASKQ_DSN")
    if not dsn:
        parser.error(f"{command} DSN is required via its optional argument or TASKQ_DSN")
    return dsn


def _run_migrate(
    dsn: str, expected_pending: tuple[tuple[str, str], ...] | None = None
) -> list[str]:
    url = _normalized_url(dsn)
    if _is_asyncpg_url(url):

        async def _go() -> list[str]:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
            try:
                async with engine.connect() as conn:
                    return await conn.run_sync(
                        lambda sync: _migrate_impl(sync, expected_pending=expected_pending)
                    )
            finally:
                await engine.dispose()

        return asyncio.run(_go())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return _migrate_impl(conn, expected_pending=expected_pending)
    finally:
        engine.dispose()


def _run_verify(dsn: str) -> VerifyReport:
    url = _normalized_url(dsn)
    if _is_asyncpg_url(url):

        async def _go() -> VerifyReport:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
            try:
                async with engine.connect() as conn:
                    return await verify(conn)
            finally:
                await engine.dispose()

        return asyncio.run(_go())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return verify_sync(conn)
    finally:
        engine.dispose()


def _print_report(report: VerifyReport) -> None:
    for check in report.checks:
        print(f"[{'ok' if check.ok else 'FAIL'}] {check.name}")
        for detail in check.details:
            print(f"       - {detail}")


def _settings_error(error: ValidationError) -> str:
    messages = [item["msg"] for item in error.errors(include_input=False, include_url=False)]
    return "; ".join(dict.fromkeys(messages)) or "worker configuration is invalid"


def _load_registry(reference: str) -> TaskRegistry:
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name or ":" in attribute_name:
        raise TaskqConfigError("registry must be exactly one module:attribute reference")
    try:
        target = getattr(importlib.import_module(module_name), attribute_name)
    except Exception as exc:
        raise TaskqConfigError("registry import failed") from exc
    if isinstance(target, TaskRegistry):
        registry = target
    else:
        if not callable(target) or inspect.iscoroutinefunction(target):
            raise TaskqConfigError("registry target must be an instance or sync factory")
        try:
            inspect.signature(target).bind()
        except (TypeError, ValueError) as exc:
            raise TaskqConfigError("registry factory must accept zero arguments") from exc
        try:
            registry = target()
        except Exception as exc:
            raise TaskqConfigError("registry factory failed") from exc
        if inspect.isawaitable(registry):
            if inspect.iscoroutine(registry):
                registry.close()
            raise TaskqConfigError("registry factory must be synchronous")
    if not isinstance(registry, TaskRegistry):
        raise TaskqConfigError("registry target did not produce a TaskRegistry")
    return registry


def _validate_subscriptions(registry: TaskRegistry, queues: tuple[str, ...]) -> None:
    queues_with_handlers = {task.queue for task in registry if task.handler is not None}
    missing = tuple(queue for queue in queues if queue not in queues_with_handlers)
    if missing:
        raise TaskqConfigError("every subscribed queue must have at least one registered handler")


def _default_worker_id() -> str:
    return f"worker:{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(8)}"


async def _run_worker(
    settings: WorkerSettings,
    registry: TaskRegistry,
    *,
    process_exit: Callable[[int], NoReturn] = os._exit,
) -> int:
    logger = logging.getLogger("taskq.worker")
    http_mode = settings.http_base_url is not None
    if http_mode:
        from taskq.http.client import AsyncTaskqHttpClient

        transport = AsyncTaskqHttpClient(
            settings.http_base_url or "",
            bearer_token=settings.http_bearer_token,
            header_name=settings.http_header_name,
            header_value=settings.http_header_value,
            claim_wait_seconds=settings.http_claim_wait_seconds,
            timeout=max(30.0, settings.http_claim_wait_seconds + 5),
        )
        await transport.start()
        notifications = None
    else:
        assert settings.dsn is not None and settings.pool_size is not None
        dsn = settings.dsn.get_secret_value()
        transport = SqlTaskqTransport.from_dsn(
            dsn,
            pool_size=settings.pool_size,
            max_overflow=0,
            expected_environment=settings.expected_environment,
            expected_installation_id=settings.expected_installation_id,
            allow_production=settings.allow_production,
        )
        notifications = PostgresNotificationSource(dsn) if settings.listen else None
    service = WorkerService(
        transport,
        registry,
        settings.worker_id or _default_worker_id(),
        options=WorkerServiceOptions(
            queues=settings.queues,
            batch=settings.batch,
            poll_interval=settings.poll_interval,
            listen=settings.listen,
            presence_interval=settings.presence_interval,
            cancel_inflight_claim_on_stop=http_mode and settings.http_claim_wait_seconds > 0,
        ),
        supervisor_options=WorkerOptions(
            concurrency=settings.concurrency,
            sync_workers=settings.sync_workers,
            soft_stop_timeout=settings.soft_stop_timeout,
        ),
        notifications=notifications,
    )
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    signal_count = 0

    async def terminate(*, hard: bool) -> None:
        await service.stop(cancel=hard)

    async def monitor_process_exit() -> None:
        while not service.stopped:
            if service.requires_process_exit:
                await service._prepare_process_exit()
                await transport.aclose()
                logger.critical("worker.process_exit_required")
                logging.shutdown()
                process_exit(3)
                return
            await asyncio.sleep(0.01)

    def received_signal() -> None:
        nonlocal signal_count
        signal_count += 1
        hard = signal_count > 1
        asyncio.create_task(terminate(hard=hard), name="taskq-cli-stop")

    def check_process_exit() -> None:
        if service.requires_process_exit:
            logger.critical("worker.process_exit_required")
            logging.shutdown()
            process_exit(3)

    for candidate in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(candidate, received_signal)
        except (NotImplementedError, RuntimeError):
            continue
        installed_signals.append(candidate)

    logger.info(
        "worker.configuration",
        extra={
            "environment": settings.environment,
            "queues": settings.queues,
            "concurrency": settings.concurrency,
            "sync_workers": settings.sync_workers or settings.concurrency,
            "batch": settings.batch,
            "pool_size": settings.pool_size,
            "listener_connections": int(settings.listen),
            "transport_mode": "http" if http_mode else "sql",
        },
    )
    exit_monitor = asyncio.create_task(
        monitor_process_exit(), name="taskq-cli-process-exit-monitor"
    )
    try:
        await service.run()
        check_process_exit()
        return 1 if service.snapshot().fatal else 0
    finally:
        exit_monitor.cancel()
        await asyncio.gather(exit_monitor, return_exceptions=True)
        for candidate in installed_signals:
            loop.remove_signal_handler(candidate)
        if not service.requires_process_exit:
            await service.aclose()
        if notifications is not None:
            await notifications.aclose()
        await transport.aclose()


def _add_worker_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    worker = subparsers.add_parser("worker", help="run a DB-direct task worker")
    worker.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    worker.add_argument("--http-base-url")
    worker.add_argument(
        "--http-bearer-token",
        help="prefer TASKQ_HTTP_BEARER_TOKEN to keep the token out of process arguments",
    )
    worker.add_argument("--http-header-name")
    worker.add_argument(
        "--http-header-value",
        help="prefer TASKQ_HTTP_HEADER_VALUE to keep the value out of process arguments",
    )
    worker.add_argument("--http-claim-wait-seconds", type=float)
    worker.add_argument("--registry")
    worker.add_argument("--queue", dest="queues", action="append")
    worker.add_argument("--environment")
    worker.add_argument("--worker-id")
    worker.add_argument("--concurrency", type=int)
    worker.add_argument("--sync-workers", type=int)
    worker.add_argument("--batch", type=int)
    worker.add_argument("--poll-interval", type=float)
    worker.add_argument("--listen", action=argparse.BooleanOptionalAction, default=None)
    worker.add_argument("--presence-interval", type=float)
    worker.add_argument("--soft-stop-timeout", type=float)
    worker.add_argument("--expected-environment")
    worker.add_argument("--expected-installation-id", type=UUID)
    worker.add_argument("--allow-production", action=argparse.BooleanOptionalAction, default=None)
    worker.add_argument("--pool-size", type=int)


def _add_auth_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    auth = subparsers.add_parser("auth", help="explicit OutLabs IAM provisioning")
    commands = auth.add_subparsers(dest="auth_command", required=True)
    sync = commands.add_parser("sync-permissions", help="report or apply taskq IAM rows")
    sync.add_argument("--dsn", help="prefer TASKQ_AUTH_DSN when the DSN contains credentials")
    sync.add_argument("--schema", default=os.environ.get("TASKQ_AUTH_SCHEMA"))
    sync.add_argument("--queues", required=True, help="comma-separated canonical queue names")
    sync.add_argument("--roles", choices=("standard", "none"), default="standard")
    sync.add_argument("--role-prefix", default="taskq-")
    sync.add_argument("--apply", action="store_true")
    sync.add_argument("--reconcile", action="store_true")
    sync.add_argument("--per-queue-roles", action="store_true")


def _add_target_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    target = subparsers.add_parser(
        "target", help="inspect or explicitly bind the database target identity"
    )
    commands = target.add_subparsers(dest="target_command", required=True)

    show = commands.add_parser("show", help="show the safe target fingerprint")
    show.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    show.add_argument("--json", action="store_true", help="emit stable machine-readable JSON")

    bind = commands.add_parser(
        "bind", help="bind an unbound target or explicitly rotate a clone identity"
    )
    bind.add_argument("environment")
    bind.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    bind.add_argument("--actor", required=True)
    bind.add_argument("--expected-installation-id", type=UUID, required=True)
    bind.add_argument("--expected-binding-version", type=int, required=True)
    bind.add_argument("--rotate", action="store_true")
    bind.add_argument("--reason")
    bind.add_argument("--allow-production", action="store_true")


def _add_target_expectation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-environment")
    parser.add_argument("--expected-installation-id", type=UUID)
    parser.add_argument("--allow-production", action="store_true", default=None)


def _add_scheduler_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    scheduler = subparsers.add_parser("scheduler", help="run the framework-neutral TaskQ scheduler")
    scheduler.add_argument("scheduler_command", nargs="?", choices=("run", "doctor"), default="run")
    scheduler.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    scheduler.add_argument("--once", action="store_true")
    scheduler.add_argument("--max-batches", type=int, default=100)
    scheduler.add_argument("--max-runtime-seconds", type=float, default=300)
    scheduler.add_argument("--worker-id")
    scheduler.add_argument("--poll-interval", type=float)
    scheduler.add_argument("--jitter", type=float)
    scheduler.add_argument("--backoff-cap", type=float)
    scheduler.add_argument("--claim-limit", type=int)
    scheduler.add_argument("--lease-seconds", type=int)
    scheduler.add_argument("--error-retry-seconds", type=int)
    scheduler.add_argument("--pool-size", type=int)
    scheduler.add_argument("--json", action="store_true")
    _add_target_expectation_arguments(scheduler)


def _add_schedule_manifest_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    schedule = subparsers.add_parser(
        "schedule", help="plan, apply, or retire source-owned schedule manifests"
    )
    commands = schedule.add_subparsers(dest="schedule_command", required=True)
    plan = commands.add_parser("plan", help="render a non-mutating manifest plan")
    plan.add_argument("manifest")
    plan.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    plan.add_argument("--json", action="store_true")

    apply = commands.add_parser("apply", help="CAS-apply desired schedules without pruning")
    apply.add_argument("manifest")
    apply.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    apply.add_argument("--actor", required=True)
    apply.add_argument("--json", action="store_true")
    _add_target_expectation_arguments(apply)

    retire = commands.add_parser("retire", help="explicitly retire one owned manifest key")
    retire.add_argument("manifest")
    retire.add_argument("key")
    retire.add_argument("--dsn", help="prefer TASKQ_DSN when the DSN contains credentials")
    retire.add_argument("--actor", required=True)
    retire.add_argument("--reason", default="manifest retirement")
    retire.add_argument("--json", action="store_true")
    _add_target_expectation_arguments(retire)


async def _run_target_command(args: argparse.Namespace) -> Any:
    transport = SqlTaskqTransport.from_dsn(args.dsn)
    try:
        if args.target_command == "show":
            return await transport.get_target_identity()
        return await transport.bind_target_identity(
            args.expected_installation_id,
            args.environment,
            args.actor,
            args.expected_binding_version,
            rotate=args.rotate,
            reason=args.reason,
        )
    finally:
        await transport.aclose()


def _print_target(profile: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
        return
    installation_id = str(profile.installation_id)
    print(f"installation: {installation_id[:8]}…")
    print(f"environment: {profile.environment}")
    print(f"binding version: {profile.binding_version}")
    print(f"contract: {profile.contract_version}")
    active = profile.capabilities.get("active", [])
    print(f"capabilities: {', '.join(str(value) for value in active)}")


def _scheduler_overrides(args: argparse.Namespace) -> dict[str, object]:
    names = (
        "dsn",
        "expected_environment",
        "expected_installation_id",
        "allow_production",
        "worker_id",
        "poll_interval",
        "jitter",
        "backoff_cap",
        "claim_limit",
        "lease_seconds",
        "error_retry_seconds",
        "pool_size",
    )
    return {name: getattr(args, name) for name in names if getattr(args, name) is not None}


async def _run_scheduler_command(args: argparse.Namespace) -> Any:
    from taskq.scheduler import SchedulerSettings, scheduler_doctor, scheduler_from_settings

    if args.scheduler_command == "doctor":
        dsn = args.dsn or os.environ.get("TASKQ_DSN")
        if not dsn:
            raise TaskqConfigError("scheduler doctor requires --dsn or TASKQ_DSN")
        expected_environment = args.expected_environment or os.environ.get("TASKQ_EXPECTED_ENV")
        expected_installation_id = args.expected_installation_id
        if expected_installation_id is None:
            configured_id = os.environ.get("TASKQ_EXPECTED_INSTALLATION_ID")
            if configured_id:
                try:
                    expected_installation_id = UUID(configured_id)
                except ValueError as exc:
                    raise TaskqConfigError("TASKQ_EXPECTED_INSTALLATION_ID must be a UUID") from exc
        transport = SqlTaskqTransport.from_dsn(dsn)
        try:
            return await scheduler_doctor(
                transport,
                expected_environment=expected_environment,
                expected_installation_id=expected_installation_id,
                allow_production=bool(args.allow_production),
            )
        finally:
            await transport.aclose()

    settings = SchedulerSettings(**_scheduler_overrides(args))
    transport, service = scheduler_from_settings(settings)
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for candidate in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(candidate, service.stop)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(candidate)
    try:
        if args.once:
            return await service.run_once(
                max_batches=args.max_batches,
                max_runtime_seconds=args.max_runtime_seconds,
            )
        await service.run()
        return None
    finally:
        for candidate in installed:
            loop.remove_signal_handler(candidate)
        await transport.aclose()


def _manifest_transport(args: argparse.Namespace, *, protected: bool) -> SqlTaskqTransport:
    expected_environment = getattr(args, "expected_environment", None)
    expected_installation_id = getattr(args, "expected_installation_id", None)
    allow_production = bool(getattr(args, "allow_production", False))
    if protected and not expected_environment:
        raise TaskqConfigError(
            "manifest mutation requires --expected-environment or TASKQ_EXPECTED_ENV"
        )
    if expected_environment == "production" and not allow_production:
        raise TaskqConfigError("production requires --allow-production")
    if expected_environment == "production" and expected_installation_id is None:
        raise TaskqConfigError("production requires --expected-installation-id")
    return SqlTaskqTransport.from_dsn(
        args.dsn,
        expected_environment=expected_environment,
        expected_installation_id=expected_installation_id,
        allow_production=allow_production,
    )


async def _run_schedule_manifest_command(args: argparse.Namespace) -> Any:
    from taskq.scheduler import apply_manifest, load_manifest, plan_manifest

    manifest = load_manifest(args.manifest)
    protected = args.schedule_command in {"apply", "retire"}
    transport = _manifest_transport(args, protected=protected)
    try:
        if args.schedule_command == "plan":
            return await plan_manifest(transport, manifest)
        if args.schedule_command == "apply":
            return await apply_manifest(transport, manifest, actor=args.actor)
        plan = await plan_manifest(transport, manifest)
        entry = next((candidate for candidate in plan.entries if candidate.key == args.key), None)
        if entry is None or entry.current_version is None:
            raise TaskqConfigError("retire key is not owned by this manifest namespace/source")
        return await transport.retire_schedule(entry.name, entry.current_version, args.actor)
    finally:
        await transport.aclose()


def _print_manifest_result(result: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
        return
    if hasattr(result, "entries"):
        for entry in result.entries:
            suffix = f" ({entry.reason})" if entry.reason else ""
            print(f"{entry.action:9} {entry.name}{suffix}")
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return
    for key, value in result.model_dump(mode="json").items():
        print(f"{key}: {value}")


def _worker_overrides(args: argparse.Namespace) -> dict[str, object]:
    names = (
        "dsn",
        "http_base_url",
        "http_bearer_token",
        "http_header_name",
        "http_header_value",
        "http_claim_wait_seconds",
        "registry",
        "queues",
        "environment",
        "worker_id",
        "concurrency",
        "sync_workers",
        "batch",
        "poll_interval",
        "listen",
        "presence_interval",
        "soft_stop_timeout",
        "expected_environment",
        "expected_installation_id",
        "allow_production",
        "pool_size",
    )
    return {name: getattr(args, name) for name in names if getattr(args, name) is not None}


async def _run_auth_sync(args: argparse.Namespace) -> Any:
    try:
        from outlabs_auth import SimpleRBAC
        from taskq.http.outlabs import provision_taskq_auth
    except ModuleNotFoundError:
        raise TaskqConfigError(
            "taskq auth requires the OutLabs extra: install 'outlabs-taskq[outlabs]'"
        ) from None

    if not args.dsn:
        raise TaskqConfigError("auth DSN is required via --dsn or TASKQ_AUTH_DSN")
    queues = tuple(part.strip() for part in args.queues.split(",") if part.strip())
    if not queues:
        raise TaskqConfigError("--queues must contain at least one canonical queue")
    url = _normalized_url(args.dsn)
    if not _is_asyncpg_url(url):
        raise TaskqConfigError("taskq auth provisioning requires an asyncpg PostgreSQL DSN")
    auth = SimpleRBAC(
        database_url=_asyncpg_dsn(url),
        database_schema=args.schema,
        secret_key=secrets.token_urlsafe(48),
        auto_migrate=False,
    )
    try:
        await auth.initialize()
        async with auth.get_session() as session:
            report = await provision_taskq_auth(
                auth,
                session,
                queues=queues,
                roles=None if args.roles == "none" else "standard",
                role_prefix=args.role_prefix,
                mode="apply" if args.apply else "report",
                reconcile=args.reconcile,
                per_queue_roles=args.per_queue_roles,
            )
            if args.apply and report.ok:
                await session.commit()
            else:
                await session.rollback()
            return report
    finally:
        await auth.shutdown()


async def _run_auth_apply(
    args: argparse.Namespace,
    *,
    validate_plan: Callable[[Any], None],
) -> Any:
    """Recompute and apply one IAM plan under an auth-database transaction lock."""

    try:
        from outlabs_auth import SimpleRBAC
        from taskq.http.outlabs import provision_taskq_auth
    except ModuleNotFoundError:
        raise TaskqConfigError(
            "taskq auth requires the OutLabs extra: install 'outlabs-taskq[outlabs]'"
        ) from None

    if not args.dsn:
        raise TaskqConfigError("auth DSN is required via --dsn or TASKQ_AUTH_DSN")
    queues = tuple(part.strip() for part in args.queues.split(",") if part.strip())
    if not queues:
        raise TaskqConfigError("--queues must contain at least one canonical queue")
    url = _normalized_url(args.dsn)
    if not _is_asyncpg_url(url):
        raise TaskqConfigError("taskq auth provisioning requires an asyncpg PostgreSQL DSN")
    auth = SimpleRBAC(
        database_url=_asyncpg_dsn(url),
        database_schema=args.schema,
        secret_key=secrets.token_urlsafe(48),
        auto_migrate=False,
    )
    try:
        await auth.initialize()
        async with auth.get_session() as session:
            await session.execute(
                text(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended('taskq:cli:auth', 0))"
                )
            )
            report = await provision_taskq_auth(
                auth,
                session,
                queues=queues,
                roles=None if args.roles == "none" else "standard",
                role_prefix=args.role_prefix,
                mode="report",
                reconcile=args.reconcile,
                per_queue_roles=args.per_queue_roles,
            )
            validate_plan(report)
            applied = await provision_taskq_auth(
                auth,
                session,
                queues=queues,
                roles=None if args.roles == "none" else "standard",
                role_prefix=args.role_prefix,
                mode="apply",
                reconcile=args.reconcile,
                per_queue_roles=args.per_queue_roles,
            )
            if applied.ok:
                await session.commit()
            else:
                await session.rollback()
            return applied
    finally:
        await auth.shutdown()


def _print_auth_report(report: Any) -> None:
    print(f"mode: {report.mode}")
    for heading in ("created", "existing", "changed", "conflicting"):
        values = getattr(report, heading)
        print(f"{heading}: {len(values)}")
        for value in values:
            print(f"  - {value}")
    for note in report.policy_notes:
        print(f"policy: {note}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="taskq",
        description="Postgres-native task queue — schema install/upgrade and drift checks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_migrate = subparsers.add_parser(
        "migrate",
        help="apply missing packaged migrations under an advisory lock (ADR-004)",
    )
    p_migrate.add_argument("dsn", nargs="?", help=_DSN_HELP)

    p_verify = subparsers.add_parser(
        "verify",
        help="read-only exact-manifest drift check: catalog, grants, roles, seeds, checksums",
    )
    p_verify.add_argument("dsn", nargs="?", help=_DSN_HELP)
    _add_worker_parser(subparsers)
    _add_auth_parser(subparsers)
    _add_target_parser(subparsers)
    _add_scheduler_parser(subparsers)
    _add_schedule_manifest_parser(subparsers)

    args = parser.parse_args(argv)
    _warn_for_argv_credentials(args)

    if args.command == "migrate":
        dsn = _required_dsn(parser, args.dsn, command="migrate")
        applied = _run_migrate(dsn)
        if applied:
            for migration_id in applied:
                print(f"applied {migration_id}")
        else:
            print("schema is up to date (no pending migrations)")
    elif args.command == "verify":
        dsn = _required_dsn(parser, args.dsn, command="verify")
        report = _run_verify(dsn)
        _print_report(report)
        if not report.ok:
            raise SystemExit(1)
        print("verify: ok")
    elif args.command == "worker":
        try:
            settings = WorkerSettings(**_worker_overrides(args))
            registry = _load_registry(settings.registry)
            _validate_subscriptions(registry, settings.queues)
        except ValidationError as exc:
            parser.error(_settings_error(exc))
        except TaskqConfigError as exc:
            parser.error(str(exc))
        try:
            exit_code = asyncio.run(_run_worker(settings, registry))
        except Exception as exc:
            print(f"taskq worker failed: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(1) from None
        if exit_code:
            raise SystemExit(exit_code)
    elif args.command == "auth":
        args.dsn = args.dsn or os.environ.get("TASKQ_AUTH_DSN")
        try:
            report = asyncio.run(_run_auth_sync(args))
        except (TaskqConfigError, ValueError) as exc:
            parser.error(str(exc))
        except Exception as exc:
            print(f"taskq auth sync failed: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(1) from None
        _print_auth_report(report)
        if report.conflicting:
            raise SystemExit(2)
    elif args.command == "target":
        args.dsn = args.dsn or os.environ.get("TASKQ_DSN")
        if not args.dsn:
            parser.error("target DSN is required via --dsn or TASKQ_DSN")
        if args.target_command == "bind":
            if args.expected_binding_version < 0:
                parser.error("--expected-binding-version must be non-negative")
            if args.rotate and not args.reason:
                parser.error("--rotate requires --reason")
            if not args.rotate and args.reason:
                parser.error("--reason is valid only with --rotate")
            if args.environment == "production" and not args.allow_production:
                parser.error("binding production requires --allow-production")
        try:
            profile = asyncio.run(_run_target_command(args))
        except Exception as exc:
            print(f"taskq target failed: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(1) from None
        _print_target(profile, as_json=bool(getattr(args, "json", False)))
    elif args.command == "scheduler":
        try:
            summary = asyncio.run(_run_scheduler_command(args))
        except (ValidationError, TaskqConfigError) as exc:
            parser.error(_settings_error(exc) if isinstance(exc, ValidationError) else str(exc))
        except TaskqValidationError:
            print("taskq scheduler refused the configured target", file=sys.stderr)
            raise SystemExit(2) from None
        except (TaskqVersionError, TaskqCapabilityError, TaskqUnavailableError) as exc:
            print(f"taskq scheduler unavailable: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(3) from None
        except Exception as exc:
            print(f"taskq scheduler failed: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(1) from None
        if summary is not None:
            if args.json:
                print(
                    json.dumps(
                        summary.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            else:
                for key, value in summary.model_dump(mode="json").items():
                    print(f"{key}: {value}")
            if getattr(summary, "outcome", None) == "budget_exhausted":
                raise SystemExit(3)
            if getattr(summary, "ready", True) is False:
                raise SystemExit(2)
    elif args.command == "schedule":
        args.dsn = args.dsn or os.environ.get("TASKQ_DSN")
        if not args.dsn:
            parser.error("schedule DSN is required via --dsn or TASKQ_DSN")
        if hasattr(args, "expected_environment") and args.expected_environment is None:
            args.expected_environment = os.environ.get("TASKQ_EXPECTED_ENV")
        if hasattr(args, "expected_installation_id") and args.expected_installation_id is None:
            value = os.environ.get("TASKQ_EXPECTED_INSTALLATION_ID")
            if value:
                try:
                    args.expected_installation_id = UUID(value)
                except ValueError:
                    parser.error("TASKQ_EXPECTED_INSTALLATION_ID must be a UUID")
        try:
            result = asyncio.run(_run_schedule_manifest_command(args))
        except (ValidationError, TaskqConfigError) as exc:
            parser.error(_settings_error(exc) if isinstance(exc, ValidationError) else str(exc))
        except TaskqValidationError:
            print("taskq schedule refused the configured target", file=sys.stderr)
            raise SystemExit(2) from None
        except (TaskqVersionError, TaskqCapabilityError, TaskqUnavailableError) as exc:
            print(f"taskq schedule unavailable: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(3) from None
        except Exception as exc:
            print(f"taskq schedule failed: {type(exc).__name__}", file=sys.stderr)
            raise SystemExit(1) from None
        _print_manifest_result(result, as_json=args.json)


if __name__ == "__main__":
    main()
