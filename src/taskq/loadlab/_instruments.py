"""Transport instrumentation and deterministic fault injection for L-scenarios."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from taskq.errors import TaskqError


@dataclass(frozen=True, slots=True)
class ClaimEvent:
    """One observed claim call on one worker's transport."""

    worker_id: str
    queue: str
    monotonic: float
    state: str
    jobs: int
    error_code: str | None


@dataclass(slots=True)
class ClaimFaultPlan:
    """Deterministic, phase-driven claim-fault schedule.

    ``mode == "retryable"`` makes every claim raise ``retryable_error``;
    ``mode == "corruption"`` makes every claim raise ``fatal_error``;
    ``arm_fatal()`` makes exactly the next claim raise ``fatal_error`` (then
    the plan returns to clean). Shared across workers when shared.
    """

    retryable_error: type[TaskqError] | None = None
    fatal_error: type[TaskqError] | None = None
    mode: str = "clean"
    calls: int = 0
    injected_retryable: int = 0
    injected_fatal: int = 0
    _fatal_armed: bool = False

    def arm_fatal(self) -> None:
        self._fatal_armed = True

    def next_fault(self) -> TaskqError | None:
        self.calls += 1
        if self._fatal_armed and self.fatal_error is not None:
            self._fatal_armed = False
            self.mode = "clean"
            self.injected_fatal += 1
            return self.fatal_error()
        if self.mode == "retryable" and self.retryable_error is not None:
            self.injected_retryable += 1
            return self.retryable_error()
        if self.mode == "corruption" and self.fatal_error is not None:
            self.injected_fatal += 1
            return self.fatal_error()
        return None


@dataclass(slots=True)
class FleetInstruments:
    """Shared recorder for every instrumented transport in one scenario run."""

    claim_events: list[ClaimEvent] = field(default_factory=list)
    settle_calls: int = 0

    def claim_calls(self, *, since: float | None = None) -> int:
        return sum(1 for event in self.claim_events if since is None or event.monotonic >= since)

    def empty_claims(self, *, since: float | None = None) -> int:
        return sum(
            1
            for event in self.claim_events
            if event.state == "empty" and (since is None or event.monotonic >= since)
        )

    def error_claims(self, *, since: float | None = None) -> int:
        return sum(
            1
            for event in self.claim_events
            if event.error_code is not None and (since is None or event.monotonic >= since)
        )


class InstrumentedTransport:
    """RunnerTransport wrapper: records claim traffic, injects planned faults.

    Only ``claim`` is intercepted for faults; settle verbs are counted and
    passed through so fencing, followups, and heartbeats stay real.
    """

    def __init__(
        self,
        inner: Any,
        *,
        worker_id: str,
        instruments: FleetInstruments,
        fault_plan: ClaimFaultPlan | None = None,
    ) -> None:
        self._inner = inner
        self._worker_id = worker_id
        self._instruments = instruments
        self._fault_plan = fault_plan
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def claim(self, queue: str, worker_id: str, **options: Any) -> Any:
        if self._fault_plan is not None:
            async with self._lock:
                fault = self._fault_plan.next_fault()
            if fault is not None:
                self._instruments.claim_events.append(
                    ClaimEvent(
                        worker_id=self._worker_id,
                        queue=queue,
                        monotonic=time.monotonic(),
                        state="error",
                        jobs=0,
                        error_code=fault.code.value,
                    )
                )
                raise fault
        result = await self._inner.claim(queue, worker_id, **options)
        self._instruments.claim_events.append(
            ClaimEvent(
                worker_id=self._worker_id,
                queue=queue,
                monotonic=time.monotonic(),
                state=result.state.value,
                jobs=len(result.jobs),
                error_code=None,
            )
        )
        return result

    async def complete(self, *args: Any, **options: Any) -> Any:
        self._instruments.settle_calls += 1
        return await self._inner.complete(*args, **options)

    async def fail(self, *args: Any, **options: Any) -> Any:
        self._instruments.settle_calls += 1
        return await self._inner.fail(*args, **options)

    async def aclose(self) -> None:
        await self._inner.aclose()
