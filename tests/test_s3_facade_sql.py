"""Live PostgreSQL proof for the mounted S3-02 facade path."""

from __future__ import annotations

from uuid import UUID

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from taskq.http import ClaimWaitHub, TaskqFacadeTransports, create_taskq_app, no_auth_for_tests
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


async def test_live_asgi_enqueue_claim_presence_and_fenced_complete(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue('s3_live', '{}'::jsonb, 's3-test')")
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    resources = TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
    )
    app = _mounted(create_taskq_app(resources, authorizer=no_auth_for_tests()))
    headers = {"Taskq-Protocol-Version": "1"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            enqueued = await client.post(
                "/taskq/v1/queues/s3_live/jobs",
                headers=headers,
                json={
                    "job_type": "tests.echo",
                    "payload": {"value": "live"},
                    "idempotency_key": "s3-live-1",
                },
            )
            assert enqueued.status_code == 201
            job_id = UUID(enqueued.json()["data"]["job_id"])

            claimed = await client.post(
                "/taskq/v1/queues/s3_live/claims",
                headers=headers,
                json={"worker_id": "s3-worker", "wait_seconds": 0},
            )
            assert claimed.status_code == 200
            assert claimed.json()["outcome"] == "claimed"
            wire_job = claimed.json()["data"]["jobs"][0]
            assert UUID(wire_job["job_id"]) == job_id
            attempt_id = UUID(wire_job["attempt_id"])
            lease_before = await pg.fetchval(
                "SELECT lease_expires_at FROM taskq.jobs WHERE id = $1", job_id
            )

            presence = await client.post(
                "/taskq/v1/workers/heartbeat",
                headers=headers,
                json={"worker_id": "s3-worker", "queues": ["s3_live"]},
            )
            assert presence.json()["outcome"] == "continue"
            lease_after = await pg.fetchval(
                "SELECT lease_expires_at FROM taskq.jobs WHERE id = $1", job_id
            )
            assert lease_after == lease_before

            completed = await client.post(
                f"/taskq/v1/jobs/{job_id}/complete",
                headers=headers,
                json={
                    "attempt_id": str(attempt_id),
                    "worker_id": "s3-worker",
                    "result": {"ok": True},
                },
            )
            assert completed.status_code == 200
            assert completed.json()["outcome"] == "ok"
            assert "attempt_id" not in completed.text
            row = await pg.fetchrow("SELECT status, result FROM taskq.jobs WHERE id = $1", job_id)
            assert row is not None
            assert row["status"] == "succeeded"
    finally:
        await transport.aclose()


async def test_live_asgi_oversized_multibyte_failure_is_settled_and_byte_bounded(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "s3_diagnostic"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 's3-test')", queue)
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    resources = TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
    )
    app = _mounted(create_taskq_app(resources, authorizer=no_auth_for_tests()))
    headers = {"Taskq-Protocol-Version": "1"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            enqueued = await client.post(
                f"/taskq/v1/queues/{queue}/jobs",
                headers=headers,
                json={"job_type": "tests.failure", "payload": {}},
            )
            job_id = UUID(enqueued.json()["data"]["job_id"])
            claimed = await client.post(
                f"/taskq/v1/queues/{queue}/claims",
                headers=headers,
                json={"worker_id": "s3-worker", "wait_seconds": 0},
            )
            job = claimed.json()["data"]["jobs"][0]

            failed = await client.post(
                f"/taskq/v1/jobs/{job_id}/fail",
                headers=headers,
                json={
                    "attempt_id": job["attempt_id"],
                    "worker_id": "s3-worker",
                    "error": "é" * 4_000,
                    "retryable": False,
                },
            )
            assert failed.status_code == 200
            assert failed.json()["outcome"] == "dead"
            row = await pg.fetchrow(
                "SELECT status, octet_length(error) AS bytes FROM taskq.jobs WHERE id = $1",
                job_id,
            )
            assert row is not None
            assert dict(row) == {"status": "failed", "bytes": 2048}
    finally:
        await transport.aclose()
