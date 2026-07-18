"""Deterministic private harness utilities for S2-04 worker tests."""

from __future__ import annotations

import asyncio
import heapq
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from taskq.errors import TaskqUnavailableError
from taskq.protocol import (
    ClaimResult,
    ClaimState,
    HeartbeatResult,
    JobStatus,
    SettleDeadResult,
    SettleOkResult,
    SettleResult,
    SettleRetryScheduledResult,
)


class ManualClock:
    def __init__(self) -> None:
        self._time = 0.0
        self._sequence = 0
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self._time

    async def sleep(self, delay: float) -> None:
        if delay < 0:
            raise ValueError("delay must be non-negative")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._sequence += 1
        heapq.heappush(self._sleepers, (self._time + delay, self._sequence, future))
        self._wake_due()
        await future

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("clock cannot move backwards")
        self._time += seconds
        self._wake_due()

    @property
    def sleeping(self) -> int:
        return sum(not item[2].done() for item in self._sleepers)

    def _wake_due(self) -> None:
        while self._sleepers and self._sleepers[0][0] <= self._time:
            _, _, future = heapq.heappop(self._sleepers)
            if not future.done():
                future.set_result(None)


@dataclass(frozen=True, slots=True)
class RecordedCall:
    command: str
    arguments: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _DroppedResponse:
    error: BaseException


@dataclass(frozen=True, slots=True)
class _ReplayResult:
    value: object


class ScriptedTransport:
    """Worker-command fake with explicit, ordered outcomes and safe reprs."""

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self.semantic_applications: dict[str, int] = defaultdict(int)
        self._scripts: dict[str, deque[object]] = defaultdict(deque)

    def __repr__(self) -> str:
        return f"ScriptedTransport(commands={[call.command for call in self.calls]!r})"

    def script(self, command: str, *steps: object) -> None:
        self._scripts[command].extend(steps)

    def drop_response_after_apply(self, command: str, *, replay: object) -> None:
        self._scripts[command].extend(
            (_DroppedResponse(TaskqUnavailableError()), _ReplayResult(replay))
        )

    async def _next(self, command: str, arguments: Mapping[str, Any], default: object) -> Any:
        self.calls.append(RecordedCall(command, dict(arguments)))
        scripted = self._scripts[command].popleft() if self._scripts[command] else default
        if isinstance(scripted, _DroppedResponse):
            self.semantic_applications[command] += 1
            raise scripted.error
        if isinstance(scripted, _ReplayResult):
            return scripted.value
        if isinstance(scripted, BaseException):
            raise scripted
        self.semantic_applications[command] += 1
        return scripted

    async def heartbeat(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> HeartbeatResult:
        return await self._next(
            "heartbeat",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "progress": progress,
                "stats": stats,
            },
            HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None),
        )

    async def claim(
        self,
        queue: str,
        worker_id: str,
        *,
        batch: int = 1,
        job_types: Sequence[str] | None = None,
        lease_seconds: int | None = None,
        affinity_key: str | None = None,
        job_id: UUID | None = None,
    ) -> ClaimResult:
        return await self._next(
            "claim",
            {
                "queue": queue,
                "worker_id": worker_id,
                "batch": batch,
                "job_types": job_types,
                "lease_seconds": lease_seconds,
                "affinity_key": affinity_key,
                "job_id": job_id,
            },
            ClaimResult(state=ClaimState.EMPTY),
        )

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
        followups: Sequence[Mapping[str, Any]] | None = None,
    ) -> SettleResult:
        return await self._next(
            "complete",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "result": result,
                "stats": stats,
                "followups": followups,
            },
            SettleOkResult(job_status=JobStatus.SUCCEEDED, scheduled_at=None),
        )

    async def fail(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
        progress: Mapping[str, Any] | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return await self._next(
            "fail",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "error": error,
                "retryable": retryable,
                "retry_after_seconds": retry_after_seconds,
                "progress": progress,
                "stats": stats,
            },
            (
                SettleRetryScheduledResult(job_status=JobStatus.QUEUED, scheduled_at=None)
                if retryable
                else SettleDeadResult(job_status=JobStatus.FAILED, scheduled_at=None)
            ),
        )

    async def snooze(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        delay_seconds: int,
        *,
        reason: str | None = None,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return await self._next(
            "snooze",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "delay_seconds": delay_seconds,
                "reason": reason,
                "progress": progress,
            },
            SettleOkResult(job_status=JobStatus.QUEUED, scheduled_at=None),
        )

    async def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: str,
        *,
        delay_seconds: int = 0,
        progress: Mapping[str, Any] | None = None,
    ) -> SettleResult:
        return await self._next(
            "release",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "cause": cause,
                "delay_seconds": delay_seconds,
                "progress": progress,
            },
            SettleOkResult(job_status=JobStatus.QUEUED, scheduled_at=None),
        )

    async def cancel_running(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, reason: str
    ) -> SettleResult:
        return await self._next(
            "cancel_running",
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "reason": reason,
            },
            SettleOkResult(job_status=JobStatus.CANCELLED, scheduled_at=None),
        )

    async def _settle_default(
        self, command: str, job_id: UUID, attempt_id: UUID, worker_id: str
    ) -> SettleResult:
        return await self._next(
            command,
            {"job_id": job_id, "attempt_id": attempt_id, "worker_id": worker_id},
            SettleOkResult(job_status=JobStatus.QUEUED, scheduled_at=None),
        )

    async def aclose(self) -> None:
        return None


__all__ = ["ManualClock", "RecordedCall", "ScriptedTransport"]
