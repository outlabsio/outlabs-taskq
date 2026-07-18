"""S2-04D bounded admission, executor, and soft-stop lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event, Lock
from uuid import uuid4

import pytest
from pydantic import BaseModel

from taskq import (
    JobContext,
    JobRunState,
    Task,
    TaskRegistry,
    WorkerCapacityError,
    WorkerOptions,
    WorkerSupervisor,
)
from taskq.errors import TaskqConflictError
from taskq.protocol import ClaimedJob
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _claim(value: int = 1) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue="worker",
        job_type="math.wait",
        priority=100,
        payload={"value": value},
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
    *,
    clock: ManualClock,
    transport: ScriptedTransport | None = None,
    concurrency: int = 2,
    sync_workers: int | None = None,
    timeout: float | None = None,
) -> WorkerSupervisor:
    task = Task(
        name="math.wait",
        queue="worker",
        input_model=Input,
        output_model=Output,
        handler=handler,  # type: ignore[arg-type]
    )
    return WorkerSupervisor(
        transport or ScriptedTransport(),  # type: ignore[arg-type]
        TaskRegistry([task]),
        "worker-1",
        options=WorkerOptions(
            concurrency=concurrency,
            sync_workers=sync_workers,
            soft_stop_timeout=timeout,
        ),
        clock=clock,
    )


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 50) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


async def test_submission_reserves_capacity_and_wakes_waiter() -> None:
    gates = {1: asyncio.Event(), 2: asyncio.Event()}

    async def handler(payload: Input) -> Output:
        await gates[payload.value].wait()
        return Output(doubled=payload.value * 2)

    supervisor = _supervisor(handler, clock=ManualClock())
    supervisor.start()
    first = supervisor.submit(_claim(1))
    second = supervisor.submit(_claim(2))
    assert supervisor.available_slots == 0
    with pytest.raises(WorkerCapacityError, match="capacity"):
        supervisor.submit(_claim(3))

    capacity = asyncio.create_task(supervisor.wait_for_capacity())
    await asyncio.sleep(0)
    assert not capacity.done()
    gates[1].set()
    await first
    await capacity
    assert supervisor.available_slots == 1
    gates[2].set()
    await second
    await supervisor.aclose()


async def test_two_capacity_waiters_observe_one_freed_slot_without_overshoot() -> None:
    gates = {1: asyncio.Event(), 2: asyncio.Event()}

    async def handler(payload: Input) -> Output:
        await gates[payload.value].wait()
        return Output(doubled=payload.value * 2)

    supervisor = _supervisor(handler, clock=ManualClock(), concurrency=1)
    supervisor.start()
    first = supervisor.submit(_claim(1))
    waiters = [asyncio.create_task(supervisor.wait_for_capacity()) for _ in range(2)]
    await asyncio.sleep(0)
    gates[1].set()
    await first
    await asyncio.gather(*waiters)
    second = supervisor.submit(_claim(2))
    with pytest.raises(WorkerCapacityError, match="capacity"):
        supervisor.submit(_claim(2))
    gates[2].set()
    await second
    await supervisor.aclose()


async def test_duplicate_attempt_is_rejected_while_active() -> None:
    release = asyncio.Event()

    async def handler(payload: Input) -> Output:
        await release.wait()
        return Output(doubled=payload.value * 2)

    supervisor = _supervisor(handler, clock=ManualClock())
    claim = _claim()
    supervisor.start()
    running = supervisor.submit(claim)
    with pytest.raises(WorkerCapacityError, match="already running"):
        supervisor.submit(claim)
    release.set()
    await running
    await supervisor.aclose()


async def test_bounded_sync_executor_heartbeats_jobs_waiting_for_thread() -> None:
    release = Event()
    started = 0
    lock = Lock()

    def handler(payload: Input) -> Output:
        nonlocal started
        with lock:
            started += 1
        release.wait()
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    supervisor = _supervisor(
        handler,
        clock=clock,
        transport=transport,
        concurrency=2,
        sync_workers=1,
    )
    supervisor.start()
    jobs = (supervisor.submit(_claim(1)), supervisor.submit(_claim(2)))
    await _spin_until(lambda: started == 1 and clock.sleeping == 2)
    clock.advance(5)
    await _spin_until(lambda: [call.command for call in transport.calls].count("heartbeat") == 2)
    assert started == 1
    release.set()
    await asyncio.gather(*jobs)
    assert started == 2
    await supervisor.aclose()


@pytest.mark.parametrize("is_async", [False, True])
async def test_dispatch_uses_registered_positional_arity(is_async: bool) -> None:
    calls: list[int] = []
    if is_async:

        async def handler(payload: Input, *, flag: bool = True, **kwargs: object) -> Output:
            assert flag and kwargs == {}
            calls.append(payload.value)
            return Output(doubled=payload.value * 2)

    else:

        def handler(payload: Input, *, flag: bool = True, **kwargs: object) -> Output:
            assert flag and kwargs == {}
            calls.append(payload.value)
            return Output(doubled=payload.value * 2)

    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    report = await supervisor.run_job(_claim(3))
    assert report.settlement_command == "complete"
    assert calls == [3]
    await supervisor.aclose()


async def test_soft_stop_closes_intake_but_normal_result_wins_before_deadline() -> None:
    release = asyncio.Event()

    async def handler(ctx: JobContext, payload: Input) -> Output:
        await release.wait()
        return Output(doubled=payload.value * 2)

    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    supervisor.start()
    running = supervisor.submit(_claim())
    stopping = asyncio.create_task(supervisor.stop())
    await _spin_until(lambda: not supervisor.accepting)
    with pytest.raises(WorkerCapacityError, match="not accepting"):
        supervisor.submit(_claim(2))
    release.set()
    report = await running
    await stopping
    assert report.state is JobRunState.SETTLED
    assert [call.command for call in transport.calls] == ["complete"]
    assert supervisor.stopped


async def test_deadline_hard_cancels_async_and_releases_budget_free() -> None:
    async def handler(payload: Input) -> Output:
        await asyncio.Event().wait()
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=clock, transport=transport, timeout=2)
    supervisor.start()
    running = supervisor.submit(_claim())
    await _spin_until(lambda: clock.sleeping == 1)
    stopping = asyncio.create_task(supervisor.stop())
    await _spin_until(lambda: clock.sleeping == 2)
    clock.advance(2)
    report = await running
    await stopping
    assert report.settlement_command == "release"
    assert report.cancellation_reason.value == "shutdown"
    assert transport.calls[0].arguments["worker_id"] == "worker-1"
    assert supervisor.stopped


async def test_live_sync_thread_is_never_released_at_deadline() -> None:
    started = Event()
    release = Event()

    def handler(payload: Input) -> Output:
        started.set()
        release.wait()
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=clock, transport=transport, sync_workers=1, timeout=2)
    supervisor.start()
    running = supervisor.submit(_claim())
    assert await asyncio.to_thread(started.wait, 1)
    stopping = asyncio.create_task(supervisor.stop())
    await _spin_until(lambda: clock.sleeping == 2)
    clock.advance(2)
    await _spin_until(lambda: supervisor.requires_process_exit)
    assert not stopping.done()
    assert not any(call.command == "release" for call in transport.calls)
    release.set()
    report = await running
    await stopping
    assert report.settlement_command == "complete"
    assert not supervisor.requires_process_exit


async def test_live_sync_thread_keeps_heartbeating_after_deadline() -> None:
    started = Event()
    release = Event()

    def handler(payload: Input) -> Output:
        started.set()
        release.wait()
        return Output(doubled=payload.value * 2)

    clock = ManualClock()
    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=clock, transport=transport, sync_workers=1, timeout=2)
    supervisor.start()
    running = supervisor.submit(_claim())
    assert await asyncio.to_thread(started.wait, 1)
    stopping = asyncio.create_task(supervisor.stop())
    await _spin_until(lambda: clock.sleeping == 2)
    clock.advance(2)
    await _spin_until(lambda: supervisor.requires_process_exit)
    clock.advance(3)
    await _spin_until(lambda: [call.command for call in transport.calls] == ["heartbeat"])
    assert not any(call.command == "release" for call in transport.calls)
    release.set()
    assert (await running).settlement_command == "complete"
    await stopping


async def test_fatal_job_auto_stop_drains_second_live_job() -> None:
    async def handler(ctx: JobContext, payload: Input) -> Output:
        if payload.value == 1:
            return Output(doubled=2)
        while not ctx.should_cancel():
            await asyncio.sleep(0)
        ctx.raise_if_cancelled()
        raise AssertionError("unreachable")

    transport = ScriptedTransport()
    transport.script("complete", TaskqConflictError())
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    supervisor.start()
    fatal = supervisor.submit(_claim(1))
    draining = supervisor.submit(_claim(2))
    fatal_report = await fatal
    drained_report = await draining
    await supervisor.stop()
    assert fatal_report.fatal
    assert drained_report.settlement_command == "release"
    assert supervisor.stopped


async def test_external_run_job_cancellation_soft_stops_and_reraises() -> None:
    async def handler(ctx: JobContext, payload: Input) -> Output:
        while not ctx.should_cancel():
            await asyncio.sleep(0)
        ctx.raise_if_cancelled()
        raise AssertionError("unreachable")

    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    running = asyncio.create_task(supervisor.run_job(_claim()))
    await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert running.cancelled()
    assert [call.command for call in transport.calls] == ["release"]
    assert supervisor.stopped


async def test_second_stop_call_escalates_shared_infinite_drain() -> None:
    async def handler(payload: Input) -> Output:
        await asyncio.Event().wait()
        return Output(doubled=payload.value * 2)

    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    supervisor.start()
    running = supervisor.submit(_claim())
    graceful = asyncio.create_task(supervisor.stop())
    await _spin_until(lambda: not supervisor.accepting and not graceful.done())
    escalated = asyncio.create_task(supervisor.stop(cancel=True))
    report = await running
    await asyncio.gather(graceful, escalated)
    assert report.settlement_command == "release"
    assert supervisor.stopped
    assert supervisor._stop_task is not None and supervisor._stop_task.done()


async def test_external_submit_cancellation_releases_then_reraises() -> None:
    started = asyncio.Event()

    async def handler(payload: Input) -> Output:
        started.set()
        await asyncio.Event().wait()
        return Output(doubled=payload.value * 2)

    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    supervisor.start()
    running = supervisor.submit(_claim())
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert running.cancelled()
    assert [call.command for call in transport.calls] == ["release"]
    await supervisor.aclose()


async def test_external_submit_cancellation_before_start_still_releases() -> None:
    async def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    transport = ScriptedTransport()
    supervisor = _supervisor(handler, clock=ManualClock(), transport=transport)
    supervisor.start()
    running = supervisor.submit(_claim())
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert running.cancelled()
    await _spin_until(lambda: [call.command for call in transport.calls] == ["release"])
    await supervisor.aclose()


async def test_job_and_lifecycle_tasks_are_joined_after_stop() -> None:
    async def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    supervisor = _supervisor(handler, clock=ManualClock())
    supervisor.start()
    await supervisor.submit(_claim())
    await supervisor.stop()
    leaked = {
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith("taskq-")
    }
    assert leaked == set()


async def test_cancelled_stop_waiter_is_rejoined_by_aclose() -> None:
    release = asyncio.Event()

    async def handler(payload: Input) -> Output:
        await release.wait()
        return Output(doubled=payload.value * 2)

    before = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    supervisor = _supervisor(handler, clock=ManualClock())
    supervisor.start()
    running = supervisor.submit(_claim())
    await asyncio.sleep(0)
    stop_waiter = asyncio.create_task(supervisor.stop())
    await _spin_until(lambda: not supervisor.accepting)
    stop_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_waiter
    assert supervisor._stop_task is not None and not supervisor._stop_task.done()
    release.set()
    await running
    await supervisor.aclose()
    await asyncio.sleep(0)
    after = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    assert after == before
