"""B1–B4 benchmark runner (Harness §5).

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
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import migrate

SCENARIOS = ("B1", "B2", "B3", "B4")


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


async def _reset(admin: asyncpg.Connection) -> None:
    rows = await admin.fetch(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'taskq'"
    )
    keep = {"schema_migrations", "meta"}
    names = [row["tablename"] for row in rows if row["tablename"] not in keep]
    if names:
        targets = ", ".join(f'taskq."{name}"' for name in names)
        await admin.execute(f"TRUNCATE {targets} CASCADE")


async def _ensure_queue(operator: asyncpg.Connection, queue: str) -> None:
    row = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'benchmark')", queue
    )
    assert row is not None


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
    queue: str,
    scale: Scale,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    results = []
    for repetition in range(repetitions):
        producers = [await _connect_role(dsn, "taskq_producer") for _ in range(scale.producers)]
        workers = [await _connect_role(dsn, "taskq_runner") for _ in range(scale.workers)]
        stop = asyncio.Event()
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
            while not stop.is_set():
                started = time.perf_counter()
                await _enqueue_one(conn, queue, f"b4-{seed}-{repetition}-{lane}-{index}")
                enqueue_latencies.append(time.perf_counter() - started)
                accepted += 1
                index += 1

        async def work(conn: asyncpg.Connection, lane: int) -> None:
            nonlocal settled
            rng = random.Random(seed + repetition * 1000 + lane)
            worker = f"bench-b4-{repetition}-{lane}"
            while not stop.is_set():
                started = time.perf_counter()
                batch = await conn.fetchrow("SELECT * FROM taskq.claim_jobs($1, $2)", queue, worker)
                if batch["state"] == "empty":
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

        tasks = [
            *(asyncio.create_task(produce(conn, lane)) for lane, conn in enumerate(producers)),
            *(asyncio.create_task(work(conn, lane)) for lane, conn in enumerate(workers)),
        ]
        started = time.perf_counter()
        await asyncio.sleep(scale.duration_seconds)
        stop.set()
        await asyncio.gather(*tasks)
        duration = time.perf_counter() - started
        for conn in (*producers, *workers):
            await conn.close()
        results.append(
            {
                "accepted": accepted,
                "settled": settled,
                "duration_seconds": duration,
                "throughput_rows_per_second": settled / duration,
                "enqueue_latency": _latency_summary(enqueue_latencies),
                "e2e_latency": _latency_summary(e2e_latencies),
            }
        )
    return results


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
        await _reset(admin)
        await _ensure_queue(operator, queue)
        before = await _database_snapshot(admin)
        loop_delay = await _event_loop_delay()
        if scenario == "B1":
            runs = await _b1(producer, queue, scale, repetitions, seed)
        elif scenario == "B2":
            runs = await _b2(producer, queue, scale, repetitions, seed)
        elif scenario == "B3":
            runs = await _b3(producer, runner, operator, scale, repetitions, seed)
        else:
            runs = await _b4(dsn, queue, scale, repetitions, seed)
        explain_queue = "bench_b3_0" if scenario == "B3" else queue
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
                "database_reset": "caller scratch database; taskq state truncated before scenario",
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
    finally:
        await runner.close()
        await producer.close()
        await operator.close()
        await admin.close()

    if output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = Path("bench/results") / f"{stamp}-{scenario.lower()}-{scale_name}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result, output


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
