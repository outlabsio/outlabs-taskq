"""Dedicated PostgreSQL LISTEN connection for the DB-direct worker service."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from typing import Any

import asyncpg
from sqlalchemy.engine import make_url

from taskq.errors import TaskqConfigError

_CHANNEL = re.compile(r"taskq_[a-z0-9_]{1,57}\Z")


def _asyncpg_dsn(dsn: str) -> str:
    url = make_url(dsn)
    if url.drivername == "postgres":
        url = url.set(drivername="postgresql")
    elif url.drivername.startswith("postgresql"):
        url = url.set(drivername="postgresql")
    else:
        raise TaskqConfigError("notification source requires a PostgreSQL DSN")
    return url.render_as_string(hide_password=False)


class PostgresNotificationSource:
    """One reconnectable, non-pooled asyncpg LISTEN session."""

    def __init__(self, dsn: str) -> None:
        self._dsn = _asyncpg_dsn(dsn)
        self._connection: asyncpg.Connection[Any] | None = None
        self._disconnected = asyncio.Event()
        self._closed = False
        self._lock = asyncio.Lock()
        self._channels: set[str] = set()
        self._nudge: Callable[[], None] | None = None
        self._listener: Callable[..., None] | None = None

    def __repr__(self) -> str:
        return "PostgresNotificationSource()"

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(self._channels))

    @staticmethod
    def _validated_channels(channels: Sequence[str], *, empty_ok: bool = False) -> set[str]:
        result = set(channels)
        if (not result and not empty_ok) or any(
            _CHANNEL.fullmatch(channel) is None for channel in result
        ):
            raise TaskqConfigError("invalid taskq notification channel")
        return result

    async def connect(self, channels: Sequence[str], nudge: Callable[[], None]) -> None:
        requested = self._validated_channels(channels)
        async with self._lock:
            if self._closed:
                raise TaskqConfigError("notification source is closed")
            if self._connection is not None and not self._connection.is_closed():
                raise TaskqConfigError("notification source is already connected")
            self._channels.update(requested)
            self._nudge = nudge
            disconnected = asyncio.Event()
            self._disconnected = disconnected
            connection = await asyncpg.connect(self._dsn)
            try:
                connection.add_termination_listener(lambda _connection: disconnected.set())

                def notified(
                    _connection: asyncpg.Connection[Any],
                    _pid: int,
                    _channel: str,
                    _payload: str,
                ) -> None:
                    callback = self._nudge
                    if callback is not None:
                        callback()

                for channel in sorted(self._channels):
                    await connection.add_listener(channel, notified)
            except BaseException:
                await connection.close()
                raise
            self._listener = notified
            self._connection = connection

    async def add_channels(self, channels: Sequence[str]) -> None:
        requested = self._validated_channels(channels, empty_ok=True)
        async with self._lock:
            if self._closed:
                raise TaskqConfigError("notification source is closed")
            additions = requested - self._channels
            self._channels.update(additions)
            connection = self._connection
            listener = self._listener
            if connection is None or connection.is_closed() or listener is None:
                return
            for channel in sorted(additions):
                await connection.add_listener(channel, listener)

    async def remove_channels(self, channels: Sequence[str]) -> None:
        requested = self._validated_channels(channels, empty_ok=True)
        async with self._lock:
            removals = requested & self._channels
            self._channels.difference_update(removals)
            connection = self._connection
            listener = self._listener
            if connection is None or connection.is_closed() or listener is None:
                return
            for channel in sorted(removals):
                await connection.remove_listener(channel, listener)

    async def wait_disconnected(self) -> None:
        if self._connection is None:
            raise TaskqConfigError("notification source is not connected")
        await self._disconnected.wait()

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            connection, self._connection = self._connection, None
            self._listener = None
        if connection is not None and not connection.is_closed():
            await connection.close()
        self._disconnected.set()


__all__ = ["PostgresNotificationSource"]
