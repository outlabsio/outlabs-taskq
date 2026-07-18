"""T3-R — bounded randomized SQL-kernel stress with replayable seeds.

The workload intentionally asserts global invariants rather than a particular
interleaving (Harness §2): no duplicate live claims, no repeated attempt token,
conservation of every enqueue result, and no wedged jobs after drain + tick.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field

import asyncpg
import pytest

from conftest import RoleConnect

pytestmark = pytest.mark.taskq_sql

_ENQUEUE = (
    "SELECT * FROM taskq.enqueue($1, $2, $3::jsonb, "
    "p_idempotency_key => $4, p_max_attempts => 4::smallint, "
    "p_backoff_mode => 'fixed', p_backoff_base => 1, p_backoff_cap => 1)"
)
_CLAIM = "SELECT * FROM taskq.claim_jobs($1, $2)"


@dataclass(slots=True)
class StressState:
    seed: int
    enqueued_ids: set[object] = field(default_factory=set)
    active_claims: dict[object, object] = field(default_factory=dict)
    seen_attempts: set[object] = field(default_factory=set)
    enqueue_calls: int = 0
    created_count: int = 0
    claim_count: int = 0
    errors: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record_enqueue(self, row: asyncpg.Record) -> None:
        async with self.lock:
            self.enqueue_calls += 1
            self.created_count += int(row["created"])
            self.enqueued_ids.add(row["job_id"])

    async def begin_claim(
        self,
        monitor: asyncpg.Connection,
        job: asyncpg.Record,
        worker_id: str,
    ) -> None:
        job_id = job["job_id"]
        attempt_id = job["attempt_id"]
        async with self.lock:
            prior_attempt = self.active_claims.get(job_id)
            if prior_attempt is not None:
                # A settle can commit and make the job claimable before its
                # coroutine resumes to clear the Python marker. The durable
                # ledger distinguishes that harmless scheduling window from
                # a real simultaneous claim.
                prior_status = await monitor.fetchval(
                    "SELECT status FROM taskq.job_attempts WHERE id = $1", prior_attempt
                )
                if prior_status == "running":
                    self.errors.append(
                        f"duplicate live claim job={job_id} "
                        f"attempts={prior_attempt},{attempt_id} worker={worker_id}"
                    )
            if attempt_id in self.seen_attempts:
                self.errors.append(f"repeated attempt token={attempt_id} worker={worker_id}")
            self.active_claims[job_id] = attempt_id
            self.seen_attempts.add(attempt_id)
            self.claim_count += 1

    async def end_claim(self, job_id: object, attempt_id: object) -> None:
        async with self.lock:
            if self.active_claims.get(job_id) == attempt_id:
                del self.active_claims[job_id]

    async def fail(self, actor: str, exc: BaseException) -> None:
        async with self.lock:
            self.errors.append(f"{actor}: {type(exc).__name__}: {exc}")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float = 0.1) -> float:
    value = float(os.environ.get(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


async def _producer_loop(
    conn: asyncpg.Connection,
    queue: str,
    lane: int,
    seed: int,
    deadline: float,
    stop: asyncio.Event,
    state: StressState,
) -> None:
    rng = random.Random(seed)
    sequence = 0
    try:
        while time.monotonic() < deadline and not stop.is_set():
            # Twenty percent of calls converge on a small shared key ring;
            # the rest create unique work. Both outcomes are recorded.
            if rng.random() < 0.20:
                key = f"shared-{rng.randrange(32)}"
            else:
                key = f"producer-{lane}-{sequence}"
            row = await conn.fetchrow(
                _ENQUEUE,
                queue,
                "test.stress",
                '{"source":"t3-r"}',
                key,
            )
            assert row is not None
            await state.record_enqueue(row)
            sequence += 1
            await asyncio.sleep(rng.uniform(0.005, 0.015))
    except BaseException as exc:
        await state.fail(f"producer-{lane}", exc)
        stop.set()


async def _settle_randomly(
    conn: asyncpg.Connection,
    job: asyncpg.Record,
    worker_id: str,
    rng: random.Random,
) -> str:
    choice = rng.random()
    if choice < 0.35:
        row = await conn.fetchrow(
            "SELECT * FROM taskq.complete_job($1, $2, $3)",
            job["job_id"],
            job["attempt_id"],
            worker_id,
        )
    elif choice < 0.55:
        row = await conn.fetchrow(
            "SELECT * FROM taskq.fail_job($1, $2, $3, $4, p_retryable => false)",
            job["job_id"],
            job["attempt_id"],
            worker_id,
            "random non-retryable failure",
        )
    elif choice < 0.70:
        row = await conn.fetchrow(
            "SELECT * FROM taskq.fail_job($1, $2, $3, $4, "
            "p_retryable => true, p_retry_after_seconds => 0)",
            job["job_id"],
            job["attempt_id"],
            worker_id,
            "random retryable failure",
        )
    elif choice < 0.83:
        row = await conn.fetchrow(
            "SELECT * FROM taskq.release_job($1, $2, $3, p_delay_seconds => 0)",
            job["job_id"],
            job["attempt_id"],
            worker_id,
        )
    elif choice < 0.94:
        row = await conn.fetchrow(
            "SELECT * FROM taskq.snooze_job($1, $2, $3, 0, 'random snooze')",
            job["job_id"],
            job["attempt_id"],
            worker_id,
        )
    else:
        row = await conn.fetchrow(
            "SELECT * FROM taskq.cancel_running_job($1, $2, $3, 'random worker cancel')",
            job["job_id"],
            job["attempt_id"],
            worker_id,
        )
    assert row is not None
    return row["result"]


async def _worker_loop(
    conn: asyncpg.Connection,
    monitor: asyncpg.Connection,
    queue: str,
    lane: int,
    seed: int,
    stop: asyncio.Event,
    state: StressState,
) -> None:
    rng = random.Random(seed)
    worker_id = f"stress-worker-{lane}"
    try:
        while not stop.is_set():
            batch = await conn.fetchrow(_CLAIM, queue, worker_id)
            assert batch is not None
            if batch["state"] == "empty":
                await asyncio.sleep(0.003)
                continue
            assert batch["state"] == "claimed"
            job = batch["jobs"][0]
            await state.begin_claim(monitor, job, worker_id)
            try:
                result = await _settle_randomly(conn, job, worker_id, rng)
                assert result in {
                    "ok",
                    "dead",
                    "retry_scheduled",
                    "already_settled",
                    "settle_conflict",
                }
            finally:
                await state.end_claim(job["job_id"], job["attempt_id"])
    except BaseException as exc:
        await state.fail(worker_id, exc)
        stop.set()


async def _cancel_loop(
    operator: asyncpg.Connection,
    seed: int,
    stop: asyncio.Event,
    state: StressState,
) -> None:
    rng = random.Random(seed)
    try:
        while not stop.is_set():
            async with state.lock:
                candidates = tuple(state.enqueued_ids)
            if candidates:
                job_id = rng.choice(candidates)
                row = await operator.fetchrow(
                    "SELECT * FROM taskq.cancel_job($1, 't3-r', 'random operator cancel')",
                    job_id,
                )
                assert row is not None
                assert row["result"] in {"cancelled", "cancel_requested", "already_terminal"}
            await asyncio.sleep(rng.uniform(0.01, 0.03))
    except BaseException as exc:
        await state.fail("operator-canceller", exc)
        stop.set()


async def _drain(
    conn: asyncpg.Connection,
    pg: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
    queue: str,
    seed: int,
) -> None:
    for pass_index in range(200):
        batch = await conn.fetchrow(
            "SELECT * FROM taskq.claim_jobs($1, 'stress-drain', p_batch => 50)", queue
        )
        assert batch is not None
        if batch["state"] == "claimed":
            for job in batch["jobs"]:
                settled = await conn.fetchrow(
                    "SELECT * FROM taskq.complete_job($1, $2, 'stress-drain')",
                    job["job_id"],
                    job["attempt_id"],
                )
                assert settled["result"] == "ok", f"seed={seed} drain settle={dict(settled)}"
            continue

        assert batch["state"] == "empty"
        await housekeeper.fetchval("SELECT taskq.tick(200)")
        active = await pg.fetchval(
            "SELECT count(*) FROM taskq.jobs "
            "WHERE queue = $1 AND status IN ('blocked','queued','running')",
            queue,
        )
        if active == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"seed={seed} drain did not converge within 200 passes")


async def test_randomized_stress_preserves_global_invariants(
    pg: asyncpg.Connection,
    role_conn: RoleConnect,
    operator: asyncpg.Connection,
    housekeeper: asyncpg.Connection,
    request: pytest.FixtureRequest,
) -> None:
    duration = _env_float("TASKQ_STRESS_SECONDS", 30.0)
    producer_count = _env_int("TASKQ_STRESS_PRODUCERS", 3)
    worker_count = _env_int("TASKQ_STRESS_WORKERS", 5)
    seed = int(os.environ.get("TASKQ_STRESS_SEED", str(random.SystemRandom().getrandbits(63))))
    request.node.user_properties.append(("taskq_stress_seed", seed))
    print(f"T3-R seed={seed} seconds={duration} producers={producer_count} workers={worker_count}")

    queue = "t3_stress"
    ensured = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 't3-r')",
        queue,
        '{"default_max_attempts":4,"default_backoff_mode":"fixed",'
        '"default_backoff_base":1,"default_backoff_cap":1}',
    )
    assert ensured is not None

    producers = [await role_conn("taskq_producer") for _ in range(producer_count)]
    workers = [await role_conn("taskq_runner") for _ in range(worker_count)]
    drainer = await role_conn("taskq_runner")
    stop = asyncio.Event()
    state = StressState(seed=seed)
    deadline = time.monotonic() + duration

    producer_tasks = [
        asyncio.create_task(
            _producer_loop(
                conn,
                queue,
                lane,
                seed + 10_000 + lane,
                deadline,
                stop,
                state,
            )
        )
        for lane, conn in enumerate(producers)
    ]
    worker_tasks = [
        asyncio.create_task(_worker_loop(conn, pg, queue, lane, seed + 20_000 + lane, stop, state))
        for lane, conn in enumerate(workers)
    ]
    cancel_task = asyncio.create_task(_cancel_loop(operator, seed + 30_000, stop, state))

    await asyncio.gather(*producer_tasks)
    stop.set()
    await asyncio.gather(*worker_tasks, cancel_task)
    assert not state.errors, f"seed={seed} workload errors: {state.errors}"
    assert not state.active_claims, f"seed={seed} claims left active: {state.active_claims}"
    assert state.enqueue_calls > 0 and state.claim_count > 0

    await _drain(drainer, pg, housekeeper, queue, seed)
    await housekeeper.fetchval("SELECT taskq.tick(200)")

    rows = await pg.fetch("SELECT id, status FROM taskq.jobs WHERE queue = $1 ORDER BY id", queue)
    row_ids = {row["id"] for row in rows}
    assert row_ids == state.enqueued_ids, (
        f"seed={seed} conservation mismatch returned={len(state.enqueued_ids)} rows={len(row_ids)}"
    )
    assert len(rows) == state.created_count == len(state.enqueued_ids)
    assert all(row["status"] in {"succeeded", "failed", "cancelled"} for row in rows)
    assert (
        await pg.fetchval("SELECT count(*) FROM taskq.job_attempts WHERE status = 'running'") == 0
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM ("
            "SELECT job_id FROM taskq.job_attempts WHERE status = 'running' "
            "GROUP BY job_id HAVING count(*) > 1) duplicate_running"
        )
        == 0
    )
    assert not state.errors, f"seed={seed} invariant errors: {state.errors}"
