from __future__ import annotations

from uuid import uuid4

from taskq.protocol import JobDetail


def test_job_detail_accepts_the_facades_omitted_nullable_projection_fields() -> None:
    job_id = uuid4()

    detail = JobDetail.model_validate(
        {
            "job_id": str(job_id),
            "queue": "qdarte_pilot",
            "job_type": "qdarte.cluster_research.pilot",
            "status": "queued",
            "priority": 100,
            "attempt_count": 0,
            "failure_count": 0,
            "max_attempts": 1,
            "created_at": "2026-07-21T20:00:00Z",
            "scheduled_at": "2026-07-21T20:00:00Z",
            "updated_at": "2026-07-21T20:00:00Z",
        }
    )

    assert detail.job_id == job_id
    assert detail.outcome is detail.started_at is detail.finished_at is None
