"""SQL contract 0.6.8 — provider flow identity survives continuations."""

from __future__ import annotations

import json

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql

POLICY = "f" * 64
FLOW_KEY = "provider:scrapfly:test"


async def test_policy_member_followup_inherits_parent_flow_key(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "continuation_flow"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'test')", queue)
    workflow = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,ARRAY[$2],$3,2,$4)",
        "continuation-flow-inheritance",
        queue,
        "test",
        POLICY,
    )
    assert workflow is not None
    workflow_id = workflow["workflow_id"]
    root = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,$2,'{}'::jsonb,p_workflow_id=>$3,"
        "p_step_key=>'root',p_flow_key=>$4)",
        queue,
        "tests.flow_root",
        workflow_id,
        FLOW_KEY,
    )
    assert root is not None
    await producer.fetchrow("SELECT * FROM taskq.seal_workflow($1,'test')", workflow_id)
    claimed = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,NULL,ARRAY[$3],false)",
        queue,
        "flow-worker",
        POLICY,
    )
    assert claimed is not None and claimed["state"] == "claimed"
    job = claimed["jobs"][0]
    followups = [
        {
            "step": "page-2",
            "job_type": "tests.flow_root",
            "payload": {"page": 2},
            "workflow_member": True,
        }
    ]
    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,'{}'::jsonb,NULL,$4::jsonb,$5)",
        job["job_id"],
        job["attempt_id"],
        "flow-worker",
        json.dumps(followups),
        POLICY,
    )
    assert settled is not None and settled["result"] == "ok"
    child_flow = await pg.fetchval(
        "SELECT flow_key FROM taskq.jobs WHERE parent_job_id=$1",
        job["job_id"],
    )
    assert child_flow == FLOW_KEY
    assert await pg.fetchval("SELECT taskq.has_capability('continuation_flow_inheritance')") is True


async def test_detached_followup_does_not_inherit_flow_key(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    queue = "detached_flow"
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'test')", queue)
    parent = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,$2,'{}'::jsonb,p_flow_key=>$3)",
        queue,
        "tests.detached_root",
        FLOW_KEY,
    )
    assert parent is not None
    claimed = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2)", queue, "detached-worker"
    )
    assert claimed is not None and claimed["state"] == "claimed"
    job = claimed["jobs"][0]
    settled = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3,p_followups=>$4::jsonb)",
        job["job_id"],
        job["attempt_id"],
        "detached-worker",
        json.dumps([{"step": "child", "job_type": "tests.detached_child"}]),
    )
    assert settled is not None and settled["result"] == "ok"
    child_flow = await pg.fetchval(
        "SELECT flow_key FROM taskq.jobs WHERE parent_job_id=$1",
        job["job_id"],
    )
    assert child_flow is None
