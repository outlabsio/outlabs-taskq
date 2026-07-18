"""S2-05A notification-as-hint and authoritative polling kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from taskq import (
    ClaimedJob,
    Task,
    TaskRegistry,
    WorkerOptions,
    WorkerService,
    WorkerServiceOptions,
    WorkerServiceState,
)
from taskq.protocol import ClaimResult, ClaimState
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _claim(queue: str, value: int = 1) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue=queue,
        job_type=f"{queue}.work",
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


def _registry(*queues: str, release: asyncio.Event | None = None) -> TaskRegistry:
    tasks = []
    for queue in queues:

        async def handler(payload: Input, *, _queue: str = queue) -> Output:
            assert _queue in queues
            if release is not None:
                await release.wait()
            return Output(doubled=payload.value * 2)

        tasks.append(
            Task(
                name=f"{queue}.work",
                queue=queue,
                input_model=Input,
                output_model=Output,
                handler=handler,
            )
        )
    return TaskRegistry(tasks)


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 100) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


class ScriptedNotifications:
    def __init__(self) -> None:
        self.connect_count = 0
        self.channels: tuple[str, ...] = ()
        self._nudge: Callable[[], None] | None = None
        self._disconnected = asyncio.Event()
        self.closed = False

    async def connect(self, channels: Sequence[str], nudge: Callable[[], None]) -> None:
        self.connect_count += 1
        self.channels = tuple(channels)
        self._nudge = nudge
        self._disconnected = asyncio.Event()

    async def wait_disconnected(self) -> None:
        await self._disconnected.wait()

    def nudge(self) -> None:
        assert self._nudge is not None
        self._nudge()

    def disconnect(self) -> None:
        self._disconnected.set()

    async def aclose(self) -> None:
        self.closed = True
        self._disconnected.set()


@pytest.mark.parametrize(
    "options",
    [
        {"queues": ()},
        {"queues": ("a", "a")},
        {"queues": ("Bad",)},
        {"queues": ("a",), "batch": 51},
        {"queues": ("a",), "poll_interval": 0},
        {"queues": ("a",), "listener_backoff_base": 2, "listener_backoff_cap": 1},
    ],
)
def test_service_options_reject_invalid_boundaries(options: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkerServiceOptions(**options)


async def test_poll_only_claims_and_submits_without_notification_source() -> None:
    transport = ScriptedTransport()
    transport.script("claim", ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),)))
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    await service.start()
    await _spin_until(lambda: any(call.command == "complete" for call in transport.calls))
    assert service.ready
    assert service.snapshot().claimed_jobs == 1
    await service.aclose()
    assert service.stopped


async def test_notification_wakes_claim_before_poll_deadline() -> None:
    clock = ManualClock()
    notifications = ScriptedNotifications()
    transport = ScriptedTransport()
    transport.script(
        "claim",
        ClaimResult(state=ClaimState.EMPTY),
        ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),)),
    )
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), poll_interval=30),
        notifications=notifications,
        clock=clock,
    )
    await service.start()
    await _spin_until(lambda: [call.command for call in transport.calls].count("claim") == 1)
    notifications.nudge()
    await _spin_until(lambda: any(call.command == "complete" for call in transport.calls))
    assert clock.monotonic() == 0
    assert service.snapshot().notification_nudges >= 2  # connect catch-up + explicit nudge
    await service.aclose()


async def test_hot_queue_rotates_before_claiming_again() -> None:
    release = asyncio.Event()
    transport = ScriptedTransport()
    transport.script(
        "claim",
        ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),)),
        ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("beta"),)),
    )
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha", "beta", release=release),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha", "beta"), batch=1, listen=False),
        supervisor_options=WorkerOptions(concurrency=2),
        clock=ManualClock(),
    )
    await service.start()
    await _spin_until(lambda: [call.command for call in transport.calls].count("claim") == 2)
    claim_queues = [
        call.arguments["queue"] for call in transport.calls if call.command == "claim"
    ]
    assert claim_queues == ["alpha", "beta"]
    release.set()
    await service.aclose()


async def test_listener_disconnect_degrades_then_reconnects_with_catchup() -> None:
    clock = ManualClock()
    notifications = ScriptedNotifications()
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",)),
        notifications=notifications,
        clock=clock,
    )
    await service.start()
    assert service.ready
    assert notifications.channels == ("taskq_alpha",)
    notifications.disconnect()
    await _spin_until(
        lambda: service.state is WorkerServiceState.DEGRADED and clock.sleeping >= 2
    )
    clock.advance(0.25)
    await _spin_until(lambda: notifications.connect_count == 2)
    assert service.ready
    assert service.snapshot().listener_reconnects == 1
    await service.aclose()
    assert notifications.closed
