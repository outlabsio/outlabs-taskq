"""SQL contract 0.2.5 definition plus metadata-only continuation activation."""

from __future__ import annotations

import hashlib

import asyncpg
import pytest

from taskq.sql import discover_migrations
from taskq.sql.manifest import FUNCTIONS


EXPECTED_PREDECESSOR_CHECKSUMS = {
    "0001_initial.sql": "6b4a2c2514ebf481d21093f75e31b3678e0ec63dba455f91812fb5703c461c5c",
    "0002_contract_0_1_1.sql": "f1cd5d2d7cafa52d93143ef655b2b55e18bbe386aa8b31d298f3805a0c0783be",
    "0003_contract_0_1_2.sql": "378f46ba22efc79f3b543e2fd29dce0482f2edde8bcf04fcbac3eb782289adef",
    "0004_read_models.sql": "1daef07c90cd900818b13ba91a5e0937392333a2108d190db73f247fa8cf6a25",
    "0005_read_model_conformance.sql": "e5540af8be26520355ddfbaded7542622902d193a8f1eea56715a3a3dc40fdda",
    "0006_activate_ready_read_model.sql": "c63feb7bb50a26597d87dcb350882f25b0b9053865e31ddeb1c1b2a20cb10d8c",
    "0007_admission_reservations.sql": "99c76b0e2c787c0f72ace34b864d098cc1977a091ed635af0bda8510f3790696",
    "0008_followups.sql": "b1643b14111120287ec54a2e3c2c5b5e06ddfbbdba46e70bc73dbc349e3ee00e",
    "0009_workflows.sql": "20ae03a5d03261a9ffc438a0c0abd6284f674129aa666e282fcb4853e3cf5722",
    "0010_schedules.sql": "b85fff9b37ec3282bea06d3001057f48738731d95b10e7e2c8f56465537ebf4a",
    "0011_finite_projections.sql": "c521ebc91c3ef01e54836ac98b99898fd62fff4462ab326dfa42b7fc2fdb25a1",
    "0012_activate_finite_projections.sql": "db270ada964ff432e0389f71663c79755a9d0b58ccd6dccac8e52d56ec6bfbf5",
    "0013_workflow_page_composite_repair.sql": "bd8dcc10dc3667a60529315b358e4a3688a0ff92c134713f39260c5779a077c1",
    "0014_worker_presence_projection.sql": "37d4403302e65aa0c6f38a742f219e165477de39e2e2b6dcf7c10ace4d130ebf",
    "0015_activate_worker_presence.sql": "df1944694b75787679f28d6f8687848c1e4a3beb20eea3e6d1fffbd944f63b00",
    "0016_workflow_continuations.sql": "56ebe6c416af19d9c1c659402d0409fd675315748382f423aa58d4bcadc8d552",
}


def test_0017_follows_byte_immutable_0016_definition() -> None:
    migrations = discover_migrations()
    by_name = {migration.filename: migration for migration in migrations}
    assert "0017_activate_workflow_continuations.sql" in by_name
    for migration in migrations:
        if migration.filename not in EXPECTED_PREDECESSOR_CHECKSUMS:
            continue
        assert (
            EXPECTED_PREDECESSOR_CHECKSUMS[migration.filename]
            == hashlib.sha256(migration.sql.encode()).hexdigest()
        )


def test_0_2_5_machine_surface_has_exact_wfc_overloads() -> None:
    assert {
        "taskq._reserve_workflow_members(uuid,integer,text)",
        "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)",
        "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)",
        "taskq.create_workflow(text,text,jsonb,text[],text,integer,text)",
    } <= set(FUNCTIONS)


@pytest.mark.taskq_sql
async def test_0017_metadata_activates_workflow_continuations(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
) -> None:
    assert (
        await pg.fetchval("SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'")
        == "0.5.1"
    )
    assert await pg.fetchval("SELECT taskq.has_capability('workflow_continuations')") is True
    assert (
        await pg.fetchval(
            "SELECT (value->'active') ? 'workflow_continuations' "
            "FROM taskq.meta WHERE key='capabilities'"
        )
        is True
    )
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue('active_wfc','{}'::jsonb,'test')")
    created = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow($1,$2,$3,$4,$5,$6,$7)",
        "active-policy",
        "dag",
        "{}",
        ["active_wfc"],
        "test",
        10,
        "a" * 64,
    )
    assert created is not None and created["outcome"] == "created"


@pytest.mark.taskq_sql
async def test_legacy_create_is_budgeted_and_reserved_namespace_is_closed(
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    pg: asyncpg.Connection,
) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue('wfc_legacy','{}'::jsonb,'test')")
    workflow = await producer.fetchrow(
        "SELECT * FROM taskq.create_workflow("
        "'wfc-legacy','dag','{}'::jsonb,ARRAY['wfc_legacy'],'test')"
    )
    assert workflow is not None
    row = await pg.fetchrow(
        "SELECT w.member_limit,w.continuation_policy_hash,c.admitted_total "
        "FROM taskq.workflows AS w JOIN taskq.workflow_member_counts AS c "
        "ON c.workflow_id=w.id WHERE w.id=$1",
        workflow["workflow_id"],
    )
    assert row is not None
    assert tuple(row) == (10_000, None, 0)

    with pytest.raises(asyncpg.PostgresError) as reserved:
        await producer.fetchrow(
            "SELECT * FROM taskq.enqueue("
            "'wfc_legacy','test.job','{}'::jsonb,p_idempotency_key=>'chain:held')"
        )
    assert reserved.value.sqlstate == "TQ422"
    assert reserved.value.detail == '{"reason":"reserved_idempotency_namespace"}'


@pytest.mark.taskq_sql
async def test_legacy_claim_body_is_null_policy_only_and_new_frontier_is_bounded(
    pg: asyncpg.Connection,
) -> None:
    old_definition = await pg.fetchval(
        "SELECT pg_get_functiondef('taskq._claim_jobs_unattested(text,text,integer,text[],"
        "integer,text,uuid,boolean)'::regprocedure)"
    )
    new_definition = await pg.fetchval(
        "SELECT pg_get_functiondef('taskq._claim_jobs_unattested(text,text,integer,text[],"
        "integer,text,uuid,text[],boolean)'::regprocedure)"
    )
    assert "j.continuation_policy_hash IS NULL" in old_definition
    assert "unnest(array_prepend(NULL::text,v_hashes))" in new_definition
    assert new_definition.count("LIMIT v_batch") == 2
    assert "cardinality(v_hashes) > 32" in new_definition
