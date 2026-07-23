"""DB-free schedule fake parity and deterministic-clock evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from taskq import ScheduleDefinition
from taskq.schedules import evaluate_schedule
from taskq.testing import FakeTaskQClient


def _definition() -> ScheduleDefinition:
    return ScheduleDefinition.model_validate(
        {
            "target": {
                "kind": "job",
                "queue": "scheduled",
                "job_type": "tests.scheduled",
                "payload": {"source": "fake"},
            },
            "recurrence": {"kind": "interval", "interval_seconds": 60},
            "catchup_policy": "fire_all",
            "max_catchup": 2,
        }
    )


async def test_fake_schedule_lifecycle_evaluation_replay_and_retry_gate() -> None:
    instant = [datetime(2026, 1, 1, tzinfo=UTC)]
    fake = FakeTaskQClient(queues=("scheduled",), clock=lambda: instant[0])

    created = await fake.put_schedule("tests.minute", _definition(), "test")
    assert created.outcome == "created"
    assert created.profile.next_fire_at == instant[0]
    assert (await fake.get_schedule_authorization_projection("tests.minute")).queue == "scheduled"

    first = (await fake.claim_schedules("housekeeper")).schedules[0]
    compilation = evaluate_schedule(
        recurrence=first.recurrence,
        catchup_policy=first.catchup_policy,
        max_catchup=first.max_catchup,
        initialized=first.initialized,
        next_fire_at=first.next_fire_at,
        as_of=first.as_of,
    )
    initialized = await fake.fire_schedule(
        first.schedule_id,
        first.token,
        first.definition_version,
        compilation.occurrences,
        compilation.next_fire_at,
    )
    assert initialized.outcome == "initialized"
    assert initialized.jobs_enqueued == 0

    instant[0] += timedelta(seconds=60)
    second = (await fake.claim_schedules("housekeeper")).schedules[0]
    firing = evaluate_schedule(
        recurrence=second.recurrence,
        catchup_policy=second.catchup_policy,
        max_catchup=second.max_catchup,
        initialized=second.initialized,
        next_fire_at=second.next_fire_at,
        as_of=second.as_of,
    )
    fired = await fake.fire_schedule(
        second.schedule_id,
        second.token,
        second.definition_version,
        firing.occurrences,
        firing.next_fire_at,
    )
    replay = await fake.fire_schedule(
        second.schedule_id,
        second.token,
        second.definition_version,
        firing.occurrences,
        firing.next_fire_at,
    )
    assert (fired.outcome, fired.jobs_enqueued, fired.replayed) == ("fired", 1, False)
    assert (replay.outcome, replay.jobs_enqueued, replay.replayed) == ("fired", 1, True)
    assert len(fake.enqueues) == 1
    assert fake.enqueues[0].idempotency_key is not None

    instant[0] += timedelta(seconds=60)
    third = (await fake.claim_schedules("housekeeper")).schedules[0]
    recorded = await fake.schedule_error(
        third.schedule_id,
        third.token,
        third.definition_version,
        "bounded-category",
        retry_seconds=30,
    )
    assert recorded.outcome == "error_recorded"
    assert (await fake.claim_schedules("housekeeper")).state == "empty"
    instant[0] += timedelta(seconds=30)
    assert (await fake.claim_schedules("housekeeper")).state == "claimed"

    profile = await fake.get_schedule("tests.minute")
    retired = await fake.retire_schedule("tests.minute", profile.version, "test")
    repeated = await fake.retire_schedule("tests.minute", retired.profile.version, "test")
    assert retired.outcome == "retired"
    assert repeated.outcome == "already_retired"


async def test_fake_schedule_conflicts_match_typed_sql_reasons() -> None:
    fake = FakeTaskQClient(queues=("scheduled",))
    created = await fake.put_schedule("tests.conflict", _definition(), "test")
    changed = _definition().model_copy(update={"paused": True})

    try:
        await fake.put_schedule("tests.conflict", changed, "test")
    except Exception as error:
        assert getattr(error, "details", None) == {
            "reason": "schedule_mismatch",
            "current_version": created.profile.version,
        }
    else:
        raise AssertionError("identity-changing replay must conflict")
