"""Stable exception-to-CLI normalization."""

from __future__ import annotations

import json
from typing import Any

import click
from pydantic import ValidationError

from taskq.errors import TaskqConfigError, TaskqError

from .models import CliErrorBody, CliErrorEnvelope


class CliSafetyError(TaskqConfigError):
    """A locally refused mutation that has not crossed a transport boundary."""


class CliTargetBindingRequired(CliSafetyError):
    """A migration reached the intentional unbound-target activation barrier."""

    def __init__(self) -> None:
        super().__init__("database target binding is required before scheduler activation")


class CliTimeoutError(TimeoutError):
    """A finite CLI wait condition was not reached."""


class CliOperationError(RuntimeError):
    """A known non-retryable local runtime outcome."""


_SENSITIVE_DETAIL_KEYS = (
    "attempt_id",
    "attempt_fence",
    "authorization",
    "connection",
    "credential",
    "dsn",
    "headers",
    "password",
    "payload",
    "progress",
    "result",
    "secret",
    "sql",
    "token",
)


def _safe_detail(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "[bounded]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:16]:
            normalized = str(key)[:64]
            lowered = normalized.lower()
            if any(fragment in lowered for fragment in _SENSITIVE_DETAIL_KEYS):
                continue
            result[normalized] = _safe_detail(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_detail(item, depth=depth + 1) for item in list(value)[:16]]
    if isinstance(value, str):
        if "://" in value or any(fragment in value.lower() for fragment in ("bearer ", "apikey")):
            return "[redacted]"
        return value[:256]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:256]


def _bounded_details(value: dict[str, Any]) -> dict[str, Any]:
    safe = _safe_detail(value)
    return safe if isinstance(safe, dict) else {}


# Reason -> operator hint for taskq-defined SQLSTATEs surfaced through the SQL driver.
_TASKQ_SQLSTATE_HINTS = {
    "target_unbound": (
        "bind the target before migrating past the identity checkpoint: "
        "taskq target bind --environment <env> --actor <actor>"
    ),
}


def _taskq_domain_error(exc: BaseException) -> tuple[str, str, dict[str, Any]] | None:
    """Recognize a taskq-defined SQLSTATE (``TQxxx``) raised by the SQL contract.

    Migrations and functions raise these with an actionable operator message — e.g.
    migration 0020's "run taskq target bind first" — but they reach the CLI wrapped as
    a driver ``DBAPIError``, which the catch-all below would otherwise collapse into an
    opaque ``CLI_INTERNAL`` "command failed with DBAPIError". Walk the ``.orig`` /
    ``__cause__`` chain to recover the SQLSTATE (on the SQLAlchemy wrapper) and the clean
    message + ``DETAIL`` payload (on the underlying asyncpg error). Returns
    ``(sqlstate, message, detail)`` or ``None`` when this is not a taskq domain error.
    """
    sqlstate: str | None = None
    message: str | None = None
    detail: dict[str, Any] = {}
    seen: set[int] = set()
    cursor: BaseException | None = exc
    for _ in range(6):
        if cursor is None or id(cursor) in seen:
            break
        seen.add(id(cursor))
        state = getattr(cursor, "sqlstate", None)
        if sqlstate is None and isinstance(state, str) and state.startswith("TQ"):
            sqlstate = state
        if message is None:
            # asyncpg exposes the clean message/detail as .message/.detail; psycopg
            # exposes them through a .diag Diagnostic (.message_primary/.message_detail).
            diag = getattr(cursor, "diag", None)
            candidate = getattr(cursor, "message", None) or getattr(diag, "message_primary", None)
            if isinstance(candidate, str) and candidate:
                message = candidate
                raw_detail = getattr(cursor, "detail", None) or getattr(
                    diag, "message_detail", None
                )
                if isinstance(raw_detail, str) and raw_detail.strip().startswith("{"):
                    try:
                        parsed = json.loads(raw_detail)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict):
                        detail = parsed
        cursor = getattr(cursor, "orig", None) or getattr(cursor, "__cause__", None)
    if sqlstate is None:
        return None
    return sqlstate, message or "taskq contract operation refused", detail


