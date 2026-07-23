"""S2-02 complete live SQL transport and least-capability role matrix."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from taskq import TaskqInternalError, TaskqTransport
from taskq.protocol import (
    COMMAND_SPECS,
    ENQUEUE_RESULT_ADAPTER,
    SETTLE_RESULT_ADAPTER,
    ClaimState,
    CommandName,
    EnqueueCommand,
    EnqueueManyItem,
    EnqueueStatus,
    Followup,
    SettleOutcome,
)
from taskq.sql.manifest import (
    FUNCTIONS,
    PUBLIC_ERRORS,
    PUBLIC_FUNCTIONS,
    REPLAY_RULES,
)
from taskq.sql.transport import METHOD_FUNCTIONS, SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql


@pytest.fixture
async def transports(sqlalchemy_dsn: str) -> AsyncIterator[dict[str, SqlTaskqTransport]]:
    result: dict[str, SqlTaskqTransport] = {}
    for capability in ("producer", "runner", "observer", "operator", "housekeeper"):
        role = f"taskq_{capability}"
        engine = create_async_engine(
            sqlalchemy_dsn,
            connect_args={"server_settings": {"role": role}},
        )
        result[capability] = SqlTaskqTransport(engine)
    try:
        yield result
    finally:
        for transport in result.values():
            await transport.engine.dispose()


def test_transport_method_ledger_is_exactly_the_public_manifest() -> None:
    assert set(METHOD_FUNCTIONS.values()) == set(PUBLIC_FUNCTIONS)
    assert len(METHOD_FUNCTIONS) == len(PUBLIC_FUNCTIONS) == 36
    assert METHOD_FUNCTIONS == {
        command.value: spec.sql_function for command, spec in COMMAND_SPECS.items()
    }
    for command, spec in COMMAND_SPECS.items():
        method = command.value
        assert inspect.iscoroutinefunction(getattr(TaskqTransport, method))
        assert inspect.iscoroutinefunction(getattr(SqlTaskqTransport, method))
        assert spec.capability.value in FUNCTIONS[spec.sql_function].grants
        assert {code.value for code in spec.errors} == set(PUBLIC_ERRORS[spec.sql_function])
        assert spec.replay_rule.value == REPLAY_RULES[spec.sql_function]
        assert spec.outcomes
        assert spec.retryable_errors <= spec.errors


def test_tagged_result_discriminators_are_exactly_the_tier0_outcomes() -> None:
    enqueue_schema = ENQUEUE_RESULT_ADAPTER.json_schema()
    assert enqueue_schema["discriminator"]["propertyName"] == "status"
    assert set(enqueue_schema["discriminator"]["mapping"]) == {
        outcome.value for outcome in EnqueueStatus
    }

    settle_schema = SETTLE_RESULT_ADAPTER.json_schema()
    assert settle_schema["discriminator"]["propertyName"] == "result"
    tagged_outcomes = set(settle_schema["discriminator"]["mapping"])
    assert tagged_outcomes == {outcome.value for outcome in SettleOutcome}
    fenced_commands = (
        CommandName.COMPLETE,
        CommandName.FAIL,
        CommandName.SNOOZE,
        CommandName.RELEASE,
        CommandName.CANCEL_RUNNING,
    )
    assert set().union(*(COMMAND_SPECS[command].outcomes for command in fenced_commands)) == (
        tagged_outcomes
    )


def test_sql_adapter_contains_no_taskq_table_dml() -> None:
    source = inspect.getsource(SqlTaskqTransport).lower()
    for verb in ("insert into", "update", "delete from"):
        assert f"{verb} taskq." not in source


async def _queue(transports: dict[str, SqlTaskqTransport], name: str) -> None:
    result = await transports["operator"].ensure_queue(name, actor="s2-test")
    assert result.result == "created"


async def _enqueue(
    transports: dict[str, SqlTaskqTransport], queue: str, *, keyed: str | None = None
) -> UUID:
    result = await transports["producer"].enqueue(
        EnqueueCommand(
            queue=queue,
            job_type="tests.echo",
            payload={"value": 1},
            idempotency_key=keyed,
        )
    )
    return result.job_id


async def _claim(
    transports: dict[str, SqlTaskqTransport], queue: str, worker: str
) -> tuple[UUID, UUID]:
    result = await transports["runner"].claim(queue, worker)
    assert result.state is ClaimState.CLAIMED
    return result.jobs[0].job_id, result.jobs[0].attempt_id


async def test_producer_transport_created_existed_bulk_and_typed_error(
    pg: object, transports: dict[str, SqlTaskqTransport]
) -> None:
    del pg  # activates the per-test clean database fixture
    await _queue(transports, "s2_producer")
    command = EnqueueCommand(
        queue="s2_producer",
        job_type="tests.echo",
        payload={"value": 1},
        idempotency_key="same",
    )
    created = await transports["producer"].enqueue(command)
    existed = await transports["producer"].enqueue(command)
    assert created.status is EnqueueStatus.CREATED
    assert existed.status is EnqueueStatus.EXISTED
    assert existed.job_id == created.job_id

    bulk = await transports["producer"].enqueue_many(
        "s2_producer",
        [
            EnqueueManyItem(job_type="tests.echo", payload={"i": 0}, idempotency_key="bulk"),
            EnqueueManyItem(job_type="tests.echo", payload={"i": 1}, idempotency_key="bulk"),
        ],
    )
    assert [item.status for item in bulk] == [EnqueueStatus.CREATED, EnqueueStatus.EXISTED]
    assert bulk[0].job_id == bulk[1].job_id

    with pytest.raises(TaskqInternalError):
        await transports["producer"].get_contract_meta()


async def test_concurrent_transport_dedup_matches_protocol_outcomes(
    pg: object, transports: dict[str, SqlTaskqTransport]
) -> None:
    del pg
    await _queue(transports, "s2_dedup")
    command = EnqueueCommand(
        queue="s2_dedup",
        job_type="tests.echo",
        payload={"value": 1},
        idempotency_key="same-key",
    )
    results = await asyncio.gather(*(transports["producer"].enqueue(command) for _ in range(20)))
    assert [result.status for result in results].count(EnqueueStatus.CREATED) == 1
    assert [result.status for result in results].count(EnqueueStatus.EXISTED) == 19
    assert len({result.job_id for result in results}) == 1


async def test_runner_transport_all_commands_and_fence_redaction(
    pg: object, transports: dict[str, SqlTaskqTransport], caplog: pytest.LogCaptureFixture
) -> None:
    del pg
    queue = "s2_runner"
    await _queue(transports, queue)
    empty = await transports["runner"].claim(queue, "worker")
    assert empty.state is ClaimState.EMPTY
    assert await transports["runner"].worker_heartbeat("worker", [queue]) is False

    await _enqueue(transports, queue)
    claim = await transports["runner"].claim(queue, "worker")
    assert claim.state is ClaimState.CLAIMED and claim.jobs[0].lease_seconds == 300
    job_id, attempt_id = claim.jobs[0].job_id, claim.jobs[0].attempt_id
    assert str(attempt_id) not in caplog.text
    heartbeat = await transports["runner"].heartbeat(
        job_id, attempt_id, "worker", progress={"cursor": 1}, stats={"cpu": 2}
    )
    assert heartbeat.ok and not heartbeat.cancel_requested
    completed = await transports["runner"].complete(
        job_id, attempt_id, "worker", result={"answer": 2}
    )
    assert completed.result == "ok" and completed.job_status == "succeeded"

    await _enqueue(transports, queue)
    job_id, attempt_id = await _claim(transports, queue, "worker")
    failed = await transports["runner"].fail(job_id, attempt_id, "worker", "nope", retryable=False)
    assert failed.result == "dead" and failed.job_status == "failed"

    await _enqueue(transports, queue)
    job_id, attempt_id = await _claim(transports, queue, "worker")
    snoozed = await transports["runner"].snooze(job_id, attempt_id, "worker", 0, reason="later")
    assert snoozed.job_status == "queued"
    job_id, attempt_id = await _claim(transports, queue, "worker")
    released = await transports["runner"].release(
        job_id, attempt_id, "worker", "released", delay_seconds=0
    )
    assert released.job_status == "queued"
    job_id, attempt_id = await _claim(transports, queue, "worker")
    cancelled = await transports["runner"].cancel_running(job_id, attempt_id, "worker", "stop")
    assert cancelled.result == "ok" and cancelled.job_status == "cancelled"
    assert str(attempt_id) not in caplog.text


async def test_runner_transport_serializes_typed_cross_queue_followup(
    pg: object, transports: dict[str, SqlTaskqTransport]
) -> None:
    await _queue(transports, "s2_parent")
    await _queue(transports, "s2_child")
    await _enqueue(transports, "s2_parent")
    job_id, attempt_id = await _claim(transports, "s2_parent", "worker")
    settled = await transports["runner"].complete(
        job_id,
        attempt_id,
        "worker",
        followups=(
            Followup(
                step="child",
                job_type="tests.child",
                queue="s2_child",
                payload={"value": 7},
            ),
        ),
    )
    assert settled.result == "ok"
    child = await pg.fetchrow(  # type: ignore[union-attr]
        "SELECT queue,job_type,parent_job_id,idempotency_key,payload "
        "FROM taskq.jobs WHERE parent_job_id=$1",
        job_id,
    )
    assert child is not None
    assert dict(child) == {
        "queue": "s2_child",
        "job_type": "tests.child",
        "parent_job_id": job_id,
        "idempotency_key": f"chain:{job_id}:child",
        "payload": '{"value": 7}',
    }


async def test_observer_and_housekeeper_transport(
    pg: object, transports: dict[str, SqlTaskqTransport]
) -> None:
    del pg
    queue = "s2_observer"
    await _queue(transports, queue)
    job_id = await _enqueue(transports, queue)
    tick = await transports["housekeeper"].tick()
    assert "reaped" in tick
    assert isinstance(await transports["housekeeper"].janitor(), dict)

    projection = await transports["observer"].get_authorization_projection(job_id)
    assert projection is not None and projection.queue == queue
    detail = await transports["observer"].get_job(job_id, include_payload=True)
    assert detail is not None and detail.payload == {"value": 1}
    assert await transports["observer"].get_job(uuid4()) is None
    stats = await transports["observer"].get_queue_stats(queue)
    assert len(stats) == 1 and stats[0].queue == queue
    meta = await transports["observer"].get_contract_meta()
    assert meta.contract_version == "0.2.0"
    names = {metric.name for metric in await transports["observer"].metrics()}
    assert "taskq_ready" in names


async def test_operator_transport_complete_surface(
    pg: object, transports: dict[str, SqlTaskqTransport]
) -> None:
    del pg
    operator = transports["operator"]
    queue = "s2_operator"
    await _queue(transports, queue)
    assert (await operator.ensure_queue(queue, actor="s2-test")).result == "unchanged"
    assert await operator.pause_queue(queue, "s2-test") == "paused"
    assert await operator.pause_queue(queue, "s2-test") == "already_paused"
    assert await operator.resume_queue(queue, "s2-test") == "resumed"
    assert await operator.set_concurrency_limit("resource", 2, "s2-test") == "created"
    assert await operator.update_queue_profile("s2_missing", {}, "s2-test", 1) == (
        "missing",
        None,
        None,
    )

    await transports["runner"].worker_heartbeat("shutdown-worker", [queue])
    assert (
        await operator.request_worker_shutdown(
            worker_id="shutdown-worker", queue=None, actor="s2-test"
        )
        == 1
    )
    assert await transports["runner"].worker_heartbeat("shutdown-worker", [queue]) is True

    future = EnqueueCommand(
        queue=queue,
        job_type="tests.echo",
        payload={},
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    future_id = (await transports["producer"].enqueue(future)).job_id
    assert await operator.reprioritize(future_id, 7, "s2-test") == "ok"
    assert await operator.run_now(future_id, "s2-test") == "ok"
    cancelled = await operator.cancel(future_id, "s2-test", "stop")
    assert cancelled.result == "cancelled"

    for _ in range(2):
        await _enqueue(transports, queue)
    assert await operator.purge_queued(queue, 10, "s2-test", "cleanup") == 2

    await _enqueue(transports, queue)
    failed_id, attempt_id = await _claim(transports, queue, "op-worker")
    await transports["runner"].fail(failed_id, attempt_id, "op-worker", "fail", retryable=False)
    assert await operator.redrive(failed_id, "s2-test") is True

    await _enqueue(transports, queue)
    failed_id, attempt_id = await _claim(transports, queue, "op-worker")
    await transports["runner"].fail(failed_id, attempt_id, "op-worker", "fail", retryable=False)
    redriven = await operator.redrive_failed(queue, 10, "s2-test")
    assert redriven.redriven == 1 and redriven.skipped == 0

    await _enqueue(transports, queue)
    running_id, _ = await _claim(transports, queue, "expire-one")
    assert await operator.expire_job(running_id, "s2-test") == "expired_and_reaped"

    await _enqueue(transports, queue)
    await _claim(transports, queue, "expire-worker")
    expired = await operator.expire_worker_leases("expire-worker", "s2-test")
    assert expired.matched == 1 and expired.reaped == 1 and expired.skipped == 0


async def test_owned_and_borrowed_engine_close_semantics(sqlalchemy_dsn: str) -> None:
    borrowed_engine = create_async_engine(sqlalchemy_dsn)
    borrowed = SqlTaskqTransport(borrowed_engine)
    await borrowed.aclose()
    assert borrowed_engine.sync_engine.pool is not None
    await borrowed.aclose()
    await borrowed_engine.dispose()

    owned = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    assert isinstance(owned, TaskqTransport)
    await owned.aclose()
    await owned.aclose()


async def test_sql_transport_has_no_background_tasks_or_checked_out_resources(
    sqlalchemy_dsn: str,
) -> None:
    before = asyncio.all_tasks()
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    pool = transport.engine.sync_engine.pool
    assert pool.checkedout() == 0  # type: ignore[attr-defined]
    assert asyncio.all_tasks() == before
    assert (await transport.get_contract_meta()).contract_version == "0.2.0"
    await asyncio.sleep(0)
    assert pool.checkedout() == 0  # type: ignore[attr-defined]
    assert asyncio.all_tasks() == before
    await transport.aclose()


async def test_every_capability_role_rejects_a_cross_capability_call(
    pg: object, transports: dict[str, SqlTaskqTransport]
) -> None:
    del pg
    probes = [
        transports["producer"].get_contract_meta(),
        transports["runner"].ensure_queue("forbidden", actor="test"),
        transports["observer"].enqueue(
            EnqueueCommand(queue="missing", job_type="tests.echo", payload={})
        ),
        transports["operator"].claim("missing", "worker"),
        transports["housekeeper"].metrics(),
    ]
    for probe in probes:
        with pytest.raises(TaskqInternalError):
            await probe


async def test_transport_owned_transaction_commit_error_and_cancellation(
    pg: object, sqlalchemy_dsn: str
) -> None:
    del pg
    engine = create_async_engine(sqlalchemy_dsn)
    transport = SqlTaskqTransport(engine)
    await transport._run(
        lambda conn: SqlTaskqTransport._scalar(
            conn,
            "SELECT result FROM taskq.ensure_queue('s2_atomic', '{}'::jsonb, 'test')",
        )
    )

    async def enqueue_then_fail(conn: AsyncConnection) -> None:
        await conn.execute(
            text("SELECT * FROM taskq.enqueue('s2_atomic', 'tests.error', '{}'::jsonb)")
        )
        await conn.execute(text("SELECT 1 / 0"))

    with pytest.raises(TaskqInternalError):
        await transport._run(enqueue_then_fail)

    started = asyncio.Event()

    async def enqueue_then_wait(conn: AsyncConnection) -> None:
        await conn.execute(
            text("SELECT * FROM taskq.enqueue('s2_atomic', 'tests.cancel', '{}'::jsonb)")
        )
        started.set()
        await asyncio.Future()

    pending = asyncio.create_task(transport._run(enqueue_then_wait))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    async with engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM taskq.jobs WHERE job_type LIKE 'tests.%'")
        )
    assert count == 0
    await engine.dispose()


@pytest.mark.parametrize(
    "rows",
    [
        [{"input_index": 1, "job_id": uuid4(), "outcome": "created"}],
        [
            {"input_index": 1, "job_id": uuid4(), "outcome": "created"},
            {"input_index": 1, "job_id": uuid4(), "outcome": "existed"},
        ],
        [
            {"input_index": 1, "job_id": uuid4(), "outcome": "created"},
            {"input_index": 2, "job_id": uuid4(), "outcome": "replaced"},
        ],
        [
            {"input_index": 1, "job_id": uuid4(), "outcome": "created"},
            {"input_index": 2, "job_id": None, "outcome": "existed"},
        ],
    ],
)
async def test_bulk_malformed_rows_are_atomic_tq500(
    sqlalchemy_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
) -> None:
    engine = create_async_engine(sqlalchemy_dsn)
    transport = SqlTaskqTransport(engine)

    async def fake_many(*args: object, **kwargs: object) -> list[dict[str, object]]:
        return rows

    monkeypatch.setattr(transport, "_many", fake_many)
    items = [EnqueueManyItem(job_type="tests.echo"), EnqueueManyItem(job_type="tests.echo")]
    with pytest.raises(TaskqInternalError):
        await transport.enqueue_many("queue", items)
    await engine.dispose()


async def test_function_specific_outcome_drift_is_tq500(
    sqlalchemy_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(sqlalchemy_dsn)
    transport = SqlTaskqTransport(engine)

    async def wrong_settle_row(*args: object, **kwargs: object) -> dict[str, object]:
        return {"result": "dead", "job_status": "failed", "scheduled_at": None}

    monkeypatch.setattr(transport, "_one", wrong_settle_row)
    with pytest.raises(TaskqInternalError):
        await transport.complete(uuid4(), uuid4(), "worker")

    async def wrong_operator_outcome(*args: object, **kwargs: object) -> str:
        return "resumed"

    monkeypatch.setattr(transport, "_operator_scalar", wrong_operator_outcome)
    with pytest.raises(TaskqInternalError):
        await transport.pause_queue("queue", "actor")
    await engine.dispose()
