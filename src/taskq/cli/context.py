"""Secret-free named context loading and connection resolution."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr, ValidationError

from taskq.errors import TaskqConfigError

from .models import ContextDefinition, ContextFile, ResolvedConnection


_ENVIRONMENT_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def default_config_path() -> Path:
    configured = os.environ.get("TASKQ_CONFIG")
    if configured:
        return Path(configured).expanduser()
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root).expanduser() / "taskq" / "config.toml"
    return Path.home() / ".config" / "taskq" / "config.toml"


def load_context_file(path: str | Path | None = None, *, required: bool = True) -> ContextFile:
    target = Path(path).expanduser() if path is not None else default_config_path()
    if not target.exists():
        if required:
            raise TaskqConfigError(f"context file does not exist: {target}")
        return ContextFile(version=1, contexts={})
    if not target.is_file():
        raise TaskqConfigError(f"context path is not a file: {target}")
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
        return ContextFile.model_validate(raw)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise TaskqConfigError("context file is invalid") from exc


def redacted_context(value: ContextDefinition) -> dict[str, object]:
    return value.model_dump(mode="json", exclude_none=True)


def _secret_from_environment(name: str | None, *, label: str) -> SecretStr | None:
    if name is None:
        return None
    value = os.environ.get(name)
    if not value:
        raise TaskqConfigError(f"{label} environment variable is missing or empty: {name}")
    return SecretStr(value)


def _validated_environment_variable_name(name: str | None, *, label: str) -> str | None:
    if name is None:
        return None
    if not _ENVIRONMENT_VARIABLE_NAME.fullmatch(name):
        raise TaskqConfigError(f"{label} must be an environment-variable name")
    return name


def _validated_http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TaskqConfigError("HTTP base URL must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise TaskqConfigError("HTTP base URL cannot contain credentials")
    return value.rstrip("/")


def resolve_connection(
    *,
    context_name: str | None,
    config_path: str | Path | None,
    dsn: str | None = None,
    dsn_env: str | None = None,
    http_base_url: str | None = None,
    http_bearer_token: str | None = None,
    http_header_name: str | None = None,
    http_header_value: str | None = None,
    expected_environment: str | None = None,
    expected_installation_id: UUID | None = None,
    actor: str | None = None,
) -> ResolvedConnection:
    dsn_env = _validated_environment_variable_name(dsn_env, label="DSN environment variable")
    if dsn is not None and dsn_env is not None:
        raise TaskqConfigError("configure exactly one of --dsn or --dsn-env")

    definition: ContextDefinition | None = None
    if context_name is not None:
        context_file = load_context_file(config_path)
        definition = context_file.contexts.get(context_name)
        if definition is None:
            raise TaskqConfigError(f"unknown context: {context_name}")

    configured_environment = definition.expected_environment if definition else None
    configured_installation = definition.expected_installation_id if definition else None
    if (
        configured_environment is not None
        and expected_environment is not None
        and configured_environment != expected_environment
    ):
        raise TaskqConfigError("explicit environment conflicts with selected context")
    if (
        configured_installation is not None
        and expected_installation_id is not None
        and configured_installation != expected_installation_id
    ):
        raise TaskqConfigError("explicit installation id conflicts with selected context")

    final_environment = expected_environment or configured_environment
    final_installation = expected_installation_id or configured_installation
    configured_actor = definition.actor if definition else None
    if configured_actor is not None and actor is not None and configured_actor != actor:
        raise TaskqConfigError("explicit actor conflicts with selected context")
    final_actor = actor or configured_actor or os.environ.get("TASKQ_ACTOR")

    # Endpoint and target selection are always explicit. Environment variables
    # may carry credentials and the direct-SQL actor, never target constraints.
    explicit_sql = dsn
    explicit_sql_env = dsn_env
    explicit_http = http_base_url
    if (explicit_sql or explicit_sql_env) and explicit_http:
        raise TaskqConfigError("configure exactly one of SQL DSN source or HTTP base URL")

    if definition is not None:
        if explicit_sql or explicit_sql_env or explicit_http:
            explicit_transport = "sql" if explicit_sql or explicit_sql_env else "http"
            if explicit_transport != definition.transport:
                raise TaskqConfigError("explicit transport conflicts with selected context")
        if definition.transport == "sql":
            resolved_dsn = (
                SecretStr(explicit_sql)
                if explicit_sql
                else _secret_from_environment(explicit_sql_env or definition.dsn_env, label="DSN")
            )
            auth_dsn = (
                _secret_from_environment(definition.auth_dsn_env, label="auth DSN")
                if definition.auth_dsn_env
                else None
            )
            return ResolvedConnection(
                context=context_name,
                transport="sql",
                dsn=resolved_dsn,
                auth_dsn=auth_dsn,
                expected_environment=final_environment,
                expected_installation_id=final_installation,
                actor=final_actor,
            )
        if final_actor is not None:
            raise TaskqConfigError("HTTP contexts reject actor spoofing")
        if definition.bearer_token_env is not None and (
            http_header_name is not None or http_header_value is not None
        ):
            raise TaskqConfigError("explicit HTTP credential source conflicts with context")
        if definition.header_name is not None and http_bearer_token is not None:
            raise TaskqConfigError("explicit HTTP credential source conflicts with context")
        if (
            definition.header_name is not None
            and http_header_name is not None
            and definition.header_name != http_header_name
        ):
            raise TaskqConfigError("explicit HTTP header name conflicts with context")
        base_url = _validated_http_url(explicit_http or definition.base_url or "")
        bearer = (
            SecretStr(http_bearer_token)
            if http_bearer_token
            else _secret_from_environment(definition.bearer_token_env, label="bearer token")
        )
        header_name = http_header_name or definition.header_name
        header_value = (
            SecretStr(http_header_value)
            if http_header_value
            else _secret_from_environment(definition.header_value_env, label="header value")
        )
        return ResolvedConnection(
            context=context_name,
            transport="http",
            base_url=base_url,
            bearer_token=bearer,
            header_name=header_name,
            header_value=header_value,
            expected_environment=final_environment,
            expected_installation_id=final_installation,
            actor=final_actor,
        )

    if explicit_sql or explicit_sql_env:
        return ResolvedConnection(
            transport="sql",
            dsn=(
                SecretStr(explicit_sql)
                if explicit_sql
                else _secret_from_environment(explicit_sql_env, label="DSN")
            ),
            expected_environment=final_environment,
            expected_installation_id=final_installation,
            actor=final_actor,
        )
    if explicit_http:
        if final_actor is not None:
            raise TaskqConfigError("HTTP connections reject actor spoofing")
        bearer_value = http_bearer_token or os.environ.get("TASKQ_HTTP_BEARER_TOKEN")
        header_name = http_header_name or os.environ.get("TASKQ_HTTP_HEADER_NAME")
        header_value = http_header_value or os.environ.get("TASKQ_HTTP_HEADER_VALUE")
        bearer = SecretStr(bearer_value) if bearer_value else None
        header_secret = SecretStr(header_value) if header_value else None
        if (bearer is None) == (not (header_name and header_secret)):
            raise TaskqConfigError("HTTP connection requires exactly one credential source")
        return ResolvedConnection(
            transport="http",
            base_url=_validated_http_url(explicit_http),
            bearer_token=bearer,
            header_name=header_name,
            header_value=header_secret,
            expected_environment=final_environment,
            expected_installation_id=final_installation,
        )
    raise TaskqConfigError(
        "select --context or provide exactly one of --dsn/--dsn-env/--http-base-url"
    )


__all__ = [
    "default_config_path",
    "load_context_file",
    "redacted_context",
    "resolve_connection",
]
