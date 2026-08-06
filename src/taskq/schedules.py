"""Deterministic calendar evaluation for database-stamped schedule claims."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import re
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from taskq.errors import TaskqValidationError

_CRON_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_CRON_PART_RE = re.compile(r"^(?:\*|[0-9]+)(?:-[0-9]+)?(?:/[1-9][0-9]*)?$")


@dataclass(frozen=True, slots=True)
class ScheduleEvaluation:
    occurrences: tuple[datetime, ...]
    next_fire_at: datetime


def validate_cron(expression: str, timezone: str) -> None:
    """Validate the closed numeric five-field grammar and IANA zone."""

    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expression must contain exactly five fields")
    for field, (minimum, maximum) in zip(fields, _CRON_BOUNDS, strict=True):
        for part in field.split(","):
            if not _CRON_PART_RE.fullmatch(part):
                raise ValueError("cron uses unsupported syntax")
            base, _, step = part.partition("/")
            if step and int(step) <= 0:
                raise ValueError("cron step must be positive")
            if base == "*":
                continue
            start_text, separator, end_text = base.partition("-")
            start = int(start_text)
            end = int(end_text) if separator else start
            if start < minimum or start > maximum or end < minimum or end > maximum:
                raise ValueError("cron value is outside its field bounds")
            if separator and start > end:
                raise ValueError("cron ranges must be ascending")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown IANA timezone") from exc


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TaskqValidationError(details={"field": field})
    return value.astimezone(UTC)


def _resolved_wall_time(local_naive: datetime, zone: ZoneInfo) -> datetime | None:
    """Resolve a wall minute to the earlier fold, or skip a nonexistent gap."""

    candidate = local_naive.replace(tzinfo=zone, fold=0)
    roundtrip = candidate.astimezone(UTC).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != local_naive:
        return None
    return candidate.astimezone(UTC)


def _cron_next(expression: str, zone: ZoneInfo, after: datetime) -> datetime:
    local = after.astimezone(zone).replace(tzinfo=None)
    iterator = croniter(expression, local, day_or=True)
    for _ in range(370):
        wall = iterator.get_next(datetime)
        resolved = _resolved_wall_time(wall, zone)
        if resolved is not None and resolved > after:
            return resolved
    raise TaskqValidationError(details={"field": "recurrence"})


def _cron_latest(expression: str, zone: ZoneInfo, at_or_before: datetime) -> datetime:
    local = at_or_before.astimezone(zone).replace(tzinfo=None) + timedelta(microseconds=1)
    iterator = croniter(expression, local, day_or=True)
    for _ in range(370):
        wall = iterator.get_prev(datetime)
        resolved = _resolved_wall_time(wall, zone)
        if resolved is not None and resolved <= at_or_before:
            return resolved
    raise TaskqValidationError(details={"field": "recurrence"})


def _next(recurrence: Mapping[str, Any], after: datetime) -> datetime:
    kind = recurrence.get("kind")
    if kind == "interval":
        seconds = recurrence.get("interval_seconds")
        if (
            not isinstance(seconds, int)
            or isinstance(seconds, bool)
            or not 60 <= seconds <= 31_536_000
        ):
            raise TaskqValidationError(details={"field": "recurrence"})
        return after + timedelta(seconds=seconds)
    if kind == "cron":
        expression = recurrence.get("expression")
        timezone = recurrence.get("timezone")
        if not isinstance(expression, str) or not isinstance(timezone, str):
            raise TaskqValidationError(details={"field": "recurrence"})
        try:
            validate_cron(expression, timezone)
        except ValueError as exc:
            raise TaskqValidationError(details={"field": "recurrence"}, cause=exc) from exc
        return _cron_next(expression, ZoneInfo(timezone), after)
    raise TaskqValidationError(details={"field": "recurrence"})


def smear_offset_seconds(schedule_id: UUID, smear_seconds: int | None) -> int:
    """Deterministic per-schedule firing offset, 0 when smear is off.

    A stable, process-independent hash of the schedule id modulo ``smear_seconds``
    (positive, in ``[0, smear_seconds)``). Two schedules sharing a cron expression
    get different constant offsets, so their firings de-align instead of stampeding
    on the same instant; a single schedule always gets the SAME offset, so its
    cadence stays drift-free (the whole lattice shifts by one constant, never
    accumulates). Uses blake2b, not the salted builtin ``hash()``, so the offset
    is identical across processes and restarts.
    """

    if not smear_seconds or smear_seconds <= 0:
        return 0
    digest = hashlib.blake2b(schedule_id.bytes, digest_size=8).digest()
    return int.from_bytes(digest, "big") % smear_seconds


def evaluate_schedule(
    *,
    recurrence: Mapping[str, Any],
    catchup_policy: Literal["skip", "fire_once", "fire_all"] | str,
    max_catchup: int,
    initialized: bool,
    next_fire_at: datetime,
    as_of: datetime,
    smear_offset_seconds: int = 0,
) -> ScheduleEvaluation:
    """Evaluate one claim using only its database-provided instants.

    ``smear_offset_seconds`` (from :func:`smear_offset_seconds`) shifts the
    schedule's whole recurrence lattice by one per-schedule constant: the inputs
    are moved into the un-smeared base frame, the pure evaluation runs there, and
    every output instant is shifted back. This is exact for both interval and
    cron lattices (a constant time translation preserves ordering, the
    due<=as_of relationship, and 'next after last occurrence'), and drift-free
    because the stored next_fire_at is always base_point + offset, so successive
    claims recover the same base lattice. 0 (the default) is a pure passthrough.
    """

    if smear_offset_seconds:
        off = timedelta(seconds=smear_offset_seconds)
        base = _evaluate_base(
            recurrence=recurrence,
            catchup_policy=catchup_policy,
            max_catchup=max_catchup,
            initialized=initialized,
            next_fire_at=next_fire_at - off,
            as_of=as_of - off,
        )
        return ScheduleEvaluation(
            occurrences=tuple(instant + off for instant in base.occurrences),
            next_fire_at=base.next_fire_at + off,
        )
    return _evaluate_base(
        recurrence=recurrence,
        catchup_policy=catchup_policy,
        max_catchup=max_catchup,
        initialized=initialized,
        next_fire_at=next_fire_at,
        as_of=as_of,
    )


def _evaluate_base(
    *,
    recurrence: Mapping[str, Any],
    catchup_policy: Literal["skip", "fire_once", "fire_all"] | str,
    max_catchup: int,
    initialized: bool,
    next_fire_at: datetime,
    as_of: datetime,
) -> ScheduleEvaluation:

    due = _aware_utc(next_fire_at, "next_fire_at")
    cutoff = _aware_utc(as_of, "as_of")
    if due > cutoff or not 1 <= max_catchup <= 100:
        raise TaskqValidationError(details={"field": "schedule_claim"})
    if not initialized or catchup_policy == "skip":
        cursor = cutoff
        return ScheduleEvaluation(occurrences=(), next_fire_at=_next(recurrence, cursor))
    if catchup_policy == "fire_once":
        if recurrence.get("kind") == "interval":
            seconds = recurrence.get("interval_seconds")
            if not isinstance(seconds, int) or isinstance(seconds, bool):
                raise TaskqValidationError(details={"field": "recurrence"})
            missed = int((cutoff - due).total_seconds() // seconds)
            latest = due + timedelta(seconds=missed * seconds)
        else:
            expression = recurrence.get("expression")
            timezone = recurrence.get("timezone")
            if not isinstance(expression, str) or not isinstance(timezone, str):
                raise TaskqValidationError(details={"field": "recurrence"})
            try:
                validate_cron(expression, timezone)
            except ValueError as exc:
                raise TaskqValidationError(details={"field": "recurrence"}, cause=exc) from exc
            latest = _cron_latest(expression, ZoneInfo(timezone), cutoff)
        if latest < due:
            raise TaskqValidationError(details={"field": "schedule_claim"})
        return ScheduleEvaluation(
            occurrences=(latest,),
            next_fire_at=_next(recurrence, cutoff),
        )
    if catchup_policy != "fire_all":
        raise TaskqValidationError(details={"field": "catchup_policy"})
    occurrences: list[datetime] = []
    cursor = due
    while cursor <= cutoff and len(occurrences) < max_catchup:
        occurrences.append(cursor)
        cursor = _next(recurrence, cursor)
    return ScheduleEvaluation(occurrences=tuple(occurrences), next_fire_at=cursor)


__all__ = [
    "ScheduleEvaluation",
    "evaluate_schedule",
    "smear_offset_seconds",
    "validate_cron",
]
