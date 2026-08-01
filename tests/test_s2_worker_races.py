"""S2-04-AUDIT repeated choreographed worker races without sleeps."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import Event
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from taskq import JobContext, JobRunOutcome, Task, TaskRegistry, WorkerOptions, WorkerSupervisor
from taskq.protocol import ClaimedJob, HeartbeatResult, SettleResult
from tests.worker_support import ManualClock, ScriptedTransport

ROUNDS = 5


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _claim() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue="audit_race",
        job_type="audit.race",
        priority=100,
        payload={"value": 2},
        headers={},
        progress=None,
        attempt_id=uuid4(),
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        lease_expires_at=datetime.now(UTC),
        lease_seconds=15,
    )


def _supervisor(
    handler: object,
    transport: ScriptedTransport,
    clock: ManualClock,
    *,
    timeout: float | None = None,
) -> WorkerSupervisor:
    task = Task(
        name="audit.race",
        queue="audit_race",
        input_model=Input,
        output_model=Output,
        handler=handler,  # type: ignore[arg-type]
    )
    return WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        TaskRegistry([task]),
        "worker-audit",
        options=WorkerOptions(soft_stop_timeout=timeout),
        clock=clock,
    )


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 50) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


class _HeartbeatBarrier(ScriptedTransport):
    def __init__(self, result: HeartbeatResult) -> None:
        super().__init__()
        self.result = result
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.progress: list[Mapping[str, Any] | None] = []
        self.block_next = True

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
        self.progress.append(progress)
        if self.block_next:
            self.block_next = False
            self.entered.set()
            await self.release.wait()
        await super().heartbeat(
            job_id,
            attempt_id,
            worker_id,
            lease_seconds=lease_seconds,
            progress=progress,
            stats=stats,
        )
        return self.result


async def test_handler_return_vs_heartbeat_loss_has_one_authoritative_winner() -> None:
    for loss_first in (False, True):
        for _ in range(ROUNDS):
            handler_release = asyncio.Event()

            async def handler(payload: Input) -> Output:
                await handler_release.wait()
                return Output(doubled=payload.value * 2)

            clock = ManualClock()
            transport = _HeartbeatBarrier(
                HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
            )
            supervisor = _supervisor(handler, transport, clock)
            running = asyncio.create_task(supervisor.run_job(_claim()))
            await _spin_until(lambda: clock.sleeping == 1)
            clock.advance(5)
            await transport.entered.wait()
            if loss_first:
                transport.release.set()
                report = await running
                assert report.outcome is JobRunOutcome.OWNERSHIP_LOST
                assert [call.command for call in transport.calls] == ["heartbeat"]
            else:
                handler_release.set()
                report = await running
                assert report.settlement_command == "complete"
                assert not any(call.command == "heartbeat" for call in transport.calls)
            await supervisor.aclose()


async def test_operator_cancel_vs_complete_obeys_observation_order() -> None:
    for cancel_first in (False, True):
        for _ in range(ROUNDS):
            handler_release = asyncio.Event()

            async def handler(payload: Input) -> Output:
                await handler_release.wait()
                return Output(doubled=payload.value * 2)

            clock = ManualClock()
            transport = _HeartbeatBarrier(
                HeartbeatResult(ok=True, cancel_requested=True, lease_expires_at=None)
            )
            supervisor = _supervisor(handler, transport, clock)
            running = asyncio.create_task(supervisor.run_job(_claim()))
            await _spin_until(lambda: clock.sleeping == 1)
            clock.advance(5)
            await transport.entered.wait()
            if cancel_first:
                transport.release.set()
                await _spin_until(lambda: clock.sleeping >= 2)
                handler_release.set()
                report = await running
                assert report.settlement_command == "cancel_running"
            else:
                handler_release.set()
                report = await running
                assert report.settlement_command == "complete"
            await supervisor.aclose()


class _CompleteBarrier(ScriptedTransport):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, *args: Any, **kwargs: Any) -> SettleResult:
        self.entered.set()
        await self.release.wait()
        return await super().complete(*args, **kwargs)


async def test_shutdown_deadline_does_not_interrupt_settlement_critical_section() -> None:
    async def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    for _ in range(ROUNDS):
        clock = ManualClock()
        transport = _CompleteBarrier()
        supervisor = _supervisor(handler, transport, clock, timeout=2)
        supervisor.start()
        running = supervisor.submit(_claim())
        await transport.entered.wait()
        stopping = asyncio.create_task(supervisor.stop())
        await _spin_until(lambda: clock.sleeping == 2)
        clock.advance(2)
        await asyncio.sleep(0)
        assert not running.done() and not stopping.done()
        transport.release.set()
        report = await running
        await stopping
        assert report.settlement_command == "complete"
        assert [call.command for call in transport.calls] == ["complete"]


async def test_checkpoint_update_during_heartbeat_survives_old_snapshot_ack() -> None:
    for _ in range(ROUNDS):
        second = asyncio.Event()
        second_done = asyncio.Event()
        finish = asyncio.Event()

        async def handler(ctx: JobContext, payload: Input) -> Output:
            await ctx.checkpoint({"cursor": 1})
            await second.wait()
            await ctx.checkpoint({"cursor": 2})
            second_done.set()
            await finish.wait()
            return Output(doubled=payload.value * 2)

        clock = ManualClock()
        transport = _HeartbeatBarrier(
            HeartbeatResult(ok=True, cancel_requested=False, lease_expires_at=None)
        )
        supervisor = _supervisor(handler, transport, clock)
        running = asyncio.create_task(supervisor.run_job(_claim()))
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        await transport.entered.wait()
        assert transport.progress == [{"cursor": 1}]
        second.set()
        await second_done.wait()
        transport.release.set()
        await _spin_until(lambda: clock.sleeping == 1)
        clock.advance(5)
        await _spin_until(lambda: len(transport.progress) == 2)
        assert transport.progress == [{"cursor": 1}, {"cursor": 2}]
        finish.set()
        await running
        await supervisor.aclose()


async def test_sync_return_vs_lease_loss_never_settles_after_loss() -> None:
    for loss_first in (False, True):
        for _ in range(ROUNDS):
            started = Event()
            handler_release = Event()

            def handler(payload: Input) -> Output:
                started.set()
                handler_release.wait()
                return Output(doubled=payload.value * 2)

            clock = ManualClock()
            transport = _HeartbeatBarrier(
                HeartbeatResult(ok=False, cancel_requested=False, lease_expires_at=None)
            )
            supervisor = _supervisor(handler, transport, clock)
            running = asyncio.create_task(supervisor.run_job(_claim()))
            assert await asyncio.to_thread(started.wait, 1)
            await _spin_until(lambda: clock.sleeping == 1)
            clock.advance(5)
            await transport.entered.wait()
            if loss_first:
                transport.release.set()
                await _spin_until(lambda: len(transport.calls) == 1)
                handler_release.set()
                report = await running
                assert report.outcome is JobRunOutcome.OWNERSHIP_LOST
                assert [call.command for call in transport.calls] == ["heartbeat"]
            else:
                handler_release.set()
                report = await running
                assert report.settlement_command == "complete"
            await supervisor.aclose()


async def test_sync_executor_threads_return_to_baseline() -> None:
    import threading

    before = {thread.ident for thread in threading.enumerate() if thread.name.startswith("taskq-")}

    def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    supervisor = _supervisor(handler, ScriptedTransport(), ManualClock())
    await supervisor.run_job(_claim())
    await supervisor.aclose()
    after = {thread.ident for thread in threading.enumerate() if thread.name.startswith("taskq-")}
    assert after == before


async def test_supervised_failures_leave_no_unobserved_task_exceptions() -> None:
    observed: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: observed.append(context))

    async def handler(payload: Input) -> Output:
        raise RuntimeError(f"failed {payload.value}")

    try:
        supervisor = _supervisor(handler, ScriptedTransport(), ManualClock())
        report = await supervisor.run_job(_claim())
        assert report.settlement_command == "fail"
        await supervisor.aclose()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert observed == []
    finally:
        loop.set_exception_handler(previous)
