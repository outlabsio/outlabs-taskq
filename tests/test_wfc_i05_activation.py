"""WFC-I05 immutable metadata activation and rollback-bridge proof."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import _migrate_impl, discover_migrations, verify
from conftest import activate_scheduler_contract

pytestmark = pytest.mark.taskq_sql

INACTIVE_CAPABILITIES = [
    "admission_reservations",
    "dependencies_workflows",
    "followups",
    "read_model_list_finished",
    "read_model_list_ready",
    "read_model_list_running",
    "read_model_workflow",
    "schedules",
    "worker_presence",
]


def _database_dsn(dsn: str, database: str, *, sqlalchemy: bool = False) -> str:
    parts = urlsplit(dsn)
    scheme = "postgresql+asyncpg" if sqlalchemy else parts.scheme.split("+", 1)[0]
    return urlunsplit((scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _create_database(taskq_dsn: str, prefix: str) -> tuple[str, asyncpg.Connection]:
    database = f"{prefix}_{uuid4().hex}"
    admin = await asyncpg.connect(_database_dsn(taskq_dsn, "postgres"))
    await admin.execute(f'CREATE DATABASE "{database}"')
    return database, admin


def test_0017_is_strictly_metadata_only() -> None:
    migration = discover_migrations()[16]
    assert migration.id == "0017_activate_workflow_continuations"
    statements = migration.statements()
    assert len(statements) == 2
    assert statements[0].lstrip().startswith("-- outlabs-taskq")
    assert "\nDO $$" in statements[0]
    assert statements[1].lstrip().startswith("INSERT INTO taskq.meta")
    lowered = "\n".join(statements).lower()
    for forbidden in (
        "create function",
        "create index",
        "alter table",
        "alter function",
        "drop ",
        "grant ",
        "revoke ",
    ):
        assert forbidden not in lowered


async def test_0016_to_0017_is_fail_closed_then_activates_exactly(
    taskq_dsn: str,
) -> None:
    database, admin = await _create_database(taskq_dsn, "taskq_wfc_i05_transition")
    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        assert [migration.id for migration in migrations[15:17]] == [
            "0016_workflow_continuations",
            "0017_activate_workflow_continuations",
        ]
        async with engine.connect() as conn:
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[:16])
            )
            assert applied[-1] == "0016_workflow_continuations"
            capabilities = (
                await conn.exec_driver_sql(
                    "SELECT value->'active' FROM taskq.meta WHERE key='capabilities'"
                )
            ).scalar_one()
            assert capabilities == INACTIVE_CAPABILITIES
            assert (
                await conn.exec_driver_sql("SELECT taskq.has_capability('workflow_continuations')")
            ).scalar_one() is False
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.ensure_queue('wfc_i05','{}'::jsonb,'proof')"
            )
            await conn.commit()
            with pytest.raises(DBAPIError) as inactive:
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.create_workflow("
                    "'wfc-i05-inactive','dag','{}'::jsonb,ARRAY['wfc_i05'],"
                    "'proof',10,$1)",
                    ("a" * 64,),
                )
            assert getattr(inactive.value, "orig", inactive.value).sqlstate == "TQ501"
            await conn.rollback()

            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[16:17])
            )
            assert applied == ["0017_activate_workflow_continuations"]
            capabilities = (
                await conn.exec_driver_sql(
                    "SELECT value->'active' FROM taskq.meta WHERE key='capabilities'"
                )
            ).scalar_one()
            assert capabilities == [*INACTIVE_CAPABILITIES, "workflow_continuations"]
            created = (
                await conn.exec_driver_sql(
                    "SELECT * FROM taskq.create_workflow("
                    "'wfc-i05-active','dag','{}'::jsonb,ARRAY['wfc_i05'],"
                    "'proof',10,$1)",
                    ("a" * 64,),
                )
            ).one()
            assert created.outcome == "created"
            applied = await conn.run_sync(
                lambda sync_conn: _migrate_impl(sync_conn, migrations[17:18])
            )
            assert applied == ["0018_trusted_effect_fence"]
            await activate_scheduler_contract(conn, migrations)
            report = await verify(conn)
            assert report.ok, report
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()


async def test_0017_refuses_dirty_reserved_namespace_atomically(
    taskq_dsn: str,
) -> None:
    database, admin = await _create_database(taskq_dsn, "taskq_wfc_i05_dirty_key")
    engine = create_async_engine(_database_dsn(taskq_dsn, database, sqlalchemy=True))
    try:
        migrations = discover_migrations()
        async with engine.connect() as conn:
            await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, migrations[:16]))
            await conn.exec_driver_sql(
                "SELECT * FROM taskq.ensure_queue('dirty','{}'::jsonb,'proof')"
            )
            await conn.exec_driver_sql(
                """
                INSERT INTO taskq.jobs(
                    id,queue,job_type,status,priority,payload,idempotency_key,
                    scheduled_at,lease_seconds,max_attempts,backoff_mode,
                    backoff_base_seconds,backoff_cap_seconds
                ) VALUES (
                    taskq.uuid7(),'dirty','proof','queued',100,'{}'::jsonb,'chain:held',
                    now(),60,3,'exponential',1,60
                )
                """
            )
            await conn.commit()

            with pytest.raises(DBAPIError) as refused:
                await conn.run_sync(lambda sync_conn: _migrate_impl(sync_conn, migrations[16:17]))
            assert getattr(refused.value, "orig", refused.value).sqlstate == "TQ422"
            await conn.rollback()
            assert (
                await conn.exec_driver_sql("SELECT taskq.has_capability('workflow_continuations')")
            ).scalar_one() is False
            assert (
                await conn.exec_driver_sql(
                    "SELECT count(*) FROM taskq.schema_migrations "
                    "WHERE id='0017_activate_workflow_continuations'"
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()


async def test_activation_preserves_old_worker_rollback_and_new_policy_drain(
    pg: asyncpg.Connection,
    operator: asyncpg.Connection,
    producer: asyncpg.Connection,
    runner: asyncpg.Connection,
) -> None:
    """Old workers drain null-policy work while A/B workers drain their cohorts."""

    suffix = uuid4().hex
    queue = f"wfc_i05_bridge_{suffix}"
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'wfc-i05')",
        queue,
    )

    legacy = await producer.fetchrow(
        "SELECT * FROM taskq.enqueue($1,'proof.legacy','{}'::jsonb)",
        queue,
    )
    assert legacy is not None and legacy["created"] is True

    policy_jobs: dict[str, object] = {}
    for label, policy_hash in (("a", "a" * 64), ("b", "b" * 64)):
        workflow = await producer.fetchrow(
            "SELECT * FROM taskq.create_workflow($1,'dag','{}'::jsonb,ARRAY[$2],'wfc-i05',1,$3)",
            f"wfc-i05-bridge-{label}-{suffix}",
            queue,
            policy_hash,
        )
        assert workflow is not None and workflow["outcome"] == "created"
        member = await producer.fetchrow(
            "SELECT * FROM taskq.enqueue("
            "$1,'proof.policy','{}'::jsonb,"
            "p_workflow_id=>$2,p_step_key=>'root')",
            queue,
            workflow["workflow_id"],
        )
        assert member is not None and member["created"] is True
        policy_jobs[label] = member["job_id"]

    old_target_policy = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,$3)",
        queue,
        "old-target-policy",
        policy_jobs["a"],
    )
    assert old_target_policy is not None
    assert old_target_policy["state"] == "unavailable"
    assert old_target_policy["jobs"] == []

    old_batch = await runner.fetchrow(
        "SELECT * FROM taskq.claim_jobs($1,$2,p_batch=>1)",
        queue,
        "old-worker",
    )
    assert old_batch is not None and old_batch["state"] == "claimed"
    assert [job["job_id"] for job in old_batch["jobs"]] == [legacy["job_id"]]
    old_job = old_batch["jobs"][0]
    old_complete = await runner.fetchrow(
        "SELECT * FROM taskq.complete_job($1,$2,$3)",
        old_job["job_id"],
        old_job["attempt_id"],
        "old-worker",
    )
    assert old_complete is not None and tuple(old_complete)[:2] == ("ok", "succeeded")

    for label, policy_hash in (("a", "a" * 64), ("b", "b" * 64)):
        new_batch = await runner.fetchrow(
            "SELECT * FROM taskq.claim_jobs($1,$2,1,NULL,NULL,NULL,$3,$4::text[])",
            queue,
            f"new-worker-{label}",
            policy_jobs[label],
            [policy_hash],
        )
        assert new_batch is not None and new_batch["state"] == "claimed"
        assert [job["job_id"] for job in new_batch["jobs"]] == [policy_jobs[label]]
        new_job = new_batch["jobs"][0]
        completed = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1,$2,$3,NULL,NULL,'[]'::jsonb,$4)",
            new_job["job_id"],
            new_job["attempt_id"],
            f"new-worker-{label}",
            policy_hash,
        )
        assert completed is not None and tuple(completed)[:2] == ("ok", "succeeded")

    statuses = await pg.fetch(
        "SELECT id,status FROM taskq.jobs WHERE id=ANY($1::uuid[]) ORDER BY id",
        [legacy["job_id"], *policy_jobs.values()],
    )
    assert [row["status"] for row in statuses] == ["succeeded"] * 3
