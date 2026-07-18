"""Real-process worker CLI signal, fatal, and unsafe-sync exit evidence."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from collections.abc import Callable

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


async def _spawn_worker(
    taskq_dsn: str,
    queue: str,
    *,
    mode: str,
    started_file: Path | None = None,
    soft_stop_timeout: float | None = None,
) -> asyncio.subprocess.Process:
    environment = os.environ.copy()
    environment.update(
        {
            "TASKQ_CLI_TEST_QUEUE": queue,
            "TASKQ_CLI_TEST_MODE": mode,
        }
    )
    if started_file is not None:
        environment["TASKQ_CLI_STARTED_FILE"] = str(started_file)
    command = [
        sys.executable,
        "-m",
        "taskq.cli",
        "worker",
        "--dsn",
        taskq_dsn,
        "--registry",
        "tests.worker_cli_registry:registry",
        "--queue",
        queue,
        "--environment",
        "test",
        "--no-listen",
        "--poll-interval",
        "0.1",
    ]
    if soft_stop_timeout is not None:
        command.extend(("--soft-stop-timeout", str(soft_stop_timeout)))
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=Path(__file__).parents[1],
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _wait_for(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("subprocess condition timed out")
        await asyncio.sleep(0.01)


async def _wait_for_presence(pg: asyncpg.Connection, queue: str) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while not await pg.fetchval(
        "SELECT EXISTS (SELECT 1 FROM taskq.workers WHERE $1 = ANY(queues))", queue
    ):
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("worker presence timed out")
        await asyncio.sleep(0.01)


async def _finish(process: asyncio.subprocess.Process, expected: int) -> tuple[bytes, bytes]:
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    stdout, stderr = await process.communicate()
    assert process.returncode == expected, (stdout.decode(), stderr.decode())
    return stdout, stderr


async def _enqueue(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    queue: str,
) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'cli-audit')", queue)
    await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'cli.test', '{\"value\":1}'::jsonb)", queue
    )


async def test_cli_first_signal_cleanly_soft_stops(
    pg: asyncpg.Connection,
    taskq_dsn: str,
    operator: asyncpg.Connection,
) -> None:
    queue = "cli_signal_clean"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'cli-audit')", queue)
    process = await _spawn_worker(taskq_dsn, queue, mode="complete")
    try:
        await _wait_for_presence(pg, queue)
        process.send_signal(signal.SIGTERM)
        await _finish(process, 0)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_cli_second_signal_escalates_async_drain(
    taskq_dsn: str,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    tmp_path: Path,
) -> None:
    queue = "cli_signal_hard"
    started = tmp_path / "async-started"
    await _enqueue(operator, producer, queue)
    process = await _spawn_worker(taskq_dsn, queue, mode="async_wait", started_file=started)
    try:
        await _wait_for(started.exists)
        process.send_signal(signal.SIGTERM)
        for _ in range(10):
            await asyncio.sleep(0)
        assert process.returncode is None
        process.send_signal(signal.SIGTERM)
        await _finish(process, 0)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_cli_unknown_queue_is_fatal_without_traceback_secret(
    taskq_dsn: str,
) -> None:
    process = await _spawn_worker(taskq_dsn, "cli_missing_queue", mode="complete")
    _, stderr = await _finish(process, 1)
    assert b"postgresql://" not in stderr


async def test_cli_live_sync_handler_uses_required_process_exit(
    pg: asyncpg.Connection,
    taskq_dsn: str,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    tmp_path: Path,
) -> None:
    queue = "cli_sync_exit"
    started = tmp_path / "sync-started"
    await _enqueue(operator, producer, queue)
    process = await _spawn_worker(
        taskq_dsn,
        queue,
        mode="sync_wait",
        started_file=started,
        soft_stop_timeout=0,
    )
    try:
        await _wait_for(started.exists)
        process.send_signal(signal.SIGTERM)
        await _finish(process, 3)
        row = await pg.fetchrow(
            "SELECT status, failure_count FROM taskq.jobs WHERE queue=$1", queue
        )
        assert row is not None and row["status"] == "running" and row["failure_count"] == 0
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM taskq.job_events e JOIN taskq.jobs j ON j.id=e.job_id "
                "WHERE j.queue=$1 AND e.event_type='released'",
                queue,
            )
            == 0
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
