from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from taskq import TaskQ
from taskq.errors import TaskqConfigError, TaskqConflictError
from taskq.execution import Cancel, Complete, NonRetryable, Retry, Snooze
from taskq.protocol import (
    AdmissionCancelOutcome,
    AdmissionFinishOutcome,
    AdmissionReserveOutcome,
    ClaimState,
    EnqueueCommand,
    EnqueueManyItem,
    EnqueueStatus,
    JobStatus,
    SettleOutcome,
)
from taskq.testing import FakeTaskQClient
from taskq.transport import ProducerTransport


async def _enqueue_and_claim(
    fake: FakeTaskQClient,
    *,
    suffix: str,
    max_attempts: int = 3,
):
    result = await fake.enqueue(
        EnqueueCommand(
            queue="testing",
            job_type=f"tests.{suffix}",
            payload={"value": suffix},
            headers={"trace": {"id": suffix}},
            idempotency_key=suffix,
            max_attempts=max_attempts,
        )
    )
    claimed = await fake.claim("testing", "worker", job_id=result.job_id)
    assert claimed.state is ClaimState.CLAIMED
    return claimed.jobs[0]


@pytest.mark.asyncio
async def test_fake_enqueue_dedup_and_matchers_are_typed_and_fence_free() -> None:
    fake = FakeTaskQClient()
    command = EnqueueCommand(
        queue="testing",
        job_type="tests.email",
        payload={"recipient": "person@example.test"},
        headers={"trace": {"id": "abc"}},
        idempotency_key="same",
    )

    created = await fake.enqueue(command)
    existed = await fake.enqueue(command)

    assert created.status is EnqueueStatus.CREATED
    assert existed.status is EnqueueStatus.EXISTED
    assert created.job_id == existed.job_id
    matches = fake.assert_enqueued(
        "tests.email",
        count=2,
        where={"payload.recipient": "person@example.test", "headers.trace.id": "abc"},
    )
    assert {item.status for item in matches} == {"created", "existed"}
    assert "attempt" not in repr(fake)
    assert "attempt" not in repr(matches)

    with pytest.raises(AssertionError, match="expected 1 enqueue"):
        fake.assert_enqueued("tests.missing")
    with pytest.raises(TaskqConfigError, match="not available"):
        fake.assert_enqueued("tests.email", count=2, where={"attempt_id": "secret"})
    with pytest.raises(TaskqConfigError, match="safe dotted"):
        fake.assert_enqueued("tests.email", count=2, where={"payload.x;select": 1})


@pytest.mark.asyncio
async def test_fake_admission_lifecycle_matches_producer_contract() -> None:
    fake = FakeTaskQClient(queues=("admission",))
    assert isinstance(fake, ProducerTransport)

    reserved = await fake.reserve_admission("admission", "key", "a" * 64, handle=None)
    assert reserved.outcome is AdmissionReserveOutcome.RESERVED

    pending = await fake.reserve_admission("admission", "key", "a" * 64, handle=None)
    assert pending.outcome is AdmissionReserveOutcome.PENDING

    finished = await fake.finish_admission(
        "admission",
        "key",
        reserved.handle,
        {"job_type": "tests.admitted", "payload": {"value": 1}},
        {"accepted": True},
    )
    assert finished.outcome is AdmissionFinishOutcome.CREATED

    replay = await fake.finish_admission(
        "admission",
        "key",
        reserved.handle,
        {"job_type": "tests.admitted", "payload": {"value": 1}},
        {"accepted": True},
    )
    assert replay.outcome is AdmissionFinishOutcome.EXISTED
    assert replay.job_id == finished.job_id
    assert replay.receipt == {"accepted": True}

    admitted = await fake.reserve_admission("admission", "key", "a" * 64, handle=None)
    assert admitted.outcome is AdmissionReserveOutcome.ADMITTED
    assert admitted.job_id == finished.job_id

    with pytest.raises(TaskqConflictError) as mismatch:
        await fake.reserve_admission("admission", "key", "b" * 64)
    assert mismatch.value.details == {"reason": "idempotency_mismatch"}

    cancelled_reservation = await fake.reserve_admission("admission", "cancel", "c" * 64)
    cancelled = await fake.cancel_admission("admission", "cancel", cancelled_reservation.handle)
    assert cancelled.outcome is AdmissionCancelOutcome.CANCELLED
    cancelled_replay = await fake.cancel_admission(
        "admission", "cancel", cancelled_reservation.handle
    )
    assert cancelled_replay.outcome is AdmissionCancelOutcome.ALREADY_CANCELLED
    with pytest.raises(TaskqConflictError) as cancelled_finish:
        await fake.finish_admission(
            "admission",
            "cancel",
            cancelled_reservation.handle,
            {"job_type": "tests.cancelled", "payload": {}},
        )
    assert cancelled_finish.value.details == {"reason": "reservation_cancelled"}


