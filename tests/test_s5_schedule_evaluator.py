"""Deterministic native schedule calendar and catch-up evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from taskq import ScheduleDefinition, TaskqValidationError
from uuid import UUID

from taskq.schedules import evaluate_schedule, smear_offset_seconds


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


@pytest.mark.parametrize("policy", ["fire_once", "fire_all"])
def test_dst_gap_and_fold_remain_single_instants_under_firing_policies(policy: str) -> None:
    gap = evaluate_schedule(
        recurrence={
            "kind": "cron",
            "expression": "30 2 * * *",
            "timezone": "America/New_York",
        },
        catchup_policy=policy,
        max_catchup=3,
        initialized=True,
        next_fire_at=_utc(2025, 3, 8, 7, 30),
        as_of=_utc(2025, 3, 10, 7),
    )
    assert _utc(2025, 3, 9, 7, 30) not in gap.occurrences
    assert gap.occurrences[-1] == _utc(2025, 3, 10, 6, 30)

    fold = evaluate_schedule(
        recurrence={
            "kind": "cron",
            "expression": "30 1 * * *",
            "timezone": "America/New_York",
        },
        catchup_policy=policy,
        max_catchup=3,
        initialized=True,
        next_fire_at=_utc(2025, 11, 1, 5, 30),
        as_of=_utc(2025, 11, 2, 7),
    )
    assert fold.occurrences.count(_utc(2025, 11, 2, 5, 30)) == 1
    assert _utc(2025, 11, 2, 6, 30) not in fold.occurrences


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


def test_smear_offset_is_deterministic_positive_and_bounded() -> None:
    a = UUID(int=1234567890)
    assert smear_offset_seconds(a, None) == 0
    assert smear_offset_seconds(a, 0) == 0
    off = smear_offset_seconds(a, 300)
    assert 0 <= off < 300
    assert off == smear_offset_seconds(a, 300)  # stable across calls/processes
    assert smear_offset_seconds(UUID(int=9876543210), 300) != off  # distinct schedules differ


def test_smear_de_aligns_co_cron_schedules_and_stays_drift_free() -> None:
    cron = {"kind": "cron", "expression": "0 * * * *", "timezone": "UTC"}
    due = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    as_of = datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)
    hour_13 = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)

    offsets = {
        UUID(int=1234567890): None,
        UUID(int=9876543210): None,
    }
    fires = {}
    for sid in offsets:
        off = smear_offset_seconds(sid, 300)
        offsets[sid] = off
        ev = evaluate_schedule(
            recurrence=cron,
            catchup_policy="fire_once",
            max_catchup=1,
            initialized=True,
            next_fire_at=due + timedelta(seconds=off),
            as_of=as_of + timedelta(seconds=off),
            smear_offset_seconds=off,
        )
        # each lands on its own base lattice point (13:00) shifted by its constant
        assert ev.next_fire_at == hour_13 + timedelta(seconds=off)
        fires[sid] = ev.next_fire_at
    # the whole point: two schedules sharing one cron no longer fire together
    assert len(set(fires.values())) == 2

    # drift-free: re-evaluating from a smeared next keeps the same constant offset
    sid = UUID(int=1234567890)
    off = offsets[sid]
    n1 = fires[sid]
    n2 = evaluate_schedule(
        recurrence=cron,
        catchup_policy="fire_once",
        max_catchup=1,
        initialized=True,
        next_fire_at=n1,
        as_of=n1 + timedelta(seconds=1),
        smear_offset_seconds=off,
    )
    assert n2.next_fire_at == datetime(2026, 1, 1, 14, 0, tzinfo=UTC) + timedelta(seconds=off)


def test_smear_fire_all_shifts_the_whole_interval_lattice() -> None:
    rec = {"kind": "interval", "interval_seconds": 3600}
    off = 90
    ev = evaluate_schedule(
        recurrence=rec,
        catchup_policy="fire_all",
        max_catchup=100,
        initialized=True,
        next_fire_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + timedelta(seconds=off),
        as_of=datetime(2026, 1, 1, 14, 5, tzinfo=UTC),
        smear_offset_seconds=off,
    )
    # every occurrence is the base hourly lattice shifted by the same 90s
    assert [o.minute for o in ev.occurrences] == [1, 1, 1]
    assert all(o.second == 30 for o in ev.occurrences)


def test_smear_zero_is_exact_passthrough() -> None:
    rec = {"kind": "interval", "interval_seconds": 3600}
    due = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    as_of = datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)
    kwargs = dict(
        recurrence=rec,
        catchup_policy="fire_once",
        max_catchup=1,
        initialized=True,
        next_fire_at=due,
        as_of=as_of,
    )
    assert evaluate_schedule(**kwargs, smear_offset_seconds=0) == evaluate_schedule(**kwargs)
