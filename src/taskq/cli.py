"""taskq CLI — `taskq migrate <dsn>` / `taskq verify <dsn>` (ADR-004).

DSN handling: a bare ``postgresql://`` (or ``postgres://``) DSN runs on the
bundled asyncpg driver (the CLI drives it with a private event loop, so the
command itself is synchronous). A DSN that names an explicit synchronous
driver — e.g. ``postgresql+psycopg2://`` — runs on a plain synchronous
engine, provided that driver is installed in the host environment.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import logging
import os
import secrets
import signal
import socket
import sys
from collections.abc import Callable
from typing import NoReturn

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from taskq.errors import TaskqConfigError
from taskq.registry import TaskRegistry
from taskq.settings import WorkerSettings
from taskq.sql import VerifyReport, migrate, migrate_sync, verify, verify_sync
from taskq.sql.notifications import PostgresNotificationSource
from taskq.sql.transport import SqlTaskqTransport
from taskq.worker import WorkerOptions, WorkerService, WorkerServiceOptions

_DSN_HELP = (
    "postgresql://user:pass@host:port/dbname "
    "(bare DSNs use the bundled asyncpg driver; postgresql+psycopg2://... "
    "selects a synchronous engine instead)"
)


def _normalized_url(dsn: str) -> URL:
    url = make_url(dsn)
    if url.drivername == "postgres":  # legacy alias SQLAlchemy no longer accepts
        url = url.set(drivername="postgresql")
    return url


def _is_asyncpg_url(url: URL) -> bool:
    return url.drivername == "postgresql" or url.drivername.endswith("+asyncpg")


def _run_migrate(dsn: str) -> list[str]:
    url = _normalized_url(dsn)
    if _is_asyncpg_url(url):

        async def _go() -> list[str]:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
            try:
                async with engine.connect() as conn:
                    return await migrate(conn)
            finally:
                await engine.dispose()

        return asyncio.run(_go())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return migrate_sync(conn)
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
    worker.add_argument("--dsn")
    worker.add_argument("--http-base-url")
    worker.add_argument("--http-bearer-token")
    worker.add_argument("--http-header-name")
    worker.add_argument("--http-header-value")
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
    worker.add_argument("--allow-production", action=argparse.BooleanOptionalAction, default=None)
    worker.add_argument("--pool-size", type=int)


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
        "allow_production",
        "pool_size",
    )
    return {name: getattr(args, name) for name in names if getattr(args, name) is not None}


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
    p_migrate.add_argument("dsn", help=_DSN_HELP)

    p_verify = subparsers.add_parser(
        "verify",
        help="read-only exact-manifest drift check: catalog, grants, roles, seeds, checksums",
    )
    p_verify.add_argument("dsn", help=_DSN_HELP)
    _add_worker_parser(subparsers)

    args = parser.parse_args(argv)

    if args.command == "migrate":
        applied = _run_migrate(args.dsn)
        if applied:
            for migration_id in applied:
                print(f"applied {migration_id}")
        else:
            print("schema is up to date (no pending migrations)")
    elif args.command == "verify":
        report = _run_verify(args.dsn)
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


if __name__ == "__main__":
    main()
