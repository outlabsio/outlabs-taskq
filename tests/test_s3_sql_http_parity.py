"""T6 independent SQL/HTTP parity and durable-state oracles."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from taskq.http import (
    AsyncTaskqHttpClient,
    ClaimWaitHub,
    TaskqFacadeTransports,
    create_taskq_app,
    no_auth_for_tests,
)
from taskq.protocol import EnqueueCommand, HttpCommandName, JobDetail
from taskq.sql.transport import SqlTaskqTransport

pytestmark = pytest.mark.taskq_sql


def _mounted(taskq_app: FastAPI) -> FastAPI:
    host = FastAPI()
    host.mount("/taskq", taskq_app)
    return host


def _assert_job_matches_raw(job: JobDetail, row: Mapping[str, Any]) -> None:
    """Compare a read projection with a raw table oracle, not another adapter."""

    assert job.job_id == row["id"]
    assert job.queue == row["queue"]
    assert job.job_type == row["job_type"]
    assert job.status.value == row["status"]
    assert job.priority == row["priority"]
    assert job.failure_count == row["failure_count"]
    assert job.max_attempts == row["max_attempts"]
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
    assert job.payload == payload
    assert job.result == result


async def _run_contract_scenario(
    transport: SqlTaskqTransport | AsyncTaskqHttpClient,
    queue: str,
) -> tuple[list[str], JobDetail, object]:
    command = EnqueueCommand(
        queue=queue,
        job_type="tests.parity",
        payload={"value": "contract-vector"},
        idempotency_key="parity-key",
        max_attempts=3,
    )
    first = await transport.enqueue(command)
    duplicate = await transport.enqueue(command)
    claimed = await transport.claim(queue, "parity-worker", lease_seconds=30)
    assert len(claimed.jobs) == 1
    job = claimed.jobs[0]
    heartbeat = await transport.heartbeat(
        job.job_id,
        job.attempt_id,
        "parity-worker",
        lease_seconds=30,
        progress={"phase": "settling"},
    )
    completed = await transport.complete(
        job.job_id,
        job.attempt_id,
        "parity-worker",
        result={"ok": True},
    )
    replayed = await transport.complete(
        job.job_id,
        job.attempt_id,
        "parity-worker",
        result={"ok": True},
    )
    detail = await transport.get_job(
        job.job_id,
        include_payload=True,
        include_result=True,
        include_progress=True,
    )
    assert detail is not None
    if isinstance(transport, AsyncTaskqHttpClient):
        stats = await transport.command(
            HttpCommandName.GET_QUEUE_STATS,
            path_params={"queue": queue},
        )
    else:
        stats = await transport.get_queue_stats(queue)
    outcomes = [
        first.status.value,
        duplicate.status.value,
        claimed.state.value,
        "heartbeat_ok" if heartbeat.ok else "heartbeat_lost",
        completed.result.value,
        replayed.result.value,
    ]
    return outcomes, detail, stats


async def test_sql_and_live_http_share_outcomes_and_match_raw_state(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queues = ("parity_sql", "parity_http")
    for queue in queues:
        await operator.fetchrow(
            "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'parity-audit')",
            queue,
        )

    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    resources = TaskqFacadeTransports(
        producer=transport,
        runner=transport,
        observer=transport,
        authorization=transport,
        claim_wait_hub=ClaimWaitHub(),
    )
    app = _mounted(create_taskq_app(resources, authorizer=no_auth_for_tests()))
    asgi = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client = AsyncTaskqHttpClient(
        "http://test",
        bearer_token="parity-test-only",
        client=asgi,
        claim_wait_seconds=0,
    )
    try:
        sql_outcomes, sql_job, sql_stats = await _run_contract_scenario(transport, queues[0])
        http_outcomes, http_job, http_stats = await _run_contract_scenario(client, queues[1])
        assert (
            http_outcomes
            == sql_outcomes
            == [
                "created",
                "existed",
                "claimed",
                "heartbeat_ok",
                "ok",
                "already_settled",
            ]
        )

        for job in (sql_job, http_job):
            raw = await pg.fetchrow(
                "SELECT id, queue, job_type, status, priority, failure_count, "
                "max_attempts, payload, result FROM taskq.jobs WHERE id=$1",
                job.job_id,
            )
            assert raw is not None
            _assert_job_matches_raw(job, raw)

        # No snapshot tick has run: both public surfaces must preserve the
        # contract's honest empty-observer posture, which the raw function confirms.
        assert sql_stats == http_stats == []
        assert await pg.fetch("SELECT * FROM taskq.get_queue_stats($1)", queues[0]) == []
        assert await pg.fetch("SELECT * FROM taskq.get_queue_stats($1)", queues[1]) == []
    finally:
        await client.aclose()
        await asgi.aclose()
        await transport.aclose()


async def test_read_oracle_rejects_a_deliberately_mutated_projection(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    queue = "parity_mutation"
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'parity-audit')", queue
    )
    transport = SqlTaskqTransport.from_dsn(sqlalchemy_dsn)
    try:
        result = await transport.enqueue(
            EnqueueCommand(queue=queue, job_type="tests.parity", payload={"value": 1})
        )
        job = await transport.get_job(result.job_id, include_payload=True)
        raw = await pg.fetchrow(
            "SELECT id, queue, job_type, status, priority, failure_count, max_attempts, "
            "payload, result FROM taskq.jobs WHERE id=$1",
            result.job_id,
        )
        assert job is not None and raw is not None
        _assert_job_matches_raw(job, raw)
        mutated = job.model_copy(update={"priority": job.priority + 1})
        with pytest.raises(AssertionError):
            _assert_job_matches_raw(mutated, raw)
    finally:
        await transport.aclose()
