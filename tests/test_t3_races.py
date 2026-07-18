"""T3 — deterministic, choreographed PostgreSQL race tests.

Each case uses real capability-role sessions. Advisory locks are starting
barriers; open transactions hold the production row/index/relation locks that
create the exact interleaving under test (Harness §2). Waiting is observed via
``pg_stat_activity`` rather than guessed with sleeps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import asyncpg
import pytest

from conftest import RoleConnect

pytestmark = pytest.mark.taskq_sql

_ROUNDS = 20
_WAIT_TIMEOUT = 3.0
_ENQUEUE = (
    "SELECT * FROM taskq.enqueue($1, $2, '{}'::jsonb, "
    "p_idempotency_key => $3, p_concurrency_key => $4)"
)
_CLAIM = "SELECT * FROM taskq.claim_jobs($1, $2)"


def _barrier_key(case: int, round_index: int, lane: int = 0) -> int:
    return 7_310_000_000 + case * 100_000 + round_index * 100 + lane


async def _make_queue(operator: asyncpg.Connection, name: str) -> None:
    row = await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 't3')", name)
    assert row is not None


async def _enqueue_one(
    producer: asyncpg.Connection,
    queue: str,
    key: str,
    concurrency_key: str | None = None,
) -> asyncpg.Record:
    row = await producer.fetchrow(_ENQUEUE, queue, "test.race", key, concurrency_key)
    assert row is not None
    return row


async def _claim_one(runner: asyncpg.Connection, queue: str, worker_id: str) -> asyncpg.Record:
    batch = await runner.fetchrow(_CLAIM, queue, worker_id)
    assert batch is not None
    assert batch["state"] == "claimed"
    assert len(batch["jobs"]) == 1
    return batch["jobs"][0]


async def _arm_barrier(controller: asyncpg.Connection, key: int) -> None:
    await controller.fetchval("SELECT pg_advisory_lock($1)", key)


async def _release_barrier(controller: asyncpg.Connection, key: int) -> None:
    released = await controller.fetchval("SELECT pg_advisory_unlock($1)", key)
    assert released is True


async def _fetchrow_after_barrier(
    conn: asyncpg.Connection,
    key: int,
    query: str,
    *args: object,
) -> asyncpg.Record | None:
    await conn.fetchval("SELECT pg_advisory_lock($1)", key)
    released = await conn.fetchval("SELECT pg_advisory_unlock($1)", key)
    assert released is True
    return await conn.fetchrow(query, *args)


async def _wait_for_lock(
    monitor: asyncpg.Connection,
    pid: int,
    expected_events: Iterable[str],
) -> str:
    expected = set(expected_events)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _WAIT_TIMEOUT
    last: tuple[str | None, str | None, str | None] | None = None
    while loop.time() < deadline:
        row = await monitor.fetchrow(
            "SELECT state, wait_event_type, wait_event "
            "FROM pg_catalog.pg_stat_activity WHERE pid = $1",
            pid,
        )
        if row is not None:
            last = (row["state"], row["wait_event_type"], row["wait_event"])
            if row["wait_event_type"] == "Lock" and row["wait_event"] in expected:
                return row["wait_event"]
        await asyncio.sleep(0.005)
    raise AssertionError(f"pid {pid} never waited on {sorted(expected)}; last state={last!r}")


async def _finish_task(task: asyncio.Task[asyncpg.Record | None]) -> asyncpg.Record:
    row = await asyncio.wait_for(task, timeout=_WAIT_TIMEOUT)
    assert row is not None
    return row


class TestChoreographedRaces:
    async def test_same_key_enqueue_converges_to_one_created(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
    ) -> None:
        queue = "t3_same_key"
        await _make_queue(operator, queue)
        first_producer = await role_conn("taskq_producer")
        second_producer = await role_conn("taskq_producer")
        second_pid = second_producer.get_server_pid()

        for round_index in range(_ROUNDS):
            key = f"same-{round_index}"
            barrier = _barrier_key(1, round_index)
            transaction = first_producer.transaction()
            await transaction.start()
            first = await _enqueue_one(first_producer, queue, key)
            assert first["created"] is True

            await _arm_barrier(pg, barrier)
            contender = asyncio.create_task(
                _fetchrow_after_barrier(
                    second_producer,
                    barrier,
                    _ENQUEUE,
                    queue,
                    "test.race",
                    key,
                    None,
                )
            )
            await _wait_for_lock(pg, second_pid, {"advisory"})
            await _release_barrier(pg, barrier)
            await _wait_for_lock(pg, second_pid, {"transactionid", "spectoken"})
            await transaction.commit()

            second = await _finish_task(contender)
            assert second["created"] is False
            assert second["job_id"] == first["job_id"]
            assert (
                await pg.fetchval(
                    "SELECT count(*) FROM taskq.jobs WHERE queue = $1 AND idempotency_key = $2",
                    queue,
                    key,
                )
                == 1
            )

    async def test_hold_open_claim_prevents_double_claim(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "t3_double_claim"
        await _make_queue(operator, queue)
        first_runner = await role_conn("taskq_runner")
        second_runner = await role_conn("taskq_runner")

        for round_index in range(_ROUNDS):
            enqueued = await _enqueue_one(producer, queue, f"claim-{round_index}")
            transaction = first_runner.transaction()
            await transaction.start()
            first = await _claim_one(first_runner, queue, f"claim-a-{round_index}")
            assert first["job_id"] == enqueued["job_id"]

            barrier = _barrier_key(2, round_index)
            await _arm_barrier(pg, barrier)
            contender = asyncio.create_task(
                _fetchrow_after_barrier(
                    second_runner,
                    barrier,
                    _CLAIM,
                    queue,
                    f"claim-b-{round_index}",
                )
            )
            await _wait_for_lock(pg, second_runner.get_server_pid(), {"advisory"})
            await _release_barrier(pg, barrier)
            second = await _finish_task(contender)
            assert second["state"] == "empty"
            assert not second["jobs"]
            await transaction.commit()

            assert (
                await pg.fetchval(
                    "SELECT count(*) FROM taskq.job_attempts "
                    "WHERE job_id = $1 AND status = 'running'",
                    enqueued["job_id"],
                )
                == 1
            )
            settled = await first_runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                first["job_id"],
                first["attempt_id"],
                f"claim-a-{round_index}",
            )
            assert settled["result"] == "ok"

        index_ok = await pg.fetchval(
            "SELECT i.indisunique AND pg_get_expr(i.indpred, i.indrelid) = "
            "$$(status = 'running'::text)$$ "
            "FROM pg_catalog.pg_index i "
            "WHERE i.indexrelid = 'taskq.uq_job_attempts_running'::regclass"
        )
        assert index_ok is True

    async def test_heartbeat_loses_fence_after_targeted_reap(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "t3_reap_fence"
        await _make_queue(operator, queue)
        claiming_runner = await role_conn("taskq_runner")
        heartbeat_runner = await role_conn("taskq_runner")

        for round_index in range(_ROUNDS):
            await _enqueue_one(producer, queue, f"reap-{round_index}")
            worker = f"reap-worker-{round_index}"
            job = await _claim_one(claiming_runner, queue, worker)

            transaction = operator.transaction()
            await transaction.start()
            expired = await operator.fetchval("SELECT taskq.expire_job($1, 't3')", job["job_id"])
            assert expired == "expired_and_reaped"

            barrier = _barrier_key(3, round_index)
            await _arm_barrier(pg, barrier)
            heartbeat = asyncio.create_task(
                _fetchrow_after_barrier(
                    heartbeat_runner,
                    barrier,
                    "SELECT * FROM taskq.heartbeat($1, $2, $3)",
                    job["job_id"],
                    job["attempt_id"],
                    worker,
                )
            )
            await _wait_for_lock(pg, heartbeat_runner.get_server_pid(), {"advisory"})
            await _release_barrier(pg, barrier)
            await _wait_for_lock(pg, heartbeat_runner.get_server_pid(), {"transactionid", "tuple"})
            await transaction.commit()

            lost = await _finish_task(heartbeat)
            assert lost["ok"] is False
            assert lost["lease_expires_at"] is None
            assert (
                await pg.fetchval(
                    "SELECT status FROM taskq.job_attempts WHERE id = $1", job["attempt_id"]
                )
                == "expired"
            )

    async def test_cross_verb_settle_race_returns_conflict(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "t3_settle_race"
        await _make_queue(operator, queue)
        completing_runner = await role_conn("taskq_runner")
        failing_runner = await role_conn("taskq_runner")

        for round_index in range(_ROUNDS):
            await _enqueue_one(producer, queue, f"settle-{round_index}")
            worker = f"settle-worker-{round_index}"
            job = await _claim_one(completing_runner, queue, worker)

            transaction = completing_runner.transaction()
            await transaction.start()
            completed = await completing_runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                job["job_id"],
                job["attempt_id"],
                worker,
            )
            assert completed["result"] == "ok"

            barrier = _barrier_key(4, round_index)
            await _arm_barrier(pg, barrier)
            failure = asyncio.create_task(
                _fetchrow_after_barrier(
                    failing_runner,
                    barrier,
                    "SELECT * FROM taskq.fail_job($1, $2, $3, $4)",
                    job["job_id"],
                    job["attempt_id"],
                    worker,
                    "late conflicting failure",
                )
            )
            await _wait_for_lock(pg, failing_runner.get_server_pid(), {"advisory"})
            await _release_barrier(pg, barrier)
            await _wait_for_lock(pg, failing_runner.get_server_pid(), {"transactionid", "tuple"})
            await transaction.commit()

            conflict = await _finish_task(failure)
            assert conflict["result"] == "settle_conflict"
            assert conflict["job_status"] == "succeeded"

    async def test_concurrency_cap_never_overshoots_under_ten_claims(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "t3_cap"
        concurrency_key = "t3:cap:one"
        await _make_queue(operator, queue)
        assert (
            await operator.fetchval(
                "SELECT taskq.set_concurrency_limit($1, 1, 't3')", concurrency_key
            )
            == "created"
        )
        runners = [await role_conn("taskq_runner") for _ in range(10)]

        for round_index in range(_ROUNDS):
            for lane in range(10):
                await _enqueue_one(
                    producer,
                    queue,
                    f"cap-{round_index}-{lane}",
                    concurrency_key,
                )

            barriers = [_barrier_key(5, round_index, lane) for lane in range(10)]
            for barrier in barriers:
                await _arm_barrier(pg, barrier)
            claims = [
                asyncio.create_task(
                    _fetchrow_after_barrier(
                        runner,
                        barrier,
                        _CLAIM,
                        queue,
                        f"cap-worker-{round_index}-{lane}",
                    )
                )
                for lane, (runner, barrier) in enumerate(zip(runners, barriers, strict=True))
            ]
            for runner in runners:
                await _wait_for_lock(pg, runner.get_server_pid(), {"advisory"})
            for barrier in barriers:
                await _release_barrier(pg, barrier)

            batches = [await _finish_task(claim) for claim in claims]
            winners = [
                (lane, batch) for lane, batch in enumerate(batches) if batch["state"] == "claimed"
            ]
            assert len(winners) == 1
            assert all(batch["state"] in {"claimed", "empty"} for batch in batches)
            assert (
                await pg.fetchval(
                    "SELECT count(*) FROM taskq.jobs "
                    "WHERE status = 'running' AND concurrency_key = $1",
                    concurrency_key,
                )
                == 1
            )

            lane, winner = winners[0]
            job = winner["jobs"][0]
            settled = await runners[lane].fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                job["job_id"],
                job["attempt_id"],
                f"cap-worker-{round_index}-{lane}",
            )
            assert settled["result"] == "ok"

    async def test_pause_allows_only_the_already_in_flight_claim(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        claiming_runner = await role_conn("taskq_runner")
        checking_runner = await role_conn("taskq_runner")

        for round_index in range(_ROUNDS):
            queue = f"t3_pause_{round_index}"
            await _make_queue(operator, queue)
            enqueued = await _enqueue_one(producer, queue, f"pause-{round_index}")

            transaction = pg.transaction()
            await transaction.start()
            await pg.execute("LOCK TABLE taskq.job_attempts IN ACCESS EXCLUSIVE MODE")
            barrier = _barrier_key(6, round_index)
            await _arm_barrier(pg, barrier)
            claim = asyncio.create_task(
                _fetchrow_after_barrier(
                    claiming_runner,
                    barrier,
                    _CLAIM,
                    queue,
                    f"pause-worker-{round_index}",
                )
            )
            await _wait_for_lock(pg, claiming_runner.get_server_pid(), {"advisory"})
            await _release_barrier(pg, barrier)
            await _wait_for_lock(pg, claiming_runner.get_server_pid(), {"relation"})

            paused = await operator.fetchval(
                "SELECT taskq.pause_queue($1, 't3', 'pause during claim')", queue
            )
            assert paused == "paused"
            await transaction.commit()

            slipped = await _finish_task(claim)
            assert slipped["state"] == "claimed"
            assert len(slipped["jobs"]) == 1
            assert slipped["jobs"][0]["job_id"] == enqueued["job_id"]

            after_pause = await checking_runner.fetchrow(
                _CLAIM, queue, f"pause-check-{round_index}"
            )
            assert after_pause["state"] == "paused"
            assert not after_pause["jobs"]

            job = slipped["jobs"][0]
            settled = await claiming_runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                job["job_id"],
                job["attempt_id"],
                f"pause-worker-{round_index}",
            )
            assert settled["result"] == "ok"
