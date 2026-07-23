"""R3-F01 exact-manifest verification and rollback-only corruption probes."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import verify
from taskq.sql.manifest import FUNCTIONS


@dataclass(frozen=True, slots=True)
class Corruption:
    name: str
    statements: tuple[str, ...]
    failed_check: str
    detail: str


CASES = (
    Corruption(
        "missing_function",
        ("DROP FUNCTION taskq.metrics()",),
        "function_catalog",
        "missing function 'taskq.metrics()'",
    ),
    Corruption(
        "extra_hardened_function",
        (
            """
            CREATE FUNCTION taskq.verify_extra() RETURNS integer
            LANGUAGE sql SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp
            AS 'SELECT 1'
            """,
            "ALTER FUNCTION taskq.verify_extra() OWNER TO taskq_owner",
            "REVOKE EXECUTE ON FUNCTION taskq.verify_extra() FROM PUBLIC",
        ),
        "function_catalog",
        "unexpected function 'taskq.verify_extra()'",
    ),
    Corruption(
        "argument_default",
        (
            """
            CREATE OR REPLACE FUNCTION taskq.truncate_utf8(
                p_value text, p_max_bytes integer DEFAULT 9
            ) RETURNS text
            LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp
            AS 'BEGIN RETURN p_value; END'
            """,
        ),
        "function_catalog",
        "arguments is",
    ),
    Corruption(
        "result_shape",
        (
            "DROP FUNCTION taskq.get_contract_meta()",
            """
            CREATE FUNCTION taskq.get_contract_meta() RETURNS text
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp
            AS 'SELECT ''0.1.1''::text'
            """,
            "ALTER FUNCTION taskq.get_contract_meta() OWNER TO taskq_owner",
            "REVOKE EXECUTE ON FUNCTION taskq.get_contract_meta() FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION taskq.get_contract_meta() TO taskq_observer",
        ),
        "function_catalog",
        "result is",
    ),
    Corruption(
        "language",
        (
            """
            CREATE OR REPLACE FUNCTION taskq.has_capability(p_name text) RETURNS boolean
            LANGUAGE plpgsql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, taskq, pg_temp
            AS 'BEGIN RETURN false; END'
            """,
        ),
        "function_catalog",
        "language is",
    ),
    Corruption(
        "volatility",
        ("ALTER FUNCTION taskq.metrics() VOLATILE",),
        "function_catalog",
        "volatility",
    ),
    Corruption(
        "parallel_safety",
        ("ALTER FUNCTION taskq.uuid7() PARALLEL UNSAFE",),
        "function_catalog",
        "parallel",
    ),
    Corruption(
        "strictness",
        ("ALTER FUNCTION taskq.metrics() STRICT",),
        "function_catalog",
        "unexpectedly STRICT",
    ),
    Corruption(
        "leakproof",
        ("ALTER FUNCTION taskq.metrics() LEAKPROOF",),
        "function_catalog",
        "unexpectedly LEAKPROOF",
    ),
    Corruption(
        "security_invoker",
        ("ALTER FUNCTION taskq.metrics() SECURITY INVOKER",),
        "function_hardening",
        "not SECURITY DEFINER",
    ),
    Corruption(
        "function_owner",
        (
            "ALTER FUNCTION taskq.enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb) OWNER TO taskq_operator",
        ),
        "function_hardening",
        "expected 'taskq_owner'",
    ),
    Corruption(
        "search_path",
        ("ALTER FUNCTION taskq.heartbeat(uuid,uuid,text,integer,jsonb,jsonb) RESET search_path",),
        "function_hardening",
        "no pinned search_path",
    ),
    Corruption(
        "public_execute",
        (
            "GRANT EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) TO PUBLIC",
        ),
        "no_public_execute",
        "EXECUTE granted to PUBLIC",
    ),
    Corruption(
        "wrong_capability_grant",
        ("GRANT EXECUTE ON FUNCTION taskq.enqueue_many(text,jsonb) TO taskq_runner",),
        "function_privileges",
        "EXECUTE grants",
    ),
    Corruption(
        "function_grant_option",
        (
            "GRANT EXECUTE ON FUNCTION taskq.enqueue_many(text,jsonb) TO taskq_producer WITH GRANT OPTION",
        ),
        "function_privileges",
        "EXECUTE grants",
    ),
    Corruption(
        "role_login",
        ("ALTER ROLE taskq_housekeeper LOGIN",),
        "role_manifest",
        "rolcanlogin",
    ),
    Corruption(
        "role_elevation",
        ("ALTER ROLE taskq_producer CREATEDB",),
        "role_manifest",
        "rolcreatedb",
    ),
    Corruption(
        "role_setting",
        ("ALTER ROLE taskq_runner SET statement_timeout = '31s'",),
        "role_manifest",
        "settings are",
    ),
    Corruption(
        "role_membership",
        ("GRANT taskq_operator TO taskq_observer",),
        "role_manifest",
        "unexpectedly a member",
    ),
    Corruption(
        "missing_table",
        ("ALTER TABLE taskq.workers RENAME TO workers_missing",),
        "relation_catalog",
        "missing table 'workers'",
    ),
    Corruption(
        "extra_table",
        ("CREATE TABLE taskq.verify_extra_table (id integer)",),
        "relation_catalog",
        "unexpected table 'verify_extra_table'",
    ),
    Corruption(
        "table_shape",
        ("ALTER TABLE taskq.jobs ADD COLUMN verify_extra integer",),
        "table_shapes",
        "definition differs",
    ),
    Corruption(
        "relation_owner",
        ("ALTER TABLE taskq.workers OWNER TO taskq_operator",),
        "relation_catalog",
        "expected 'taskq_owner'",
    ),
    Corruption(
        "composite_shape",
        ("ALTER TYPE taskq.settle_result ADD ATTRIBUTE verify_extra integer",),
        "composite_shapes",
        "shape is",
    ),
    Corruption(
        "constraint",
        ("ALTER TABLE taskq.jobs DROP CONSTRAINT jobs_priority_check",),
        "constraints",
        "definition differs",
    ),
    Corruption(
        "missing_critical_index",
        ("DROP INDEX taskq.jobs_claim_idx",),
        "indexes",
        "missing indexes 'jobs_claim_idx'",
    ),
    Corruption(
        "extra_index",
        ("CREATE INDEX verify_extra_idx ON taskq.jobs (created_at)",),
        "indexes",
        "unexpected indexes 'verify_extra_idx'",
    ),
    Corruption(
        "view_definition",
        (
            """
            CREATE OR REPLACE VIEW taskq.dead_jobs AS
            SELECT id, queue, job_type, outcome, error, failure_count,
                   expiry_streak, finished_at, workflow_id, payload
              FROM taskq.jobs
             WHERE status = 'failed' AND false
             ORDER BY finished_at DESC
            """,
        ),
        "views",
        "definition differs",
    ),
    Corruption(
        "schema_owner",
        ("ALTER SCHEMA taskq OWNER TO taskq_operator",),
        "schema_exists",
        "expected 'taskq_owner'",
    ),
    Corruption(
        "schema_create_grant",
        ("GRANT CREATE ON SCHEMA taskq TO taskq_producer",),
        "relation_privileges",
        "schema grants are",
    ),
    Corruption(
        "base_table_dml",
        ("GRANT UPDATE ON taskq.jobs TO taskq_producer",),
        "relation_privileges",
        "relation grants are",
    ),
    Corruption(
        "safe_view_grant",
        ("GRANT SELECT ON taskq.dead_jobs TO taskq_operator",),
        "relation_privileges",
        "relation grants are",
    ),
    Corruption(
        "control_seed",
        ("DELETE FROM taskq.control_state WHERE key = 'stats_snapshot'",),
        "seed_state",
        "missing control seed 'stats_snapshot'",
    ),
    Corruption(
        "contract_version",
        ("UPDATE taskq.meta SET value = '\"9.9\"'::jsonb WHERE key = 'contract_version'",),
        "seed_state",
        "meta rows are",
    ),
    Corruption(
        "deferred_seed",
        ("INSERT INTO taskq.queues(name) VALUES ('_system')",),
        "seed_state",
        "deferred seed queue '_system' is present",
    ),
    Corruption(
        "external_foreign_key",
        (
            "CREATE SCHEMA taskq_verify_external",
            "CREATE TABLE taskq_verify_external.host_jobs (queue text REFERENCES taskq.queues(name))",
        ),
        "external_foreign_keys",
        "cross-schema foreign key",
    ),
)


def _failed_check(report: object, name: str) -> object:
    matches = [check for check in report.failures if check.name == name]
    assert len(matches) == 1, f"expected one failed {name!r} check, got {matches!r}"
    return matches[0]


def test_machine_manifest_has_closed_0_2_1_function_surface() -> None:
    assert len(FUNCTIONS) == 65
    assert "taskq.truncate_utf8(text,integer)" in FUNCTIONS
    assert "taskq.list_jobs(text,text,integer,jsonb)" in FUNCTIONS
    assert "taskq.reserve_admission(text,text,text,uuid,integer,integer)" in FUNCTIONS
    assert "taskq._enqueue_followup(uuid,text,jsonb,integer)" in FUNCTIONS
    assert "taskq.create_workflow(text,text,jsonb,text[],text)" in FUNCTIONS
    assert all("PUBLIC" not in spec.grants for spec in FUNCTIONS.values())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.taskq_sql
async def test_verify_rejects_rollback_only_catalog_corruption(
    case: Corruption,
    pg: asyncpg.Connection,
    sqlalchemy_dsn: str,
) -> None:
    engine = create_async_engine(sqlalchemy_dsn)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                for statement in case.statements:
                    await conn.exec_driver_sql(statement)
                report = await verify(conn)
                assert not report.ok
                failed = _failed_check(report, case.failed_check)
                assert any(case.detail in detail for detail in failed.details), failed.details
            finally:
                await transaction.rollback()

            restored = await verify(conn)
            assert restored.ok, "\n".join(
                f"{check.name}: {'; '.join(check.details)}" for check in restored.failures
            )
    finally:
        await engine.dispose()


@pytest.mark.taskq_sql
async def test_verify_runs_inside_read_only_transaction(
    pg: asyncpg.Connection, sqlalchemy_dsn: str
) -> None:
    engine = create_async_engine(sqlalchemy_dsn)
    try:
        async with engine.connect() as conn, conn.begin():
            await conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            report = await verify(conn)
            assert report.ok
    finally:
        await engine.dispose()
