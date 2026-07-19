"""Generation-safe, connection-free long-poll waiter hub."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from taskq.errors import TaskqUnavailableError


@dataclass(slots=True)
class ClaimWaitSubscription:
    _hub: ClaimWaitHub
    _event: asyncio.Event
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
        await self._hub._remove(self._event)

    async def __aenter__(self) -> ClaimWaitSubscription:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class ClaimWaitHub:
    """Coalesce untrusted hints into generations; never performs SQL itself."""

    def __init__(self) -> None:
        self._generation = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Event] = set()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def closed(self) -> bool:
        return self._closed

    async def subscribe(self, observed_generation: int) -> ClaimWaitSubscription:
        event = asyncio.Event()
        async with self._lock:
            if self._closed:
                raise TaskqUnavailableError(details={"reason": "claim_wait_hub_stopped"})
            self._subscribers.add(event)
            if observed_generation != self._generation:
                event.set()
        return ClaimWaitSubscription(self, event)

    async def notify(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._generation += 1
            subscribers = tuple(self._subscribers)
        for event in subscribers:
            event.set()

    async def shutdown(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            subscribers = tuple(self._subscribers)
        for event in subscribers:
            event.set()

    async def _remove(self, event: asyncio.Event) -> None:
        async with self._lock:
            self._subscribers.discard(event)


__all__ = ["ClaimWaitHub", "ClaimWaitSubscription"]
