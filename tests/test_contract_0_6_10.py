"""Queue admission ownership contract checks that need no database."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest

from taskq.http.runtime import SUPPORTED_SQL_CONTRACT_VERSIONS
from taskq.sql import discover_migrations
from taskq.sql.manifest import CONTRACT_VERSION


MIGRATION = Path(__file__).parents[1] / "src/taskq/sql/migrations/0046_queue_admission_owner.sql"


def test_queue_admission_owner_migration_is_packaged_and_versioned():
    migrations = discover_migrations()
    assert migrations[45].id == "0046_queue_admission_owner"
    assert CONTRACT_VERSION == "0.6.11"
    assert "0.6.10" in SUPPORTED_SQL_CONTRACT_VERSIONS


def test_queue_admission_owner_has_database_boundary_guards():
    sql = MIGRATION.read_text()
    assert "SECURITY DEFINER" in sql
    assert "session_user" in sql
    assert "jobs_admission_owner_guard" in sql
    assert "terminal jobs on bound queues cannot be redriven" in sql
    assert "FOR SHARE" in sql
    assert "attest_target" in sql


@asynccontextmanager
async def _login_roles(taskq_dsn, pg):
    suffix = uuid4().hex[:10]
    roles = [f"e2e_owner_{suffix}", f"e2e_other_{suffix}", f"e2e_operator_{suffix}"]
    password = "e2e-password"
    for role in roles:
        await pg.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
    await pg.execute(f'GRANT taskq_producer TO "{roles[0]}"')
    await pg.execute(f'GRANT taskq_producer TO "{roles[1]}"')
    await pg.execute(f'GRANT taskq_operator TO "{roles[2]}"')
    parsed = urlparse(taskq_dsn.replace("postgresql+asyncpg://", "postgresql://"))
    conns = [
        await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port,
            user=role,
            password=password,
            database=parsed.path[1:],
        )
        for role in roles
    ]
    try:
        yield roles, conns
    finally:
        for conn in conns:
            await conn.close()
        for role in roles:
            await pg.execute(f'DROP ROLE IF EXISTS "{role}"')


@pytest.mark.taskq_sql
async def test_direct_login_owner_boundary_and_immutable_binding(taskq_dsn, pg):
    """Exercise 0046 through independent LOGIN sessions, not SET ROLE."""
    async with _login_roles(taskq_dsn, pg) as (roles, conns):
        owner, other, operator = conns
        queue = f"e2e_owner_{roles[0][-10:]}"
        await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'e2e')", queue)
        identity = await pg.fetchrow("SELECT installation_id FROM taskq.get_target_identity()")
        await operator.fetchval(
            "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
            queue,
            roles[0],
            identity["installation_id"],
        )
        await owner.fetchrow("SELECT * FROM taskq.enqueue($1,'e2e.job','{}'::jsonb)", queue)
        with pytest.raises(asyncpg.PostgresError) as denied:
            await other.fetchrow("SELECT * FROM taskq.enqueue($1,'e2e.job','{}'::jsonb)", queue)
        assert denied.value.sqlstate == "TQ425"
        with pytest.raises(asyncpg.PostgresError) as immutable:
            await operator.fetchval(
                "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
                queue,
                roles[0],
                identity["installation_id"],
            )
        assert immutable.value.sqlstate == "TQ409"


@pytest.mark.taskq_sql
async def test_bind_rejects_set_role_impersonation_and_control_owner(taskq_dsn, pg):
    async with _login_roles(taskq_dsn, pg) as (roles, conns):
        owner, _, operator = conns
        queue = f"e2e_impersonation_{roles[0][-10:]}"
        await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'e2e')", queue)
        identity = await pg.fetchrow("SELECT installation_id FROM taskq.get_target_identity()")
        with pytest.raises(asyncpg.PostgresError) as control:
            await operator.fetchval(
                "SELECT taskq.bind_queue_admission_owner($1,'taskq_operator','test',$2,false)",
                queue,
                identity["installation_id"],
            )
        assert control.value.sqlstate == "TQ422"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await owner.execute("SET ROLE taskq_operator")


@pytest.mark.taskq_sql
async def test_bound_terminal_single_and_bulk_redrive_rejected_unbound_positive(
    taskq_dsn, pg, runner
):
    async with _login_roles(taskq_dsn, pg) as (roles, conns):
        owner, _, operator = conns
        identity = await pg.fetchrow("SELECT installation_id FROM taskq.get_target_identity()")
        bound = f"e2e_redrive_bound_{roles[0][-10:]}"
        plain = f"e2e_redrive_plain_{roles[0][-10:]}"
        for queue in (bound, plain):
            await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'e2e')", queue)
        await operator.fetchval(
            "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
            bound,
            roles[0],
            identity["installation_id"],
        )
        for queue in (bound, plain):
            job = await owner.fetchrow(
                "SELECT * FROM taskq.enqueue($1,'e2e.redrive','{}'::jsonb)", queue
            )
            claimed = (
                await runner.fetchrow("SELECT * FROM taskq.claim_jobs($1,'e2e-redrive')", queue)
            )["jobs"][0]
            await runner.fetchrow(
                "SELECT * FROM taskq.fail_job($1,$2,'e2e-redrive','x',false)",
                claimed["job_id"],
                claimed["attempt_id"],
            )
            if queue == bound:
                with pytest.raises(asyncpg.PostgresError) as single:
                    await operator.fetchval("SELECT taskq.redrive_job($1,'e2e')", job["job_id"])
                assert single.value.sqlstate == "TQ409"
                bulk = await operator.fetchrow(
                    "SELECT * FROM taskq.redrive_failed($1,10,'e2e',0)", queue
                )
                assert bulk["redriven"] == 0
                assert (
                    await pg.fetchval("SELECT status FROM taskq.jobs WHERE id=$1", job["job_id"])
                    == "failed"
                )
            else:
                assert await operator.fetchval("SELECT taskq.redrive_job($1,'e2e')", job["job_id"])


@pytest.mark.taskq_sql
async def test_owner_workflow_enqueue_allowed_nonowner_rejected(taskq_dsn, pg):
    async with _login_roles(taskq_dsn, pg) as (roles, conns):
        owner, other, operator = conns
        queue = f"e2e_workflow_{roles[0][-10:]}"
        await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'e2e')", queue)
        identity = await pg.fetchrow("SELECT installation_id FROM taskq.get_target_identity()")
        await operator.fetchval(
            "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
            queue,
            roles[0],
            identity["installation_id"],
        )
        workflow = await owner.fetchrow(
            "SELECT * FROM taskq.create_workflow($1,'dag', '{}'::jsonb, ARRAY[$2], 'e2e')",
            uuid4().hex,
            queue,
        )
        await owner.fetchrow(
            "SELECT * FROM taskq.enqueue($1,'e2e.workflow','{}'::jsonb,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,$2,'owner-step')",
            queue,
            workflow["workflow_id"],
        )
        with pytest.raises(asyncpg.PostgresError) as denied:
            await other.fetchrow(
                "SELECT * FROM taskq.enqueue($1,'e2e.workflow','{}'::jsonb,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,$2,'other-step')",
                queue,
                workflow["workflow_id"],
            )
        assert denied.value.sqlstate == "TQ425"


@pytest.mark.taskq_sql
async def test_bind_vs_enqueue_both_orders_serialize_without_stray_rows(taskq_dsn, pg):
    async with _login_roles(taskq_dsn, pg) as (roles, conns):
        owner, other, operator = conns
        identity = await pg.fetchrow("SELECT installation_id FROM taskq.get_target_identity()")
        first_queue = f"e2e_bind_first_{roles[0][-10:]}"
        second_queue = f"e2e_insert_first_{roles[0][-10:]}"
        third_queue = f"e2e_insert_first_{roles[0][-10:]}x"
        for queue in (first_queue, second_queue, third_queue):
            await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1,'{}'::jsonb,'e2e')", queue)
        await operator.fetchval(
            "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
            first_queue,
            roles[0],
            identity["installation_id"],
        )
        binder = operator.transaction()
        await binder.start()
        await operator.fetchval(
            "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
            second_queue,
            roles[0],
            identity["installation_id"],
        )
        blocked = asyncio.create_task(
            other.fetchrow("SELECT * FROM taskq.enqueue($1,'e2e.job','{}'::jsonb)", second_queue)
        )
        await asyncio.sleep(0.1)
        assert not blocked.done()
        await binder.commit()
        with pytest.raises(asyncpg.PostgresError) as denied:
            await blocked
        assert denied.value.sqlstate == "TQ425"
        tx = owner.transaction()
        await tx.start()
        await owner.fetchrow("SELECT * FROM taskq.enqueue($1,'e2e.job','{}'::jsonb)", third_queue)
        contender = asyncio.create_task(
            operator.fetchval(
                "SELECT taskq.bind_queue_admission_owner($1,$2,'test',$3,false)",
                third_queue,
                roles[0],
                identity["installation_id"],
            )
        )
        await asyncio.sleep(0.1)
        assert not contender.done()
        await tx.commit()
        with pytest.raises(asyncpg.PostgresError) as occupied:
            await contender
        assert occupied.value.sqlstate == "TQ409"
