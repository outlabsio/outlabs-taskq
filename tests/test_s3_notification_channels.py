"""Dynamic notification-channel lifecycle and reconnect race vectors."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from taskq.errors import TaskqConfigError
from taskq.sql.notifications import PostgresNotificationSource


class _Connection:
    def __init__(self) -> None:
        self.closed = False
        self.listeners: dict[str, Callable[..., None]] = {}
        self.terminated: Callable[[object], None] | None = None

    def is_closed(self) -> bool:
        return self.closed

    def add_termination_listener(self, callback: Callable[[object], None]) -> None:
        self.terminated = callback

    async def add_listener(self, channel: str, callback: Callable[..., None]) -> None:
        self.listeners[channel] = callback

    async def remove_listener(self, channel: str, callback: Callable[..., None]) -> None:
        assert self.listeners[channel] is callback
        del self.listeners[channel]

    async def close(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.closed = True
        assert self.terminated is not None
        self.terminated(self)


async def test_channels_added_during_reconnect_are_not_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[_Connection] = []
    reconnect_started = asyncio.Event()
    allow_reconnect = asyncio.Event()

    async def connect(_dsn: str) -> _Connection:
        connection = _Connection()
        connections.append(connection)
        if len(connections) == 2:
            reconnect_started.set()
            await allow_reconnect.wait()
        return connection

    monkeypatch.setattr("taskq.sql.notifications.asyncpg.connect", connect)
    source = PostgresNotificationSource("postgresql://user:pass@localhost/db")
    nudges = 0

    def nudge() -> None:
        nonlocal nudges
        nudges += 1

    await source.connect(["taskq_alpha"], nudge)
    first = connections[0]
    first.terminate()
    await source.wait_disconnected()

    reconnect = asyncio.create_task(source.connect(["taskq_alpha"], nudge))
    await reconnect_started.wait()
    addition = asyncio.create_task(source.add_channels(["taskq_beta"]))
    await asyncio.sleep(0)
    assert not addition.done()
    allow_reconnect.set()
    await asyncio.gather(reconnect, addition)

    second = connections[1]
    assert source.channels == ("taskq_alpha", "taskq_beta")
    assert set(second.listeners) == {"taskq_alpha", "taskq_beta"}
    second.listeners["taskq_beta"](second, 1, "taskq_beta", "")
    assert nudges == 1

    await source.remove_channels(["taskq_alpha"])
    assert source.channels == ("taskq_beta",)
    assert set(second.listeners) == {"taskq_beta"}

    old_termination = first.terminated
    assert old_termination is not None
    old_termination(first)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(source.wait_disconnected(), timeout=0.01)

    await source.aclose()
    assert second.closed


async def test_dynamic_channel_validation_and_disconnected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    async def connect(_dsn: str) -> _Connection:
        return connection

    monkeypatch.setattr("taskq.sql.notifications.asyncpg.connect", connect)
    source = PostgresNotificationSource("postgresql://user:pass@localhost/db")
    with pytest.raises(TaskqConfigError, match="invalid taskq notification channel"):
        await source.add_channels(["unsafe-channel"])

    await source.add_channels(["taskq_later"])
    await source.connect(["taskq_initial"], lambda: None)
    assert source.channels == ("taskq_initial", "taskq_later")
    assert set(connection.listeners) == {"taskq_initial", "taskq_later"}
    await source.aclose()
