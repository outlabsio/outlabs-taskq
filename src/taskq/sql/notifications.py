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

    def __repr__(self) -> str:
        return "PostgresNotificationSource()"

    async def connect(self, channels: Sequence[str], nudge: Callable[[], None]) -> None:
        if self._closed:
            raise TaskqConfigError("notification source is closed")
        if self._connection is not None and not self._connection.is_closed():
            raise TaskqConfigError("notification source is already connected")
        if not channels or any(_CHANNEL.fullmatch(channel) is None for channel in channels):
            raise TaskqConfigError("invalid taskq notification channel")

        self._disconnected = asyncio.Event()
        connection = await asyncpg.connect(self._dsn)
        try:
            connection.add_termination_listener(lambda _connection: self._disconnected.set())

            def notified(
                _connection: asyncpg.Connection[Any],
                _pid: int,
                _channel: str,
                _payload: str,
            ) -> None:
                nudge()

            for channel in channels:
                await connection.add_listener(channel, notified)
        except BaseException:
            await connection.close()
            raise
        self._connection = connection

    async def wait_disconnected(self) -> None:
        if self._connection is None:
            raise TaskqConfigError("notification source is not connected")
        await self._disconnected.wait()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection, self._connection = self._connection, None
        if connection is not None and not connection.is_closed():
            await connection.close()
        self._disconnected.set()


__all__ = ["PostgresNotificationSource"]
