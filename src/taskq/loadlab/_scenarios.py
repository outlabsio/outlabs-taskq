"""L-series load-behavior scenarios (harness spec SS4). Toy-first, report-only.

L4 encodes remaining known defects of the unmodified contract (red before
green): its ``defect_observations`` assert the defective behavior is
REPRODUCED, and flip in the same PR as the fixes (P7/P8). L5 flipped green
with P1b: it now asserts the survivable claim path (backoff, ride-out of a
single misclassified error, bounded fail-closed under sustained corruption).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import asyncpg

from taskq.bench import _attested_fetchrow, _connect_role
from taskq.errors import TaskqUnavailableError, TaskqValidationError
from taskq.loadlab._chassis import (
    LoadInput,
    LoadOutput,
    QueueSampler,
    ScenarioContext,
    WorkerHandle,
    latency_summary,
    wait_until,
)
from taskq.loadlab._instruments import ClaimFaultPlan

SCENARIOS = ("L1", "L2", "L4", "L5", "L6")


def _check(name: str, ok: bool, **detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


async def _start_fleet(handles: list[WorkerHandle]) -> None:
    for handle in handles:
        await handle.service.start()
    for handle in handles:
        await wait_until(
            _snapshot_swept(handle),
            timeout=10.0,
            message=f"worker {handle.service.worker_id} never swept",
        )


def _snapshot_swept(handle: WorkerHandle):
    async def probe() -> bool:
        return handle.service.snapshot().claim_sweeps >= 1

    return probe


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


# --------------------------------------------------------------------------- L1


async def _l1_notify_herd(ctx: ScenarioContext) -> dict[str, Any]:
    queue, task_name = "load_l1", "load.noop"
    await ctx.ensure_queue(queue)

    async def handler(payload: LoadInput) -> LoadOutput:
        return LoadOutput(value=payload.value)

    fleet = [
        ctx.build_worker(
            queue,
            f"l1-worker-{index}",
            handler=handler,
            task_name=task_name,
            poll_interval=10.0,
            listen=True,
        )
        for index in range(ctx.scale.herd_workers)
    ]
    producer = await ctx.producer()
    try:
        await _start_fleet(fleet)
        await ctx.enqueue_one(producer, queue, task_name, f"l1-warmup-{ctx.seed}")
        await ctx.wait_settled(queue, 1)

        mark = time.monotonic()
        enqueue_times: list[float] = []
        for index in range(ctx.scale.trickle_jobs):
            enqueue_times.append(time.monotonic())
            await ctx.enqueue_one(producer, queue, task_name, f"l1-{ctx.seed}-{index}", value=index)
            await asyncio.sleep(ctx.scale.trickle_gap_seconds)
        await ctx.wait_settled(queue, 1 + ctx.scale.trickle_jobs)

        claims = ctx.instruments.claim_calls(since=mark)
        empties = ctx.instruments.empty_claims(since=mark)
        herd_sizes = []
        for enqueue_time in enqueue_times:
            herd_sizes.append(
                sum(
                    1
                    for event in ctx.instruments.claim_events
                    if enqueue_time <= event.monotonic <= enqueue_time + 0.4
                )
            )
        wake_latencies = []
        for enqueue_time in enqueue_times:
            claimed = [
                event.monotonic - enqueue_time
                for event in ctx.instruments.claim_events
                if event.state == "claimed" and enqueue_time <= event.monotonic
            ]
            if claimed:
                wake_latencies.append(min(claimed))

        attempts = await ctx.attempts_per_job(queue)
        succeeded = await ctx.count_status(queue, ("succeeded",))
        fatal = [h.service.worker_id for h in fleet if h.service.snapshot().fatal]
        checks = [
            _check(
                "all_jobs_delivered", succeeded == 1 + ctx.scale.trickle_jobs, succeeded=succeeded
            ),
            _check(
                "single_attempt_per_job",
                attempts == {1: 1 + ctx.scale.trickle_jobs},
                **{"attempts": {str(k): v for k, v in attempts.items()}},
            ),
            _check("no_worker_fatal", not fatal, fatal_workers=fatal),
        ]
        return {
            "metrics": {
                "workers": ctx.scale.herd_workers,
                "jobs": ctx.scale.trickle_jobs,
                "claim_calls": claims,
                "claim_calls_per_delivered_job": claims / max(1, ctx.scale.trickle_jobs),
                "empty_claim_ratio": empties / max(1, claims),
                "mean_herd_size_per_enqueue": sum(herd_sizes) / max(1, len(herd_sizes)),
            },
            "wake_latency": latency_summary(wake_latencies),
            "invariant_checks": checks,
        }
    finally:
        for handle in fleet:
            await handle.aclose()
        await producer.close()


# --------------------------------------------------------------------------- L2


async def _l2_retry_storm(ctx: ScenarioContext) -> dict[str, Any]:
    queue, task_name = "load_l2", "load.flaky"
    await ctx.ensure_queue(
        queue,
        {
            "default_backoff_base": 2,
            "default_backoff_cap": 3,
            "default_backoff_mode": "exponential",
        },
    )
    seen: set[int] = set()

    async def handler(payload: LoadInput) -> LoadOutput:
        if payload.value not in seen:
            seen.add(payload.value)
            raise RuntimeError("first attempt fails by design")
        return LoadOutput(value=payload.value)

    fleet = [
        ctx.build_worker(
            queue,
            f"l2-worker-{index}",
            handler=handler,
            task_name=task_name,
            poll_interval=0.3,
            listen=True,
        )
        for index in range(ctx.scale.storm_workers)
    ]
    producer = await ctx.producer()
    try:
        await _start_fleet(fleet)
        specs = [
            {
                "job_type": task_name,
                "payload": {"value": index},
                "idempotency_key": f"l2-{ctx.seed}-{index}",
            }
            for index in range(ctx.scale.cohort_jobs)
        ]
        rows = await producer.fetch(
            "SELECT * FROM taskq.enqueue_many($1, $2::jsonb)", queue, json.dumps(specs)
        )
        assert len(rows) == ctx.scale.cohort_jobs
        await ctx.wait_settled(queue, ctx.scale.cohort_jobs)

        second_attempts = [
            float(row["at"])
            for row in await ctx.admin.fetch(
                "WITH ranked AS ("
                "  SELECT a.claimed_at, row_number() OVER ("
                "    PARTITION BY a.job_id ORDER BY a.claimed_at) AS n"
                "  FROM taskq.job_attempts a JOIN taskq.jobs j ON j.id = a.job_id"
                "  WHERE j.queue = $1)"
                " SELECT extract(epoch FROM claimed_at) AS at FROM ranked WHERE n = 2 ORDER BY 1",
                queue,
            )
        ]
        scheduled = []
        for row in await ctx.admin.fetch(
            "SELECT e.data->>'next_at' AS next_at FROM taskq.job_events e"
            " JOIN taskq.jobs j ON j.id = e.job_id"
            " WHERE j.queue = $1 AND e.event_type = 'retry_scheduled'",
            queue,
        ):
            if row["next_at"]:
                scheduled.append(datetime.fromisoformat(row["next_at"]).timestamp())

        attempts = await ctx.attempts_per_job(queue)
        checks = [
            _check(
                "all_jobs_delivered",
                await ctx.count_status(queue, ("succeeded",)) == ctx.scale.cohort_jobs,
            ),
            _check(
                "exactly_two_attempts_per_job",
                attempts == {2: ctx.scale.cohort_jobs},
                **{"attempts": {str(k): v for k, v in attempts.items()}},
            ),
            _check(
                "retry_budget_respected",
                max(attempts, default=0) <= 2,
            ),
        ]
        observed_window = (
            max(second_attempts) - min(second_attempts) if len(second_attempts) > 1 else 0.0
        )
        scheduled_window = max(scheduled) - min(scheduled) if len(scheduled) > 1 else 0.0
        scheduled_p90_window = (
            _percentile(scheduled, 0.95) - _percentile(scheduled, 0.05)
            if len(scheduled) > 1
            else 0.0
        )
        return {
            "metrics": {
                "cohort_jobs": ctx.scale.cohort_jobs,
                "retry_scheduled_dispersion_s": scheduled_window,
                "retry_scheduled_p90_window_s": scheduled_p90_window,
                "retry_observed_dispersion_s": observed_window,
            },
            "invariant_checks": checks,
        }
    finally:
        for handle in fleet:
            await handle.aclose()
        await producer.close()


# --------------------------------------------------------------------------- L4


async def _l4_admission_vs_drain(ctx: ScenarioContext) -> dict[str, Any]:
    queue, task_name = "load_l4", "load.slow"
    await ctx.ensure_queue(queue, {"max_depth": ctx.scale.max_depth})

    async def handler(payload: LoadInput) -> LoadOutput:
        await asyncio.sleep(ctx.scale.handler_delay_seconds)
        return LoadOutput(value=payload.value)

    def build() -> WorkerHandle:
        return ctx.build_worker(
            queue,
            "l4-drain",
            handler=handler,
            task_name=task_name,
            concurrency=4,
            batch=4,
            poll_interval=0.5,
            listen=True,
        )

    total = ctx.scale.contention_jobs
    index_queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(total):
        index_queue.put_nowait(index)
    running = asyncio.Event()
    running.set()
    created_events: list[tuple[float, int]] = []
    backpressure_events: list[float] = []
    other_errors: list[str] = []
    admission_latencies: list[float] = []
    created_keys: list[str] = []

    async def produce(conn: asyncpg.Connection) -> None:
        while True:
            await running.wait()
            try:
                index = index_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            key = f"l4-{ctx.seed}-{index}"
            while True:
                await running.wait()
                started = time.monotonic()
                try:
                    row = await conn.fetchrow(
                        "SELECT * FROM taskq.enqueue($1, $2, $3::jsonb, p_idempotency_key => $4)",
                        queue,
                        task_name,
                        json.dumps({"value": index}),
                        key,
                    )
                except asyncpg.PostgresError as exc:
                    state = exc.sqlstate or "unknown"
                    if state == "TQ429":
                        backpressure_events.append(time.monotonic())
                        await asyncio.sleep(0.5)
                        continue
                    other_errors.append(state)
                    return
                assert row is not None
                admission_latencies.append(time.monotonic() - started)
                if row["created"]:
                    created_events.append((time.monotonic(), index))
                    created_keys.append(key)
                break

    producers = [await ctx.producer() for _ in range(ctx.scale.producers)]
    producer_tasks = [asyncio.create_task(produce(conn)) for conn in producers]
    replay_conn = await ctx.producer()
    worker: WorkerHandle | None = None
    try:

        async def backpressure_seen() -> bool:
            return len(backpressure_events) > 0

        async with QueueSampler(ctx.dsn, queue) as sampler:
            await wait_until(
                backpressure_seen,
                timeout=30.0,
                message="depth backpressure never observed with drain off",
            )
            running.clear()
            await asyncio.sleep(0.6)

            # Defect probe: at pinned depth, replaying an ALREADY-ADMITTED key is
            # itself rejected by the depth gate (probe precedes the idempotency
            # check) — recovery reconciliation is blocked exactly when needed.
            replay_blocked_at_cap = False
            probe_key = created_keys[0]
            try:
                await replay_conn.fetchrow(
                    "SELECT * FROM taskq.enqueue($1, $2, $3::jsonb, p_idempotency_key => $4)",
                    queue,
                    task_name,
                    json.dumps({"value": int(probe_key.rsplit("-", 1)[1])}),
                    probe_key,
                )
            except asyncpg.PostgresError as exc:
                replay_blocked_at_cap = exc.sqlstate == "TQ429"

            # Open bounded headroom by hand (direct fenced SQL claims), then
            # deterministically replay the frozen queued set — the shard-3 shape.
            runner = await _connect_role(ctx.dsn, "taskq_runner")
            try:
                for drain_index in range(10):
                    batch = await _attested_fetchrow(
                        runner,
                        "SELECT * FROM taskq.claim_jobs($1, $2)",
                        queue,
                        f"l4-manual-{drain_index}",
                    )
                    assert batch is not None and batch["state"] == "claimed"
                    job = batch["jobs"][0]
                    settled = await runner.fetchrow(
                        "SELECT * FROM taskq.complete_job($1, $2, $3)",
                        job["job_id"],
                        job["attempt_id"],
                        f"l4-manual-{drain_index}",
                    )
                    assert settled is not None and settled["result"] == "ok"
            finally:
                await runner.close()
            queued_keys = [
                row["idempotency_key"]
                for row in await ctx.admin.fetch(
                    "SELECT idempotency_key FROM taskq.jobs "
                    "WHERE queue=$1 AND status='queued' ORDER BY idempotency_key",
                    queue,
                )
            ]
            replay_created = 0
            for key in queued_keys:
                row = await replay_conn.fetchrow(
                    "SELECT * FROM taskq.enqueue($1, $2, $3::jsonb, p_idempotency_key => $4)",
                    queue,
                    task_name,
                    json.dumps({"value": int(key.rsplit("-", 1)[1])}),
                    key,
                )
                assert row is not None
                replay_created += 1 if row["created"] else 0
            replayed = len(queued_keys)
            running.set()

            worker = build()
            await worker.service.start()
            await asyncio.sleep(1.0)
            await worker.aclose()
            outage_started = time.monotonic()
            await asyncio.sleep(ctx.scale.outage_seconds)
            outage_ended = time.monotonic()
            worker = build()
            await worker.service.start()
            await asyncio.gather(*producer_tasks)
            await ctx.wait_settled(queue, total)
        frozen_window = (max(outage_started, outage_ended - 1.0), outage_ended)
        frozen_creations = sum(
            1 for at, _ in created_events if frozen_window[0] <= at <= frozen_window[1]
        )
        frozen_backpressure = sum(
            1 for at in backpressure_events if frozen_window[0] <= at <= frozen_window[1]
        )
        job_count = await ctx.admin.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE queue=$1", queue
        )
        succeeded = await ctx.count_status(queue, ("succeeded",))
        depth_overshoot = max(0, sampler.max_depth() - ctx.scale.max_depth)
        checks = [
            _check("no_lost_or_duplicate_jobs", int(job_count or 0) == total, jobs=job_count),
            _check("all_jobs_executed_once", succeeded == total, succeeded=succeeded),
            _check(
                "replay_converged_zero_new",
                replay_created == 0 and replayed > 0,
                replayed=replayed,
                replay_created=replay_created,
            ),
            _check("typed_backpressure_only", not other_errors, other_errors=other_errors[:5]),
            _check(
                "no_worker_fatal",
                worker is not None and not worker.service.snapshot().fatal,
            ),
        ]
        defect = {
            "advisory_depth_overshoot_rows": depth_overshoot,
            "admission_frozen_during_consumer_outage": (
                frozen_creations == 0 and frozen_backpressure > 0
            ),
            "frozen_window_creations": frozen_creations,
            "frozen_window_backpressure": frozen_backpressure,
            "backpressure_is_exception_shaped_without_retry_hint": True,
            "replay_of_existing_key_rejected_at_max_depth": replay_blocked_at_cap,
        }
        return {
            "metrics": {
                "cohort_jobs": total,
                "created": len(created_events),
                "backpressure_rejections": len(backpressure_events),
                "max_observed_depth": sampler.max_depth(),
                "depth_overshoot_rows": depth_overshoot,
            },
            "admission_latency": latency_summary(admission_latencies),
            "depth_series_samples": len(sampler.samples),
            "invariant_checks": checks,
            "defect_observations": defect,
        }
    finally:
        for task in producer_tasks:
            task.cancel()
        await asyncio.gather(*producer_tasks, return_exceptions=True)
        for conn in producers:
            await conn.close()
        await replay_conn.close()
        if worker is not None:
            await worker.aclose()


# --------------------------------------------------------------------------- L5


async def _l5_claim_error_posture(ctx: ScenarioContext) -> dict[str, Any]:
    """Post-P1b posture: claim errors back off, one misclassification survives,
    sustained corruption still fails closed within the bounded threshold."""

    queue, task_name = "load_l5", "load.noop5"
    await ctx.ensure_queue(queue)

    async def handler(payload: LoadInput) -> LoadOutput:
        return LoadOutput(value=payload.value)

    plan = ClaimFaultPlan(retryable_error=TaskqUnavailableError, fatal_error=TaskqValidationError)
    overrides = {
        "claim_backoff_base": 0.2,
        "claim_backoff_cap": 1.0,
        "claim_fatal_threshold": 8,
    }
    worker = ctx.build_worker(
        queue,
        "l5-victim",
        handler=handler,
        task_name=task_name,
        poll_interval=3.0,
        listen=True,
        fault_plan=plan,
        service_overrides=overrides,
    )
    producer = await ctx.producer()
    recovered: WorkerHandle | None = None
    try:
        await _start_fleet([worker])

        # Phase 1 — retryable-error window: attempts follow the jittered
        # backoff cadence, not the nudge cadence, and the worker survives.
        plan.mode = "retryable"
        mark = time.monotonic()
        nudges = ctx.scale.nudge_jobs
        for index in range(nudges):
            await ctx.enqueue_one(producer, queue, task_name, f"l5-{ctx.seed}-{index}", value=index)
            await asyncio.sleep(0.15)
        await asyncio.sleep(0.4)
        error_claims = ctx.instruments.error_claims(since=mark)
        survived_window = not worker.service.snapshot().fatal
        plan.mode = "clean"
        await ctx.wait_settled(queue, nudges)

        # Phase 2 — a single misclassified non-retryable claim error is ridden
        # out through the same backoff instead of killing the worker.
        plan.arm_fatal()
        await ctx.enqueue_one(producer, queue, task_name, f"l5-{ctx.seed}-trigger2", value=99998)
        await ctx.wait_settled(queue, nudges + 1)
        survived_single_fatal = not worker.service.snapshot().fatal

        # Phase 3 — sustained corruption-shaped errors still fail closed within
        # the bounded consecutive-error threshold.
        plan.mode = "corruption"
        await ctx.enqueue_one(producer, queue, task_name, f"l5-{ctx.seed}-trigger3", value=99999)
        await wait_until(
            _went_fatal(worker),
            timeout=25.0,
            message="worker did not fail closed under sustained corruption errors",
        )
        went_fatal = worker.service.snapshot().fatal
        await worker.aclose()

        recovered = ctx.build_worker(
            queue,
            "l5-recovery",
            handler=handler,
            task_name=task_name,
            poll_interval=0.3,
            listen=True,
        )
        await recovered.service.start()
        await ctx.wait_settled(queue, nudges + 2)
        attempts = await ctx.attempts_per_job(queue)
        backoff_active = 1 <= error_claims <= max(3, int(nudges * 0.6))
        checks = [
            _check(
                "all_jobs_delivered",
                await ctx.count_status(queue, ("succeeded",)) == nudges + 2,
            ),
            _check(
                "no_duplicate_attempts",
                attempts == {1: nudges + 2},
                **{"attempts": {str(k): v for k, v in attempts.items()}},
            ),
            _check(
                "claim_backoff_active",
                backoff_active,
                error_claims=error_claims,
                nudges=nudges,
            ),
            _check("survived_retryable_window", survived_window),
            _check("survived_single_nonretryable", survived_single_fatal),
            _check("sustained_corruption_fails_closed", went_fatal),
        ]
        return {
            "metrics": {
                "nudges_sent": nudges,
                "claim_errors_during_window": error_claims,
                "claim_errors_per_nudge": error_claims / max(1, nudges),
            },
            "invariant_checks": checks,
            "defect_observations": {
                "claim_backoff_active": backoff_active,
                "single_nonretryable_survived": survived_single_fatal,
                "sustained_corruption_fails_closed": went_fatal,
                "expected_posture": "post-P1b",
            },
        }
    finally:
        await producer.close()
        if recovered is not None:
            await recovered.aclose()


def _went_fatal(handle: WorkerHandle):
    async def probe() -> bool:
        return handle.service.snapshot().fatal

    return probe


# --------------------------------------------------------------------------- L6


async def _l6_counter_contention(ctx: ScenarioContext) -> dict[str, Any]:
    """Wave 2a activation gate: counter exactness under concurrent mixed load,
    and the trigger's write-path overhead measured enabled vs disabled."""

    queue, task_name = "load_l6", "load.mixed"
    await ctx.ensure_queue(queue)
    seen: set[int] = set()

    async def handler(payload: LoadInput) -> LoadOutput:
        if payload.value % 5 == 0 and payload.value not in seen:
            seen.add(payload.value)
            raise RuntimeError("first attempt fails by design")
        return LoadOutput(value=payload.value)

    total = max(60, ctx.scale.cohort_jobs * 3)
    fleet = [
        ctx.build_worker(
            queue,
            f"l6-worker-{index}",
            handler=handler,
            task_name=task_name,
            concurrency=2,
            batch=2,
            poll_interval=0.3,
            listen=True,
        )
        for index in range(3)
    ]
    producers = [await ctx.producer() for _ in range(4)]
    operator_cancels = 3
    try:
        await _start_fleet(fleet)

        async def produce(conn: asyncpg.Connection, lane: int) -> None:
            for index in range(lane, total, len(producers)):
                await conn.fetchrow(
                    "SELECT * FROM taskq.enqueue($1, $2, $3::jsonb, p_idempotency_key => $4)",
                    queue,
                    task_name,
                    json.dumps({"value": index}),
                    f"l6-{ctx.seed}-{index}",
                )

        await asyncio.gather(*(produce(conn, lane) for lane, conn in enumerate(producers)))
        cancelled = 0
        for _ in range(operator_cancels):
            job_id = await ctx.admin.fetchval(
                "SELECT id FROM taskq.jobs WHERE queue=$1 AND status='queued'"
                " AND cancel_requested_at IS NULL LIMIT 1",
                queue,
            )
            if job_id is None:
                continue
            row = await ctx.operator.fetchrow(
                "SELECT * FROM taskq.cancel_job($1, 'loadlab', 'l6')", job_id
            )
            if row is not None and row["result"] in ("ok", "cancelled", "already_cancelled"):
                cancelled += 1
        await wait_until(
            _l6_all_terminal(ctx, queue, total),
            timeout=ctx.scale.settle_timeout_seconds,
            message="l6 mixed workload did not fully settle",
        )
        for handle in fleet:
            await handle.aclose()
        fleet = []

        # retention pass exercises the DELETE branch mid-scenario
        await ctx.admin.execute(
            "UPDATE taskq.jobs SET finished_at = now() - interval '30 days'"
            " WHERE queue=$1 AND status IN ('succeeded','failed','cancelled')"
            " AND id IN (SELECT id FROM taskq.jobs WHERE queue=$1"
            "            AND status IN ('succeeded','failed','cancelled') LIMIT 20)",
            queue,
        )
        janitor = await _connect_role(ctx.dsn, "taskq_housekeeper")
        try:
            await _attested_fetchrow(janitor, "SELECT taskq.janitor()")
        finally:
            await janitor.close()

        counters = dict(
            await ctx.admin.fetchrow(
                "SELECT blocked, queued, running, enqueued_total, requeued_total,"
                " succeeded_total, failed_total, cancelled_total"
                " FROM taskq.queue_counters WHERE queue=$1",
                queue,
            )
        )
        truth = dict(
            await ctx.admin.fetchrow(
                "SELECT count(*) FILTER (WHERE status='blocked') AS blocked,"
                " count(*) FILTER (WHERE status='queued') AS queued,"
                " count(*) FILTER (WHERE status='running') AS running"
                " FROM taskq.jobs WHERE queue=$1",
                queue,
            )
        )
        exact = all(counters[key] == truth[key] for key in ("blocked", "queued", "running"))
        conserved = (
            counters["enqueued_total"] == total
            and counters["succeeded_total"] + counters["failed_total"] + counters["cancelled_total"]
            >= total - truth["queued"] - truth["running"] - truth["blocked"]
        )

        # Overhead: identical direct-SQL claim/complete bursts, trigger on vs off.
        burst = 150
        prep = producers[0]

        async def timed_burst(tag: str) -> float:
            await ctx.enqueue_many(prep, queue, task_name, f"l6-burst-{tag}-{ctx.seed}", burst)
            runner = await _connect_role(ctx.dsn, "taskq_runner")
            started = time.monotonic()
            try:
                for index in range(burst):
                    batch = await _attested_fetchrow(
                        runner, "SELECT * FROM taskq.claim_jobs($1, $2)", queue, f"l6-{tag}"
                    )
                    assert batch is not None and batch["state"] == "claimed"
                    job = batch["jobs"][0]
                    settled = await runner.fetchrow(
                        "SELECT * FROM taskq.complete_job($1, $2, $3)",
                        job["job_id"],
                        job["attempt_id"],
                        f"l6-{tag}",
                    )
                    assert settled is not None and settled["result"] == "ok"
            finally:
                await runner.close()
            return time.monotonic() - started

        on_seconds = await timed_burst("on")
        await ctx.admin.execute("ALTER TABLE taskq.jobs DISABLE TRIGGER jobs_queue_counters_trg")
        try:
            off_seconds = await timed_burst("off")
        finally:
            await ctx.admin.execute("ALTER TABLE taskq.jobs ENABLE TRIGGER jobs_queue_counters_trg")
            await ctx.admin.execute("TRUNCATE taskq.queue_counters")
            await ctx.admin.execute(
                "INSERT INTO taskq.queue_counters (queue, blocked, queued, running,"
                " enqueued_total, requeued_total, succeeded_total, failed_total,"
                " cancelled_total)"
                " SELECT q.name, COALESCE(c.blocked,0), COALESCE(c.queued,0),"
                " COALESCE(c.running,0), COALESCE(c.total,0), 0, COALESCE(c.succeeded,0),"
                " COALESCE(c.failed,0), COALESCE(c.cancelled,0)"
                " FROM taskq.queues q LEFT JOIN ("
                "  SELECT j.queue, count(*) AS total,"
                "  count(*) FILTER (WHERE j.status='blocked') AS blocked,"
                "  count(*) FILTER (WHERE j.status='queued') AS queued,"
                "  count(*) FILTER (WHERE j.status='running') AS running,"
                "  count(*) FILTER (WHERE j.status='succeeded') AS succeeded,"
                "  count(*) FILTER (WHERE j.status='failed') AS failed,"
                "  count(*) FILTER (WHERE j.status='cancelled') AS cancelled"
                "  FROM taskq.jobs j GROUP BY j.queue) c ON c.queue = q.name"
            )
        overhead_pct = ((on_seconds - off_seconds) / off_seconds * 100) if off_seconds else 0.0
        rebuilt = dict(
            await ctx.admin.fetchrow(
                "SELECT blocked, queued, running FROM taskq.queue_counters WHERE queue=$1",
                queue,
            )
        )
        truth_after = dict(
            await ctx.admin.fetchrow(
                "SELECT count(*) FILTER (WHERE status='blocked') AS blocked,"
                " count(*) FILTER (WHERE status='queued') AS queued,"
                " count(*) FILTER (WHERE status='running') AS running"
                " FROM taskq.jobs WHERE queue=$1",
                queue,
            )
        )
        checks = [
            _check("counter_levels_exact_after_mixed_load", exact, counters=counters, truth=truth),
            _check("cumulatives_conserved", conserved, counters=counters, total=total),
            _check("rebackfill_exact", rebuilt == truth_after, rebuilt=rebuilt, truth=truth_after),
            _check(
                "cancels_recorded", counters["cancelled_total"] >= cancelled, cancelled=cancelled
            ),
        ]
        return {
            "metrics": {
                "mixed_jobs": total,
                "burst_jobs": burst,
                "trigger_on_seconds": round(on_seconds, 3),
                "trigger_off_seconds": round(off_seconds, 3),
                "trigger_overhead_pct": round(overhead_pct, 2),
                "throughput_on_jobs_s": round(burst / on_seconds, 1) if on_seconds else 0,
                "throughput_off_jobs_s": round(burst / off_seconds, 1) if off_seconds else 0,
            },
            "invariant_checks": checks,
        }
    finally:
        for handle in fleet:
            await handle.aclose()
        for conn in producers:
            await conn.close()


def _l6_all_terminal(ctx: ScenarioContext, queue: str, total: int):
    async def probe() -> bool:
        value = await ctx.admin.fetchval(
            "SELECT count(*) FROM taskq.jobs WHERE queue=$1"
            " AND status IN ('succeeded','failed','cancelled')",
            queue,
        )
        return int(value or 0) >= total

    return probe


SCENARIO_RUNNERS = {
    "L1": _l1_notify_herd,
    "L2": _l2_retry_storm,
    "L4": _l4_admission_vs_drain,
    "L5": _l5_claim_error_posture,
    "L6": _l6_counter_contention,
}
