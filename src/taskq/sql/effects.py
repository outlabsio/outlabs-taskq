"""Borrowed-transaction adapter for the trusted host-effect fence."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from taskq.errors import TaskqInternalError, taskq_error_from_exception


@dataclass(frozen=True, slots=True)
class WorkflowEffectCounts:
    """Exact status counts for the admitted attempt's workflow."""

    blocked: int
    queued: int
    running: int
    succeeded: int
    failed: int
    cancelled: int


@dataclass(frozen=True, slots=True)
class ActiveEffectAttempt:
    """Admitted identity returned while the caller holds the TaskQ job lock."""

    payload: dict[str, Any]
    workflow_id: UUID | None
    workflow_counts: WorkflowEffectCounts | None


async def lock_active_effect_attempt(
    connection: AsyncConnection,
    *,
    job_id: UUID,
    attempt_id: UUID,
    worker_id: str,
    queue: str,
    job_type: str,
) -> ActiveEffectAttempt | None:
    """Lock an exact live attempt inside the caller-owned transaction.

    ``connection`` must already participate in the domain transaction whose
    effect is being fenced. The row lock is released only when that transaction
    commits or rolls back.
    """

    try:
        result = await connection.execute(
            text(
                """
                SELECT *
                FROM taskq.lock_active_effect_attempt(
                    :job_id, :attempt_id, :worker_id, :queue, :job_type
                )
                """
            ),
            {
                "job_id": job_id,
                "attempt_id": attempt_id,
                "worker_id": worker_id,
                "queue": queue,
                "job_type": job_type,
            },
        )
        row = result.mappings().first()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise taskq_error_from_exception(exc) from exc
    if row is None:
        return None
    payload = row["payload"]
    workflow_id = row["workflow_id"]
    workflow_counts = row["workflow_counts"]
    if not isinstance(payload, Mapping) or (
        workflow_id is not None and not isinstance(workflow_id, UUID)
    ):
        raise TaskqInternalError()
    parsed_counts: WorkflowEffectCounts | None = None
    if workflow_counts is not None:
        if workflow_id is None or not isinstance(workflow_counts, Mapping):
            raise TaskqInternalError()
        expected = {"blocked", "queued", "running", "succeeded", "failed", "cancelled"}
        if set(workflow_counts) != expected or any(
            not isinstance(workflow_counts[key], int) or workflow_counts[key] < 0
            for key in expected
        ):
            raise TaskqInternalError()
        parsed_counts = WorkflowEffectCounts(**{key: workflow_counts[key] for key in expected})
    return ActiveEffectAttempt(
        payload=dict(payload),
        workflow_id=workflow_id,
        workflow_counts=parsed_counts,
    )


__all__ = [
    "ActiveEffectAttempt",
    "WorkflowEffectCounts",
    "lock_active_effect_attempt",
]
