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


def _effect_copy(
    value: dict[str, Any],
    *,
    boundary: str,
    max_bytes: int,
) -> dict[str, Any]:
    """Validate one explicitly bounded handler/reporter JSON object."""

    if not isinstance(value, dict):
        raise TaskqConfigError(f"effect {boundary} must be a JSON object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskqConfigError(f"effect {boundary} must be JSON serializable") from exc
    if len(encoded) > max_bytes:
        label = f"{max_bytes // 1024}KB" if max_bytes % 1024 == 0 else f"{max_bytes}-byte"
        raise TaskqConfigError(f"effect {boundary} exceeds the configured {label} limit")
    return deepcopy(value)


EffectReporterCallback: TypeAlias = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
BlockingRunnerCallback: TypeAlias = Callable[
    [Callable[..., Any], tuple[Any, ...], dict[str, Any]],
    Awaitable[Any],
]


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
        workflow_id: UUID | None = None,
        workflow_step: str | None = None,
        cancellation: CancellationToken | None = None,
        effect_reporter: EffectReporterCallback | None = None,
        effect_request_max_bytes: int = 8192,
        effect_response_max_bytes: int = 8192,
        blocking_runner: BlockingRunnerCallback | None = None,
    ) -> None:
        for field, value in (
            ("effect_request_max_bytes", effect_request_max_bytes),
            ("effect_response_max_bytes", effect_response_max_bytes),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1024 <= value <= 8_388_608
            ):
                raise TaskqConfigError(f"{field} must be between 1024 and 8388608 bytes")
        self.job_id = job_id
        self.queue = queue
        self.job_type = job_type
        self.payload = payload
        self.headers = MappingProxyType(deepcopy(headers or {}))
        self.attempt_number = attempt_number
        self.failure_count = failure_count
        self.max_attempts = max_attempts
        self.workflow_id = workflow_id
        self.workflow_step = workflow_step
        self.cancellation = cancellation or CancellationToken()
        self._effect_reporter = effect_reporter
        self._blocking_runner = blocking_runner
        self._effect_request_max_bytes = effect_request_max_bytes
        self._effect_response_max_bytes = effect_response_max_bytes
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
        copied = _effect_copy(
            request,
            boundary="request",
            max_bytes=self._effect_request_max_bytes,
        )
        result = await reporter(copied)
        self.raise_if_cancelled()
        return _effect_copy(
            result,
            boundary="response",
            max_bytes=self._effect_response_max_bytes,
        )

    async def run_sync(
        self,
        function: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run blocking work through the supervising runtime when available.

        Supervisor-owned execution lets the process lifecycle detect a thread
        that outlives lease ownership or the shutdown deadline. Direct contexts
        retain a small standalone fallback for tests and non-worker utilities.
        """

        if not callable(function):
            raise TaskqConfigError("run_sync requires a callable")
        self.raise_if_cancelled()
        runner = self._blocking_runner
        if runner is None:
            result = await asyncio.to_thread(function, *args, **kwargs)
        else:
            result = await runner(function, args, kwargs)
        self.raise_if_cancelled()
        return result

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
