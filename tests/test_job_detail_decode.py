"""Regression vectors for additive nullable job projections."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from taskq.protocol import JobDetail, JobListItem, JobStatus


def test_omitted_nullable_job_fields_decode_as_none() -> None:
    timestamp = datetime(2026, 7, 21, tzinfo=UTC)
    common = {
        "job_id": uuid4(),
        "job_type": "qdarte.cluster_research.pilot",
        "status": JobStatus.SUCCEEDED,
        "priority": 0,
        "attempt_count": 1,
        "failure_count": 0,
        "max_attempts": 1,
        "created_at": timestamp,
        "scheduled_at": timestamp,
        "updated_at": timestamp,
    }

    detail = JobDetail.model_validate({"queue": "qdarte_pilot", **common})
    item = JobListItem.model_validate(common)

    assert (detail.outcome, detail.started_at, detail.finished_at) == (None, None, None)
    assert (item.outcome, item.started_at, item.finished_at) == (None, None, None)
