"""Handler execution primitives for the Stage-2 worker runtime."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from enum import StrEnum
from threading import Event, Lock
from types import MappingProxyType
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from taskq.errors import TaskqConfigError
from taskq.protocol import Followup


class _ExecutionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Complete(_ExecutionModel):
    result: dict[str, Any] = Field(default_factory=dict)
    followups: tuple[Followup, ...] = Field(default=(), max_length=20)


class Snooze(_ExecutionModel):
    delay_seconds: int = Field(ge=0, le=2_592_000)
    progress: dict[str, Any] | None = None
    reason: str | None = None


class Cancel(_ExecutionModel):
    reason: str = Field(min_length=1)


class Retry(_ExecutionModel):
    after_seconds: int | None = Field(default=None, ge=0, le=2_592_000)
    error: str | None = None
    progress: dict[str, Any] | None = None


class NonRetryable(_ExecutionModel):
    error: str = Field(min_length=1)
    progress: dict[str, Any] | None = None


HandlerResult: TypeAlias = Complete | Snooze | Cancel | Retry | NonRetryable
HANDLER_RESULT_TYPES = (Complete, Snooze, Cancel, Retry, NonRetryable)


class CancellationReason(StrEnum):
    SHUTDOWN = "shutdown"
    OPERATOR = "operator"
    LEASE_LOST = "lease_lost"


_CANCELLATION_PRIORITY = {
    CancellationReason.SHUTDOWN: 1,
    CancellationReason.OPERATOR: 2,
    CancellationReason.LEASE_LOST: 3,
}


class CancellationToken:
    """Thread-safe, monotonic cancellation state shared with sync handlers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._event = Event()
        self._reason: CancellationReason | None = None

    @property
    def reason(self) -> CancellationReason | None:
        with self._lock:
            return self._reason

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: CancellationReason) -> bool:
        with self._lock:
            current = self._reason
            if (
                current is not None
                and _CANCELLATION_PRIORITY[current] >= (_CANCELLATION_PRIORITY[reason])
            ):
                return False
            self._reason = reason
            self._event.set()
            return True


class TaskCancelled(Exception):
    """Cooperative handler cancellation carrying only a safe reason."""

    def __init__(self, reason: CancellationReason) -> None:
        self.reason = reason
        super().__init__(f"task execution cancelled: {reason.value}")

    def __repr__(self) -> str:
        return f"TaskCancelled(reason={self.reason.value!r})"


def _checkpoint_copy(progress: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(progress, dict):
        raise TaskqConfigError("checkpoint must be a JSON object")
    try:
        encoded = json.dumps(progress, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskqConfigError("checkpoint must be JSON serializable") from exc
    if len(encoded) > 2048:
        raise TaskqConfigError("checkpoint exceeds the 2KB limit")
    return deepcopy(progress)


def _effect_request_copy(request: dict[str, Any]) -> dict[str, Any]:
    """Validate the deliberately small handler-to-reporter data boundary."""
    if not isinstance(request, dict):
        raise TaskqConfigError("effect request must be a JSON object")
    try:
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskqConfigError("effect request must be JSON serializable") from exc
    if len(encoded) > 8192:
        raise TaskqConfigError("effect request exceeds the 8KB limit")
    return deepcopy(request)


EffectReporterCallback: TypeAlias = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class JobContext:
    """Fence-free handler context with thread-safe checkpoint staging."""

    def __init__(
        self,
        *,
        job_id: UUID,
        queue: str,
        job_type: str,
        payload: BaseModel,
        headers: dict[str, Any] | None,
        progress: dict[str, Any] | None,
        attempt_number: int,
        failure_count: int,
        max_attempts: int,
        cancellation: CancellationToken | None = None,
        effect_reporter: EffectReporterCallback | None = None,
    ) -> None:
        self.job_id = job_id
        self.queue = queue
        self.job_type = job_type
        self.payload = payload
        self.headers = MappingProxyType(deepcopy(headers or {}))
        self.attempt_number = attempt_number
        self.failure_count = failure_count
        self.max_attempts = max_attempts
        self.cancellation = cancellation or CancellationToken()
        self._effect_reporter = effect_reporter
        self._checkpoint_lock = Lock()
        self._progress = _checkpoint_copy(progress) if progress is not None else None
        self._pending_generation = 0
        self._pending_progress: dict[str, Any] | None = None

    def __repr__(self) -> str:
        return (
            f"JobContext(job_id={self.job_id!r}, queue={self.queue!r}, "
            f"job_type={self.job_type!r}, attempt_number={self.attempt_number!r})"
        )

    @property
    def progress(self) -> dict[str, Any] | None:
        with self._checkpoint_lock:
            return deepcopy(self._progress)

    @property
    def cancel_requested(self) -> bool:
        return self.cancellation.is_cancelled

    def should_cancel(self) -> bool:
        return self.cancellation.is_cancelled

    def raise_if_cancelled(self) -> None:
        reason = self.cancellation.reason
        if reason is not None:
            raise TaskCancelled(reason)

    async def checkpoint(self, progress: dict[str, Any]) -> None:
        self.checkpoint_nowait(progress)
        await asyncio.sleep(0)

    def checkpoint_nowait(self, progress: dict[str, Any]) -> None:
        copied = _checkpoint_copy(progress)
        with self._checkpoint_lock:
            self._progress = copied
            self._pending_generation += 1
            self._pending_progress = copied

    async def report_effect(self, request: dict[str, Any]) -> dict[str, Any]:
        """Request one bounded host effect through the runtime-owned reporter.

        The callback is deliberately absent from ordinary workers.  It never
        exposes an attempt identity to the handler; the supervising runtime
        binds that identity immediately before invoking its trusted reporter.
        """
        self.raise_if_cancelled()
        reporter = self._effect_reporter
        if reporter is None:
            raise TaskqConfigError("JobContext has no trusted effect reporter")
        copied = _effect_request_copy(request)
        result = await reporter(copied)
        self.raise_if_cancelled()
        return _effect_request_copy(result)

    def _pending_checkpoint(self) -> tuple[int, dict[str, Any]] | None:
        with self._checkpoint_lock:
            if self._pending_progress is None:
                return None
            return self._pending_generation, deepcopy(self._pending_progress)

    def _ack_checkpoint(self, generation: int) -> None:
        with self._checkpoint_lock:
            if generation == self._pending_generation:
                self._pending_progress = None


__all__ = [
    "Cancel",
    "CancellationReason",
    "CancellationToken",
    "Complete",
    "EffectReporterCallback",
    "Followup",
    "HANDLER_RESULT_TYPES",
    "HandlerResult",
    "JobContext",
    "NonRetryable",
    "Retry",
    "Snooze",
    "TaskCancelled",
]
