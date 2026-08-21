"""S2-05A notification-as-hint and authoritative polling kernel."""

from __future__ import annotations

import asyncio
import logging
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
from taskq.errors import TaskqConfigError
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


class FlakyNotifications(ScriptedNotifications):
    """Notification source whose first `failures` connects fail (None: all)."""

    def __init__(self, *, failures: int | None) -> None:
        super().__init__()
        self._remaining_failures = failures

    async def connect(self, channels: Sequence[str], nudge: Callable[[], None]) -> None:
        if self._remaining_failures is None or self._remaining_failures > 0:
            if self._remaining_failures is not None:
                self._remaining_failures -= 1
            self.connect_count += 1
            raise OSError("connection refused")
        await super().connect(channels, nudge)


async def _advance_until(
    clock: ManualClock, predicate: Callable[[], bool], *, step: float, turns: int = 200
) -> None:
    for _ in range(turns):
        if predicate():
            return
        clock.advance(step)
        for _ in range(5):
            await asyncio.sleep(0)
    assert predicate()


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
    claim_call = next(call for call in transport.calls if call.command == "claim")
    assert claim_call.arguments["job_types"] == ("alpha.work",)
    await service.aclose()
    assert service.stopped


def test_service_claim_filter_excludes_handlerless_metadata_and_is_bounded() -> None:
    async def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    registry = TaskRegistry(
        (
            Task(
                name="alpha.bound",
                queue="alpha",
                input_model=Input,
                output_model=Output,
                handler=handler,
            ),
            Task(
                name="alpha.metadata",
                queue="alpha",
                input_model=Input,
                output_model=Output,
            ),
        )
    )
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        registry,
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
    )
    assert service.claim_job_types("alpha") == ("alpha.bound",)
    with pytest.raises(TaskqConfigError, match="not subscribed"):
        service.claim_job_types("beta")

    with pytest.raises(TaskqConfigError, match="no bound handlers"):
        WorkerService(
            ScriptedTransport(),  # type: ignore[arg-type]
            TaskRegistry(tuple(registry)[1:]),
            "worker-1",
            options=WorkerServiceOptions(queues=("alpha",), listen=False),
        )

    too_many = TaskRegistry(
        Task(
            name=f"alpha.work{index}",
            queue="alpha",
            input_model=Input,
            output_model=Output,
            handler=handler,
        )
        for index in range(21)
    )
    with pytest.raises(TaskqConfigError, match="20 job-type"):
        WorkerService(
            ScriptedTransport(),  # type: ignore[arg-type]
            too_many,
            "worker-1",
            options=WorkerServiceOptions(queues=("alpha",), listen=False),
        )


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


async def test_paused_queue_uses_bounded_probe_and_ignores_nudges() -> None:
    clock = ManualClock()
    notifications = ScriptedNotifications()
    transport = ScriptedTransport()
    transport.script(
        "claim",
        ClaimResult(state=ClaimState.PAUSED),
        ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim("alpha"),)),
    )
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(
            queues=("alpha",),
            poll_interval=0.1,
            paused_poll_interval=10,
            poll_jitter=0,
        ),
        notifications=notifications,
        clock=clock,
    )
    await service.start()
    await _spin_until(lambda: [call.command for call in transport.calls].count("claim") == 1)

    for _ in range(10):
        notifications.nudge()
        await asyncio.sleep(0)
    assert [call.command for call in transport.calls].count("claim") == 1
    assert service.ready

    clock.advance(9.9)
    await asyncio.sleep(0)
    assert [call.command for call in transport.calls].count("claim") == 1
    clock.advance(0.1)
    await _spin_until(lambda: any(call.command == "complete" for call in transport.calls))
    assert [call.command for call in transport.calls].count("claim") == 2
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
    claim_queues = [call.arguments["queue"] for call in transport.calls if call.command == "claim"]
    assert claim_queues == ["alpha", "beta"]
    release.set()
    await service.aclose()


async def test_initial_ready_transition_stays_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    with caplog.at_level(logging.INFO, logger="taskq.worker"):
        await service.start()
    assert service.ready
    ready_levels = [record.levelno for record in caplog.records if record.message == "worker.ready"]
    assert ready_levels == [logging.INFO]
    await service.aclose()


async def test_startup_listener_failure_and_recovery_are_visible_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    notifications = FlakyNotifications(failures=1)
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",)),
        notifications=notifications,
        clock=clock,
    )
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        await service.start()
        assert service.state is WorkerServiceState.DEGRADED
        await _advance_until(clock, lambda: service.ready, step=0.25)
    assert notifications.connect_count == 2
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    messages = [record.message for record in warnings]
    assert "worker.degraded" in messages
    assert "worker.ready" in messages
    error = next(record for record in warnings if record.message == "listener.error")
    assert error.error == "OSError: connection refused"
    assert error.suppressed_errors == 0
    await service.aclose()


async def test_listener_connect_errors_warn_throttled_to_backoff_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    notifications = FlakyNotifications(failures=None)
    service = WorkerService(
        ScriptedTransport(),  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-1",
        options=WorkerServiceOptions(
            queues=("alpha",), listener_backoff_base=0.25, listener_backoff_cap=60
        ),
        notifications=notifications,
        clock=clock,
    )
    with caplog.at_level(logging.WARNING, logger="taskq.worker"):
        await service.start()
        assert notifications.connect_count == 1
        await _advance_until(clock, lambda: notifications.connect_count >= 3, step=0.25)
        errors = [record for record in caplog.records if record.message == "listener.error"]
        assert len(errors) == 1  # repeats inside the cap window are suppressed
        clock.advance(60)
        logged = notifications.connect_count
        await _advance_until(clock, lambda: notifications.connect_count > logged, step=0.25)
    errors = [record for record in caplog.records if record.message == "listener.error"]
    assert len(errors) == 2
    assert errors[0].suppressed_errors == 0
    assert errors[1].suppressed_errors == notifications.connect_count - 2
    assert service.state is WorkerServiceState.DEGRADED
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
    await _spin_until(lambda: service.state is WorkerServiceState.DEGRADED and clock.sleeping >= 2)
    clock.advance(0.25)
    await _spin_until(lambda: notifications.connect_count == 2)
    assert service.ready
    assert service.snapshot().listener_reconnects == 1
    await service.aclose()
    assert notifications.closed
