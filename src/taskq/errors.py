"""Stable public errors and message-free database error normalization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from taskq.protocol import TQ_ERROR_REGISTRY, TqCode

_SENSITIVE_DETAIL_KEYS = frozenset(
    {"attempt_id", "connection", "dsn", "query", "raw_sql", "sql", "statement"}
)
_AVAILABILITY_STATES = frozenset({"53300", "57P01", "57P02", "57P03"})


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    return {
        str(key): value
        for key, value in details.items()
        if str(key).lower() not in _SENSITIVE_DETAIL_KEYS
    }


class TaskqError(Exception):
    code: TqCode

    def __init__(
        self,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.details = _safe_details(details)
        self.cause = cause
        self.retryable = TQ_ERROR_REGISTRY[self.code].retryable
        super().__init__(f"{self.code}: {TQ_ERROR_REGISTRY[self.code].category}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, retryable={self.retryable!r})"


class TaskqNotFoundError(TaskqError):
    code = TqCode.NOT_FOUND


class TaskqConflictError(TaskqError):
    code = TqCode.CONFLICT


class TaskqValidationError(TaskqError):
    code = TqCode.VALIDATION


class TaskqVersionError(TaskqError):
    code = TqCode.VERSION


class TaskqBackpressureError(TaskqError):
    code = TqCode.BACKPRESSURE


class TaskqInternalError(TaskqError):
    code = TqCode.INTERNAL


class TaskqCapabilityError(TaskqError):
    code = TqCode.CAPABILITY


class TaskqUnavailableError(TaskqError):
    code = TqCode.UNAVAILABLE


class TaskqConfigError(ValueError):
    """Invalid local configuration, before a taskq command is issued."""


class UnknownTaskError(LookupError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"task is not registered: {name!r}")


_ERROR_TYPES: dict[TqCode, type[TaskqError]] = {
    TqCode.NOT_FOUND: TaskqNotFoundError,
    TqCode.CONFLICT: TaskqConflictError,
    TqCode.VALIDATION: TaskqValidationError,
    TqCode.VERSION: TaskqVersionError,
    TqCode.BACKPRESSURE: TaskqBackpressureError,
    TqCode.INTERNAL: TaskqInternalError,
    TqCode.CAPABILITY: TaskqCapabilityError,
    TqCode.UNAVAILABLE: TaskqUnavailableError,
}


def _exception_chain(exc: BaseException) -> list[BaseException]:
    pending: list[BaseException] = [exc]
    result: list[BaseException] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        for candidate in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(candidate, BaseException):
                pending.append(candidate)
    return result


def taskq_error_from_exception(exc: BaseException) -> TaskqError:
    """Normalize from SQLSTATE attributes only; exception text is never inspected."""

    state: str | None = None
    for current in _exception_chain(exc):
        candidate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(candidate, str):
            state = candidate
            break

    try:
        code = TqCode(state) if state is not None else TqCode.INTERNAL
    except ValueError:
        if state is not None and (state.startswith("08") or state in _AVAILABILITY_STATES):
            code = TqCode.UNAVAILABLE
        else:
            code = TqCode.INTERNAL
    return _ERROR_TYPES[code](cause=exc)


__all__ = [
    "TaskqBackpressureError",
    "TaskqCapabilityError",
    "TaskqConfigError",
    "TaskqConflictError",
    "TaskqError",
    "TaskqInternalError",
    "TaskqNotFoundError",
    "TaskqUnavailableError",
    "TaskqValidationError",
    "TaskqVersionError",
    "UnknownTaskError",
    "taskq_error_from_exception",
]
