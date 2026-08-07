"""Shared chassis for L-scenarios: DB lifecycle, fleet building, invariants, artifacts.

Deliberately reuses the benchmark chassis (`taskq.bench`) for database
lifecycle, fingerprints, and latency summaries — the harness spec fixes that
reuse boundary. Everything here targets throwaway databases created from the
provided DSN's server; no consumer database is ever touched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import socket
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from taskq import Task, TaskRegistry, WorkerOptions, WorkerService, WorkerServiceOptions
from taskq.bench import (
    _connect_role,
    _database_dsn,
    _database_snapshot,
    _ensure_queue,
    _git_sha,
    _latency_summary,
    _migrate,
    _plain_dsn,
    _reset_fingerprint,
    _sqlalchemy_dsn,
)
from taskq.loadlab._instruments import ClaimFaultPlan, FleetInstruments, InstrumentedTransport
from taskq.sql.notifications import PostgresNotificationSource
from taskq.sql.transport import SqlTaskqTransport

__all__ = [
    "LOAD_SCALES",
    "LoadInput",
    "LoadOutput",
    "LoadScale",
    "ScenarioContext",
    "WorkerHandle",
    "run_scenario",
]


class LoadInput(BaseModel):
    value: int


class LoadOutput(BaseModel):
    value: int


@dataclass(frozen=True, slots=True)
class LoadScale:
    herd_workers: int
    trickle_jobs: int
    trickle_gap_seconds: float
    storm_workers: int
    cohort_jobs: int
    producers: int
    contention_jobs: int
    max_depth: int
    handler_delay_seconds: float
    outage_seconds: float
    nudge_jobs: int
    settle_timeout_seconds: float


LOAD_SCALES = {
    "toy": LoadScale(
        herd_workers=4,
        trickle_jobs=12,
        trickle_gap_seconds=0.12,
        storm_workers=3,
        cohort_jobs=24,
        producers=6,
        contention_jobs=150,
        max_depth=40,
        handler_delay_seconds=0.03,
        outage_seconds=1.5,
        nudge_jobs=10,
        settle_timeout_seconds=60.0,
    ),
    "small": LoadScale(
        herd_workers=12,
        trickle_jobs=60,
        trickle_gap_seconds=0.08,
        storm_workers=8,
        cohort_jobs=400,
        producers=10,
        contention_jobs=2_000,
        max_depth=400,
        handler_delay_seconds=0.02,
        outage_seconds=3.0,
        nudge_jobs=40,
        settle_timeout_seconds=180.0,
    ),
    "full": LoadScale(
        herd_workers=30,
        trickle_jobs=200,
        trickle_gap_seconds=0.05,
        storm_workers=20,
        cohort_jobs=5_000,
        producers=20,
        contention_jobs=20_000,
        max_depth=4_000,
        handler_delay_seconds=0.01,
        outage_seconds=10.0,
        nudge_jobs=100,
        settle_timeout_seconds=600.0,
    ),
}


@dataclass(slots=True)
class WorkerHandle:
    service: WorkerService
    transport: InstrumentedTransport
    engine: Any
    notifications: PostgresNotificationSource | None

    async def aclose(self) -> None:
        await self.service.aclose()
        await self.transport.aclose()
        await self.engine.dispose()


@dataclass(slots=True)
class ScenarioContext:
    dsn: str
    admin: asyncpg.Connection
    operator: asyncpg.Connection
    scale: LoadScale
    scale_name: str
    seed: int
    repetition: int
    instruments: FleetInstruments

    async def producer(self) -> asyncpg.Connection:
        return await _connect_role(self.dsn, "taskq_producer")

    async def ensure_queue(self, queue: str, profile: dict[str, Any] | None = None) -> None:
        if profile:
            row = await self.operator.fetchrow(
                "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 'loadlab')",
                queue,
                json.dumps(profile),
            )
            assert row is not None
        else:
            await _ensure_queue(self.operator, queue)

    def build_worker(
        self,
        queue: str,
        worker_id: str,
        *,
        handler: Callable[[LoadInput], Awaitable[Any]],
        task_name: str,
        concurrency: int = 1,
        batch: int = 1,
        poll_interval: float = 5.0,
        listen: bool = True,
        retry: Any = True,
        fault_plan: ClaimFaultPlan | None = None,
        service_overrides: dict[str, Any] | None = None,
    ) -> WorkerHandle:
        engine = create_async_engine(
            _sqlalchemy_dsn(self.dsn),
            connect_args={"server_settings": {"role": "taskq_runner"}},
        )
        transport = InstrumentedTransport(
            SqlTaskqTransport(
                engine,
                expected_environment=os.environ.get("TASKQ_EXPECTED_ENV", "benchmark"),
            ),
            worker_id=worker_id,
            instruments=self.instruments,
            fault_plan=fault_plan,
        )
        registry = TaskRegistry(
            [
                Task(
                    name=task_name,
                    queue=queue,
                    input_model=LoadInput,
                    output_model=LoadOutput,
                    handler=handler,
                    retry=retry,
                )
            ]
        )
        notifications = PostgresNotificationSource(self.dsn) if listen else None
        service_options: dict[str, Any] = {
            "queues": (queue,),
            "batch": batch,
            "poll_interval": poll_interval,
            "listen": listen,
            "presence_interval": 60.0,
        }
        if service_overrides:
            service_options.update(service_overrides)
        service = WorkerService(
            transport,
            registry,
            worker_id,
            options=WorkerServiceOptions(**service_options),
            supervisor_options=WorkerOptions(concurrency=concurrency),
            notifications=notifications,
        )
        return WorkerHandle(
            service=service, transport=transport, engine=engine, notifications=notifications
        )

    async def enqueue_one(
        self, producer: asyncpg.Connection, queue: str, task_name: str, key: str, value: int = 1
    ) -> asyncpg.Record:
        row = await producer.fetchrow(
            "SELECT * FROM taskq.enqueue($1, $2, $3::jsonb, p_idempotency_key => $4)",
            queue,
            task_name,
            json.dumps({"value": value}),
            key,
        )
        assert row is not None
        return row

    async def enqueue_many(
        self, producer: asyncpg.Connection, queue: str, task_name: str, prefix: str, count: int
    ) -> int:
        # taskq.enqueue_many caps a single call at 1000 specs; chunk so the full
        # scale tier (cohort/contention in the thousands) seeds without tripping it.
        total = 0
        for start in range(0, count, 1000):
            specs = [
                {"job_type": task_name, "payload": {"value": 1}, "idempotency_key": f"{prefix}-{i}"}
                for i in range(start, min(start + 1000, count))
            ]
            rows = await producer.fetch(
                "SELECT * FROM taskq.enqueue_many($1, $2::jsonb)", queue, json.dumps(specs)
            )
            total += len(rows)
        return total

    async def count_status(self, queue: str, statuses: tuple[str, ...]) -> int:
        value = await self.admin.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE queue=$1 AND status = ANY($2::text[])",
            queue,
            list(statuses),
        )
        assert value is not None
        return int(value)

    async def wait_settled(
        self, queue: str, expected_succeeded: int, *, timeout: float | None = None
    ) -> None:
        await wait_until(
            lambda: self._succeeded_at_least(queue, expected_succeeded),
            timeout=timeout or self.scale.settle_timeout_seconds,
            message=f"queue {queue!r} did not reach {expected_succeeded} succeeded jobs",
        )

    async def _succeeded_at_least(self, queue: str, expected: int) -> bool:
        return await self.count_status(queue, ("succeeded",)) >= expected

    async def attempts_per_job(self, queue: str) -> dict[int, int]:
        rows = await self.admin.fetch(
            "SELECT attempts, count(*) AS jobs FROM ("
            "  SELECT j.id, count(a.id) AS attempts FROM taskq.jobs j"
            "  LEFT JOIN taskq.job_attempts a ON a.job_id = j.id"
            "  WHERE j.queue = $1 GROUP BY j.id) grouped"
            " GROUP BY attempts ORDER BY attempts",
            queue,
        )
        return {int(row["attempts"]): int(row["jobs"]) for row in rows}


async def wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout: float,
    interval: float = 0.05,
    message: str = "condition not reached",
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if await predicate():
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        await asyncio.sleep(interval)


class QueueSampler:
    """Background per-interval sampler of live queue counts (own connection)."""

    def __init__(self, dsn: str, queue: str, interval: float = 0.25) -> None:
        self._dsn = dsn
        self._queue = queue
        self._interval = interval
        self.samples: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def __aenter__(self) -> QueueSampler:
        self._task = asyncio.create_task(self._run(), name="loadlab-sampler")
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        conn = await asyncpg.connect(_plain_dsn(self._dsn))
        try:
            while not self._stop.is_set():
                row = await conn.fetchrow(
                    "SELECT count(*) FILTER (WHERE status IN ('blocked','queued')) AS depth, "
                    "count(*) FILTER (WHERE status='running') AS running "
                    "FROM taskq.jobs WHERE queue=$1",
                    self._queue,
                )
                assert row is not None
                self.samples.append(
                    {
                        "monotonic": time.monotonic(),
                        "depth": int(row["depth"]),
                        "running": int(row["running"]),
                    }
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                except TimeoutError:
                    continue
        finally:
            await conn.close()

    def max_depth(self) -> int:
        return max((sample["depth"] for sample in self.samples), default=0)


def summarize_metric_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, list[float]] = {}
    for run in runs:
        for name, value in run.get("metrics", {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics.setdefault(name, []).append(float(value))
    return {f"median_{name}": statistics.median(values) for name, values in metrics.items()}


async def _create_fresh_database(dsn: str, scenario: str) -> tuple[str, str]:
    database = f"taskq_load_{scenario.lower()}_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(dsn, "postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()
    return _database_dsn(dsn, database), database


async def _drop_fresh_database(dsn: str, database: str) -> None:
    admin = await asyncpg.connect(_database_dsn(dsn, "postgres"))
    try:
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
    finally:
        await admin.close()


async def run_scenario(
    scenario: str,
    *,
    dsn: str,
    scale_name: str = "toy",
    repetitions: int = 1,
    seed: int = 1,
    output: Path | None = None,
) -> tuple[dict[str, Any], Path | None]:
    from taskq.loadlab._scenarios import SCENARIO_RUNNERS

    scenario = scenario.upper()
    runner = SCENARIO_RUNNERS[scenario]
    scale = LOAD_SCALES[scale_name]
    fresh_dsn, database = await _create_fresh_database(dsn, scenario)
    try:
        await _migrate(fresh_dsn)
        admin = await asyncpg.connect(_plain_dsn(fresh_dsn))
        operator = await _connect_role(fresh_dsn, "taskq_operator")
        try:
            fingerprint = await _reset_fingerprint(admin)
            before = await _database_snapshot(admin)
            runs: list[dict[str, Any]] = []
            for repetition in range(repetitions):
                context = ScenarioContext(
                    dsn=fresh_dsn,
                    admin=admin,
                    operator=operator,
                    scale=scale,
                    scale_name=scale_name,
                    seed=seed + repetition * 1000,
                    repetition=repetition,
                    instruments=FleetInstruments(),
                )
                runs.append(await runner(context))
            after = await _database_snapshot(admin)
            wal_bytes = await admin.fetchval(
                "SELECT pg_wal_lsn_diff($1::pg_lsn, $2::pg_lsn)",
                after["wal_lsn"],
                before["wal_lsn"],
            )
            invariant_checks = [check for run in runs for check in run.get("invariant_checks", [])]
            result: dict[str, Any] = {
                "scenario": scenario,
                "scale": scale_name,
                "method": {
                    "repetitions": repetitions,
                    "seed": seed,
                    "database_reset": ("fresh database created for scenario and dropped afterward"),
                    "reset_fingerprint": fingerprint,
                },
                "runs": runs,
                "summary": summarize_metric_runs(runs),
                "invariants": {
                    "ok": all(check["ok"] for check in invariant_checks),
                    "checks": invariant_checks,
                },
                "defect_observations": next(
                    (
                        run["defect_observations"]
                        for run in reversed(runs)
                        if "defect_observations" in run
                    ),
                    None,
                ),
                "envelopes": {"accepted": None, "note": "no envelope accepted for this runner"},
                "database": {"before": before, "after": after, "wal_bytes": int(wal_bytes or 0)},
                "postgres": {
                    "settings": before["settings"],
                    "settings_fingerprint_sha256": hashlib.sha256(
                        json.dumps(before["settings"], sort_keys=True).encode()
                    ).hexdigest(),
                },
                "environment": {
                    "git_sha": _git_sha(),
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "hostname": socket.gethostname(),
                    "generated_at": datetime.now(UTC).isoformat(),
                },
            }
        finally:
            await operator.close()
            await admin.close()
    finally:
        await _drop_fresh_database(dsn, database)
    if output is not None:
        output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result, output


def latency_summary(seconds: list[float]) -> dict[str, float]:
    return _latency_summary(seconds)
