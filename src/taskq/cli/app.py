"""Resource-oriented, non-interactive TaskQ command line application."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from argparse import Namespace
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

import click
import yaml
from pydantic import TypeAdapter
from sqlalchemy import create_engine, text

from taskq import __version__
from taskq.errors import TaskqCapabilityError, TaskqConfigError
from taskq.protocol import (
    EnqueueCommand,
    EnqueueManyItem,
    ScheduleState,
    TargetIdentityProfile,
)
from taskq.scheduler import (
    SchedulerSettings,
    apply_manifest,
    load_manifest,
    plan_manifest,
    scheduler_from_settings,
)
from taskq.settings import WorkerSettings
from taskq.sql import discover_migrations

from . import _runtime
from .context import default_config_path, load_context_file, redacted_context, resolve_connection
from .cursor import decode_cursor, encode_cursor
from .errors import CliOperationError, CliSafetyError, CliTimeoutError, normalize_error
from .models import CliMeta, CliSuccessEnvelope, OutputFormat, ResolvedConnection
from .output import jsonable, render_error, render_success
from .specs import COMMAND_SPECS
from .transport import CliTransport, open_cli_transport


_OUTPUTS = click.Choice(("table", "json", "yaml", "jsonl", "name"), case_sensitive=True)
_COMMAND_CONTEXT = "taskq.cli/v1"


def _extract_output(argv: list[str]) -> OutputFormat:
    for index, value in enumerate(argv):
        if value in {"-o", "--output"} and index + 1 < len(argv):
            candidate = argv[index + 1]
            if candidate in {"table", "json", "yaml", "jsonl", "name"}:
                return candidate  # type: ignore[return-value]
        if value.startswith("--output="):
            candidate = value.partition("=")[2]
            if candidate in {"table", "json", "yaml", "jsonl", "name"}:
                return candidate  # type: ignore[return-value]
    return "table"


def _extract_request_id(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--request-id" and index + 1 < len(argv):
            return argv[index + 1][:200]
        if value.startswith("--request-id="):
            return value.partition("=")[2][:200]
    return None


def _normalize_global_options(argv: list[str]) -> list[str]:
    """Allow root connection/output options before or after a command path."""

    value_options = {
        "-o",
        "--output",
        "--context",
        "--config",
        "--dsn",
        "--dsn-env",
        "--http-base-url",
        "--http-bearer-token",
        "--http-header-name",
        "--http-header-value",
        "--expected-environment",
        "--expected-installation-id",
        "--actor",
        "--request-id",
    }
    flag_options = {"--allow-production", "--yes", "--version"}
    prefixes = tuple(f"{value}=" for value in value_options if value.startswith("--"))
    global_values: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value in value_options:
            global_values.append(value)
            if index + 1 < len(argv):
                global_values.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        elif value in flag_options or value.startswith(prefixes):
            global_values.append(value)
            index += 1
            continue
        remaining.append(value)
        index += 1
    return [*global_values, *remaining]


@dataclass(slots=True)
class CliState:
    output: OutputFormat
    context_name: str | None
    config_path: str | None
    dsn: str | None
    dsn_env: str | None
    http_base_url: str | None
    http_bearer_token: str | None
    http_header_name: str | None
    http_header_value: str | None
    expected_environment: str | None
    expected_installation_id: UUID | None
    actor: str | None
    allow_production: bool
    yes: bool
    request_id: str
    command: str = "taskq"

    def connection(self) -> ResolvedConnection:
        return resolve_connection(
            context_name=self.context_name,
            config_path=self.config_path,
            dsn=self.dsn,
            dsn_env=self.dsn_env,
            http_base_url=self.http_base_url,
            http_bearer_token=self.http_bearer_token,
            http_header_name=self.http_header_name,
            http_header_value=self.http_header_value,
            expected_environment=self.expected_environment,
            expected_installation_id=self.expected_installation_id,
            actor=self.actor,
        )

    def emit(
        self,
        command: str,
        kind: str,
        data: Any,
        *,
        connection: ResolvedConnection | None = None,
        target: Any = None,
        next_cursor: str | None = None,
        sensitive: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> None:
        render_success(
            CliSuccessEnvelope(
                kind=kind,
                command=command,
                data=jsonable(data),
                meta=CliMeta(
                    context=connection.context if connection else self.context_name,
                    transport=connection.transport if connection else None,
                    target=jsonable(target) if target is not None else None,
                    next_cursor=next_cursor,
                    sensitive_fields_included=sensitive,
                    request_id=self.request_id,
                ),
                warnings=warnings,
            ),
            self.output,
        )


pass_state = click.make_pass_decorator(CliState)


def _validate_target(
    state: CliState,
    connection: ResolvedConnection,
    target: Any,
    *,
    mutates: bool,
    destructive: bool,
) -> None:
    environment = target.environment
    installation_id = target.installation_id
    if mutates and connection.expected_environment is None:
        raise CliSafetyError("mutation requires an explicit expected environment")
    if connection.expected_environment and environment != connection.expected_environment:
        raise CliSafetyError("target environment does not match the selected context")
    if (
        connection.expected_installation_id
        and installation_id != connection.expected_installation_id
    ):
        raise CliSafetyError("target installation does not match the selected context")
    if mutates and environment == "unbound":
        raise CliSafetyError("mutations are refused while the target is unbound")
    if destructive and not state.yes:
        raise CliSafetyError("this destructive command requires --yes")
    if mutates and environment == "production":
        if connection.expected_installation_id is None:
            raise CliSafetyError("production mutation requires an expected installation id")
        if not state.allow_production:
            raise CliSafetyError("production mutation requires literal --allow-production")
        if not state.yes:
            raise CliSafetyError("production mutation requires --yes")


async def _transport_operation(
    state: CliState,
    command: str,
    kind: str,
    operation: Callable[[CliTransport], Awaitable[Any]],
    *,
    mutates: bool | None = None,
    destructive: bool | None = None,
    sensitive: bool = False,
    cursor: tuple[dict[str, Any], Callable[[Any], Any]] | None = None,
) -> Any:
    state.command = command
    spec = COMMAND_SPECS[command]
    connection = state.connection()
    if connection.transport not in spec.transports:
        raise TaskqCapabilityError(details={"command": command, "transport": connection.transport})
    effective_mutation = spec.mutates if mutates is None else mutates
    if connection.transport == "sql" and effective_mutation and not connection.actor:
        raise TaskqConfigError(
            "direct-SQL mutation requires --actor, TASKQ_ACTOR, or context actor"
        )
    async with open_cli_transport(connection) as transport:
        target = await transport.target()
        _validate_target(
            state,
            connection,
            target,
            mutates=effective_mutation,
            destructive=spec.destructive if destructive is None else destructive,
        )
        if spec.capability is not None:
            active = set((await transport.meta()).capabilities.get("active", ()))
            if spec.capability not in active:
                raise TaskqCapabilityError(
                    details={"command": command, "capability": spec.capability}
                )
        result = await operation(transport)
        next_token = None
        if cursor is not None:
            filters, getter = cursor
            next_value = getter(result)
            next_token = encode_cursor(
                command=command,
                transport=connection.transport,
                target=str(target.installation_id),
                filters=filters,
                value=next_value,
            )
        rendered_result = result
        if isinstance(result, dict) and "next_cursor_value" in result:
            rendered_result = {
                key: value for key, value in result.items() if key != "next_cursor_value"
            }
        state.emit(
            command,
            kind,
            rendered_result,
            connection=connection,
            target=target,
            next_cursor=next_token,
            sensitive=sensitive,
        )
        return result


async def _detached_runtime_operation(
    state: CliState,
    command: str,
    kind: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    """Preflight a runtime, close the probe transport, then start its owned transport.

    Workers and schedulers construct their own long-lived transport from settings. Keeping
    the short-lived CLI preflight open for the lifetime of either process wastes a second
    pool/client and can exhaust small managed-Postgres connection budgets.
    """

    state.command = command
    spec = COMMAND_SPECS[command]
    connection = state.connection()
    if connection.transport not in spec.transports:
        raise TaskqCapabilityError(details={"command": command, "transport": connection.transport})
    if connection.transport == "sql" and spec.mutates and not connection.actor:
        raise TaskqConfigError(
            "direct-SQL mutation requires --actor, TASKQ_ACTOR, or context actor"
        )

    async with open_cli_transport(connection) as transport:
        target = await transport.target()
        _validate_target(
            state,
            connection,
            target,
            mutates=spec.mutates,
            destructive=spec.destructive,
        )
        if spec.capability is not None:
            active = set((await transport.meta()).capabilities.get("active", ()))
            if spec.capability not in active:
                raise TaskqCapabilityError(
                    details={"command": command, "capability": spec.capability}
                )

    result = await operation()
    state.emit(command, kind, result, connection=connection, target=target)
    return result


def _run(operation: Awaitable[Any]) -> Any:
    return asyncio.run(operation)


def _load_input(path: str) -> Any:
    if path == "-":
        raw = sys.stdin.read()
        suffix = ""
    else:
        target = Path(path)
        raw = target.read_text(encoding="utf-8")
        suffix = target.suffix.lower()
    try:
        return json.loads(raw) if suffix == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise TaskqConfigError("input is not valid JSON or YAML") from exc


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-o", "--output", type=_OUTPUTS, default="table", show_default=True)
@click.option("--context", "context_name")
@click.option("--config", "config_path", type=click.Path(dir_okay=False))
@click.option("--dsn", help="Prefer an environment-backed context when credentials are present.")
@click.option(
    "--dsn-env",
    help="Name of the environment variable containing the PostgreSQL DSN.",
)
@click.option("--http-base-url")
@click.option("--http-bearer-token", hidden=True)
@click.option("--http-header-name")
@click.option("--http-header-value", hidden=True)
@click.option("--expected-environment")
@click.option("--expected-installation-id", type=click.UUID)
@click.option("--actor")
@click.option("--allow-production", is_flag=True)
@click.option("--yes", is_flag=True, help="Acknowledge a destructive or production mutation.")
@click.option("--request-id", default=None)
@click.version_option(__version__, prog_name="taskq")
@click.pass_context
def cli(
    ctx: click.Context,
    output: OutputFormat,
    context_name: str | None,
    config_path: str | None,
    dsn: str | None,
    dsn_env: str | None,
    http_base_url: str | None,
    http_bearer_token: str | None,
    http_header_name: str | None,
    http_header_value: str | None,
    expected_environment: str | None,
    expected_installation_id: UUID | None,
    actor: str | None,
    allow_production: bool,
    yes: bool,
    request_id: str | None,
) -> None:
    """Operate TaskQ completely from a terminal or coding agent."""

    ctx.obj = CliState(
        output=output,
        context_name=context_name,
        config_path=config_path,
        dsn=dsn,
        dsn_env=dsn_env,
        http_base_url=http_base_url,
        http_bearer_token=http_bearer_token,
        http_header_name=http_header_name,
        http_header_value=http_header_value,
        expected_environment=expected_environment,
        expected_installation_id=expected_installation_id,
        actor=actor,
        allow_production=allow_production,
        yes=yes,
        request_id=request_id or str(uuid4()),
    )


@cli.command("commands")
@pass_state
def commands_command(state: CliState) -> None:
    """Emit the complete stable command catalog."""

    state.emit(
        "commands", "CommandCatalog", {"items": [spec.as_dict() for spec in COMMAND_SPECS.values()]}
    )


@cli.command("schema")
@click.argument("command_path")
@pass_state
def schema_command(state: CliState, command_path: str) -> None:
    """Emit input/output JSON Schemas and operational metadata."""

    spec = COMMAND_SPECS.get(command_path)
    if spec is None:
        raise TaskqConfigError(f"unknown command path: {command_path}")
    state.emit("schema", "CommandSchema", spec.as_dict())


@cli.command("completion")
@click.argument("shell", type=click.Choice(("bash", "zsh", "fish")))
def completion_command(shell: str) -> None:
    """Generate a completion script without modifying shell files."""

    from click.shell_completion import get_completion_class

    completion_class = get_completion_class(shell)
    if completion_class is None:
        raise TaskqConfigError(f"completion is unavailable for {shell}")
    complete = completion_class(cli, {}, "taskq", "_TASKQ_COMPLETE")
    click.echo(complete.source())


@cli.command("version")
@click.option("--remote", is_flag=True, help="Also inspect the selected TaskQ target.")
@pass_state
def version_command(state: CliState, remote: bool) -> None:
    """Show CLI/package and optional remote contract versions."""

    if not remote:
        state.emit(
            "version", "Version", {"client_version": __version__, "cli_api": _COMMAND_CONTEXT}
        )
        return

    async def operation(transport: CliTransport) -> Any:
        meta = await transport.meta()
        return {
            "client_version": __version__,
            "cli_api": _COMMAND_CONTEXT,
            "contract_version": meta.contract_version,
            "capabilities": meta.capabilities,
        }

    _run(_transport_operation(state, "version", "Version", operation))


@cli.command("capabilities")
@pass_state
def capabilities_command(state: CliState) -> None:
    """Show active server capabilities and command availability."""

    async def operation(transport: CliTransport) -> Any:
        meta = await transport.meta()
        active = set(meta.capabilities.get("active", []))
        items = []
        for spec in COMMAND_SPECS.values():
            available = transport.mode in spec.transports and (
                spec.capability is None or spec.capability in active
            )
            items.append(
                {
                    "command": spec.path,
                    "available": available,
                    "capability": spec.capability,
                    "transports": list(spec.transports),
                }
            )
        return {"contract_version": meta.contract_version, "active": sorted(active), "items": items}

    _run(_transport_operation(state, "capabilities", "Capabilities", operation))


@cli.command("doctor")
@pass_state
def doctor_command(state: CliState) -> None:
    """Run safe, read-only readiness checks with remediation codes."""

    async def operation(transport: CliTransport) -> Any:
        checks: list[dict[str, Any]] = []
        meta = await transport.meta()
        checks.append({"code": "contract", "status": "pass", "detail": meta.contract_version})
        try:
            health = await transport.scheduler_health()
        except (TaskqCapabilityError, PermissionError):
            checks.append(
                {
                    "code": "scheduler_health",
                    "status": "warn",
                    "detail": "unavailable to the selected principal or contract",
                    "remediation": "grant global read access or use a scheduler context",
                }
            )
        else:
            checks.append(
                {
                    "code": "scheduler_health",
                    "status": "pass",
                    "detail": jsonable(health),
                }
            )
        return {
            "ready": not any(check["status"] == "fail" for check in checks),
            "degraded": any(check["status"] == "warn" for check in checks),
            "checks": checks,
        }

    result = _run(_transport_operation(state, "doctor", "DoctorReport", operation))
    if result["degraded"]:
        raise SystemExit(5)


@cli.group("context")
def context_group() -> None:
    """Inspect secret-free named connection contexts."""


@context_group.command("list")
@pass_state
def context_list_command(state: CliState) -> None:
    context_file = load_context_file(state.config_path, required=False)
    state.emit(
        "context.list",
        "ContextList",
        {
            "path": str(
                Path(state.config_path).expanduser() if state.config_path else default_config_path()
            ),
            "items": [
                {"name": name, **redacted_context(value)}
                for name, value in sorted(context_file.contexts.items())
            ],
        },
    )


@context_group.command("show")
@click.argument("name")
@pass_state
def context_show_command(state: CliState, name: str) -> None:
    context_file = load_context_file(state.config_path)
    value = context_file.contexts.get(name)
    if value is None:
        raise TaskqConfigError(f"unknown context: {name}")
    state.emit("context.show", "Context", {"name": name, **redacted_context(value)})


@context_group.command("validate")
@pass_state
def context_validate_command(state: CliState) -> None:
    value = load_context_file(state.config_path)
    state.emit(
        "context.validate",
        "ContextValidation",
        {"valid": True, "contexts": len(value.contexts)},
    )


def _migration_plan(dsn: str) -> dict[str, Any]:
    migrations = discover_migrations()
    url = _runtime._normalized_url(dsn)
    if _runtime._is_asyncpg_url(url):
        from sqlalchemy.ext.asyncio import create_async_engine

        async def read() -> tuple[list[str], dict[str, Any] | None]:
            engine = create_async_engine(url.set(drivername="postgresql+asyncpg"))
            try:
                async with engine.connect() as connection:
                    ledger_exists = await connection.scalar(
                        text("SELECT to_regclass('taskq.schema_migrations')")
                    )
                    applied: list[str] = []
                    if ledger_exists is not None:
                        rows = await connection.execute(
                            text("SELECT id FROM taskq.schema_migrations ORDER BY id")
                        )
                        applied = [str(row[0]) for row in rows]
                    target_exists = await connection.scalar(
                        text("SELECT to_regprocedure('taskq.get_target_identity()')")
                    )
                    target = None
                    if target_exists is not None:
                        row = (
                            (
                                await connection.execute(
                                    text("SELECT * FROM taskq.get_target_identity()")
                                )
                            )
                            .mappings()
                            .one()
                        )
                        target = jsonable(dict(row))
                    return applied, target
            finally:
                await engine.dispose()

        applied, target = asyncio.run(read())
    else:
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                ledger_exists = connection.scalar(
                    text("SELECT to_regclass('taskq.schema_migrations')")
                )
                applied = (
                    [
                        str(row[0])
                        for row in connection.execute(
                            text("SELECT id FROM taskq.schema_migrations ORDER BY id")
                        )
                    ]
                    if ledger_exists is not None
                    else []
                )
                target_exists = connection.scalar(
                    text("SELECT to_regprocedure('taskq.get_target_identity()')")
                )
                target = (
                    jsonable(
                        dict(
                            connection.execute(text("SELECT * FROM taskq.get_target_identity()"))
                            .mappings()
                            .one()
                        )
                    )
                    if target_exists is not None
                    else None
                )
        finally:
            engine.dispose()
    applied_set = set(applied)
    pending = [migration for migration in migrations if migration.id not in applied_set]
    digest_source = {
        "client_version": __version__,
        "target": target,
        "applied": applied,
        "pending": [{"id": item.id, "checksum": item.checksum} for item in pending],
    }
    digest = hashlib.sha256(
        json.dumps(digest_source, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**digest_source, "plan_digest": digest, "changes": bool(pending)}


@cli.group("db")
def db_group() -> None:
    """Plan, apply, and verify the packaged SQL contract."""


def _sql_dsn(state: CliState) -> str:
    connection = state.connection()
    if connection.transport != "sql" or connection.dsn is None:
        raise TaskqConfigError("database commands require a direct SQL context")
    return connection.dsn.get_secret_value()


@db_group.command("plan")
@pass_state
def db_plan_command(state: CliState) -> None:
    plan = _migration_plan(_sql_dsn(state))
    state.emit(
        "db.plan",
        "MigrationPlan",
        plan,
        connection=state.connection(),
        target=plan["target"],
    )


@db_group.command("migrate")
@click.option("--plan-digest", required=True)
@pass_state
def db_migrate_command(state: CliState, plan_digest: str) -> None:
    if not state.yes:
        raise CliSafetyError("database migration requires --yes")
    dsn = _sql_dsn(state)
    connection = state.connection()
    if not connection.actor:
        raise TaskqConfigError(
            "direct-SQL mutation requires --actor, TASKQ_ACTOR, or context actor"
        )
    plan = _migration_plan(dsn)
    if plan["plan_digest"] != plan_digest:
        raise CliSafetyError("migration plan digest no longer matches current state")
    if plan["target"] is not None:
        target = TargetIdentityProfile.model_validate(plan["target"])
        _validate_target(
            state,
            connection,
            target,
            mutates=target.environment != "unbound",
            destructive=True,
        )
    expected_pending = tuple((str(item["id"]), str(item["checksum"])) for item in plan["pending"])
    applied = _runtime._run_migrate(dsn, expected_pending)
    state.emit(
        "db.migrate",
        "MigrationResult",
        {"applied": applied, "up_to_date": not applied},
        connection=connection,
        target=plan["target"],
    )


@db_group.command("verify")
@pass_state
def db_verify_command(state: CliState) -> None:
    report = _runtime._run_verify(_sql_dsn(state))
    state.emit("db.verify", "VerifyReport", report, connection=state.connection())
    if not report.ok:
        raise SystemExit(1)


@cli.group("target")
def target_group() -> None:
    """Inspect and explicitly bind database target identity."""


@target_group.command("show")
@pass_state
def target_show_command(state: CliState) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.target()

    _run(_transport_operation(state, "target.show", "TargetIdentity", operation))


@target_group.command("bind")
@click.argument("environment")
@click.option("--expected-binding-version", type=click.IntRange(min=0), required=True)
@click.option("--rotate", is_flag=True)
@click.option("--reason")
@pass_state
def target_bind_command(
    state: CliState,
    environment: str,
    expected_binding_version: int,
    rotate: bool,
    reason: str | None,
) -> None:
    if not state.yes:
        raise CliSafetyError("target bind/rotate requires --yes")
    if rotate and not reason:
        raise TaskqConfigError("--rotate requires --reason")
    if not rotate and reason:
        raise TaskqConfigError("--reason is valid only with --rotate")
    connection = state.connection()
    if connection.transport != "sql" or connection.dsn is None:
        raise TaskqConfigError("target bind requires a direct SQL context")
    if connection.expected_installation_id is None:
        raise CliSafetyError("target bind requires an expected installation id")
    if not connection.actor:
        raise TaskqConfigError("target bind requires an actor")
    if environment == "production" and not state.allow_production:
        raise CliSafetyError("binding production requires literal --allow-production")

    async def bind() -> Any:
        from taskq.sql.transport import SqlTaskqTransport

        transport = SqlTaskqTransport.from_dsn(connection.dsn.get_secret_value())
        try:
            return await transport.bind_target_identity(
                connection.expected_installation_id,
                environment,
                connection.actor or "",
                expected_binding_version,
                rotate=rotate,
                reason=reason,
            )
        finally:
            await transport.aclose()

    profile = _run(bind())
    state.emit("target.bind", "TargetIdentity", profile, connection=connection, target=profile)


def _profile_input(path: str) -> dict[str, Any]:
    from .models import CliQueueProfileInput

    value = CliQueueProfileInput.model_validate(_load_input(path))
    return value.model_dump(exclude_none=True)


@cli.group("queue")
def queue_group() -> None:
    """Inspect and operate queue configuration and bounded queue state."""


@queue_group.command("list")
@pass_state
def queue_list_command(state: CliState) -> None:
    async def operation(transport: CliTransport) -> Any:
        return {"items": jsonable(await transport.queue_stats())}

    _run(_transport_operation(state, "queue.list", "QueueList", operation))


@queue_group.command("show")
@click.argument("queue")
@pass_state
def queue_show_command(state: CliState, queue: str) -> None:
    async def operation(transport: CliTransport) -> Any:
        profile, stats = await asyncio.gather(
            transport.queue_show(queue), transport.queue_stats(queue)
        )
        return {"profile": jsonable(profile), "stats": jsonable(stats)}

    _run(_transport_operation(state, "queue.show", "Queue", operation))


@queue_group.command("ensure")
@click.argument("queue")
@click.option(
    "--input", "input_path", required=True, type=click.Path(dir_okay=False, allow_dash=True)
)
@pass_state
def queue_ensure_command(state: CliState, queue: str, input_path: str) -> None:
    profile = _profile_input(input_path)

    async def operation(transport: CliTransport) -> Any:
        return await transport.queue_ensure(queue, profile)

    _run(_transport_operation(state, "queue.ensure", "QueueMutation", operation))


@queue_group.command("update")
@click.argument("queue")
@click.option(
    "--input", "input_path", required=True, type=click.Path(dir_okay=False, allow_dash=True)
)
@click.option("--expected-version", type=click.IntRange(min=1), required=True)
@pass_state
def queue_update_command(
    state: CliState, queue: str, input_path: str, expected_version: int
) -> None:
    profile = _profile_input(input_path)

    async def operation(transport: CliTransport) -> Any:
        return await transport.queue_update(queue, profile, expected_version)

    _run(_transport_operation(state, "queue.update", "QueueMutation", operation))


@queue_group.command("pause")
@click.argument("queue")
@click.option("--reason")
@pass_state
def queue_pause_command(state: CliState, queue: str, reason: str | None) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.queue_pause(queue, reason)

    _run(_transport_operation(state, "queue.pause", "QueueControl", operation))


@queue_group.command("resume")
@click.argument("queue")
@pass_state
def queue_resume_command(state: CliState, queue: str) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.queue_resume(queue)

    _run(_transport_operation(state, "queue.resume", "QueueControl", operation))


@queue_group.command("purge")
@click.argument("queue")
@click.option("--limit", type=click.IntRange(min=1, max=1000), default=100, show_default=True)
@click.option("--reason")
@pass_state
def queue_purge_command(state: CliState, queue: str, limit: int, reason: str | None) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.queue_purge(queue, limit, reason)

    _run(_transport_operation(state, "queue.purge", "QueuePurge", operation))


@queue_group.command("redrive-failed")
@click.argument("queue")
@click.option("--limit", type=click.IntRange(min=1, max=1000), default=100, show_default=True)
@pass_state
def queue_redrive_failed_command(state: CliState, queue: str, limit: int) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.queue_redrive_failed(queue, limit)

    _run(_transport_operation(state, "queue.redrive-failed", "QueueRedrive", operation))


def _next_page_value(page: Any) -> Any:
    for field in ("next_after", "next_cursor"):
        value = getattr(page, field, None)
        if value is not None:
            return jsonable(value)
    return None


def _page_items(page: Any) -> list[Any]:
    return list(getattr(page, "items", ()))


async def _collect_pages(
    fetch_page: Callable[[int, Any], Awaitable[Any]],
    *,
    limit: int,
    cursor: Any,
    all_pages: bool,
    next_value: Callable[[Any], Any] = _next_page_value,
) -> dict[str, Any]:
    """Collect a bounded result or exhaust a paginated resource explicitly."""
    items: list[Any] = []
    as_of: Any = None
    raw_cursor = cursor
    page_size = min(limit, 100)
    while True:
        remaining = None if all_pages else limit - len(items)
        if remaining is not None and remaining <= 0:
            break
        page = await fetch_page(
            page_size if remaining is None else min(page_size, remaining), raw_cursor
        )
        as_of = getattr(page, "as_of", as_of)
        items.extend(_page_items(page))
        raw_cursor = next_value(page)
        if raw_cursor is None:
            break
    return {"as_of": as_of, "items": items, "next_cursor_value": raw_cursor}


@cli.group("job")
def job_group() -> None:
    """Inspect, submit, wait for, and control jobs."""


@job_group.command("list")
@click.option("--queue", required=True)
@click.option(
    "--view",
    type=click.Choice(
        ("ready", "scheduled", "blocked", "running", "cancel_requested", "failed", "finished")
    ),
    required=True,
)
@click.option("--limit", type=click.IntRange(min=1, max=10_000), default=50, show_default=True)
@click.option("--cursor")
@click.option("--all", "all_pages", is_flag=True)
@pass_state
def job_list_command(
    state: CliState,
    queue: str,
    view: str,
    limit: int,
    cursor: str | None,
    all_pages: bool,
) -> None:
    filters = {"queue": queue, "view": view}
    connection = state.connection()

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        if view not in {"ready", "running", "finished"}:
            active = set((await transport.meta()).capabilities.get("active", ()))
            if "read_model_job_views_v2" not in active:
                raise TaskqCapabilityError(
                    details={
                        "command": "job.list",
                        "capability": "read_model_job_views_v2",
                        "view": view,
                    }
                )
        raw_cursor = decode_cursor(
            cursor,
            command="job.list",
            transport=connection.transport,
            target=str(target.installation_id),
            filters=filters,
        )
        return await _collect_pages(
            lambda page_limit, page_cursor: transport.job_list(
                queue, view, page_limit, page_cursor
            ),
            limit=limit,
            cursor=raw_cursor,
            all_pages=all_pages,
        )

    result = _run(
        _transport_operation(
            state,
            "job.list",
            "JobList",
            operation,
            cursor=(filters, lambda page: page.get("next_cursor_value")),
        )
    )
    del result


@job_group.command("show")
@click.argument("job_id", type=click.UUID)
@click.option("--include-payload", is_flag=True)
@click.option("--include-result", is_flag=True)
@click.option("--include-progress", is_flag=True)
@click.option("--include-error", is_flag=True)
@pass_state
def job_show_command(
    state: CliState,
    job_id: UUID,
    include_payload: bool,
    include_result: bool,
    include_progress: bool,
    include_error: bool,
) -> None:
    includes = {
        "include_payload": include_payload,
        "include_result": include_result,
        "include_progress": include_progress,
        "include_error": include_error,
    }

    async def operation(transport: CliTransport) -> Any:
        return await transport.job_show(job_id, **includes)

    _run(
        _transport_operation(
            state,
            "job.show",
            "Job",
            operation,
            sensitive=any(includes.values()),
        )
    )


@job_group.command("events")
@click.argument("job_id", type=click.UUID)
@click.option("--limit", type=click.IntRange(min=1, max=100), default=50, show_default=True)
@click.option("--cursor")
@click.option("--include-details", is_flag=True)
@pass_state
def job_events_command(
    state: CliState,
    job_id: UUID,
    limit: int,
    cursor: str | None,
    include_details: bool,
) -> None:
    filters = {"job_id": str(job_id), "include_details": include_details}
    connection = state.connection()

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        raw = decode_cursor(
            cursor,
            command="job.events",
            transport=connection.transport,
            target=str(target.installation_id),
            filters=filters,
        )
        return await transport.job_events(
            job_id, limit=limit, cursor=raw, include_details=include_details
        )

    _run(
        _transport_operation(
            state,
            "job.events",
            "JobEventList",
            operation,
            sensitive=include_details,
            cursor=(filters, _next_page_value),
        )
    )


def _payload_value(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    if value.startswith("@"):
        loaded = _load_input(value[1:])
    else:
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise TaskqConfigError("--payload must be JSON or @FILE") from exc
    if not isinstance(loaded, dict):
        raise TaskqConfigError("job payload must be a JSON object")
    return loaded


@job_group.command("enqueue")
@click.option("--input", "input_path", type=click.Path(dir_okay=False, allow_dash=True))
@click.option("--queue")
@click.option("--type", "job_type")
@click.option("--payload")
@click.option("--idempotency-key")
@click.option("--priority", type=click.IntRange(min=0, max=1000))
@click.option("--allow-unkeyed", is_flag=True)
@pass_state
def job_enqueue_command(
    state: CliState,
    input_path: str | None,
    queue: str | None,
    job_type: str | None,
    payload: str | None,
    idempotency_key: str | None,
    priority: int | None,
    allow_unkeyed: bool,
) -> None:
    field_mode = any(
        value is not None for value in (queue, job_type, payload, idempotency_key, priority)
    )
    if input_path and field_mode:
        raise TaskqConfigError("--input cannot be mixed with individual enqueue fields")
    if input_path:
        command = EnqueueCommand.model_validate(_load_input(input_path))
    else:
        if not queue or not job_type:
            raise TaskqConfigError("enqueue requires --input or both --queue and --type")
        command = EnqueueCommand(
            queue=queue,
            job_type=job_type,
            payload=_payload_value(payload),
            idempotency_key=idempotency_key,
            priority=priority,
        )
    keyed = bool(command.idempotency_key or (command.workflow_id and command.step_key))
    if not keyed and not allow_unkeyed:
        raise CliSafetyError("unkeyed enqueue requires --allow-unkeyed")

    async def operation(transport: CliTransport) -> Any:
        return await transport.enqueue(command)

    _run(_transport_operation(state, "job.enqueue", "EnqueueResult", operation))


@job_group.command("enqueue-many")
@click.option(
    "--input", "input_path", required=True, type=click.Path(dir_okay=False, allow_dash=True)
)
@click.option("--allow-unkeyed", is_flag=True)
@pass_state
def job_enqueue_many_command(state: CliState, input_path: str, allow_unkeyed: bool) -> None:
    raw = _load_input(input_path)
    if not isinstance(raw, dict) or not isinstance(raw.get("queue"), str):
        raise TaskqConfigError("batch input requires queue and items")
    items = TypeAdapter(list[EnqueueManyItem]).validate_python(raw.get("items"))
    if not items:
        raise TaskqConfigError("batch input requires at least one item")
    if not allow_unkeyed and any(item.idempotency_key is None for item in items):
        raise CliSafetyError("every batch item requires idempotency_key or --allow-unkeyed")

    async def operation(transport: CliTransport) -> Any:
        results = await transport.enqueue_many(raw["queue"], items)
        return {"items": results}

    _run(_transport_operation(state, "job.enqueue-many", "EnqueueManyResult", operation))


def _job_control_command(
    state: CliState, command: str, action: str, job_id: UUID, **values: Any
) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.job_control(action, job_id, **values)

    _run(_transport_operation(state, command, "JobControl", operation))


@job_group.command("cancel")
@click.argument("job_id", type=click.UUID)
@click.option("--reason")
@pass_state
def job_cancel_command(state: CliState, job_id: UUID, reason: str | None) -> None:
    _job_control_command(state, "job.cancel", "cancel", job_id, reason=reason)


@job_group.command("redrive")
@click.argument("job_id", type=click.UUID)
@click.option("--reset-progress", is_flag=True)
@pass_state
def job_redrive_command(state: CliState, job_id: UUID, reset_progress: bool) -> None:
    _job_control_command(state, "job.redrive", "redrive", job_id, reset_progress=reset_progress)


@job_group.command("run-now")
@click.argument("job_id", type=click.UUID)
@pass_state
def job_run_now_command(state: CliState, job_id: UUID) -> None:
    _job_control_command(state, "job.run-now", "run_now", job_id)


@job_group.command("reprioritize")
@click.argument("job_id", type=click.UUID)
@click.option("--priority", type=click.IntRange(min=0, max=1000), required=True)
@pass_state
def job_reprioritize_command(state: CliState, job_id: UUID, priority: int) -> None:
    _job_control_command(state, "job.reprioritize", "reprioritize", job_id, priority=priority)


@job_group.command("expire")
@click.argument("job_id", type=click.UUID)
@pass_state
def job_expire_command(state: CliState, job_id: UUID) -> None:
    _job_control_command(state, "job.expire", "expire", job_id)


def _observation_streams(state: CliState, condition: str | None) -> bool:
    return state.output == "jsonl" or (condition is None and state.output in {"table", "name"})


def _record_observation(
    state: CliState,
    command: str,
    event: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    connection: ResolvedConnection,
    target: Any,
    stream: bool,
) -> None:
    events.append(event)
    if stream:
        state.emit(
            command,
            "WatchEvent",
            event,
            connection=connection,
            target=target,
        )


def _finish_observation(
    state: CliState,
    command: str,
    condition: str | None,
    resource: dict[str, Any],
    sequence: int,
    events: list[dict[str, Any]],
    *,
    connection: ResolvedConnection,
    target: Any,
    stream: bool,
) -> None:
    terminal = {"type": "terminal", "sequence": sequence + 1, "resource": resource}
    if stream:
        state.emit(
            command,
            "WatchEvent",
            terminal,
            connection=connection,
            target=target,
        )
    elif condition is None:
        state.emit(
            command,
            "WatchResult",
            {"items": [*events, terminal]},
            connection=connection,
            target=target,
        )
    else:
        state.emit(
            command,
            "WaitResult",
            {
                "condition": condition,
                "observed_at": datetime.now().astimezone(),
                "resource": resource,
            },
            connection=connection,
            target=target,
        )


async def _job_observe(
    state: CliState,
    command: str,
    job_id: UUID,
    *,
    condition: str | None,
    timeout: float,
    poll_interval: float,
) -> Any:
    connection = state.connection()
    async with open_cli_transport(connection) as transport:
        target = await transport.target()
        _validate_target(state, connection, target, mutates=False, destructive=False)
        started = time.monotonic()
        previous: dict[str, Any] | None = None
        sequence = 0
        events: list[dict[str, Any]] = []
        stream = _observation_streams(state, condition)
        while True:
            job = await transport.job_show(job_id)
            current = jsonable(job)
            if current != previous:
                sequence += 1
                _record_observation(
                    state,
                    command,
                    {
                        "type": "initial" if previous is None else "modified",
                        "sequence": sequence,
                        "observed_at": datetime.now().astimezone(),
                        "resource": current,
                    },
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                previous = current
            status = str(job.status)
            terminal = status in {"succeeded", "failed", "cancelled"}
            if condition is not None and (
                status == condition or (condition == "terminal" and terminal)
            ):
                _finish_observation(
                    state,
                    command,
                    condition,
                    current,
                    sequence,
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                return job
            if condition is None and terminal:
                _finish_observation(
                    state,
                    command,
                    condition,
                    current,
                    sequence,
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                return job
            if timeout > 0 and time.monotonic() - started >= timeout:
                raise CliTimeoutError
            await asyncio.sleep(poll_interval)


@job_group.command("watch")
@click.argument("job_id", type=click.UUID)
@click.option("--timeout", type=click.FloatRange(min=0), default=0.0, show_default=True)
@click.option("--poll-interval", type=click.FloatRange(min=0.5), default=2.0, show_default=True)
@pass_state
def job_watch_command(state: CliState, job_id: UUID, timeout: float, poll_interval: float) -> None:
    _run(
        _job_observe(
            state,
            "job.watch",
            job_id,
            condition=None,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    )


@job_group.command("wait")
@click.argument("job_id", type=click.UUID)
@click.option(
    "--for",
    "condition",
    type=click.Choice(("terminal", "succeeded", "failed", "cancelled")),
    default="terminal",
    show_default=True,
)
@click.option("--timeout", type=click.FloatRange(min=0.1), default=30.0, show_default=True)
@click.option("--poll-interval", type=click.FloatRange(min=0.5), default=2.0, show_default=True)
@pass_state
def job_wait_command(
    state: CliState,
    job_id: UUID,
    condition: str,
    timeout: float,
    poll_interval: float,
) -> None:
    _run(
        _job_observe(
            state,
            "job.wait",
            job_id,
            condition=condition,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    )


@cli.group("worker")
def worker_group() -> None:
    """Run workers and inspect their bounded presence records."""


@worker_group.command("run")
@click.option("--registry", required=True, help="Python module:attribute TaskRegistry reference.")
@click.option("--queue", "queues", multiple=True, required=True)
@click.option("--environment", required=True)
@click.option("--worker-id")
@click.option("--concurrency", type=click.IntRange(min=1, max=1000), default=1)
@click.option("--sync-workers", type=click.IntRange(min=1, max=1000))
@click.option("--batch", type=click.IntRange(min=1, max=50), default=1)
@click.option("--poll-interval", type=click.FloatRange(min=0.1), default=5.0)
@click.option("--listen/--no-listen", default=None)
@click.option("--presence-interval", type=click.FloatRange(min=5), default=60.0)
@click.option("--soft-stop-timeout", type=click.FloatRange(min=0))
@click.option("--pool-size", type=click.IntRange(min=1, max=1000))
@click.option("--http-claim-wait-seconds", type=click.FloatRange(min=0, max=30), default=25.0)
@pass_state
def worker_run_command(
    state: CliState,
    registry: str,
    queues: tuple[str, ...],
    environment: str,
    worker_id: str | None,
    concurrency: int,
    sync_workers: int | None,
    batch: int,
    poll_interval: float,
    listen: bool | None,
    presence_interval: float,
    soft_stop_timeout: float | None,
    pool_size: int | None,
    http_claim_wait_seconds: float,
) -> None:
    connection = state.connection()

    async def run_worker() -> Any:
        selected_listen = connection.transport == "sql" if listen is None else listen
        settings = WorkerSettings(
            dsn=connection.dsn,
            http_base_url=connection.base_url,
            http_bearer_token=connection.bearer_token,
            http_header_name=connection.header_name,
            http_header_value=connection.header_value,
            http_claim_wait_seconds=http_claim_wait_seconds,
            registry=registry,
            queues=queues,
            environment=environment,
            worker_id=worker_id,
            concurrency=concurrency,
            sync_workers=sync_workers,
            batch=batch,
            poll_interval=poll_interval,
            listen=selected_listen,
            presence_interval=presence_interval,
            soft_stop_timeout=soft_stop_timeout,
            expected_environment=connection.expected_environment,
            expected_installation_id=connection.expected_installation_id,
            allow_production=state.allow_production,
            pool_size=pool_size,
        )
        loaded = _runtime._load_registry(settings.registry)
        _runtime._validate_subscriptions(loaded, settings.queues)
        exit_code = await _runtime._run_worker(settings, loaded)
        if exit_code:
            raise CliOperationError("worker supervisor reported a fatal exit")
        return {"outcome": "stopped", "worker_id": settings.worker_id}

    _run(_detached_runtime_operation(state, "worker.run", "WorkerRun", run_worker))


@worker_group.command("list")
@click.option("--limit", type=click.IntRange(min=1, max=10_000), default=50)
@click.option("--cursor")
@click.option("--all", "all_pages", is_flag=True)
@pass_state
def worker_list_command(state: CliState, limit: int, cursor: str | None, all_pages: bool) -> None:
    filters: dict[str, Any] = {}
    connection = state.connection()

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        raw = decode_cursor(
            cursor,
            command="worker.list",
            transport=connection.transport,
            target=str(target.installation_id),
            filters=filters,
        )

        def worker_cursor(page: Any) -> Any:
            if getattr(page, "next_worker_id", None) is not None:
                return {
                    "last_seen_at": jsonable(page.next_last_seen_at),
                    "worker_id": page.next_worker_id,
                }
            return getattr(page, "next_cursor", None)

        return await _collect_pages(
            transport.workers,
            limit=limit,
            cursor=raw,
            all_pages=all_pages,
            next_value=worker_cursor,
        )

    _run(
        _transport_operation(
            state,
            "worker.list",
            "WorkerList",
            operation,
            cursor=(filters, lambda value: value["next_cursor_value"]),
        )
    )


@worker_group.command("shutdown")
@click.option("--worker-id")
@click.option("--queue")
@pass_state
def worker_shutdown_command(state: CliState, worker_id: str | None, queue: str | None) -> None:
    if (worker_id is None) == (queue is None):
        raise TaskqConfigError("select exactly one of --worker-id or --queue")

    async def operation(transport: CliTransport) -> Any:
        return await transport.worker_shutdown(worker_id, queue)

    _run(_transport_operation(state, "worker.shutdown", "WorkerShutdown", operation))


@worker_group.command("expire-leases")
@click.argument("worker_id")
@pass_state
def worker_expire_command(state: CliState, worker_id: str) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.worker_expire(worker_id)

    _run(_transport_operation(state, "worker.expire-leases", "WorkerLeaseExpiry", operation))


@cli.group("workflow")
def workflow_group() -> None:
    """Inspect and control workflow resources."""


@workflow_group.command("list")
@click.option("--view", type=click.Choice(("running", "finished")), required=True)
@click.option("--limit", type=click.IntRange(min=1, max=10_000), default=50)
@click.option("--cursor")
@click.option("--all", "all_pages", is_flag=True)
@pass_state
def workflow_list_command(
    state: CliState, view: str, limit: int, cursor: str | None, all_pages: bool
) -> None:
    filters = {"view": view}
    connection = state.connection()

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        raw = decode_cursor(
            cursor,
            command="workflow.list",
            transport=connection.transport,
            target=str(target.installation_id),
            filters=filters,
        )
        return await _collect_pages(
            lambda page_limit, page_cursor: transport.workflow_list(view, page_limit, page_cursor),
            limit=limit,
            cursor=raw,
            all_pages=all_pages,
        )

    _run(
        _transport_operation(
            state,
            "workflow.list",
            "WorkflowList",
            operation,
            cursor=(filters, lambda value: value["next_cursor_value"]),
        )
    )


@workflow_group.command("show")
@click.argument("workflow_id", type=click.UUID)
@click.option("--limit", type=click.IntRange(min=1, max=500), default=50)
@click.option("--cursor")
@pass_state
def workflow_show_command(
    state: CliState, workflow_id: UUID, limit: int, cursor: str | None
) -> None:
    filters = {"workflow_id": str(workflow_id)}
    connection = state.connection()

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        raw = decode_cursor(
            cursor,
            command="workflow.show",
            transport=connection.transport,
            target=str(target.installation_id),
            filters=filters,
        )
        return await transport.workflow_show(workflow_id, limit, raw)

    _run(
        _transport_operation(
            state,
            "workflow.show",
            "Workflow",
            operation,
            cursor=(filters, _next_page_value),
        )
    )


@workflow_group.command("create")
@click.option(
    "--input", "input_path", required=True, type=click.Path(dir_okay=False, allow_dash=True)
)
@pass_state
def workflow_create_command(state: CliState, input_path: str) -> None:
    from .models import CliWorkflowCreateInput

    value = CliWorkflowCreateInput.model_validate(_load_input(input_path))

    async def operation(transport: CliTransport) -> Any:
        return await transport.workflow_create(**value.model_dump())

    _run(_transport_operation(state, "workflow.create", "WorkflowMutation", operation))


@workflow_group.command("seal")
@click.argument("workflow_id", type=click.UUID)
@pass_state
def workflow_seal_command(state: CliState, workflow_id: UUID) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.workflow_seal(workflow_id)

    _run(_transport_operation(state, "workflow.seal", "WorkflowMutation", operation))


@workflow_group.command("cancel")
@click.argument("workflow_id", type=click.UUID)
@click.option("--reason")
@pass_state
def workflow_cancel_command(state: CliState, workflow_id: UUID, reason: str | None) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.workflow_cancel(workflow_id, reason)

    _run(_transport_operation(state, "workflow.cancel", "WorkflowMutation", operation))


async def _workflow_observe(
    state: CliState,
    command: str,
    workflow_id: UUID,
    condition: str | None,
    timeout: float,
    poll_interval: float,
) -> Any:
    connection = state.connection()
    async with open_cli_transport(connection) as transport:
        target = await transport.target()
        _validate_target(state, connection, target, mutates=False, destructive=False)
        started = time.monotonic()
        previous: dict[str, Any] | None = None
        sequence = 0
        events: list[dict[str, Any]] = []
        stream = _observation_streams(state, condition)
        while True:
            page = await transport.workflow_show(workflow_id, 1)
            current = jsonable(page.profile)
            if current != previous:
                sequence += 1
                _record_observation(
                    state,
                    command,
                    {
                        "type": "initial" if previous is None else "modified",
                        "sequence": sequence,
                        "resource": current,
                    },
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                previous = current
            status = str(page.profile.status)
            terminal = status in {"succeeded", "failed", "cancelled"}
            if condition is None and terminal:
                _finish_observation(
                    state,
                    command,
                    condition,
                    current,
                    sequence,
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                return page.profile
            if condition is not None and (
                status == condition or (condition == "terminal" and terminal)
            ):
                _finish_observation(
                    state,
                    command,
                    condition,
                    current,
                    sequence,
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                return page.profile
            if timeout > 0 and time.monotonic() - started >= timeout:
                raise CliTimeoutError
            await asyncio.sleep(poll_interval)


def _workflow_observer_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--poll-interval", type=click.FloatRange(min=0.5), default=2.0)(
        function
    )
    return click.option("--timeout", type=click.FloatRange(min=0), default=30.0)(function)


@workflow_group.command("watch")
@click.argument("workflow_id", type=click.UUID)
@_workflow_observer_options
@pass_state
def workflow_watch_command(
    state: CliState, workflow_id: UUID, timeout: float, poll_interval: float
) -> None:
    _run(_workflow_observe(state, "workflow.watch", workflow_id, None, timeout, poll_interval))


@workflow_group.command("wait")
@click.argument("workflow_id", type=click.UUID)
@click.option(
    "--for",
    "condition",
    type=click.Choice(("terminal", "succeeded", "failed", "cancelled")),
    default="terminal",
)
@_workflow_observer_options
@pass_state
def workflow_wait_command(
    state: CliState,
    workflow_id: UUID,
    condition: str,
    timeout: float,
    poll_interval: float,
) -> None:
    _run(_workflow_observe(state, "workflow.wait", workflow_id, condition, timeout, poll_interval))


@cli.group("schedule")
def schedule_group() -> None:
    """Inspect, control, and reconcile schedules."""


@schedule_group.command("list")
@click.option("--view", type=click.Choice(("active", "paused", "retired")), required=True)
@click.option("--limit", type=click.IntRange(min=1, max=10_000), default=50)
@click.option("--cursor")
@click.option("--all", "all_pages", is_flag=True)
@pass_state
def schedule_list_command(
    state: CliState, view: str, limit: int, cursor: str | None, all_pages: bool
) -> None:
    filters = {"view": view}
    connection = state.connection()

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        raw = decode_cursor(
            cursor,
            command="schedule.list",
            transport=connection.transport,
            target=str(target.installation_id),
            filters=filters,
        )
        return await _collect_pages(
            lambda page_limit, page_cursor: transport.schedule_list(view, page_limit, page_cursor),
            limit=limit,
            cursor=raw,
            all_pages=all_pages,
        )

    _run(
        _transport_operation(
            state,
            "schedule.list",
            "ScheduleList",
            operation,
            cursor=(filters, lambda value: value["next_cursor_value"]),
        )
    )


@schedule_group.command("show")
@click.argument("name")
@pass_state
def schedule_show_command(state: CliState, name: str) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.schedule_show(name)

    _run(_transport_operation(state, "schedule.show", "Schedule", operation))


def _schedule_state_command(
    state: CliState, command: str, name: str, expected_version: int, desired: ScheduleState
) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.schedule_state(name, desired, expected_version)

    _run(_transport_operation(state, command, "ScheduleMutation", operation))


@schedule_group.command("pause")
@click.argument("name")
@click.option("--expected-version", type=click.IntRange(min=1), required=True)
@pass_state
def schedule_pause_command(state: CliState, name: str, expected_version: int) -> None:
    _schedule_state_command(state, "schedule.pause", name, expected_version, ScheduleState.PAUSED)


@schedule_group.command("resume")
@click.argument("name")
@click.option("--expected-version", type=click.IntRange(min=1), required=True)
@pass_state
def schedule_resume_command(state: CliState, name: str, expected_version: int) -> None:
    _schedule_state_command(state, "schedule.resume", name, expected_version, ScheduleState.ACTIVE)


@schedule_group.command("retire")
@click.argument("name")
@click.option("--expected-version", type=click.IntRange(min=1), required=True)
@pass_state
def schedule_retire_command(state: CliState, name: str, expected_version: int) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.schedule_retire(name, expected_version)

    _run(_transport_operation(state, "schedule.retire", "ScheduleMutation", operation))


async def _schedule_observe(
    state: CliState,
    command: str,
    name: str,
    condition: str | None,
    timeout: float,
    poll_interval: float,
) -> Any:
    connection = state.connection()
    async with open_cli_transport(connection) as transport:
        target = await transport.target()
        _validate_target(state, connection, target, mutates=False, destructive=False)
        started = time.monotonic()
        previous: dict[str, Any] | None = None
        sequence = 0
        events: list[dict[str, Any]] = []
        stream = _observation_streams(state, condition)
        while True:
            profile = await transport.schedule_show(name)
            current = jsonable(profile)
            if current != previous:
                sequence += 1
                _record_observation(
                    state,
                    command,
                    {
                        "type": "initial" if previous is None else "modified",
                        "sequence": sequence,
                        "resource": current,
                    },
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                previous = current
            status = str(profile.state)
            if condition is None and status == "retired":
                _finish_observation(
                    state,
                    command,
                    condition,
                    current,
                    sequence,
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                return profile
            if condition is not None and status == condition:
                _finish_observation(
                    state,
                    command,
                    condition,
                    current,
                    sequence,
                    events,
                    connection=connection,
                    target=target,
                    stream=stream,
                )
                return profile
            if timeout > 0 and time.monotonic() - started >= timeout:
                raise CliTimeoutError
            await asyncio.sleep(poll_interval)


@schedule_group.command("watch")
@click.argument("name")
@_workflow_observer_options
@pass_state
def schedule_watch_command(
    state: CliState, name: str, timeout: float, poll_interval: float
) -> None:
    _run(_schedule_observe(state, "schedule.watch", name, None, timeout, poll_interval))


@schedule_group.command("wait")
@click.argument("name")
@click.option(
    "--for", "condition", type=click.Choice(("active", "paused", "retired")), required=True
)
@_workflow_observer_options
@pass_state
def schedule_wait_command(
    state: CliState,
    name: str,
    condition: str,
    timeout: float,
    poll_interval: float,
) -> None:
    _run(_schedule_observe(state, "schedule.wait", name, condition, timeout, poll_interval))


@schedule_group.group("manifest")
def schedule_manifest_group() -> None:
    """Review and apply source-owned schedule manifests."""


def _plan_digest(target: Any, value: Any) -> str:
    document = {
        "target": str(target.installation_id),
        "binding_version": target.binding_version,
        "plan": jsonable(value),
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@schedule_manifest_group.command("plan")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@pass_state
def schedule_manifest_plan_command(state: CliState, manifest: str) -> None:
    value = load_manifest(manifest)

    async def operation(transport: CliTransport) -> Any:
        if transport.mode != "sql":
            raise TaskqCapabilityError(details={"transport": transport.mode})
        target = await transport.target()
        plan = await plan_manifest(transport.transport, value)
        return {"plan": plan, "plan_digest": _plan_digest(target, plan)}

    _run(_transport_operation(state, "schedule.manifest.plan", "ScheduleManifestPlan", operation))


@schedule_manifest_group.command("apply")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--plan-digest", required=True)
@pass_state
def schedule_manifest_apply_command(state: CliState, manifest: str, plan_digest: str) -> None:
    value = load_manifest(manifest)

    async def operation(transport: CliTransport) -> Any:
        if transport.mode != "sql":
            raise TaskqCapabilityError(details={"transport": transport.mode})
        async with transport.transport.engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended('taskq:cli:schedule-manifest', 0))"
                )
            )
            target = await transport.transport.get_target_identity(connection=connection)
            current = await plan_manifest(transport.transport, value, connection=connection)
            if _plan_digest(target, current) != plan_digest:
                raise CliSafetyError("schedule manifest plan drifted; create a new plan")
            return await apply_manifest(
                transport.transport,
                value,
                actor=transport.connection.actor or "",
                connection=connection,
            )

    _run(_transport_operation(state, "schedule.manifest.apply", "ScheduleManifestApply", operation))


@schedule_manifest_group.command("retire")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.argument("key")
@pass_state
def schedule_manifest_retire_command(state: CliState, manifest: str, key: str) -> None:
    value = load_manifest(manifest)

    async def operation(transport: CliTransport) -> Any:
        plan = await plan_manifest(transport.transport, value)
        entry = next((candidate for candidate in plan.entries if candidate.key == key), None)
        if entry is None or entry.current_version is None:
            raise TaskqConfigError("manifest key is not currently owned by this source")
        return await transport.transport.retire_schedule(
            entry.name, entry.current_version, transport.connection.actor or ""
        )

    _run(
        _transport_operation(state, "schedule.manifest.retire", "ScheduleManifestRetire", operation)
    )


@cli.group("scheduler")
def scheduler_group() -> None:
    """Run and diagnose the standalone scheduler."""


def _scheduler_settings(
    state: CliState,
    *,
    worker_id: str | None,
    poll_interval: float,
    claim_limit: int,
    lease_seconds: int,
    pool_size: int,
) -> SchedulerSettings:
    connection = state.connection()
    if connection.transport != "sql" or connection.dsn is None:
        raise TaskqConfigError("scheduler execution requires a direct SQL context")
    if connection.expected_environment is None:
        raise TaskqConfigError("scheduler execution requires an expected environment")
    return SchedulerSettings(
        dsn=connection.dsn,
        expected_environment=connection.expected_environment,
        expected_installation_id=connection.expected_installation_id,
        allow_production=state.allow_production,
        worker_id=worker_id,
        poll_interval=poll_interval,
        claim_limit=claim_limit,
        lease_seconds=lease_seconds,
        pool_size=pool_size,
    )


def _scheduler_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--pool-size", type=click.IntRange(min=1, max=100), default=2)(function)
    function = click.option("--lease-seconds", type=click.IntRange(min=5, max=300), default=60)(
        function
    )
    function = click.option("--claim-limit", type=click.IntRange(min=1, max=100), default=10)(
        function
    )
    function = click.option("--poll-interval", type=click.FloatRange(min=0.1), default=5.0)(
        function
    )
    return click.option("--worker-id")(function)


async def _run_scheduler(
    settings: SchedulerSettings,
    *,
    once: bool,
    max_batches: int,
    max_runtime_seconds: float,
) -> Any:
    transport, service = scheduler_from_settings(settings)
    try:
        if once:
            return await service.run_once(
                max_batches=max_batches, max_runtime_seconds=max_runtime_seconds
            )
        await service.run()
        return {"outcome": "stopped"}
    finally:
        await transport.aclose()


@scheduler_group.command("run")
@_scheduler_options
@pass_state
def scheduler_run_command(
    state: CliState,
    worker_id: str | None,
    poll_interval: float,
    claim_limit: int,
    lease_seconds: int,
    pool_size: int,
) -> None:
    settings = _scheduler_settings(
        state,
        worker_id=worker_id,
        poll_interval=poll_interval,
        claim_limit=claim_limit,
        lease_seconds=lease_seconds,
        pool_size=pool_size,
    )

    async def operation() -> Any:
        return await _run_scheduler(settings, once=False, max_batches=100, max_runtime_seconds=300)

    _run(_detached_runtime_operation(state, "scheduler.run", "SchedulerRun", operation))


@scheduler_group.command("once")
@click.option("--max-batches", type=click.IntRange(min=1), default=100)
@click.option("--max-runtime-seconds", type=click.FloatRange(min=0.1), default=300.0)
@_scheduler_options
@pass_state
def scheduler_once_command(
    state: CliState,
    max_batches: int,
    max_runtime_seconds: float,
    worker_id: str | None,
    poll_interval: float,
    claim_limit: int,
    lease_seconds: int,
    pool_size: int,
) -> None:
    settings = _scheduler_settings(
        state,
        worker_id=worker_id,
        poll_interval=poll_interval,
        claim_limit=claim_limit,
        lease_seconds=lease_seconds,
        pool_size=pool_size,
    )

    async def operation() -> Any:
        return await _run_scheduler(
            settings,
            once=True,
            max_batches=max_batches,
            max_runtime_seconds=max_runtime_seconds,
        )

    _run(_detached_runtime_operation(state, "scheduler.once", "SchedulerRun", operation))


@scheduler_group.command("doctor")
@pass_state
def scheduler_doctor_command(state: CliState) -> None:
    async def operation(transport: CliTransport) -> Any:
        return {"ready": True, "health": await transport.scheduler_health()}

    _run(_transport_operation(state, "scheduler.doctor", "SchedulerDoctor", operation))


@cli.group("maintenance")
def maintenance_group() -> None:
    """Run explicit bounded maintenance operations."""


@maintenance_group.command("tick")
@click.option("--reap-limit", type=click.IntRange(min=1, max=1000), default=100)
@pass_state
def maintenance_tick_command(state: CliState, reap_limit: int) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.tick(reap_limit)

    _run(_transport_operation(state, "maintenance.tick", "MaintenanceTick", operation))


@maintenance_group.command("janitor")
@pass_state
def maintenance_janitor_command(state: CliState) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.janitor()

    _run(_transport_operation(state, "maintenance.janitor", "MaintenanceJanitor", operation))


@cli.group("auth")
def auth_group() -> None:
    """Review and reconcile OutLabs IAM permissions."""


def _auth_namespace(
    state: CliState,
    *,
    auth_dsn: str | None,
    schema: str | None,
    queues: str,
    roles: str,
    role_prefix: str,
    apply: bool,
    reconcile: bool,
    per_queue_roles: bool,
) -> Namespace:
    connection = state.connection()
    selected = auth_dsn
    if selected is None and connection.auth_dsn is not None:
        selected = connection.auth_dsn.get_secret_value()
    selected = selected or os.environ.get("TASKQ_AUTH_DSN")
    return Namespace(
        dsn=selected,
        schema=schema or os.environ.get("TASKQ_AUTH_SCHEMA"),
        queues=queues,
        roles=roles,
        role_prefix=role_prefix,
        apply=apply,
        reconcile=reconcile,
        per_queue_roles=per_queue_roles,
    )


def _auth_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--per-queue-roles", is_flag=True)(function)
    function = click.option("--reconcile", is_flag=True)(function)
    function = click.option("--role-prefix", default="taskq-")(function)
    function = click.option("--roles", type=click.Choice(("standard", "none")), default="standard")(
        function
    )
    function = click.option("--queues", required=True)(function)
    function = click.option("--schema")(function)
    return click.option("--auth-dsn", hidden=True)(function)


@auth_group.command("plan")
@_auth_options
@pass_state
def auth_plan_command(
    state: CliState,
    auth_dsn: str | None,
    schema: str | None,
    queues: str,
    roles: str,
    role_prefix: str,
    reconcile: bool,
    per_queue_roles: bool,
) -> None:
    args = _auth_namespace(
        state,
        auth_dsn=auth_dsn,
        schema=schema,
        queues=queues,
        roles=roles,
        role_prefix=role_prefix,
        apply=False,
        reconcile=reconcile,
        per_queue_roles=per_queue_roles,
    )

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()
        report = await _runtime._run_auth_sync(args)
        return {"plan": report, "plan_digest": _plan_digest(target, report)}

    _run(_transport_operation(state, "auth.plan", "AuthPlan", operation))


@auth_group.command("apply")
@click.option("--plan-digest", required=True)
@_auth_options
@pass_state
def auth_apply_command(
    state: CliState,
    plan_digest: str,
    auth_dsn: str | None,
    schema: str | None,
    queues: str,
    roles: str,
    role_prefix: str,
    reconcile: bool,
    per_queue_roles: bool,
) -> None:
    report_args = _auth_namespace(
        state,
        auth_dsn=auth_dsn,
        schema=schema,
        queues=queues,
        roles=roles,
        role_prefix=role_prefix,
        apply=False,
        reconcile=reconcile,
        per_queue_roles=per_queue_roles,
    )

    async def operation(transport: CliTransport) -> Any:
        target = await transport.target()

        def validate_plan(report: Any) -> None:
            if _plan_digest(target, report) != plan_digest:
                raise CliSafetyError("authorization plan drifted; create a new plan")

        return await _runtime._run_auth_apply(report_args, validate_plan=validate_plan)

    _run(_transport_operation(state, "auth.apply", "AuthApply", operation))


@cli.command("metrics")
@pass_state
def metrics_command(state: CliState) -> None:
    async def operation(transport: CliTransport) -> Any:
        return await transport.metrics()

    _run(_transport_operation(state, "metrics", "Metrics", operation))


def _guess_command(argv: list[str]) -> str:
    values = [value for value in argv if not value.startswith("-")]
    for start in range(len(values)):
        for length in (3, 2, 1):
            candidate = ".".join(values[start : start + length])
            if candidate in COMMAND_SPECS:
                return candidate
    return "taskq"


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its documented process exit code."""

    arguments = _normalize_global_options(list(sys.argv[1:] if argv is None else argv))
    output = _extract_output(arguments)
    command = _guess_command(arguments)
    request_id = _extract_request_id(arguments)
    try:
        result = cli.main(args=arguments, prog_name="taskq", standalone_mode=False)
        return int(result or 0)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException as exc:
        exit_code, envelope = normalize_error(exc, command=command, request_id=request_id)
        render_error(envelope, output, stream=sys.stdout if output == "jsonl" else sys.stderr)
        return exit_code


__all__ = ["COMMAND_SPECS", "cli", "main"]
