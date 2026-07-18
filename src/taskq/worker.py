"""Per-job worker supervision for already-claimed jobs (S2-04)."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from taskq.errors import TaskqConfigError, TaskqError
from taskq.execution import (
    HANDLER_RESULT_TYPES,
    Cancel,
    CancellationReason,
    CancellationToken,
    Complete,
    HandlerResult,
    JobContext,
    NonRetryable,
    Retry,
    Snooze,
    TaskCancelled,
)
from taskq.protocol import ClaimedJob, SettleOutcome, SettleResult
from taskq.registry import Task, TaskRegistry
from taskq.transport import TaskqTransport


class WorkerClock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, delay: float) -> None: ...


class RealWorkerClock:
    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


class WorkerOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    concurrency: int = Field(default=1, ge=1, le=1000)
    sync_workers: int | None = Field(default=None, ge=1, le=1000)
    soft_stop_timeout: float | None = Field(default=None, ge=0)
    cancel_grace_seconds: float = Field(default=30.0, ge=0)
    settle_max_attempts: int = Field(default=5, ge=1, le=100)
    settle_backoff_base: float = Field(default=0.25, gt=0)
    settle_backoff_cap: float = Field(default=5.0, gt=0)
    no_handler_delay_seconds: int = Field(default=60, ge=0, le=86400)

    @model_validator(mode="after")
    def _valid_bounds(self) -> WorkerOptions:
        if self.sync_workers is not None and self.sync_workers > self.concurrency:
            raise ValueError("sync_workers cannot exceed concurrency")
        if self.settle_backoff_cap < self.settle_backoff_base:
            raise ValueError("settle_backoff_cap must cover settle_backoff_base")
        return self

    @property
    def effective_sync_workers(self) -> int:
        return self.sync_workers or self.concurrency


class JobRunState(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    CANCEL_PENDING = "cancel_pending"
    SETTLING = "settling"
    SETTLED = "settled"
    OWNERSHIP_LOST = "ownership_lost"
    ABANDONED_SYNC = "abandoned_sync"
    RUNTIME_FAILED = "runtime_failed"


class JobRunOutcome(StrEnum):
    SETTLED = "settled"
    NO_HANDLER = "no_handler"
    OWNERSHIP_LOST = "ownership_lost"
    SETTLE_CONFLICT = "settle_conflict"
    RUNTIME_ERROR = "runtime_error"


class JobRunReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID
    state: JobRunState
    outcome: JobRunOutcome
    settlement_command: str | None = None
    settlement_outcome: str | None = None
    cancellation_reason: CancellationReason | None = None
    requires_process_exit: bool = False


class WorkerCapacityError(RuntimeError):
    pass


class WorkerInvariantError(RuntimeError):
    pass


class _RunControl:
    def __init__(self, *, is_sync: bool) -> None:
        self.is_sync = is_sync
        self.cancellation = CancellationToken()
        self.ownership_lost = asyncio.Event()
        self.runtime_failed = False
        self.handler: asyncio.Future[Any] | None = None
        self.operator_grace: asyncio.Task[None] | None = None


def _safe_handler_error(exc: BaseException) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class WorkerSupervisor:
    """Supervise already-claimed jobs; claiming and polling belong to S2-05."""

    def __init__(
        self,
        transport: TaskqTransport,
        registry: TaskRegistry,
        worker_id: str,
        *,
        options: WorkerOptions | None = None,
        clock: WorkerClock | None = None,
    ) -> None:
        if not worker_id or len(worker_id) > 200:
            raise TaskqConfigError("worker_id must be non-empty and at most 200 characters")
        self.transport = transport
        self.registry = registry
        self.worker_id = worker_id
        self.options = options or WorkerOptions()
        self.clock = clock or RealWorkerClock()
        self._executor: ThreadPoolExecutor | None = None

    def __repr__(self) -> str:
        return f"WorkerSupervisor(worker_id={self.worker_id!r})"

    def _executor_for_sync(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.options.effective_sync_workers,
                thread_name_prefix="taskq-handler",
            )
        return self._executor

    async def run_job(self, claim: ClaimedJob) -> JobRunReport:
        task = self.registry.resolve(claim.job_type)
        if task is None or task.handler is None:
            return await self._release_no_handler(claim)

        try:
            payload = task.input_model.model_validate(claim.payload)
        except ValidationError as exc:
            return await self._settle_intent(
                claim,
                NonRetryable(error=f"invalid_payload: {_safe_handler_error(exc)}"),
                None,
            )

        context = JobContext(
            job_id=claim.job_id,
            queue=claim.queue,
            job_type=task.name,
            payload=payload,
            headers=claim.headers,
            progress=claim.progress,
            attempt_number=claim.attempt_number,
            failure_count=claim.failure_count,
            max_attempts=claim.max_attempts,
        )
        control = _RunControl(is_sync=not task.handler_is_async)
        context.cancellation = control.cancellation
        control.handler = self._start_handler(task, context, payload)
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(claim, context, control), name="taskq-job-heartbeat"
        )

        handler_value: Any = None
        handler_error: BaseException | None = None
        try:
            handler_value = await control.handler
        except TaskCancelled as exc:
            handler_error = exc
        except asyncio.CancelledError:
            handler_error = TaskCancelled(
                control.cancellation.reason or CancellationReason.SHUTDOWN
            )
        except BaseException as exc:
            handler_error = exc

        if control.operator_grace is not None:
            control.operator_grace.cancel()
            await asyncio.gather(control.operator_grace, return_exceptions=True)

        if control.ownership_lost.is_set():
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            reason = control.cancellation.reason or CancellationReason.LEASE_LOST
            if control.runtime_failed:
                return JobRunReport(
                    job_id=claim.job_id,
                    state=JobRunState.RUNTIME_FAILED,
                    outcome=JobRunOutcome.RUNTIME_ERROR,
                    cancellation_reason=reason,
                )
            return JobRunReport(
                job_id=claim.job_id,
                state=JobRunState.OWNERSHIP_LOST,
                outcome=JobRunOutcome.OWNERSHIP_LOST,
                cancellation_reason=reason,
            )

        try:
            intent = self._normalize_handler_result(task, handler_value, handler_error)
        except (ValidationError, TaskqConfigError) as exc:
            intent = NonRetryable(error=f"invalid_handler_result: {_safe_handler_error(exc)}")
        reason = control.cancellation.reason
        if reason is CancellationReason.OPERATOR:
            intent = Cancel(reason="operator_cancel_requested")
        elif reason is CancellationReason.SHUTDOWN and isinstance(handler_error, TaskCancelled):
            intent = None

        report = await self._settle_intent(claim, intent, reason, context=context)
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        return report

    def _start_handler(
        self, task: Task[Any, Any], context: JobContext, payload: BaseModel
    ) -> asyncio.Future[Any]:
        assert task.handler is not None
        arguments = (
            (context, payload)
            if len(inspect.signature(task.handler).parameters) == 2
            else (payload,)
        )
        if task.handler_is_async:
            value = task.handler(*arguments)
            if not isinstance(value, Awaitable):
                raise WorkerInvariantError("async handler did not return an awaitable")
            return asyncio.create_task(value, name="taskq-handler")
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(self._executor_for_sync(), task.handler, *arguments)

    async def _heartbeat_loop(
        self, claim: ClaimedJob, context: JobContext, control: _RunControl
    ) -> None:
        interval = min(claim.lease_seconds / 3, 30.0)
        consecutive_failures = 0
        await self.clock.sleep(interval)
        while control.handler is not None and not control.handler.done():
            pending = context._pending_checkpoint()
            try:
                result = await self.transport.heartbeat(
                    claim.job_id,
                    claim.attempt_id,
                    self.worker_id,
                    lease_seconds=claim.lease_seconds,
                    progress=pending[1] if pending is not None else None,
                )
            except asyncio.CancelledError:
                raise
            except TaskqError as exc:
                consecutive_failures += 1
                if not exc.retryable or consecutive_failures >= 3:
                    control.runtime_failed = not exc.retryable
                    self._lose_ownership(control)
                    return
                await self.clock.sleep(min(0.25 * (2 ** (consecutive_failures - 1)), interval))
                continue

            if not result.ok:
                self._lose_ownership(control)
                return
            consecutive_failures = 0
            if pending is not None:
                context._ack_checkpoint(pending[0])
            if result.cancel_requested:
                self._request_operator_cancel(control)
            await self.clock.sleep(interval)

    def _lose_ownership(self, control: _RunControl) -> None:
        control.cancellation.cancel(CancellationReason.LEASE_LOST)
        control.ownership_lost.set()
        if not control.is_sync and control.handler is not None:
            control.handler.cancel()

    def _request_operator_cancel(self, control: _RunControl) -> None:
        control.cancellation.cancel(CancellationReason.OPERATOR)
        if control.operator_grace is None:
            control.operator_grace = asyncio.create_task(
                self._operator_grace(control), name="taskq-operator-cancel-grace"
            )

    async def _operator_grace(self, control: _RunControl) -> None:
        await self.clock.sleep(self.options.cancel_grace_seconds)
        if not control.is_sync and control.handler is not None and not control.handler.done():
            control.handler.cancel()

    def _normalize_handler_result(
        self,
        task: Task[Any, Any],
        value: Any,
        error: BaseException | None,
    ) -> HandlerResult:
        if error is not None:
            if isinstance(error, TaskCancelled):
                return Cancel(reason=error.reason.value)
            diagnostic = _safe_handler_error(error)
            return (
                NonRetryable(error=diagnostic) if task.retry is False else Retry(error=diagnostic)
            )
        if isinstance(value, HANDLER_RESULT_TYPES):
            if isinstance(value, Complete):
                validated = task.output_model.model_validate(value.result)
                return value.model_copy(update={"result": validated.model_dump(mode="json")})
            return value
        candidate = {} if value is None else value
        validated = task.output_model.model_validate(candidate)
        return Complete(result=validated.model_dump(mode="json"))

    async def _release_no_handler(self, claim: ClaimedJob) -> JobRunReport:
        result = await self.transport.release(
            claim.job_id,
            claim.attempt_id,
            self.worker_id,
            "no_handler",
            delay_seconds=self.options.no_handler_delay_seconds,
            progress=claim.progress,
        )
        return self._report_from_settle(claim.job_id, "release", result, no_handler=True)

    async def _settle_intent(
        self,
        claim: ClaimedJob,
        intent: HandlerResult | None,
        reason: CancellationReason | None,
        *,
        context: JobContext | None = None,
    ) -> JobRunReport:
        progress = context.progress if context is not None else claim.progress
        if intent is None:
            result = await self.transport.release(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                "worker_shutdown",
                progress=progress,
            )
            command = "release"
        elif isinstance(intent, Complete):
            result = await self.transport.complete(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                result=intent.result,
                followups=intent.followups,
            )
            command = "complete"
        elif isinstance(intent, Snooze):
            result = await self.transport.snooze(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                intent.delay_seconds,
                reason=intent.reason,
                progress=intent.progress or progress,
            )
            command = "snooze"
        elif isinstance(intent, Cancel):
            result = await self.transport.cancel_running(
                claim.job_id, claim.attempt_id, self.worker_id, intent.reason
            )
            command = "cancel_running"
        elif isinstance(intent, Retry):
            result = await self.transport.fail(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                intent.error or "handler requested retry",
                retryable=True,
                retry_after_seconds=intent.after_seconds,
                progress=intent.progress or progress,
            )
            command = "fail"
        else:
            result = await self.transport.fail(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                intent.error,
                retryable=False,
                progress=intent.progress or progress,
            )
            command = "fail"
        report = self._report_from_settle(claim.job_id, command, result)
        return report.model_copy(update={"cancellation_reason": reason})

    @staticmethod
    def _report_from_settle(
        job_id: UUID, command: str, result: SettleResult, *, no_handler: bool = False
    ) -> JobRunReport:
        if result.result is SettleOutcome.LOST:
            return JobRunReport(
                job_id=job_id,
                state=JobRunState.OWNERSHIP_LOST,
                outcome=JobRunOutcome.OWNERSHIP_LOST,
                settlement_command=command,
                settlement_outcome=result.result,
            )
        if result.result is SettleOutcome.SETTLE_CONFLICT:
            return JobRunReport(
                job_id=job_id,
                state=JobRunState.RUNTIME_FAILED,
                outcome=JobRunOutcome.SETTLE_CONFLICT,
                settlement_command=command,
                settlement_outcome=result.result,
            )
        return JobRunReport(
            job_id=job_id,
            state=JobRunState.SETTLED,
            outcome=JobRunOutcome.NO_HANDLER if no_handler else JobRunOutcome.SETTLED,
            settlement_command=command,
            settlement_outcome=result.result,
        )

    async def aclose(self) -> None:
        if self._executor is not None:
            await asyncio.to_thread(self._executor.shutdown, True)
            self._executor = None


__all__ = [
    "JobRunOutcome",
    "JobRunReport",
    "JobRunState",
    "RealWorkerClock",
    "WorkerCapacityError",
    "WorkerClock",
    "WorkerInvariantError",
    "WorkerOptions",
    "WorkerSupervisor",
]
