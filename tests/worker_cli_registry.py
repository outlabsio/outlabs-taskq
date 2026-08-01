"""Subprocess-only registry for Stage-2C CLI lifecycle tests."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Event

from pydantic import BaseModel

from taskq import Task, TaskRegistry


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


def _mark_started() -> None:
    path = os.environ.get("TASKQ_CLI_STARTED_FILE")
    if path:
        Path(path).write_text("started\n", encoding="utf-8")


async def complete(payload: Input) -> Output:
    _mark_started()
    return Output(value=payload.value)


async def wait_forever(payload: Input) -> Output:
    _mark_started()
    await asyncio.Event().wait()
    return Output(value=payload.value)


def sync_wait_forever(payload: Input) -> Output:
    _mark_started()
    Event().wait()
    return Output(value=payload.value)


_MODE = os.environ.get("TASKQ_CLI_TEST_MODE", "complete")
_HANDLERS = {
    "complete": complete,
    "async_wait": wait_forever,
    "sync_wait": sync_wait_forever,
}
_QUEUE = os.environ.get("TASKQ_CLI_TEST_QUEUE", "cli_test")
registry = TaskRegistry(
    [
        Task(
            name="cli.test",
            queue=_QUEUE,
            input_model=Input,
            output_model=Output,
            handler=_HANDLERS[_MODE],
        )
    ]
)