@pytest.mark.asyncio
async def test_fake_claim_heartbeat_and_closed_outcomes() -> None:
    fake = FakeTaskQClient(queues=("known",))
    assert (await fake.claim("unknown", "worker")).state is ClaimState.UNKNOWN_QUEUE
    assert (await fake.claim("known", "worker")).state is ClaimState.EMPTY

    future = await fake.enqueue(
        EnqueueCommand(
            queue="known",
            job_type="tests.future",
            payload={},
            scheduled_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
    assert future.created
    assert (await fake.claim("known", "worker")).state is ClaimState.EMPTY

    claim = await _enqueue_and_claim(fake, suffix="heartbeat")
    alive = await fake.heartbeat(claim.job_id, claim.attempt_id, "worker")
    lost = await fake.heartbeat(claim.job_id, claim.attempt_id, "other")
    assert alive.ok and alive.lease_expires_at is not None
    assert not lost.ok


@pytest.mark.asyncio
async def test_fake_records_every_runner_settlement_intent() -> None:
    fake = FakeTaskQClient()

    complete = await _enqueue_and_claim(fake, suffix="complete")
    completed = await fake.complete(
        complete.job_id, complete.attempt_id, "worker", result={"ok": True}
    )
    assert completed.result is SettleOutcome.OK
    replay = await fake.complete(
        complete.job_id, complete.attempt_id, "worker", result={"ok": True}
    )
    conflict = await fake.cancel_running(complete.job_id, complete.attempt_id, "worker", "late")
    assert replay.result is SettleOutcome.ALREADY_SETTLED
    assert conflict.result is SettleOutcome.SETTLE_CONFLICT

    retry = await _enqueue_and_claim(fake, suffix="retry")
    retried = await fake.fail(
        retry.job_id,
        retry.attempt_id,
        "worker",
        "again",
        retry_after_seconds=0,
    )
    assert retried.result is SettleOutcome.RETRY_SCHEDULED

    dead = await _enqueue_and_claim(fake, suffix="dead", max_attempts=1)
    failed = await fake.fail(dead.job_id, dead.attempt_id, "worker", "stop")
    assert failed.result is SettleOutcome.DEAD

    snooze = await _enqueue_and_claim(fake, suffix="snooze")
    assert (
        await fake.snooze(snooze.job_id, snooze.attempt_id, "worker", 0)
    ).result is SettleOutcome.OK

    release = await _enqueue_and_claim(fake, suffix="release")
    assert (
        await fake.release(release.job_id, release.attempt_id, "worker", "released")
    ).result is SettleOutcome.OK

    cancel = await _enqueue_and_claim(fake, suffix="cancel")
    assert (
        await fake.cancel_running(cancel.job_id, cancel.attempt_id, "worker", "test")
    ).result is SettleOutcome.OK

    intents = [record.intent for record in fake.settlements]
    assert isinstance(intents[0], Complete)
    assert isinstance(intents[1], Retry)
    assert isinstance(intents[2], NonRetryable)
    assert isinstance(intents[3], Snooze)
    assert intents[4] is None and fake.settlements[4].cause == "released"
    assert isinstance(intents[5], Cancel)
    assert {job.status for job in fake.pending} == {JobStatus.QUEUED}


@pytest.mark.asyncio
async def test_fake_bulk_unsupported_and_close_boundaries() -> None:
    fake = FakeTaskQClient()
    results = await fake.enqueue_many(
        "bulk",
        [
            EnqueueManyItem(
                job_type="tests.bulk",
                payload={"index": index},
                idempotency_key=str(index),
            )
            for index in range(2)
        ],
    )
    assert len(results) == 2

    with pytest.raises(TaskqConfigError, match="unsupported"):
        await fake.get_job(results[0].job_id)
    await fake.aclose()
    await fake.aclose()
    with pytest.raises(TaskqConfigError, match="closed"):
        await fake.enqueue(EnqueueCommand(queue="bulk", job_type="tests.bulk", payload={}))


@pytest.mark.asyncio
async def test_replace_client_restores_exact_transport_on_every_exit() -> None:
    original = FakeTaskQClient()
    replacement = FakeTaskQClient()
    tq = TaskQ(original)

    with tq.replace_client(replacement) as yielded:
        assert yielded is replacement
        assert tq.transport is replacement
        with pytest.raises(TaskqConfigError, match="cannot be nested"):
            with tq.replace_client(FakeTaskQClient()):
                pass
    assert tq.transport is original

    with pytest.raises(RuntimeError, match="body"):
        with tq.replace_client(replacement):
            raise RuntimeError("body")
    assert tq.transport is original

    entered = asyncio.Event()

    async def cancelled_body() -> None:
        with tq.replace_client(replacement):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_body())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tq.transport is original
