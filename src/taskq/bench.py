"""Implemented B1–B4/B8/B11/B13/B14 benchmark scenarios (Harness §5).

This is report-only until a dedicated runner is calibrated and an envelope is
accepted. Toy runs prove the harness; they are never release baselines.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from taskq import Task, TaskRegistry, WorkerOptions, WorkerService, WorkerServiceOptions
from taskq.sql import migrate
from taskq.sql.notifications import PostgresNotificationSource
from taskq.sql.transport import SqlTaskqTransport

SCENARIOS = ("B1", "B2", "B3", "B4", "B8", "B11", "B13", "B14")


class _BenchInput(BaseModel):
    value: int


class _BenchOutput(BaseModel):
    value: int


@dataclass(frozen=True, slots=True)
class Scale:
    operations: int
    bulk_size: int
    backlog: int
    duration_seconds: float
    producers: int
    workers: int
    warmup: int


SCALES = {
    "toy": Scale(
        operations=25,
        bulk_size=1000,
        backlog=200,
        duration_seconds=0.5,
        producers=1,
        workers=2,
        warmup=3,
    ),
    "small": Scale(
        operations=1000,
        bulk_size=1000,
        backlog=10_000,
        duration_seconds=10.0,
        producers=2,
        workers=5,
        warmup=25,
    ),
    "full": Scale(
        operations=10_000,
        bulk_size=1000,
        backlog=1_000_000,
        duration_seconds=60.0,
        producers=4,
        workers=20,
        warmup=100,
    ),
}


def _plain_dsn(dsn: str) -> str:
    scheme, sep, rest = dsn.partition("://")
    return scheme.split("+", 1)[0] + sep + rest


def _sqlalchemy_dsn(dsn: str) -> str:
    _, sep, rest = _plain_dsn(dsn).partition("://")
    return "postgresql+asyncpg" + sep + rest


def _database_dsn(dsn: str, database: str) -> str:
    parts = urlsplit(_plain_dsn(dsn))
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _create_fresh_database(dsn: str, scenario: str) -> tuple[str, str]:
    database = f"taskq_bench_{scenario.lower()}_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()
    return _database_dsn(dsn, database), database


async def _drop_fresh_database(dsn: str, database: str) -> None:
    admin = await asyncpg.connect(_database_dsn(dsn, "postgres"))
    try:
        await admin.execute(f'DROP DATABASE "{database}"')
    finally:
        await admin.close()


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _latency_summary(seconds: Sequence[float]) -> dict[str, float]:
    milliseconds = [value * 1000 for value in seconds]
    return {
        "p50_ms": _percentile(milliseconds, 0.50),
        "p95_ms": _percentile(milliseconds, 0.95),
        "p99_ms": _percentile(milliseconds, 0.99),
        "max_ms": max(milliseconds, default=0.0),
    }


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


async def _migrate(dsn: str) -> None:
    engine = create_async_engine(_sqlalchemy_dsn(dsn))
    try:
        async with engine.connect() as conn:
            await migrate(conn)
    finally:
        await engine.dispose()


async def _connect_role(dsn: str, role: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(_plain_dsn(dsn))
    await conn.execute(f"SET ROLE {role}")
    return conn


async def _ensure_queue(operator: asyncpg.Connection, queue: str) -> None:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'benchmark')", queue
    )
    assert row is not None


async def _reset_fingerprint(admin: asyncpg.Connection) -> dict[str, Any]:
    jobs = await admin.fetchrow(
        "SELECT count(*) AS rows, pg_relation_filenode('taskq.jobs') AS relfilenode, "
        "pg_total_relation_size('taskq.jobs') AS table_bytes, "
        "pg_indexes_size('taskq.jobs') AS index_bytes FROM taskq.jobs"
    )
    stats = await admin.fetchrow(
        "SELECT COALESCE(n_live_tup,0) AS live, COALESCE(n_dead_tup,0) AS dead "
        "FROM pg_catalog.pg_stat_user_tables "
        "WHERE schemaname='taskq' AND relname='jobs'"
    )
    ledger = await admin.fetch("SELECT id, checksum FROM taskq.schema_migrations ORDER BY id")
    assert jobs["rows"] == 0
    assert stats is not None and stats["live"] == 0 and stats["dead"] == 0
    return {
        "database": await admin.fetchval("SELECT current_database()"),
        "jobs_rows": jobs["rows"],
        "jobs_relfilenode": jobs["relfilenode"],
        "jobs_table_bytes": jobs["table_bytes"],
        "jobs_index_bytes": jobs["index_bytes"],
        "jobs_live_tuples": stats["live"],
        "jobs_dead_tuples": stats["dead"],
        "migration_ledger": [row["id"] for row in ledger],
        "migration_checksums_sha256": hashlib.sha256(
            json.dumps([tuple(row) for row in ledger], sort_keys=True).encode()
        ).hexdigest(),
    }


async def _database_snapshot(admin: asyncpg.Connection) -> dict[str, Any]:
    settings = {
        row["name"]: row["setting"]
        for row in await admin.fetch(
            "SELECT name, setting FROM pg_catalog.pg_settings "
            "WHERE name = ANY($1::text[]) ORDER BY name",
            [
                "max_connections",
                "shared_buffers",
                "work_mem",
                "effective_cache_size",
                "random_page_cost",
                "synchronous_commit",
                "wal_level",
            ],
        )
    }
    relation = await admin.fetchrow(
        "SELECT pg_total_relation_size('taskq.jobs') AS table_bytes, "
        "pg_indexes_size('taskq.jobs') AS index_bytes"
    )
    tuples = await admin.fetchrow(
        "SELECT n_live_tup, n_dead_tup, vacuum_count, autovacuum_count "
        "FROM pg_catalog.pg_stat_user_tables "
        "WHERE schemaname = 'taskq' AND relname = 'jobs'"
    )
    return {
        "wal_lsn": await admin.fetchval("SELECT pg_current_wal_lsn()"),
        "table_bytes": relation["table_bytes"],
        "index_bytes": relation["index_bytes"],
        "n_live_tup": tuples["n_live_tup"],
        "n_dead_tup": tuples["n_dead_tup"],
        "vacuum_count": tuples["vacuum_count"],
        "autovacuum_count": tuples["autovacuum_count"],
        "lock_waiters": await admin.fetchval(
            "SELECT count(*) FROM pg_catalog.pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ),
        "connections": await admin.fetchval(
            "SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE datname = current_database()"
        ),
        "settings": settings,
    }


async def _event_loop_delay(samples: int = 20) -> dict[str, float]:
    loop = asyncio.get_running_loop()
    delays: list[float] = []
    for _ in range(samples):
        start = loop.time()
        await asyncio.sleep(0)
        delays.append(loop.time() - start)
    return _latency_summary(delays)


async def _representative_claim_plan(admin: asyncpg.Connection, queue: str) -> dict[str, Any]:
    raw = await admin.fetchval(
        f"""
        EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
        SELECT id FROM taskq.jobs
         WHERE queue = '{queue}' AND status = 'queued'
           AND cancel_requested_at IS NULL AND scheduled_at <= now()
         ORDER BY priority, scheduled_at, id LIMIT 1
        """
    )
    assert isinstance(raw, str)
    document = json.loads(raw)
    plan = document[0]["Plan"]
    pending = [plan]
    indexes: set[str] = set()
    sequential_jobs_scan = False
    while pending:
        node = pending.pop()
        if node.get("Index Name"):
            indexes.add(node["Index Name"])
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "jobs":
            sequential_jobs_scan = True
        pending.extend(node.get("Plans", ()))
    assert "jobs_claim_idx" in indexes
    assert not sequential_jobs_scan
    assert plan["Actual Rows"] <= 1
    return {
        "query": "claim_candidate",
        "expected_index_family": "jobs_claim_idx",
        "indexes": sorted(indexes),
        "bounded_actual_rows": plan["Actual Rows"],
        "plan": document,
    }


async def _enqueue_one(producer: asyncpg.Connection, queue: str, key: str) -> asyncpg.Record:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'bench.noop', '{}'::jsonb, p_idempotency_key => $2)",
        queue,
        key,
    )
    assert row is not None
    return row


async def _bulk(
    producer: asyncpg.Connection,
    queue: str,
    prefix: str,
    size: int,
) -> int:
    specs = [
        {"job_type": "bench.noop", "payload": {}, "idempotency_key": f"{prefix}-{index}"}
        for index in range(size)
    ]
    rows = await producer.fetch(
        "SELECT * FROM taskq.enqueue_many($1, $2::jsonb)", queue, json.dumps(specs)
    )
    assert len(rows) == size
    return len(rows)


async def _b1(
    producer: asyncpg.Connection,
    queue: str,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    for index in range(scale.warmup):
        await _enqueue_one(producer, queue, f"warmup-{seed}-{index}")
    results = []
    for repetition in range(repetitions):
        latencies: list[float] = []
        started = time.perf_counter()
        for index in range(scale.operations):
            before = time.perf_counter()
            await _enqueue_one(producer, queue, f"b1-{seed}-{repetition}-{index}")
            latencies.append(time.perf_counter() - before)
        duration = time.perf_counter() - started
        results.append(
            {
                "accepted": scale.operations,
                "duration_seconds": duration,
                "throughput_rows_per_second": scale.operations / duration,
                "latency": _latency_summary(latencies),
            }
        )
    return results


async def _b2(
    producer: asyncpg.Connection,
    queue: str,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    for index in range(scale.warmup):
        await _bulk(
            producer,
            queue,
            f"warmup-{seed}-{index}",
            min(25, scale.bulk_size),
        )
    results = []
    for repetition in range(repetitions):
        started = time.perf_counter()
        accepted = await _bulk(producer, queue, f"b2-{seed}-{repetition}", scale.bulk_size)
        duration = time.perf_counter() - started
        results.append(
            {
                "accepted": accepted,
                "duration_seconds": duration,
                "throughput_rows_per_second": accepted / duration,
                "latency": _latency_summary([duration]),
            }
        )
    return results


async def _seed_backlog(producer: asyncpg.Connection, queue: str, prefix: str, count: int) -> None:
    for offset in range(0, count, 1000):
        await _bulk(producer, queue, f"{prefix}-{offset}", min(1000, count - offset))


async def _b3(
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
    operator: asyncpg.Connection,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    warmup_queue = "bench_b3_warmup"
    await _ensure_queue(operator, warmup_queue)
    await _seed_backlog(producer, warmup_queue, f"warmup-{seed}", scale.warmup)
    for index in range(scale.warmup):
        batch = await runner.fetchrow(
            "SELECT * FROM taskq.claim_jobs($1, $2)", warmup_queue, f"bench-b3-warmup-{index}"
        )
        assert batch["state"] == "claimed"
        job = batch["jobs"][0]
        settled = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1, $2, $3)",
            job["job_id"],
            job["attempt_id"],
            f"bench-b3-warmup-{index}",
        )
        assert settled["result"] == "ok"
    results = []
    for repetition in range(repetitions):
        queue = f"bench_b3_{repetition}"
        await _ensure_queue(operator, queue)
        empty_started = time.perf_counter()
        empty = await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1, 'bench-b3-empty')", queue)
        empty_latency = time.perf_counter() - empty_started
        assert empty["state"] == "empty"
        await _seed_backlog(producer, queue, f"b3-{seed}-{repetition}", scale.backlog)

        claim_latencies: list[float] = []
        e2e_latencies: list[float] = []
        samples = min(scale.operations, scale.backlog)
        started = time.perf_counter()
        for index in range(samples):
            before_claim = time.perf_counter()
            batch = await runner.fetchrow(
                "SELECT * FROM taskq.claim_jobs($1, $2)", queue, f"bench-b3-{index}"
            )
            after_claim = time.perf_counter()
            assert batch["state"] == "claimed"
            job = batch["jobs"][0]
            settled = await runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                job["job_id"],
                job["attempt_id"],
                f"bench-b3-{index}",
            )
            assert settled["result"] == "ok"
            claim_latencies.append(after_claim - before_claim)
            e2e_latencies.append(time.perf_counter() - before_claim)
        duration = time.perf_counter() - started
        results.append(
            {
                "accepted": samples,
                "settled": samples,
                "backlog_rows": scale.backlog,
                "empty_claim_ms": empty_latency * 1000,
                "duration_seconds": duration,
                "throughput_rows_per_second": samples / duration,
                "claim_latency": _latency_summary(claim_latencies),
                "e2e_latency": _latency_summary(e2e_latencies),
            }
        )
    return results


async def _b4(
    dsn: str,
    admin: asyncpg.Connection,
    queue: str,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    results = []
    for repetition in range(repetitions):
        producers = [await _connect_role(dsn, "taskq_producer") for _ in range(scale.producers)]
        workers = [await _connect_role(dsn, "taskq_runner") for _ in range(scale.workers)]
        producers_stopped = asyncio.Event()
        production_done = asyncio.Event()
        accepted = 0
        settled = 0
        enqueue_latencies: list[float] = []
        e2e_latencies: list[float] = []

        for index in range(scale.warmup):
            await _enqueue_one(producers[0], queue, f"b4-warmup-{seed}-{repetition}-{index}")
            batch = await workers[0].fetchrow(
                "SELECT * FROM taskq.claim_jobs($1, 'bench-b4-warmup')", queue
            )
            assert batch["state"] == "claimed"
            job = batch["jobs"][0]
            row = await workers[0].fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, 'bench-b4-warmup')",
                job["job_id"],
                job["attempt_id"],
            )
            assert row["result"] == "ok"

        async def produce(conn: asyncpg.Connection, lane: int) -> None:
            nonlocal accepted
            index = 0
            while not producers_stopped.is_set():
                started = time.perf_counter()
                await _enqueue_one(conn, queue, f"b4-{seed}-{repetition}-{lane}-{index}")
                enqueue_latencies.append(time.perf_counter() - started)
                accepted += 1
                index += 1

        async def work(conn: asyncpg.Connection, lane: int) -> None:
            nonlocal settled
            rng = random.Random(seed + repetition * 1000 + lane)
            worker = f"bench-b4-{repetition}-{lane}"
            while True:
                started = time.perf_counter()
                batch = await conn.fetchrow("SELECT * FROM taskq.claim_jobs($1, $2)", queue, worker)
                if batch["state"] == "empty":
                    if production_done.is_set() and settled >= accepted:
                        return
                    await asyncio.sleep(0)
                    continue
                job = batch["jobs"][0]
                if rng.random() < 0.85:
                    row = await conn.fetchrow(
                        "SELECT * FROM taskq.complete_job($1, $2, $3)",
                        job["job_id"],
                        job["attempt_id"],
                        worker,
                    )
                else:
                    row = await conn.fetchrow(
                        "SELECT * FROM taskq.fail_job($1, $2, $3, 'bench failure', "
                        "p_retryable => false)",
                        job["job_id"],
                        job["attempt_id"],
                        worker,
                    )
                assert row["result"] in {"ok", "dead"}
                settled += 1
                e2e_latencies.append(time.perf_counter() - started)

        producer_tasks = [
            asyncio.create_task(produce(conn, lane)) for lane, conn in enumerate(producers)
        ]
        worker_tasks = [asyncio.create_task(work(conn, lane)) for lane, conn in enumerate(workers)]
        started = time.perf_counter()
        await asyncio.sleep(scale.duration_seconds)
        producers_stopped.set()
        await asyncio.gather(*producer_tasks)
        production_done.set()
        production_duration = time.perf_counter() - started
        drain_started = time.perf_counter()
        drain_timeout = max(30.0, scale.duration_seconds * 2)
        await asyncio.wait_for(asyncio.gather(*worker_tasks), timeout=drain_timeout)
        drain_duration = time.perf_counter() - drain_started
        duration = time.perf_counter() - started

        prefix = f"b4-{seed}-{repetition}-%"
        conservation = await admin.fetchrow(
            "SELECT count(*) FILTER (WHERE status IN ('succeeded','failed','cancelled')) AS terminal, "
            "count(*) FILTER (WHERE status IN ('blocked','queued','running')) AS active, "
            "count(*) FILTER (WHERE status='running') AS running "
            "FROM taskq.jobs WHERE queue=$1 AND idempotency_key LIKE $2",
            queue,
            prefix,
        )
        running_attempts = await admin.fetchval(
            "SELECT count(*) FROM taskq.job_attempts a JOIN taskq.jobs j ON j.id=a.job_id "
            "WHERE j.queue=$1 AND j.idempotency_key LIKE $2 AND a.status='running'",
            queue,
            prefix,
        )
        terminal = conservation["terminal"]
        active = conservation["active"]
        conservation_equal = accepted == terminal + active
        assert settled == terminal
        assert conservation_equal
        assert active == 0
        assert conservation["running"] == 0
        assert running_attempts == 0
        for conn in (*producers, *workers):
            await conn.close()
        results.append(
            {
                "accepted": accepted,
                "settled": settled,
                "terminal": terminal,
                "remaining_active": active,
                "running_jobs": conservation["running"],
                "running_attempts": running_attempts,
                "conservation_equal": conservation_equal,
                "drained": active == 0 and running_attempts == 0,
                "production_duration_seconds": production_duration,
                "drain_duration_seconds": drain_duration,
                "drain_timeout_seconds": drain_timeout,
                "duration_seconds": duration,
                "throughput_rows_per_second": settled / duration,
                "enqueue_latency": _latency_summary(enqueue_latencies),
                "e2e_latency": _latency_summary(e2e_latencies),
            }
        )
    return results


async def _wait_for(predicate: Callable[[], bool], *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("benchmark worker condition timed out")
        await asyncio.sleep(0)


async def _enqueue_worker_job(producer: asyncpg.Connection, queue: str, key: str) -> None:
    row = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1, 'bench.noop', $2::jsonb, p_idempotency_key => $3)",
        queue,
        json.dumps({"value": 1}),
        key,
    )
    assert row is not None


async def _runner_transport(dsn: str) -> tuple[SqlTaskqTransport, Any]:
    engine = create_async_engine(
        _sqlalchemy_dsn(dsn),
        connect_args={"server_settings": {"role": "taskq_runner"}},
    )
    return SqlTaskqTransport(engine), engine


async def _b8(
    dsn: str,
    producer: asyncpg.Connection,
    queue: str,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    del scale
    runs: list[dict[str, Any]] = []
    for listen in (True, False):
        transport, engine = await _runner_transport(dsn)
        notifications = PostgresNotificationSource(dsn) if listen else None
        started = [asyncio.Event()]

        async def handler(payload: _BenchInput) -> _BenchOutput:
            started[0].set()
            return _BenchOutput(value=payload.value)

        service = WorkerService(
            transport,
            TaskRegistry(
                [
                    Task(
                        name="bench.noop",
                        queue=queue,
                        input_model=_BenchInput,
                        output_model=_BenchOutput,
                        handler=handler,
                    )
                ]
            ),
            f"bench-b8-{'notify' if listen else 'poll'}",
            options=WorkerServiceOptions(queues=(queue,), listen=listen, poll_interval=0.1),
            notifications=notifications,
        )
        try:
            await service.start()
            await _wait_for(lambda: service.snapshot().claim_sweeps >= 1)
            for repetition in range(repetitions):
                started[0] = asyncio.Event()
                before_sweeps = service.snapshot().claim_sweeps
                began = time.perf_counter()
                await _enqueue_worker_job(
                    producer,
                    queue,
                    f"b8-{seed}-{int(listen)}-{repetition}",
                )
                await asyncio.wait_for(started[0].wait(), timeout=5)
                duration = time.perf_counter() - began
                await _wait_for(lambda: service.snapshot().active_slots == 0)
                await _wait_for(lambda: service.snapshot().claim_sweeps > before_sweeps)
                runs.append(
                    {
                        "mode": "notify" if listen else "poll_only",
                        "accepted": 1,
                        "settled": 1,
                        "duration_seconds": duration,
                        "throughput_rows_per_second": 1 / duration,
                        "wake_latency": _latency_summary([duration]),
                    }
                )
        finally:
            await service.aclose()
            await transport.aclose()
            await engine.dispose()
    return runs


async def _b13(
    dsn: str,
    admin: asyncpg.Connection,
    producer: asyncpg.Connection,
    operator: asyncpg.Connection,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    job_count = max(1, min(10, scale.warmup))
    for repetition in range(repetitions):
        queue = f"bench_b13_{repetition}"
        await _ensure_queue(operator, queue)
        for index in range(job_count):
            await _enqueue_worker_job(producer, queue, f"b13-{seed}-{repetition}-{index}")
        release = asyncio.Event()

        async def handler(payload: _BenchInput) -> _BenchOutput:
            await release.wait()
            return _BenchOutput(value=payload.value)

        transport, engine = await _runner_transport(dsn)
        service = WorkerService(
            transport,
            TaskRegistry(
                [
                    Task(
                        name="bench.noop",
                        queue=queue,
                        input_model=_BenchInput,
                        output_model=_BenchOutput,
                        handler=handler,
                    )
                ]
            ),
            f"bench-b13-{repetition}",
            options=WorkerServiceOptions(
                queues=(queue,), batch=job_count, listen=False, poll_interval=0.1
            ),
            supervisor_options=WorkerOptions(concurrency=job_count),
        )
        try:
            await service.start()
            await _wait_for(lambda: service.snapshot().active_slots == job_count)
            began = time.perf_counter()
            release.set()
            await service.stop()
            duration = time.perf_counter() - began
            terminal = await admin.fetchval(
                "SELECT count(*) FROM taskq.jobs WHERE queue=$1 AND status='succeeded'",
                queue,
            )
            released = await admin.fetchval(
                "SELECT count(*) FROM taskq.job_events e JOIN taskq.jobs j ON j.id=e.job_id "
                "WHERE j.queue=$1 AND e.event_type='released'",
                queue,
            )
            expired = await admin.fetchval(
                "SELECT count(*) FROM taskq.job_events e JOIN taskq.jobs j ON j.id=e.job_id "
                "WHERE j.queue=$1 AND e.event_type='expired'",
                queue,
            )
            assert terminal == job_count and released == 0 and expired == 0
            runs.append(
                {
                    "accepted": job_count,
                    "settled": terminal,
                    "released_claims": released,
                    "expired_claims": expired,
                    "conservation_equal": terminal + released + expired == job_count,
                    "duration_seconds": duration,
                    "throughput_rows_per_second": terminal / duration,
                    "drain_latency": _latency_summary([duration]),
                }
            )
        finally:
            await service.aclose()
            await transport.aclose()
            await engine.dispose()
    return runs


async def _asgi_client(app: Any) -> tuple[Any, Any]:
    """Create the shipped generated client over a live in-process ASGI boundary."""

    try:
        import httpx

        from taskq.http import AsyncTaskqHttpClient
    except ImportError as exc:  # pragma: no cover - package-smoke guard
        raise RuntimeError("B11/B14 require the taskq HTTP extra") from exc
    raw = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench")
    client = AsyncTaskqHttpClient(
        "http://bench",
        bearer_token="benchmark-local-only",
        client=raw,
        claim_wait_seconds=0,
        max_retries=0,
    )
    return client, raw


def _mounted_bench_app(resources: Any) -> Any:
    try:
        from fastapi import FastAPI

        from taskq.http import create_taskq_app, no_auth_for_tests
    except ImportError as exc:  # pragma: no cover - package-smoke guard
        raise RuntimeError("B11/B14 require the taskq HTTP extra") from exc
    host = FastAPI()
    host.mount("/taskq", create_taskq_app(resources, authorizer=no_auth_for_tests()))
    return host


async def _b11(
    dsn: str,
    queue: str,
    scale: Scale,
    repetitions: int,
) -> list[dict[str, Any]]:
    from taskq.http import EmbeddedWorkerOptions, TaskqRuntime, TaskqRuntimeOptions

    async def handler(payload: _BenchInput) -> _BenchOutput:
        return _BenchOutput(value=payload.value)

    registry = TaskRegistry(
        [
            Task(
                name="bench.noop",
                queue=queue,
                input_model=_BenchInput,
                output_model=_BenchOutput,
                handler=handler,
            )
        ]
    )
    runs: list[dict[str, Any]] = []
    for embedded in (False, True):
        for _ in range(repetitions):
            options = TaskqRuntimeOptions(
                housekeeper_enabled=False,
                long_poll_listener_enabled=False,
                request_pool_max=2,
                embedded_worker=(
                    EmbeddedWorkerOptions(
                        queues=(queue,),
                        acknowledge_process_multiplication=True,
                        concurrency=1,
                        batch=1,
                        listen=False,
                        poll_interval=0.1,
                    )
                    if embedded
                    else None
                ),
                embedded_worker_pool_max=2,
            )
            runtime = TaskqRuntime.from_dsn(dsn, registry=registry, options=options)
            client, raw = await _asgi_client(_mounted_bench_app(runtime.facade_transports))
            try:
                await runtime.start()
                for _ in range(scale.warmup):
                    await client.get_contract_meta()
                latencies: list[float] = []
                started = time.perf_counter()
                for _ in range(scale.operations):
                    before = time.perf_counter()
                    await client.get_contract_meta()
                    latencies.append(time.perf_counter() - before)
                duration = time.perf_counter() - started
                runs.append(
                    {
                        "mode": "embedded" if embedded else "facade_only",
                        "accepted": scale.operations,
                        "duration_seconds": duration,
                        "throughput_rows_per_second": scale.operations / duration,
                        "request_latency": _latency_summary(latencies),
                        "pool_capacity": options.process_pool_capacity,
                        "listener_capacity": options.process_listener_capacity,
                    }
                )
            finally:
                await client.aclose()
                await raw.aclose()
                await runtime.stop()
    return runs


async def _b14(
    dsn: str,
    producer: asyncpg.Connection,
    queue: str,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    from taskq.http import ClaimWaitHub, TaskqFacadeTransports
    from taskq.protocol import EnqueueCommand

    transport = SqlTaskqTransport.from_dsn(_sqlalchemy_dsn(dsn))
    resources = TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
    )
    client, raw = await _asgi_client(_mounted_bench_app(resources))
    runs: list[dict[str, Any]] = []
    try:
        await client.start()
        for repetition in range(repetitions):
            sql_latencies: list[float] = []
            client_latencies: list[float] = []
            for index in range(scale.warmup):
                await client.enqueue(
                    EnqueueCommand(
                        queue=queue,
                        job_type="bench.noop",
                        payload={},
                        idempotency_key=f"b14-warmup-{seed}-{repetition}-{index}",
                    )
                )
            started = time.perf_counter()
            for index in range(scale.operations):
                before = time.perf_counter()
                await _enqueue_one(producer, queue, f"b14-sql-{seed}-{repetition}-{index}")
                sql_latencies.append(time.perf_counter() - before)
                before = time.perf_counter()
                await client.enqueue(
                    EnqueueCommand(
                        queue=queue,
                        job_type="bench.noop",
                        payload={},
                        idempotency_key=f"b14-http-{seed}-{repetition}-{index}",
                    )
                )
                client_latencies.append(time.perf_counter() - before)
            duration = time.perf_counter() - started
            sql = _latency_summary(sql_latencies)
            client_summary = _latency_summary(client_latencies)
            runs.append(
                {
                    "accepted": scale.operations * 2,
                    "duration_seconds": duration,
                    "throughput_rows_per_second": scale.operations * 2 / duration,
                    "sql_latency": sql,
                    "client_latency": client_summary,
                    "facade_overhead_p50_ms": client_summary["p50_ms"] - sql["p50_ms"],
                    "facade_overhead_p99_ms": client_summary["p99_ms"] - sql["p99_ms"],
                }
            )
    finally:
        await client.aclose()
        await raw.aclose()
        await transport.aclose()
    return runs


async def _run_scenario_in_database(
    scenario: str,
    *,
    dsn: str,
    scale_name: str = "toy",
    repetitions: int = 3,
    seed: int = 1,
    output: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    scenario = scenario.upper()
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {', '.join(SCENARIOS)}")
    if scale_name not in SCALES:
        raise ValueError(f"scale must be one of {', '.join(SCALES)}")
    if repetitions < 3:
        raise ValueError("benchmark evidence requires at least 3 repetitions")
    scale = SCALES[scale_name]
    await _migrate(dsn)
    admin = await asyncpg.connect(_plain_dsn(dsn))
    operator = await _connect_role(dsn, "taskq_operator")
    producer = await _connect_role(dsn, "taskq_producer")
    runner = await _connect_role(dsn, "taskq_runner")
    queue = f"bench_{scenario.lower()}"
    try:
        reset_fingerprint = await _reset_fingerprint(admin)
        await _ensure_queue(operator, queue)
        before = await _database_snapshot(admin)
        loop_delay = await _event_loop_delay()
        if scenario == "B1":
            runs = await _b1(producer, queue, scale, repetitions, seed)
        elif scenario == "B2":
            runs = await _b2(producer, queue, scale, repetitions, seed)
        elif scenario == "B3":
            runs = await _b3(producer, runner, operator, scale, repetitions, seed)
        elif scenario == "B4":
            runs = await _b4(dsn, admin, queue, scale, repetitions, seed)
        elif scenario == "B8":
            runs = await _b8(dsn, producer, queue, scale, repetitions, seed)
        elif scenario == "B11":
            runs = await _b11(dsn, queue, scale, repetitions)
        elif scenario == "B13":
            runs = await _b13(dsn, admin, producer, operator, scale, repetitions, seed)
        elif scenario == "B14":
            runs = await _b14(dsn, producer, queue, scale, repetitions, seed)
        else:  # pragma: no cover - guarded by SCENARIOS
            raise AssertionError("unreachable benchmark scenario")
        explain_queue = {
            "B3": "bench_b3_0",
            "B13": "bench_b13_0",
        }.get(scenario, queue)
        explain = await _representative_claim_plan(admin, explain_queue)
        after = await _database_snapshot(admin)
        wal_bytes = await admin.fetchval(
            "SELECT pg_wal_lsn_diff($1::pg_lsn, $2::pg_lsn)::bigint",
            after["wal_lsn"],
            before["wal_lsn"],
        )
        throughputs = [run["throughput_rows_per_second"] for run in runs]
        p99_values = [
            max(
                value["p99_ms"]
                for key, value in run.items()
                if key.endswith("latency") and isinstance(value, dict)
            )
            for run in runs
        ]
        accepted = sum(run.get("accepted", 0) for run in runs)
        settings_json = json.dumps(after["settings"], sort_keys=True).encode()
        result = {
            "schema_version": 1,
            "scenario": scenario,
            "scale": scale_name,
            "evidence_mode": "smoke" if scale_name == "toy" else "report_only",
            "recorded_at": datetime.now(UTC).isoformat(),
            "git_sha": _git_sha(),
            "postgres": {
                "version": await admin.fetchval("SHOW server_version"),
                "version_num": int(await admin.fetchval("SHOW server_version_num")),
                "settings": after["settings"],
                "settings_fingerprint_sha256": hashlib.sha256(settings_json).hexdigest(),
            },
            "machine": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "processor": platform.processor(),
                "python": sys.version.split()[0],
                "cpu_count": os.cpu_count(),
            },
            "workload": {"seed": seed, **asdict(scale)},
            "method": {
                "repetitions": repetitions,
                "warmup_operations": scale.warmup,
                "database_reset": "fresh database created for scenario and dropped afterward",
                "reset_fingerprint": reset_fingerprint,
                "baseline": None,
            },
            "runs": runs,
            "summary": {
                "median_throughput_rows_per_second": statistics.median(throughputs),
                "worst_p99_ms": max(p99_values, default=0.0),
            },
            "database": {
                "wal_bytes": wal_bytes,
                "wal_bytes_per_accepted": wal_bytes / accepted if accepted else None,
                "before": before,
                "after": after,
            },
            "representative_explain": explain,
            "client_event_loop_delay": loop_delay,
        }
        if scenario == "B8":
            result["summary"]["notify_p50_ms"] = statistics.median(
                run["wake_latency"]["p50_ms"] for run in runs if run["mode"] == "notify"
            )
            result["summary"]["poll_only_p50_ms"] = statistics.median(
                run["wake_latency"]["p50_ms"] for run in runs if run["mode"] == "poll_only"
            )
        elif scenario == "B11":
            facade_p99 = statistics.median(
                run["request_latency"]["p99_ms"] for run in runs if run["mode"] == "facade_only"
            )
            embedded_p99 = statistics.median(
                run["request_latency"]["p99_ms"] for run in runs if run["mode"] == "embedded"
            )
            result["summary"]["facade_only_median_p99_ms"] = facade_p99
            result["summary"]["embedded_median_p99_ms"] = embedded_p99
            result["summary"]["embedded_overhead_p99_ms"] = embedded_p99 - facade_p99
        elif scenario == "B13":
            result["summary"]["released_claims"] = sum(run["released_claims"] for run in runs)
            result["summary"]["expired_claims"] = sum(run["expired_claims"] for run in runs)
        elif scenario == "B14":
            result["summary"]["client_median_p99_ms"] = statistics.median(
                run["client_latency"]["p99_ms"] for run in runs
            )
            result["summary"]["sql_median_p99_ms"] = statistics.median(
                run["sql_latency"]["p99_ms"] for run in runs
            )
            result["summary"]["facade_median_overhead_p99_ms"] = statistics.median(
                run["facade_overhead_p99_ms"] for run in runs
            )
    finally:
        await runner.close()
        await producer.close()
        await operator.close()
        await admin.close()

    if output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = Path("taskq-bench-results") / f"{stamp}-{scenario.lower()}-{scale_name}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result, output


async def run_scenario(
    scenario: str,
    *,
    dsn: str,
    scale_name: str = "toy",
    repetitions: int = 3,
    seed: int = 1,
    output: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    scenario = scenario.upper()
    fresh_dsn, database = await _create_fresh_database(dsn, scenario)
    try:
        return await _run_scenario_in_database(
            scenario,
            dsn=fresh_dsn,
            scale_name=scale_name,
            repetitions=repetitions,
            seed=seed,
            output=output,
        )
    finally:
        await _drop_fresh_database(dsn, database)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskq-bench")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one report-only benchmark scenario")
    run.add_argument("scenario", choices=SCENARIOS)
    run.add_argument("--dsn", required=True)
    run.add_argument("--scale", choices=tuple(SCALES), default="toy")
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result, artifact = asyncio.run(
        run_scenario(
            args.scenario,
            dsn=args.dsn,
            scale_name=args.scale,
            repetitions=args.repetitions,
            seed=args.seed,
            output=args.output,
        )
    )
    print(json.dumps(result["summary"], sort_keys=True))
    print(f"artifact={artifact}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
