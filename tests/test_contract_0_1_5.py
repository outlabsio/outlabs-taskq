"""SQL contract 0.1.5 — durable two-phase admission invariants and races."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from uuid import UUID, uuid4

import asyncpg
import pytest

from conftest import RoleConnect

pytestmark = pytest.mark.taskq_sql

_RESERVE = "SELECT * FROM taskq.reserve_admission($1,$2,$3,$4,$5,$6)"
_FINISH = "SELECT * FROM taskq.finish_admission($1,$2,$3,$4::jsonb,$5::jsonb)"
_CANCEL = "SELECT * FROM taskq.cancel_admission($1,$2,$3)"
_INTENT_A = "a" * 64
_INTENT_B = "b" * 64
_WAIT_TIMEOUT = 3.0


async def _make_queue(operator: asyncpg.Connection, queue: str) -> None:
    row = await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 't5')", queue)
    assert row is not None


async def _reserve(
    producer: asyncpg.Connection,
    queue: str,
    key: str,
    intent: str,
    handle: UUID,
    reservation_ttl: int = 300,
    receipt_ttl: int = 2_592_000,
) -> asyncpg.Record:
    row = await producer.fetchrow(
        _RESERVE, queue, key, intent, handle, reservation_ttl, receipt_ttl
    )
    assert row is not None
    return row


async def _finish(
    producer: asyncpg.Connection,
    queue: str,
    key: str,
    handle: UUID,
    job: dict[str, object],
    receipt: dict[str, object],
) -> asyncpg.Record:
    row = await producer.fetchrow(
        _FINISH,
        queue,
        key,
        handle,
        json.dumps(job, separators=(",", ":")),
        json.dumps(receipt, separators=(",", ":")),
    )
    assert row is not None
    return row


def _json(value: str | dict[str, object]) -> dict[str, object]:
    return json.loads(value) if isinstance(value, str) else value


def _assert_tq(exc: pytest.ExceptionInfo[asyncpg.PostgresError], code: str, reason: str) -> None:
    assert exc.value.sqlstate == code
    assert json.loads(exc.value.detail or "{}") == {"reason": reason}


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
    raise AssertionError(f"pid {pid} never waited on {sorted(expected)}; last={last!r}")


class TestAdmissionContract:
    async def test_reserve_finish_and_all_replay_paths_converge(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "admission_replay"
        key = "tenant:operation:42"
        owner_handle = uuid4()
        contender_handle = uuid4()
        await _make_queue(operator, queue)

        reserved = await _reserve(producer, queue, key, _INTENT_A, owner_handle)
        assert reserved["outcome"] == "reserved"
        assert reserved["handle"] == owner_handle

        repeated = await _reserve(producer, queue, key, _INTENT_A, owner_handle)
        assert repeated["outcome"] == "reserved"
        pending = await _reserve(producer, queue, key, _INTENT_A, contender_handle)
        assert pending["outcome"] == "pending"
        assert 1 <= pending["retry_after_seconds"] <= 300

        job = {"payload": {"z": 2, "a": 1}, "job_type": "test.admitted"}
        receipt = {"planning": {"version": 7}, "external_id": "stable-42"}
        created = await _finish(producer, queue, key, owner_handle, job, receipt)
        assert created["outcome"] == "created"
        assert _json(created["receipt"]) == receipt

        reordered_job = {"job_type": "test.admitted", "payload": {"a": 1, "z": 2}}
        reordered_receipt = {"external_id": "stable-42", "planning": {"version": 7}}
        existed = await _finish(
            producer, queue, key, owner_handle, reordered_job, reordered_receipt
        )
        assert existed["outcome"] == "existed"
        assert existed["job_id"] == created["job_id"]
        assert _json(existed["receipt"]) == receipt

        admitted = await _reserve(producer, queue, key, _INTENT_A, contender_handle)
        assert admitted["outcome"] == "admitted"
        assert admitted["job_id"] == created["job_id"]
        assert _json(admitted["receipt"]) == receipt
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 1
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 1
        assert await pg.fetchval(
            "SELECT a.job_id = j.id AND j.admission_id = a.id "
            "FROM taskq.admissions a JOIN taskq.jobs j ON j.admission_id = a.id"
        )

        cancelled = await producer.fetchrow(_CANCEL, queue, key, owner_handle)
        assert cancelled is not None
        assert cancelled["outcome"] == "already_admitted"
        assert cancelled["job_id"] == created["job_id"]

    async def test_intent_finish_and_handle_mismatches_fail_closed(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "admission_mismatch"
        key = "same-key"
        handle = uuid4()
        await _make_queue(operator, queue)
        await _reserve(producer, queue, key, _INTENT_A, handle)

        with pytest.raises(asyncpg.PostgresError) as mismatch:
            await _reserve(producer, queue, key, _INTENT_B, uuid4())
        _assert_tq(mismatch, "TQ409", "idempotency_mismatch")

        with pytest.raises(asyncpg.PostgresError) as conflict:
            await _finish(
                producer,
                queue,
                key,
                uuid4(),
                {"job_type": "test.one", "payload": {}},
                {},
            )
        _assert_tq(conflict, "TQ409", "reservation_conflict")

        await _finish(
            producer,
            queue,
            key,
            handle,
            {"job_type": "test.one", "payload": {"value": 1}},
            {"receipt": 1},
        )
        with pytest.raises(asyncpg.PostgresError) as changed:
            await _finish(
                producer,
                queue,
                key,
                handle,
                {"job_type": "test.one", "payload": {"value": 2}},
                {"receipt": 1},
            )
        _assert_tq(changed, "TQ409", "finish_mismatch")
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 1

    async def test_cancel_is_idempotent_and_allows_new_reservation(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "admission_cancel"
        key = "cancel-and-reacquire"
        old_handle = uuid4()
        new_handle = uuid4()
        await _make_queue(operator, queue)
        await _reserve(producer, queue, key, _INTENT_A, old_handle)

        cancelled = await producer.fetchrow(_CANCEL, queue, key, old_handle)
        assert cancelled is not None and cancelled["outcome"] == "cancelled"
        repeated = await producer.fetchrow(_CANCEL, queue, key, old_handle)
        assert repeated is not None and repeated["outcome"] == "already_cancelled"

        reserved = await _reserve(producer, queue, key, _INTENT_B, new_handle)
        assert reserved["outcome"] == "reserved"
        with pytest.raises(asyncpg.PostgresError) as stale:
            await _finish(
                producer,
                queue,
                key,
                old_handle,
                {"job_type": "test.stale", "payload": {}},
                {},
            )
        _assert_tq(stale, "TQ409", "reservation_conflict")
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0

    async def test_expired_reservation_takeover_rejects_stale_finish(
        self,
        pg: asyncpg.Connection,
        stateful_time_travel: None,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "admission_takeover"
        key = "expired"
        old_handle = uuid4()
        new_handle = uuid4()
        await _make_queue(operator, queue)
        await _reserve(producer, queue, key, _INTENT_A, old_handle, reservation_ttl=15)
        assert await pg.fetchval(
            "SELECT taskq_test.rewind_admission($1,$2,interval '1 hour')", queue, key
        )

        takeover = await _reserve(producer, queue, key, _INTENT_B, new_handle)
        assert takeover["outcome"] == "reserved"
        with pytest.raises(asyncpg.PostgresError) as stale:
            await _finish(
                producer,
                queue,
                key,
                old_handle,
                {"job_type": "test.stale", "payload": {}},
                {},
            )
        _assert_tq(stale, "TQ409", "reservation_conflict")

        created = await _finish(
            producer,
            queue,
            key,
            new_handle,
            {"job_type": "test.current", "payload": {}},
            {"generation": 2},
        )
        assert created["outcome"] == "created"
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 1

    async def test_finish_reports_unreacquired_expired_and_cancelled_states(
        self,
        pg: asyncpg.Connection,
        stateful_time_travel: None,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        queue = "admission_finish_state_errors"
        await _make_queue(operator, queue)

        expired_handle = uuid4()
        await _reserve(
            producer,
            queue,
            "expired",
            _INTENT_A,
            expired_handle,
            reservation_ttl=15,
        )
        assert await pg.fetchval(
            "SELECT taskq_test.rewind_admission($1,$2,interval '1 hour')",
            queue,
            "expired",
        )
        with pytest.raises(asyncpg.PostgresError) as expired:
            await _finish(
                producer,
                queue,
                "expired",
                expired_handle,
                {"job_type": "test.expired", "payload": {}},
                {},
            )
        _assert_tq(expired, "TQ409", "reservation_expired")

        cancelled_handle = uuid4()
        await _reserve(producer, queue, "cancelled", _INTENT_A, cancelled_handle)
        cancelled = await producer.fetchrow(_CANCEL, queue, "cancelled", cancelled_handle)
        assert cancelled is not None and cancelled["outcome"] == "cancelled"
        with pytest.raises(asyncpg.PostgresError) as cancelled_finish:
            await _finish(
                producer,
                queue,
                "cancelled",
                cancelled_handle,
                {"job_type": "test.cancelled", "payload": {}},
                {},
            )
        _assert_tq(cancelled_finish, "TQ409", "reservation_cancelled")
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0

    async def test_retention_cleanup_is_bounded_and_requires_job_absence(
        self,
        pg: asyncpg.Connection,
        stateful_time_travel: None,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
        housekeeper: asyncpg.Connection,
    ) -> None:
        queue = "admission_retention"
        key = "retained"
        handle = uuid4()
        await _make_queue(operator, queue)
        await _reserve(producer, queue, key, _INTENT_A, handle, receipt_ttl=3600)
        result = await _finish(
            producer,
            queue,
            key,
            handle,
            {"job_type": "test.retained", "payload": {}},
            {"durable": True},
        )
        assert await pg.fetchval(
            "SELECT taskq_test.rewind_admission($1,$2,interval '2 hours')", queue, key
        )

        first = await housekeeper.fetchval("SELECT taskq.janitor()")
        assert _json(first)["admissions_pruned"] == 0
        await pg.execute("DELETE FROM taskq.jobs WHERE id = $1", result["job_id"])
        second = await housekeeper.fetchval("SELECT taskq.janitor()")
        assert _json(second)["admissions_pruned"] == 1
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 0

    @pytest.mark.parametrize(
        ("query", "args"),
        [
            (_RESERVE, ("missing", "k", _INTENT_A, UUID(int=0), 300, 3600)),
            (_RESERVE, ("missing", "k", "ABC", uuid4(), 300, 3600)),
            (_FINISH, ("missing", "k", uuid4(), "{}", "{}")),
        ],
    )
    async def test_invalid_or_unknown_inputs_are_typed(
        self,
        pg: asyncpg.Connection,
        producer: asyncpg.Connection,
        query: str,
        args: tuple[object, ...],
    ) -> None:
        with pytest.raises(asyncpg.PostgresError) as error:
            await producer.fetchrow(query, *args)
        assert error.value.sqlstate in {"TQ001", "TQ422"}


class TestAdmissionPrivileges:
    async def test_table_is_private_and_functions_are_producer_only(
        self,
        pg: asyncpg.Connection,
        producer: asyncpg.Connection,
        role_conn: RoleConnect,
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await producer.fetchval("SELECT count(*) FROM taskq.admissions")

        for role in ("taskq_runner", "taskq_observer", "taskq_housekeeper"):
            conn = await role_conn(role)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchrow(_RESERVE, "any", "k", _INTENT_A, uuid4(), 300, 3600)


class TestAdmissionRaces:
    async def test_concurrent_reserve_serializes_to_reserved_then_pending(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
    ) -> None:
        queue = "admission_race_reserve"
        key = "same-key"
        await _make_queue(operator, queue)
        first = await role_conn("taskq_producer")
        second = await role_conn("taskq_producer")
        first_handle = uuid4()
        second_handle = uuid4()

        transaction = first.transaction()
        await transaction.start()
        winner = await _reserve(first, queue, key, _INTENT_A, first_handle)
        assert winner["outcome"] == "reserved"
        contender = asyncio.create_task(_reserve(second, queue, key, _INTENT_A, second_handle))
        await _wait_for_lock(pg, second.get_server_pid(), {"transactionid", "spectoken"})
        await transaction.commit()

        pending = await asyncio.wait_for(contender, timeout=_WAIT_TIMEOUT)
        assert pending["outcome"] == "pending"
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 1
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0

    async def test_concurrent_finish_serializes_to_created_then_existed(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
    ) -> None:
        queue = "admission_race_finish"
        key = "same-finish"
        handle = uuid4()
        await _make_queue(operator, queue)
        first = await role_conn("taskq_producer")
        second = await role_conn("taskq_producer")
        await _reserve(first, queue, key, _INTENT_A, handle)
        job = {"job_type": "test.finish-race", "payload": {"stable": True}}
        receipt = {"planned": "once"}

        transaction = first.transaction()
        await transaction.start()
        created = await _finish(first, queue, key, handle, job, receipt)
        assert created["outcome"] == "created"
        replay = asyncio.create_task(_finish(second, queue, key, handle, job, receipt))
        await _wait_for_lock(pg, second.get_server_pid(), {"transactionid", "tuple"})
        await transaction.commit()

        existed = await asyncio.wait_for(replay, timeout=_WAIT_TIMEOUT)
        assert existed["outcome"] == "existed"
        assert existed["job_id"] == created["job_id"]
        assert await pg.fetchval("SELECT count(*) FROM taskq.admissions") == 1
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 1

    async def test_finish_wins_over_blocked_cancel_without_deleting_job(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
    ) -> None:
        queue = "admission_race_cancel"
        key = "finish-first"
        handle = uuid4()
        await _make_queue(operator, queue)
        first = await role_conn("taskq_producer")
        second = await role_conn("taskq_producer")
        await _reserve(first, queue, key, _INTENT_A, handle)

        transaction = first.transaction()
        await transaction.start()
        created = await _finish(
            first,
            queue,
            key,
            handle,
            {"job_type": "test.finish-cancel", "payload": {}},
            {"winner": "finish"},
        )
        cancellation = asyncio.create_task(second.fetchrow(_CANCEL, queue, key, handle))
        await _wait_for_lock(pg, second.get_server_pid(), {"transactionid", "tuple"})
        await transaction.commit()

        cancelled = await asyncio.wait_for(cancellation, timeout=_WAIT_TIMEOUT)
        assert cancelled is not None
        assert cancelled["outcome"] == "already_admitted"
        assert cancelled["job_id"] == created["job_id"]
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 1

    async def test_cancel_wins_over_blocked_finish_without_creating_job(
        self,
        pg: asyncpg.Connection,
        role_conn: RoleConnect,
        operator: asyncpg.Connection,
    ) -> None:
        queue = "admission_race_cancel_first"
        key = "cancel-first"
        handle = uuid4()
        await _make_queue(operator, queue)
        first = await role_conn("taskq_producer")
        second = await role_conn("taskq_producer")
        await _reserve(first, queue, key, _INTENT_A, handle)

        transaction = first.transaction()
        await transaction.start()
        cancelled = await first.fetchrow(_CANCEL, queue, key, handle)
        assert cancelled is not None and cancelled["outcome"] == "cancelled"
        finish = asyncio.create_task(
            _finish(
                second,
                queue,
                key,
                handle,
                {"job_type": "test.cancel-finish", "payload": {}},
                {"winner": "cancel"},
            )
        )
        await _wait_for_lock(pg, second.get_server_pid(), {"transactionid", "tuple"})
        await transaction.commit()

        with pytest.raises(asyncpg.PostgresError) as cancelled_finish:
            await asyncio.wait_for(finish, timeout=_WAIT_TIMEOUT)
        _assert_tq(cancelled_finish, "TQ409", "reservation_cancelled")
        assert await pg.fetchval("SELECT count(*) FROM taskq.jobs") == 0
