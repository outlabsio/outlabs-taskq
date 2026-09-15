"""Borrowed-transaction adapter for the trusted host-effect fence."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True, slots=True)
class TerminalEffectJob:
    """Admitted terminal identity held under the caller's transaction row lock."""

    status: str
    outcome: str | None
    finished_at: datetime
    payload: dict[str, Any]
    workflow_id: UUID | None


async def lock_terminal_effect_job(
    connection: AsyncConnection,
    *,
    job_id: UUID,
    queue: str,
    job_type: str,
    expected_environment: str,
    expected_installation_id: UUID,
    allow_production: bool = False,
) -> TerminalEffectJob | None:
    """Fence a host reconciliation against redrive/retention, never commit for it.

    A trusted producer must bind the returned admitted payload to its own tenant,
    generation and domain checkpoint before writing. This is SQL-only: it does
    not expose payloads to runners, observers or HTTP callers. The caller must
    already own a transaction; release of that transaction releases the lock.
    """
    if not connection.in_transaction():
        raise ValueError("terminal effect fence requires a caller-owned transaction")
    try:
        result = await connection.execute(
            text(
                "SELECT * FROM taskq.lock_terminal_effect_job("
                ":job_id,:queue,:job_type,:expected_environment,:expected_installation_id,:allow_production)"
            ),
            {
                "job_id": job_id,
                "queue": queue,
                "job_type": job_type,
                "expected_environment": expected_environment,
                "expected_installation_id": expected_installation_id,
                "allow_production": allow_production,
            },
        )
        row = result.mappings().first()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise taskq_error_from_exception(exc) from exc
    if row is None:
        return None
    if (
        row["status"] not in {"succeeded", "failed", "cancelled"}
        or not isinstance(row["finished_at"], datetime)
        or not isinstance(row["payload"], Mapping)
        or (row["outcome"] is not None and not isinstance(row["outcome"], str))
        or (row["workflow_id"] is not None and not isinstance(row["workflow_id"], UUID))
    ):
        raise TaskqInternalError()
    return TerminalEffectJob(
        status=row["status"],
        outcome=row["outcome"],
        finished_at=row["finished_at"],
        payload=dict(row["payload"]),
        workflow_id=row["workflow_id"],
    )


__all__ = [
    "TerminalEffectJob",
    "lock_terminal_effect_job",
    "ActiveEffectAttempt",
    "WorkflowEffectCounts",
    "lock_active_effect_attempt",
]
