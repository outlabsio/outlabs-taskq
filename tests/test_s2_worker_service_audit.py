"""Repeated Stage-2C service races and owned-resource ledgers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from taskq import WorkerOptions, WorkerService, WorkerServiceOptions
from taskq.errors import TaskqConflictError
from taskq.protocol import ClaimResult, ClaimState
from tests.test_s2_worker_service_poll import (
    ScriptedNotifications,
    _claim,
    _registry,
    _spin_until,
)
from tests.worker_support import ManualClock, ScriptedTransport

ROUNDS = 5


async def test_poll_deadline_vs_notification_never_loses_or_fans_out() -> None:
    for _ in range(ROUNDS):
        clock = ManualClock()
        notifications = ScriptedNotifications()
        transport = ScriptedTransport()
        service = WorkerService(
            transport,  # type: ignore[arg-type]
            _registry("alpha"),
            "worker-audit",
            options=WorkerServiceOptions(queues=("alpha",), poll_interval=10),
            notifications=notifications,
            clock=clock,
        )
        await service.start()
        await _spin_until(lambda: [call.command for call in transport.calls].count("claim") == 1)
        before = service.snapshot().claim_sweeps
        clock.advance(10)
        notifications.nudge()
        await _spin_until(lambda: service.snapshot().claim_sweeps > before)
        for _turn in range(5):
            await asyncio.sleep(0)
        assert 1 <= service.snapshot().claim_sweeps - before <= 2
        await service.aclose()


class ReconnectCloseNotifications(ScriptedNotifications):
    def __init__(self) -> None:
        super().__init__()
        self.reconnect_entered = asyncio.Event()

    async def connect(self, channels: Sequence[str], nudge: Callable[[], None]) -> None:
        if self.connect_count == 0:
            await super().connect(channels, nudge)
            return
        self.connect_count += 1
        self.reconnect_entered.set()
        await asyncio.Event().wait()


async def test_listener_reconnect_vs_close_cannot_resurrect_owned_task() -> None:
    for _ in range(ROUNDS):
        notifications = ReconnectCloseNotifications()
        clock = ManualClock()
        service = WorkerService(
            ScriptedTransport(),  # type: ignore[arg-type]
            _registry("alpha"),
            "worker-audit",
            options=WorkerServiceOptions(queues=("alpha",), listener_backoff_base=0.25),
            notifications=notifications,
            clock=clock,
        )
        await service.start()
        notifications.disconnect()
        await _spin_until(lambda: service.state.value == "degraded")
        await _spin_until(lambda: clock.sleeping >= 3)
        clock.advance(0.25)
        await notifications.reconnect_entered.wait()
        await service.aclose()
        assert notifications.closed
        assert notifications.connect_count == 2
        assert service.stopped


async def test_fatal_job_closes_admission_before_next_claim() -> None:
    for _ in range(ROUNDS):
        transport = ScriptedTransport()
        transport.script("claim", ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),)))
        transport.script("complete", TaskqConflictError())
        service = WorkerService(
            transport,  # type: ignore[arg-type]
            _registry("alpha"),
            "worker-audit",
            options=WorkerServiceOptions(queues=("alpha",), listen=False),
            supervisor_options=WorkerOptions(concurrency=1),
            clock=ManualClock(),
        )
        await service.start()
        await _spin_until(lambda: service.stopped)
        assert [call.command for call in transport.calls].count("claim") == 1
        assert service.snapshot().fatal


async def test_external_run_cancel_returns_task_ledger_to_baseline() -> None:
    before = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-audit",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    running = asyncio.create_task(service.run())
    await _spin_until(lambda: service.ready)
    running.cancel()
    try:
        await running
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0)
    after = {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}
    assert service.stopped
    assert after == before
