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