def normalize_error(
    exc: BaseException, *, command: str, request_id: str | None
) -> tuple[int, CliErrorEnvelope]:
    if isinstance(exc, CliTimeoutError):
        code, category, message, retryable, hint, exit_code, details = (
            "CLI_TIMEOUT",
            "condition_not_met",
            "the requested condition was not reached before the timeout",
            False,
            "increase --timeout or inspect the resource state",
            4,
            {},
        )
    elif isinstance(exc, CliTargetBindingRequired):
        code, category, message, retryable, hint, exit_code, details = (
            "CLI_TARGET_BINDING_REQUIRED",
            "target_binding_required",
            str(exc),
            False,
            "run target show, bind the reviewed identity, then create and apply a new plan",
            2,
            {},
        )
    elif isinstance(exc, CliSafetyError):
        code, category, message, retryable, hint, exit_code, details = (
            "CLI_SAFETY",
            "mutation_refused",
            str(exc),
            False,
            "review the target and supply the required explicit acknowledgement",
            2,
            {},
        )
    elif isinstance(exc, CliOperationError):
        code, category, message, retryable, hint, exit_code, details = (
            "CLI_OPERATION",
            "operation_failed",
            str(exc),
            False,
            "inspect the selected resource and runtime configuration",
            1,
            {},
        )
    elif isinstance(exc, (click.UsageError, TaskqConfigError, ValidationError, ValueError)):
        if isinstance(exc, ValidationError):
            messages = [item["msg"] for item in exc.errors(include_input=False, include_url=False)]
            message = "; ".join(dict.fromkeys(messages)) or "input validation failed"
        else:
            message = str(exc)
        code, category, retryable, hint, exit_code, details = (
            "CLI_CONFIG",
            "invalid_configuration",
            False,
            "run the command with --help or inspect it with taskq schema",
            2,
            {},
        )
    elif isinstance(exc, TaskqError):
        code = exc.code.value
        category = "taskq_operation_failed"
        message = str(exc)
        retryable = exc.retryable
        hint = "retry with backoff" if retryable else "inspect the resource and command input"
        exit_code = 3 if retryable else 1
        details = _bounded_details(exc.details)
    elif isinstance(exc, PermissionError):
        code = str(getattr(exc, "code", "AUTH403"))
        category, message, retryable, hint, exit_code, details = (
            "authorization_failed",
            "the selected principal is not authorized for this command",
            False,
            "use a context with the required TaskQ permission",
            1,
            {},
        )
    elif (domain := _taskq_domain_error(exc)) is not None:
        sqlstate, domain_message, detail = domain
        reason = detail.get("reason") if isinstance(detail.get("reason"), str) else None
        code, category, message, retryable, hint, exit_code, details = (
            sqlstate,
            "taskq_contract_refused",
            domain_message,
            False,
            _TASKQ_SQLSTATE_HINTS.get(reason or "", "inspect the target and command input"),
            1,
            _bounded_details(detail),
        )
    else:
        code, category, message, retryable, hint, exit_code, details = (
            "CLI_INTERNAL",
            "internal_failure",
            f"command failed with {type(exc).__name__}",
            True,
            "retry once; use --request-id to correlate persistent failures",
            3,
            {},
        )
    return exit_code, CliErrorEnvelope(
        command=command,
        error=CliErrorBody(
            code=code,
            category=category,
            message=message,
            retryable=retryable,
            hint=hint,
            details=details,
            request_id=request_id,
        ),
    )


__all__ = [
    "CliOperationError",
    "CliSafetyError",
    "CliTargetBindingRequired",
    "CliTimeoutError",
    "normalize_error",
]
