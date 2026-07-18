"""Per-job worker supervision for already-claimed jobs (S2-04)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from taskq.errors import (
    TaskqCapabilityError,
    TaskqConfigError,
    TaskqError,
    TaskqValidationError,
)
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
from taskq.protocol import COMMAND_SPECS, ClaimedJob, CommandName, SettleOutcome, SettleResult
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
    SETTLEMENT_UNKNOWN = "settlement_unknown"
    FOLLOWUP_REJECTED = "followup_rejected"
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
    fatal: bool = False


class WorkerCapacityError(RuntimeError):
    pass


class WorkerInvariantError(RuntimeError):
    pass


class _RunControl:
    def __init__(self, *, is_sync: bool) -> None:
        self.is_sync = is_sync
        self.cancellation = CancellationToken()
        self.ownership_lost = asyncio.Event()
        self.settlement_terminal = asyncio.Event()
        self.runtime_failed = False
        self.handler: asyncio.Future[Any] | None = None
        self.operator_grace: asyncio.Task[None] | None = None
        self.shutdown_deadline = False
        self.external_cancelled = False
        self.abandoned_sync = False


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
        self._accepting = False
        self._stopping = False
        self._stopped = asyncio.Event()
        self._capacity_changed = asyncio.Event()
        self._capacity_changed.set()
        self._force_stop = asyncio.Event()
        self._active: dict[tuple[UUID, UUID], asyncio.Task[JobRunReport]] = {}
        self._controls: dict[tuple[UUID, UUID], _RunControl] = {}
        self._stop_task: asyncio.Task[None] | None = None
        self._deadline_reached = False

    def __repr__(self) -> str:
        return f"WorkerSupervisor(worker_id={self.worker_id!r})"

    @property
    def available_slots(self) -> int:
        return self._free_slots if self._accepting else 0

    @property
    def _free_slots(self) -> int:
        return max(0, self.options.concurrency - len(self._active))

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    @property
    def requires_process_exit(self) -> bool:
        return any(
            control.is_sync
            and control.handler is not None
            and not control.handler.done()
            and (self._deadline_reached or control.ownership_lost.is_set())
            for control in self._controls.values()
        )

    def start(self) -> None:
        if self._stopping or self.stopped:
            raise WorkerCapacityError("worker supervisor is stopping")
        self._accepting = True
        self._refresh_capacity_event()

    async def wait_for_capacity(self) -> None:
        while self._free_slots == 0:
            if not self._accepting:
                raise WorkerCapacityError("worker supervisor is not accepting jobs")
            self._capacity_changed.clear()
            if self._free_slots > 0:
                self._capacity_changed.set()
                return
            await self._capacity_changed.wait()
        if not self._accepting:
            raise WorkerCapacityError("worker supervisor is not accepting jobs")

    def submit(self, claim: ClaimedJob) -> asyncio.Task[JobRunReport]:
        if not self._accepting:
            raise WorkerCapacityError("worker supervisor is not accepting jobs")
        return self._reserve(claim)

    def _executor_for_sync(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.options.effective_sync_workers,
                thread_name_prefix="taskq-handler",
            )
        return self._executor

    async def run_job(self, claim: ClaimedJob) -> JobRunReport:
        """Reserve and await one job without requiring the submission intake to be open."""
        if self._stopping or self.stopped:
            raise WorkerCapacityError("worker supervisor is stopping")
        running = self._reserve(claim)
        try:
            return await asyncio.shield(running)
        except asyncio.CancelledError:
            await self.stop()
            raise

    def _reserve(self, claim: ClaimedJob) -> asyncio.Task[JobRunReport]:
        key = (claim.job_id, claim.attempt_id)
        if key in self._active:
            raise WorkerCapacityError("job attempt is already running")
        if self._free_slots == 0:
            raise WorkerCapacityError("worker supervisor is at capacity")
        running = asyncio.create_task(
            self._run_reserved(claim, key),
            name="taskq-job-supervisor",
        )
        self._active[key] = running
        running.add_done_callback(
            lambda completed: self._recover_prestart_cancellation(
                completed, claim=claim, key=key
            )
        )
        self._refresh_capacity_event()
        return running

    def _recover_prestart_cancellation(
        self,
        completed: asyncio.Task[JobRunReport],
        *,
        claim: ClaimedJob,
        key: tuple[UUID, UUID],
    ) -> None:
        if (
            not completed.cancelled()
            or self._active.get(key) is not completed
            or key in self._controls
        ):
            return
        recovery = asyncio.create_task(
            self._release_prestart_cancellation(claim, key),
            name="taskq-prestart-cancellation",
        )
        self._active[key] = recovery

    async def _release_prestart_cancellation(
        self, claim: ClaimedJob, key: tuple[UUID, UUID]
    ) -> None:
        self._begin_soft_stop()
        try:
            await self._settle_intent(claim, None, CancellationReason.SHUTDOWN)
        finally:
            self._active.pop(key, None)
            self._refresh_capacity_event()
            if self._stop_task is None:
                self._stop_task = asyncio.create_task(self._stop(), name="taskq-worker-stop")

    async def _run_reserved(self, claim: ClaimedJob, key: tuple[UUID, UUID]) -> JobRunReport:
        fatal = False
        external_cancelled = False
        try:
            report = await self._execute_job(claim, key)
            if report.fatal:
                fatal = True
                self._begin_soft_stop()
            return report
        finally:
            control = self._controls.get(key)
            external_cancelled = control is not None and control.external_cancelled
            self._controls.pop(key, None)
            self._active.pop(key, None)
            self._refresh_capacity_event()
            if fatal and self._stop_task is None:
                self._stop_task = asyncio.create_task(self._stop(), name="taskq-worker-stop")
            if external_cancelled and self._stop_task is None:
                self._stop_task = asyncio.create_task(self._stop(), name="taskq-worker-stop")

    async def _execute_job(self, claim: ClaimedJob, key: tuple[UUID, UUID]) -> JobRunReport:
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
        self._controls[key] = control
        if self._stopping:
            control.cancellation.cancel(CancellationReason.SHUTDOWN)
        if self._deadline_reached:
            self._enforce_shutdown_deadline(control)
        context.cancellation = control.cancellation
        control.handler = self._start_handler(task, context, payload)
        if self._deadline_reached:
            self._enforce_shutdown_deadline(control)
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(claim, context, control), name="taskq-job-heartbeat"
        )

        handler_value: Any = None
        handler_error: BaseException | None = None
        try:
            handler_value = await asyncio.shield(control.handler)
        except TaskCancelled as exc:
            handler_error = exc
        except asyncio.CancelledError:
            if (
                control.cancellation.reason is None
                and not control.shutdown_deadline
                and not control.ownership_lost.is_set()
            ):
                control.external_cancelled = True
                self._begin_soft_stop()
                if not control.is_sync and not control.handler.done():
                    control.handler.cancel()
                await asyncio.gather(control.handler, return_exceptions=True)
            else:
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
            return self._ownership_lost_report(claim.job_id, control)

        if control.external_cancelled:
            try:
                await self._shielded_settlement(
                    self._settle_intent(
                        claim,
                        None,
                        CancellationReason.SHUTDOWN,
                        context=context,
                        control=control,
                    )
                )
            finally:
                control.settlement_terminal.set()
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            raise asyncio.CancelledError

        try:
            intent = self._normalize_handler_result(task, handler_value, handler_error)
        except (ValidationError, TaskqConfigError) as exc:
            intent = NonRetryable(error=f"invalid_handler_result: {_safe_handler_error(exc)}")
        reason = control.cancellation.reason
        if reason is CancellationReason.OPERATOR:
            intent = Cancel(reason="operator_cancel_requested")
        elif reason is CancellationReason.SHUTDOWN and (
            isinstance(handler_error, TaskCancelled) or control.shutdown_deadline
        ):
            intent = None

        try:
            return await self._settle_intent(
                claim, intent, reason, context=context, control=control
            )
        finally:
            control.settlement_terminal.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    def _refresh_capacity_event(self) -> None:
        if self._accepting and self._free_slots > 0:
            self._capacity_changed.set()
        elif self._free_slots == 0:
            self._capacity_changed.clear()

    def _begin_soft_stop(self) -> None:
        self._accepting = False
        self._stopping = True
        self._capacity_changed.set()
        for control in self._controls.values():
            control.cancellation.cancel(CancellationReason.SHUTDOWN)

    def _enforce_shutdown_deadline(self, control: _RunControl) -> None:
        control.cancellation.cancel(CancellationReason.SHUTDOWN)
        if not control.is_sync and control.handler is not None and not control.handler.done():
            control.shutdown_deadline = True
            control.handler.cancel()

    def _enforce_all_shutdown_deadlines(self) -> None:
        self._deadline_reached = True
        for control in self._controls.values():
            self._enforce_shutdown_deadline(control)

    async def stop(self, *, cancel: bool = False) -> None:
        self._begin_soft_stop()
        if cancel:
            self._force_stop.set()
            self._enforce_all_shutdown_deadlines()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name="taskq-worker-stop")
        await asyncio.shield(self._stop_task)

    async def _stop(self) -> None:
        active = tuple(self._active.values())
        if active:
            drain = asyncio.gather(*active, return_exceptions=True)
            if self.options.soft_stop_timeout is None and not self._force_stop.is_set():
                force = asyncio.create_task(
                    self._force_stop.wait(), name="taskq-worker-stop-escalation"
                )
                done, _ = await asyncio.wait((drain, force), return_when=asyncio.FIRST_COMPLETED)
                if force in done and not drain.done():
                    self._enforce_all_shutdown_deadlines()
                force.cancel()
                await asyncio.gather(force, return_exceptions=True)
            elif not self._force_stop.is_set():
                deadline = asyncio.create_task(
                    self.clock.sleep(self.options.soft_stop_timeout),
                    name="taskq-worker-stop-deadline",
                )
                force = asyncio.create_task(
                    self._force_stop.wait(), name="taskq-worker-stop-escalation"
                )
                done, _ = await asyncio.wait(
                    (drain, deadline, force), return_when=asyncio.FIRST_COMPLETED
                )
                if drain not in done:
                    self._enforce_all_shutdown_deadlines()
                deadline.cancel()
                force.cancel()
                await asyncio.gather(deadline, force, return_exceptions=True)
            await drain
        if self._executor is not None:
            await asyncio.to_thread(self._executor.shutdown, True)
            self._executor = None
        self._stopped.set()

    def _start_handler(
        self, task: Task[Any, Any], context: JobContext, payload: BaseModel
    ) -> asyncio.Future[Any]:
        assert task.handler is not None
        arguments = (
            (context, payload)
            if task.handler_positional_arity == 2
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
        while not control.settlement_terminal.is_set():
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
        if control.is_sync and control.handler is not None and not control.handler.done():
            control.abandoned_sync = True
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
        async def operation() -> SettleResult:
            return await self.transport.release(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                "no_handler",
                delay_seconds=self.options.no_handler_delay_seconds,
                progress=claim.progress,
            )

        try:
            report = await self._settle_with_retry(claim.job_id, CommandName.RELEASE, operation)
        except (TaskqValidationError, TaskqCapabilityError):
            return self._runtime_failure(claim.job_id, CommandName.RELEASE)
        if report.state is JobRunState.SETTLED:
            return report.model_copy(update={"outcome": JobRunOutcome.NO_HANDLER})
        return report

    async def _settle_intent(
        self,
        claim: ClaimedJob,
        intent: HandlerResult | None,
        reason: CancellationReason | None,
        *,
        context: JobContext | None = None,
        control: _RunControl | None = None,
    ) -> JobRunReport:
        progress = context.progress if context is not None else claim.progress
        if intent is None:

            async def operation() -> SettleResult:
                return await self.transport.release(
                    claim.job_id,
                    claim.attempt_id,
                    self.worker_id,
                    "worker_shutdown",
                    progress=progress,
                )

            command = CommandName.RELEASE
        elif isinstance(intent, Complete):

            async def operation() -> SettleResult:
                return await self.transport.complete(
                    claim.job_id,
                    claim.attempt_id,
                    self.worker_id,
                    result=intent.result,
                    followups=intent.followups,
                )

            command = CommandName.COMPLETE
        elif isinstance(intent, Snooze):

            async def operation() -> SettleResult:
                return await self.transport.snooze(
                    claim.job_id,
                    claim.attempt_id,
                    self.worker_id,
                    intent.delay_seconds,
                    reason=intent.reason,
                    progress=intent.progress or progress,
                )

            command = CommandName.SNOOZE
        elif isinstance(intent, Cancel):

            async def operation() -> SettleResult:
                return await self.transport.cancel_running(
                    claim.job_id, claim.attempt_id, self.worker_id, intent.reason
                )

            command = CommandName.CANCEL_RUNNING
        elif isinstance(intent, Retry):

            async def operation() -> SettleResult:
                return await self.transport.fail(
                    claim.job_id,
                    claim.attempt_id,
                    self.worker_id,
                    intent.error or "handler requested retry",
                    retryable=True,
                    retry_after_seconds=intent.after_seconds,
                    progress=intent.progress or progress,
                )

            command = CommandName.FAIL
        else:

            async def operation() -> SettleResult:
                return await self.transport.fail(
                    claim.job_id,
                    claim.attempt_id,
                    self.worker_id,
                    intent.error,
                    retryable=False,
                    progress=intent.progress or progress,
                )

            command = CommandName.FAIL
        try:
            report = await self._settle_with_retry(
                claim.job_id, command, operation, control=control
            )
        except (TaskqValidationError, TaskqCapabilityError) as exc:
            if not isinstance(intent, Complete) or not intent.followups:
                return self._runtime_failure(claim.job_id, command)
            report = await self._settle_invalid_followup(
                claim,
                progress,
                capability_skew=isinstance(exc, TaskqCapabilityError),
                control=control,
            )
        return report.model_copy(update={"cancellation_reason": reason})

    async def _settle_with_retry(
        self,
        job_id: UUID,
        command: CommandName,
        operation: Callable[[], Awaitable[SettleResult]],
        *,
        control: _RunControl | None = None,
    ) -> JobRunReport:
        for attempt in range(1, self.options.settle_max_attempts + 1):
            if control is not None and control.ownership_lost.is_set():
                return self._ownership_lost_report(job_id, control)
            try:
                result = await operation()
            except asyncio.CancelledError:
                raise
            except (TaskqValidationError, TaskqCapabilityError):
                raise
            except TaskqError as exc:
                if not exc.retryable:
                    return self._runtime_failure(job_id, command)
                retryable = True
            except (TimeoutError, ConnectionError):
                retryable = True
            except Exception:
                return self._runtime_failure(job_id, command)
            else:
                if result.result.value not in COMMAND_SPECS[command].outcomes:
                    return self._runtime_failure(job_id, command)
                return self._report_from_settle(job_id, command.value, result)

            if retryable and attempt < self.options.settle_max_attempts:
                delay = min(
                    self.options.settle_backoff_base * (2 ** (attempt - 1)),
                    self.options.settle_backoff_cap,
                )
                if await self._settle_delay(delay, control):
                    return self._ownership_lost_report(job_id, control)
        return JobRunReport(
            job_id=job_id,
            state=JobRunState.RUNTIME_FAILED,
            outcome=JobRunOutcome.SETTLEMENT_UNKNOWN,
            settlement_command=command.value,
            fatal=True,
        )

    async def _settle_invalid_followup(
        self,
        claim: ClaimedJob,
        progress: dict[str, Any] | None,
        *,
        capability_skew: bool,
        control: _RunControl | None = None,
    ) -> JobRunReport:
        async def terminal_fail() -> SettleResult:
            return await self.transport.fail(
                claim.job_id,
                claim.attempt_id,
                self.worker_id,
                "invalid_followup: rejected by active SQL contract",
                retryable=False,
                progress=progress,
            )

        try:
            failed = await self._settle_with_retry(
                claim.job_id, CommandName.FAIL, terminal_fail, control=control
            )
        except (TaskqValidationError, TaskqCapabilityError):
            return self._runtime_failure(claim.job_id, CommandName.FAIL)
        if failed.state is not JobRunState.SETTLED:
            return failed
        return JobRunReport(
            job_id=claim.job_id,
            state=JobRunState.RUNTIME_FAILED if capability_skew else JobRunState.SETTLED,
            outcome=JobRunOutcome.FOLLOWUP_REJECTED,
            settlement_command=CommandName.FAIL.value,
            settlement_outcome=failed.settlement_outcome,
            fatal=capability_skew,
        )

    @staticmethod
    def _runtime_failure(job_id: UUID, command: CommandName) -> JobRunReport:
        return JobRunReport(
            job_id=job_id,
            state=JobRunState.RUNTIME_FAILED,
            outcome=JobRunOutcome.RUNTIME_ERROR,
            settlement_command=command.value,
            fatal=True,
        )

    async def _settle_delay(self, delay: float, control: _RunControl | None) -> bool:
        if control is None:
            await self.clock.sleep(delay)
            return False
        sleeping = asyncio.create_task(self.clock.sleep(delay), name="taskq-settle-backoff")
        lost = asyncio.create_task(
            control.ownership_lost.wait(), name="taskq-settle-ownership"
        )
        done, _ = await asyncio.wait((sleeping, lost), return_when=asyncio.FIRST_COMPLETED)
        sleeping.cancel()
        lost.cancel()
        await asyncio.gather(sleeping, lost, return_exceptions=True)
        return lost in done

    @staticmethod
    async def _shielded_settlement(
        settlement: Awaitable[JobRunReport],
    ) -> JobRunReport:
        running = asyncio.create_task(settlement, name="taskq-shielded-settlement")
        while not running.done():
            try:
                await asyncio.shield(running)
            except asyncio.CancelledError:
                continue
        return running.result()

    @staticmethod
    def _ownership_lost_report(job_id: UUID, control: _RunControl) -> JobRunReport:
        reason = control.cancellation.reason or CancellationReason.LEASE_LOST
        if control.runtime_failed:
            return JobRunReport(
                job_id=job_id,
                state=JobRunState.RUNTIME_FAILED,
                outcome=JobRunOutcome.RUNTIME_ERROR,
                cancellation_reason=reason,
                requires_process_exit=control.abandoned_sync,
                fatal=True,
            )
        return JobRunReport(
            job_id=job_id,
            state=(
                JobRunState.ABANDONED_SYNC
                if control.abandoned_sync
                else JobRunState.OWNERSHIP_LOST
            ),
            outcome=JobRunOutcome.OWNERSHIP_LOST,
            cancellation_reason=reason,
            requires_process_exit=control.abandoned_sync,
        )

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
                fatal=True,
            )
        return JobRunReport(
            job_id=job_id,
            state=JobRunState.SETTLED,
            outcome=JobRunOutcome.NO_HANDLER if no_handler else JobRunOutcome.SETTLED,
            settlement_command=command,
            settlement_outcome=result.result,
        )

    async def aclose(self) -> None:
        await self.stop(cancel=True)


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
