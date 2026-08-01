"""Deterministic native schedule calendar and catch-up evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from taskq import ScheduleDefinition, TaskqValidationError
from taskq.schedules import evaluate_schedule


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_interval_compile_first_skip_and_bounded_fire_all() -> None:
    recurrence = {"kind": "interval", "interval_seconds": 60}
    initial = evaluate_schedule(
        recurrence=recurrence,
        catchup_policy="fire_all",
        max_catchup=3,
        initialized=False,
        next_fire_at=_utc(2026, 1, 1),
        as_of=_utc(2026, 1, 1, 0, 10),
    )
    assert initial.occurrences == ()
    assert initial.next_fire_at == _utc(2026, 1, 1, 0, 11)

    skipped = evaluate_schedule(
        recurrence=recurrence,
        catchup_policy="skip",
        max_catchup=3,
        initialized=True,
        next_fire_at=_utc(2026, 1, 1),
        as_of=_utc(2026, 1, 1, 0, 10),
    )
    assert skipped == initial

    fired = evaluate_schedule(
        recurrence=recurrence,
        catchup_policy="fire_all",
        max_catchup=3,
        initialized=True,
        next_fire_at=_utc(2026, 1, 1),
        as_of=_utc(2026, 1, 1, 0, 10),
    )
    assert fired.occurrences == (
        _utc(2026, 1, 1),
        _utc(2026, 1, 1, 0, 1),
        _utc(2026, 1, 1, 0, 2),
    )
    assert fired.next_fire_at == _utc(2026, 1, 1, 0, 3)


def test_interval_fire_once_uses_latest_due_without_iteration() -> None:
    result = evaluate_schedule(
        recurrence={"kind": "interval", "interval_seconds": 300},
        catchup_policy="fire_once",
        max_catchup=1,
        initialized=True,
        next_fire_at=_utc(2020, 1, 1),
        as_of=_utc(2026, 1, 1, 0, 2),
    )
    assert result.occurrences == (_utc(2026, 1, 1),)
    assert result.next_fire_at == _utc(2026, 1, 1, 0, 7)


def test_cron_spring_gap_is_skipped_and_fall_fold_uses_earlier_instant_once() -> None:
    recurrence = {
        "kind": "cron",
        "expression": "30 2 * * *",
        "timezone": "America/New_York",
    }
    gap = evaluate_schedule(
        recurrence=recurrence,
        catchup_policy="skip",
        max_catchup=1,
        initialized=True,
        next_fire_at=_utc(2025, 3, 8, 7, 30),
        as_of=_utc(2025, 3, 8, 8),
    )
    assert gap.next_fire_at == _utc(2025, 3, 10, 6, 30)

    folded = {
        "kind": "cron",
        "expression": "30 1 * * *",
        "timezone": "America/New_York",
    }
    first = evaluate_schedule(
        recurrence=folded,
        catchup_policy="skip",
        max_catchup=1,
        initialized=True,
        next_fire_at=_utc(2025, 11, 1, 5, 30),
        as_of=_utc(2025, 11, 1, 6),
    )
    assert first.next_fire_at == _utc(2025, 11, 2, 5, 30)
    after_first_fold = evaluate_schedule(
        recurrence=folded,
        catchup_policy="skip",
        max_catchup=1,
        initialized=True,
        next_fire_at=first.next_fire_at,
        as_of=first.next_fire_at,
    )
    assert after_first_fold.next_fire_at == _utc(2025, 11, 3, 6, 30)


def test_cron_day_of_month_and_week_are_or_and_fire_once_is_latest() -> None:
    recurrence = {
        "kind": "cron",
        "expression": "0 0 1 * 1",
        "timezone": "UTC",
    }
    result = evaluate_schedule(
        recurrence=recurrence,
        catchup_policy="fire_once",
        max_catchup=1,
        initialized=True,
        next_fire_at=_utc(2026, 1, 1),
        as_of=_utc(2026, 1, 10),
    )
    assert result.occurrences == (_utc(2026, 1, 5),)
    assert result.next_fire_at == _utc(2026, 1, 12)


@pytest.mark.parametrize(
    "recurrence",
    [
        {"kind": "cron", "expression": "0 0 * *", "timezone": "UTC"},
        {"kind": "cron", "expression": "@daily", "timezone": "UTC"},
        {"kind": "cron", "expression": "0 0 L * *", "timezone": "UTC"},
        {"kind": "cron", "expression": "60 0 * * *", "timezone": "UTC"},
        {"kind": "cron", "expression": "0 0 * * *", "timezone": "Not/AZone"},
    ],
)
def test_closed_cron_model_rejects_extensions_and_unknown_zones(
    recurrence: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ScheduleDefinition.model_validate(
            {
                "target": {
                    "kind": "job",
                    "queue": "scheduled",
                    "job_type": "tests.echo",
                },
                "recurrence": recurrence,
                "catchup_policy": "skip",
                "max_catchup": 1,
            }
        )


def test_evaluator_rejects_naive_or_not_due_claim_instants() -> None:
    with pytest.raises(TaskqValidationError):
        evaluate_schedule(
            recurrence={"kind": "interval", "interval_seconds": 60},
            catchup_policy="skip",
            max_catchup=1,
            initialized=True,
            next_fire_at=datetime(2026, 1, 1),
            as_of=_utc(2026, 1, 1),
        )
    with pytest.raises(TaskqValidationError):
        evaluate_schedule(
            recurrence={"kind": "interval", "interval_seconds": 60},
            catchup_policy="skip",
            max_catchup=1,
            initialized=True,
            next_fire_at=_utc(2026, 1, 1) + timedelta(seconds=1),
            as_of=_utc(2026, 1, 1),
        )
