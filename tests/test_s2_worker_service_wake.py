"""Wake-on-work claim loop: the held claim and supervisor capacity are the waits.

A worker re-claims the moment a slot frees after productive claims, and a
single-queue worker on a long-poll transport keeps one server-held claim
outstanding instead of sleeping ``poll_interval`` between claims. Errors,
paused queues, throttling, unheld empty answers and multi-queue services keep
their bounded waits.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel

from taskq import (
    ClaimedJob,
    Task,
    TaskRegistry,
    WorkerOptions,
    WorkerService,
    WorkerServiceOptions,
)
from taskq.errors import TaskqUnavailableError
from taskq.http.client import AsyncTaskqHttpClient
from taskq.protocol import ClaimResult, ClaimState
from taskq.transport import ClaimWaitTransport, non_owning_transport_view
from tests.worker_support import ManualClock, RecordedCall, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _job(queue: str = "alpha") -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue=queue,
        job_type=f"{queue}.work",
        priority=100,
        payload={"value": 1},
        headers={},
        progress=None,
        attempt_id=uuid4(),
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        lease_expires_at=datetime.now(UTC),
        lease_seconds=15,
    )


def _registry(*queues: str) -> TaskRegistry:
    async def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    return TaskRegistry(
        Task(
            name=f"{queue}.work",
            queue=queue,
            input_model=Input,
            output_model=Output,
            handler=handler,
        )
        for queue in queues
    )


class _MidpointRandom(random.Random):
    """Deterministic jitter: backoff factor 1.0, uniform draws at the midpoint."""

    def random(self) -> float:
        return 0.5

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


class LongPollTransport(ScriptedTransport):
    """HTTP-shaped runner: an empty claim is held until NOTIFY or its window ends.

    Scripted claim steps answer immediately (paused, throttled, errors). Without
    a script, ready jobs are claimed at once; otherwise the claim is held open
    on the manual clock until ``enqueue`` (the commit notification) or the
    window deadline, exactly like the facade's ``ClaimWaitHub`` long poll.
    """

    def __init__(self, clock: ManualClock, *, window: float, hold: bool = True) -> None:
        super().__init__()
        self._clock = clock
        self._window = window
        self._hold = hold
        self.ready: deque[ClaimedJob] = deque()
        self.claim_times: list[float] = []
        self.holding = 0
        self._notified = asyncio.Event()

    @property
    def claim_wait_seconds(self) -> float:
        return self._window

    def enqueue(self, job: ClaimedJob) -> None:
        self.ready.append(job)
        self._notified.set()

    def _take(self, batch: int) -> ClaimResult:
        jobs = tuple(self.ready.popleft() for _ in range(min(batch, len(self.ready))))
        return ClaimResult(state=ClaimState.CLAIMED, jobs=jobs)

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
        supported_policy_hashes: Sequence[str] | None = None,
        accept_throttled: bool = False,
    ) -> ClaimResult:
        self.claim_times.append(self._clock.monotonic())
        if self._scripts["claim"]:
            return await self._next("claim", {"queue": queue, "batch": batch}, None)
        self.calls.append(RecordedCall("claim", {"queue": queue, "batch": batch}))
        if self.ready:
            return self._take(batch)
        if not self._hold:
            return ClaimResult(state=ClaimState.EMPTY)
        self._notified.clear()
        self.holding += 1
        deadline = asyncio.create_task(self._clock.sleep(self._window))
        notified = asyncio.create_task(self._notified.wait())
        try:
            await asyncio.wait((deadline, notified), return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.holding -= 1
            deadline.cancel()
            notified.cancel()
            await asyncio.gather(deadline, notified, return_exceptions=True)
        if self.ready:
            return self._take(batch)
        return ClaimResult(state=ClaimState.EMPTY)


async def _settle_loop(turns: int = 200) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


async def _spin_until(predicate: Callable[[], bool], *, turns: int = 2000) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


async def _advance(clock: ManualClock, seconds: float, *, step: float = 0.5) -> None:
    """Move the manual clock only after every runnable task registered its sleep."""

    elapsed = 0.0
    while elapsed < seconds:
        await _settle_loop()
        clock.advance(step)
        elapsed += step
    await _settle_loop()


def _completed(transport: ScriptedTransport) -> int:
    return sum(call.command == "complete" for call in transport.calls)


def _service(
    transport: ScriptedTransport,
    clock: ManualClock,
    *,
    queues: tuple[str, ...] = ("alpha",),
    concurrency: int = 1,
    **options: object,
) -> WorkerService:
    return WorkerService(
        transport,  # type: ignore[arg-type]
        _registry(*queues),
        "worker-1",
        options=WorkerServiceOptions(
            queues=queues,
            batch=concurrency,
            listen=False,
            poll_jitter=0,
            # As the HTTP worker CLI does for long-poll workers: stop must not
            # wait out a held claim (here: a manual clock that never advances).
            cancel_inflight_claim_on_stop=True,
            **options,  # type: ignore[arg-type]
        ),
        supervisor_options=WorkerOptions(concurrency=concurrency),
        clock=clock,
        rng=_MidpointRandom(),
    )


def test_http_client_declares_its_long_poll_window() -> None:
    client = AsyncTaskqHttpClient(
        "http://taskq.invalid", bearer_token="t" * 32, claim_wait_seconds=20
    )
    assert isinstance(client, ClaimWaitTransport)
    assert client.claim_wait_seconds == 20
    view = non_owning_transport_view(client)
    assert view.claim_wait_seconds == 20  # delegated; WorkerService reads it by attribute
    assert not isinstance(ScriptedTransport(), ClaimWaitTransport)


async def test_busy_queue_drains_back_to_back_without_poll_sleep_on_any_transport() -> None:
    # SQL-shaped transport (no long poll): one slot, every claim fills it.
    clock = ManualClock()
    transport = ScriptedTransport()
    transport.script(
        "claim",
        *(ClaimResult(state=ClaimState.CLAIMED, jobs=(_job(),)) for _ in range(12)),
    )
    service = _service(transport, clock, poll_interval=30)
    await service.start()
    await _spin_until(
        lambda: (
            _completed(transport) == 12
            and [call.command for call in transport.calls].count("claim") == 13
        )
    )
    # 12 productive claims, each issued the moment the single slot freed, then
    # the first empty claim; the 30 s poll deadline was never waited on.
    assert clock.monotonic() == 0
    await service.aclose()


async def test_long_poll_busy_queue_drains_then_holds_one_claim() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    for _ in range(40):
        transport.ready.append(_job())
    service = _service(transport, clock, concurrency=4, poll_interval=5)
    await service.start()
    await _spin_until(lambda: _completed(transport) == 40 and transport.holding == 1)
    assert clock.monotonic() == 0
    assert service.snapshot().claimed_jobs == 40
    assert all(at == 0 for at in transport.claim_times)
    await service.aclose()


async def test_long_poll_empty_queue_makes_one_claim_per_window_not_a_spin() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    service = _service(transport, clock, poll_interval=5)
    await service.start()
    await _spin_until(lambda: transport.holding == 1)
    await _advance(clock, 100)
    # One held claim at a time, re-issued as each window ends: no poll gap
    # (classic a41 cadence would be 0, 30, 60, 90) and no spin.
    assert transport.claim_times == [0, 25, 50, 75, 100]
    assert transport.holding == 1
    await service.aclose()


async def test_long_poll_notification_answers_held_claim_immediately() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    service = _service(transport, clock, poll_interval=5)
    await service.start()
    await _spin_until(lambda: transport.holding == 1)
    await _advance(clock, 10)
    transport.enqueue(_job())
    await _spin_until(lambda: _completed(transport) == 1 and transport.holding == 1)
    assert clock.monotonic() == 10
    assert transport.claim_times == [0, 10]  # job delivered and next claim held at once
    await service.aclose()


async def test_unheld_empty_answer_falls_back_to_bounded_poll_wait() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25, hold=False)
    service = _service(transport, clock, poll_interval=5)
    await service.start()
    await _spin_until(lambda: len(transport.claim_times) == 1)
    await _advance(clock, 4.5)
    assert transport.claim_times == [0]
    await _advance(clock, 0.5)
    assert transport.claim_times == [0, 5]
    await service.aclose()


async def test_long_poll_paused_queue_keeps_paused_probe_interval() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    transport.script("claim", ClaimResult(state=ClaimState.PAUSED))
    service = _service(transport, clock, poll_interval=0.1, paused_poll_interval=10)
    await service.start()
    await _spin_until(lambda: len(transport.claim_times) == 1)
    await _advance(clock, 9.5)
    assert transport.claim_times == [0]
    await _advance(clock, 0.5)
    await _spin_until(lambda: transport.holding == 1)
    assert transport.claim_times == [0, 10]
    await service.aclose()


async def test_long_poll_claim_errors_keep_exponential_backoff() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    transport.script("claim", TaskqUnavailableError(), TaskqUnavailableError())
    service = _service(
        transport,
        clock,
        poll_interval=0.1,
        claim_backoff_base=1,
        claim_backoff_cap=30,
    )
    await service.start()
    await _spin_until(lambda: len(transport.claim_times) == 1)
    await _advance(clock, 0.5, step=0.25)
    assert transport.claim_times == [0]
    await _advance(clock, 0.5, step=0.25)
    assert transport.claim_times == [0, 1]
    await _advance(clock, 1.5, step=0.25)
    assert transport.claim_times == [0, 1]
    await _advance(clock, 0.5, step=0.25)
    await _spin_until(lambda: transport.holding == 1)
    assert transport.claim_times == [0, 1, 3]
    assert service.ready
    await service.aclose()


async def test_long_poll_throttled_claim_honors_retry_after() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    transport.script("claim", ClaimResult(state=ClaimState.THROTTLED, retry_after_seconds=4))
    service = _service(transport, clock, poll_interval=0.1)
    await service.start()
    await _spin_until(lambda: len(transport.claim_times) == 1)
    await _advance(clock, 4.5)  # 4 s retry-after, midpoint jitter (x1.15) = 4.6 s
    assert transport.claim_times == [0]
    await _advance(clock, 0.5)
    await _spin_until(lambda: transport.holding == 1)
    assert len(transport.claim_times) == 2
    await service.aclose()


async def test_multi_queue_service_keeps_classic_poll_wait() -> None:
    clock = ManualClock()
    transport = LongPollTransport(clock, window=25)
    service = _service(transport, clock, queues=("alpha", "beta"), poll_interval=5)
    await service.start()
    await _spin_until(lambda: transport.holding == 1)
    await _advance(clock, 60)
    # Stage 3: long poll is single-queue only; a multi-queue sweep is still
    # followed by the bounded poll interval.
    assert transport.claim_times == [0, 25, 55]
    await service.aclose()
