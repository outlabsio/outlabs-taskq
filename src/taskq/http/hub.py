"""Generation-safe, connection-free long-poll waiter hub."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from taskq.errors import TaskqUnavailableError


@dataclass(slots=True)
class ClaimWaitSubscription:
    _hub: ClaimWaitHub
    _event: asyncio.Event
    _queue: str | None = None
    _closed: bool = False

    async def wait(self, timeout: float) -> bool:
        if self._closed:
            return False
        if self._event.is_set():
            return True
        try:
            await asyncio.wait_for(self._event.wait(), timeout=max(0.0, timeout))
            return True
        except TimeoutError:
            return False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._hub._remove(self._event, self._queue)

    async def __aenter__(self) -> ClaimWaitSubscription:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class ClaimWaitHub:
    """Coalesce untrusted hints into generations; never performs SQL itself."""

    def __init__(self, queue_registrar: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._generation = 0
        self._queue_generations: dict[str, int] = {}
        self._closed = False
        self._lock = asyncio.Lock()
        self._subscribers: dict[str | None, set[asyncio.Event]] = {}
        self._queue_registrar = queue_registrar

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def subscriber_count(self) -> int:
        return sum(len(bucket) for bucket in self._subscribers.values())

    def queue_generation(self, queue: str) -> int:
        return self._queue_generations.get(queue, 0)

    @property
    def closed(self) -> bool:
        return self._closed

    async def prepare_queue(self, queue: str) -> None:
        if self._closed:
            raise TaskqUnavailableError(details={"reason": "claim_wait_hub_stopped"})
        if self._queue_registrar is not None:
            await self._queue_registrar(queue)

    def install_queue_registrar(self, registrar: Callable[[str], Awaitable[None]]) -> None:
        if self._queue_registrar is not None or self._generation or self._subscribers:
            raise TaskqUnavailableError(details={"reason": "claim_wait_hub_already_configured"})
        self._queue_registrar = registrar

    async def subscribe(
        self, observed_generation: int, queue: str | None = None
    ) -> ClaimWaitSubscription:
        event = asyncio.Event()
        async with self._lock:
            if self._closed:
                raise TaskqUnavailableError(details={"reason": "claim_wait_hub_stopped"})
            self._subscribers.setdefault(queue, set()).add(event)
            current = self._generation if queue is None else self._queue_generations.get(queue, 0)
            if observed_generation != current:
                event.set()
        return ClaimWaitSubscription(self, event, queue)

    async def notify(self, queue: str | None = None) -> None:
        """Wake waiters. Queue-scoped when a queue is named; global otherwise.

        A queue-scoped notify wakes that queue's waiters plus legacy global
        subscribers; a global notify (reconnect, shutdown) wakes everyone.
        """

        async with self._lock:
            if self._closed:
                return
            self._generation += 1
            if queue is None:
                for name in self._queue_generations:
                    self._queue_generations[name] += 1
                targets = [event for bucket in self._subscribers.values() for event in bucket]
            else:
                self._queue_generations[queue] = self._queue_generations.get(queue, 0) + 1
                targets = list(self._subscribers.get(queue, ()))
                targets.extend(self._subscribers.get(None, ()))
        for event in targets:
            event.set()

    async def shutdown(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            for name in self._queue_generations:
                self._queue_generations[name] += 1
            subscribers = tuple(event for bucket in self._subscribers.values() for event in bucket)
        for event in subscribers:
            event.set()

    async def _remove(self, event: asyncio.Event, queue: str | None = None) -> None:
        async with self._lock:
            self._subscribers.get(queue, set()).discard(event)


__all__ = ["ClaimWaitHub", "ClaimWaitSubscription"]
