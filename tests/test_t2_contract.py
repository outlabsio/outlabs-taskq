"""T2 — SQL contract suite against real Postgres (harness layer T2).

Asserts the Tier-0 contracts of the 0.1 Function Manifest + Transport
Protocol v1: installer idempotency, verify anti-drift, capability-role
privilege walls (ADR-010/011), typed enqueue results, the H-01 typed
``claim_batch`` states, H-03 verb-aware settle replays, budget-free release,
cancel-wins failure paths (R2-03), and the TQ501 followup capability gate.

Every test runs as the exact capability role the call is granted to
(harness §1.1 rule 3). Requires ``TASKQ_TEST_DSN`` (superuser scratch DB —
see ``conftest.py``); skips cleanly without it, or while no migration files
are packaged yet.
"""

from __future__ import annotations

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from taskq.sql import migrate, verify

pytestmark = pytest.mark.taskq_sql

_ENQUEUE = "SELECT * FROM taskq.enqueue($1, $2, '{}'::jsonb, p_idempotency_key => $3)"
_CLAIM = "SELECT * FROM taskq.claim_jobs($1, $2)"


async def _make_queue(operator: asyncpg.Connection, name: str) -> None:
    await operator.fetchrow("SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'harness')", name)


async def _enqueue_one(
    producer: asyncpg.Connection, queue: str, key: str = "k-1"
) -> asyncpg.Record:
    row = await producer.fetchrow(_ENQUEUE, queue, "test.echo", key)
    assert row is not None
    return row


async def _claim_one(runner: asyncpg.Connection, queue: str, worker: str = "w-1") -> asyncpg.Record:
    """Claim exactly one job; returns the claimed_job composite record."""
    batch = await runner.fetchrow(_CLAIM, queue, worker)
    assert batch is not None
    assert batch["state"] == "claimed", f"expected a claim, got state={batch['state']!r}"
    jobs = batch["jobs"]
    assert jobs and len(jobs) == 1
    return jobs[0]


def _failed_check(report: object, name: str) -> object:
    matches = [check for check in report.failures if check.name == name]
    assert len(matches) == 1, f"expected one failed {name!r} check, got {matches!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Installer + verify (feature 13 §5 acceptance 1–3; ADR-004; T8 seeds)
# ---------------------------------------------------------------------------


class TestMigrateAndVerify:
    async def test_migrate_is_idempotent(self, pg: asyncpg.Connection, sqlalchemy_dsn: str) -> None:
        before = [
            tuple(r)
            for r in await pg.fetch("SELECT id, checksum FROM taskq.schema_migrations ORDER BY id")
        ]
        assert before, "session fixture should have applied at least migration 0001"

        engine = create_async_engine(sqlalchemy_dsn)
        try:
            async with engine.connect() as conn:
                applied = await migrate(conn)
        finally:
            await engine.dispose()

        assert applied == []  # double invocation: nothing to do, no error
        after = [
            tuple(r)
            for r in await pg.fetch("SELECT id, checksum FROM taskq.schema_migrations ORDER BY id")
        ]
        assert after == before

    async def test_verify_ok_on_fresh_install(
        self, pg: asyncpg.Connection, sqlalchemy_dsn: str
    ) -> None:
        engine = create_async_engine(sqlalchemy_dsn)
        try:
            async with engine.connect() as conn:
                report = await verify(conn)
        finally:
            await engine.dispose()
        assert report.ok, "\n".join(f"{c.name}: {'; '.join(c.details)}" for c in report.failures)

    async def test_verify_detects_ownership_corruption(
        self, pg: asyncpg.Connection, sqlalchemy_dsn: str
    ) -> None:
        signature = await pg.fetchval(
            "SELECT p.oid::regprocedure::text FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'taskq' AND p.proname = 'enqueue'"
        )
        assert signature, "taskq.enqueue must exist in the 0.1 catalog"

        engine = create_async_engine(sqlalchemy_dsn)
        try:
            await pg.execute(f"ALTER FUNCTION {signature} OWNER TO taskq_operator")
            try:
                async with engine.connect() as conn:
                    report = await verify(conn)
                assert not report.ok
                hardening = _failed_check(report, "function_hardening")
                assert any(
                    "enqueue" in detail and "taskq_owner" in detail
                    for detail in hardening.details
                )
            finally:
                await pg.execute(f"ALTER FUNCTION {signature} OWNER TO taskq_owner")
            # Restoration proven: verify is green again (read-only both times).
            async with engine.connect() as conn:
                report = await verify(conn)
            assert report.ok
        finally:
            await engine.dispose()

    async def test_verify_detects_missing_search_path(
        self, pg: asyncpg.Connection, sqlalchemy_dsn: str
    ) -> None:
        signature = await pg.fetchval(
            "SELECT p.oid::regprocedure::text FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'taskq' AND p.proname = 'heartbeat'"
        )
        assert signature

        engine = create_async_engine(sqlalchemy_dsn)
        try:
            await pg.execute(f"ALTER FUNCTION {signature} RESET search_path")
            try:
                async with engine.connect() as conn:
                    report = await verify(conn)
                hardening = _failed_check(report, "function_hardening")
                assert any(
                    "heartbeat" in detail and "no pinned search_path" in detail
                    for detail in hardening.details
                )
            finally:
                await pg.execute(
                    f"ALTER FUNCTION {signature} "
                    "SET search_path TO pg_catalog, taskq, pg_temp"
                )
            async with engine.connect() as conn:
                report = await verify(conn)
            assert report.ok
        finally:
            await engine.dispose()

    async def test_verify_detects_public_execute(
        self, pg: asyncpg.Connection, sqlalchemy_dsn: str
    ) -> None:
        signature = await pg.fetchval(
            "SELECT p.oid::regprocedure::text FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'taskq' AND p.proname = 'complete_job'"
        )
        assert signature

        engine = create_async_engine(sqlalchemy_dsn)
        try:
            await pg.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC")
            try:
                async with engine.connect() as conn:
                    report = await verify(conn)
                public = _failed_check(report, "no_public_execute")
                assert any(
                    "complete_job" in detail and "EXECUTE granted to PUBLIC" in detail
                    for detail in public.details
                )
            finally:
                await pg.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
            async with engine.connect() as conn:
                report = await verify(conn)
            assert report.ok
        finally:
            await engine.dispose()

    async def test_verify_detects_ledger_checksum_tamper(
        self, pg: asyncpg.Connection, sqlalchemy_dsn: str
    ) -> None:
        ledger = await pg.fetchrow(
            "SELECT id, checksum FROM taskq.schema_migrations ORDER BY id LIMIT 1"
        )
        assert ledger is not None

        engine = create_async_engine(sqlalchemy_dsn)
        try:
            await pg.execute(
                "UPDATE taskq.schema_migrations SET checksum = repeat('0', 64) WHERE id = $1",
                ledger["id"],
            )
            try:
                async with engine.connect() as conn:
                    report = await verify(conn)
                migration_ledger = _failed_check(report, "migration_ledger")
                assert any(
                    ledger["id"] in detail and "checksum mismatch" in detail
                    for detail in migration_ledger.details
                )
            finally:
                await pg.execute(
                    "UPDATE taskq.schema_migrations SET checksum = $1 WHERE id = $2",
                    ledger["checksum"],
                    ledger["id"],
                )
            async with engine.connect() as conn:
                report = await verify(conn)
            assert report.ok
        finally:
            await engine.dispose()

    async def test_verify_detects_missing_capability_role(
        self, pg: asyncpg.Connection, sqlalchemy_dsn: str
    ) -> None:
        original = "taskq_housekeeper"
        displaced = "taskq_housekeeper_verify_missing"
        engine = create_async_engine(sqlalchemy_dsn)
        try:
            await pg.execute(f"ALTER ROLE {original} RENAME TO {displaced}")
            try:
                async with engine.connect() as conn:
                    report = await verify(conn)
                roles = _failed_check(report, "capability_roles_exist")
                assert roles.details == (f"role '{original}' does not exist",)
            finally:
                await pg.execute(f"ALTER ROLE {displaced} RENAME TO {original}")
            async with engine.connect() as conn:
                report = await verify(conn)
            assert report.ok
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# Privilege walls (ADR-010/011; harness §1.1 rule 3 privilege/shadow suite)
# ---------------------------------------------------------------------------


class TestPrivileges:
    async def test_producer_cannot_dml_jobs(
        self, pg: asyncpg.Connection, producer: asyncpg.Connection
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await producer.execute("UPDATE taskq.jobs SET updated_at = now()")

    async def test_producer_cannot_execute_claim_jobs(
        self, pg: asyncpg.Connection, producer: asyncpg.Connection
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await producer.fetchrow(_CLAIM, "any_queue", "w-1")

    async def test_runner_cannot_execute_enqueue(
        self, pg: asyncpg.Connection, runner: asyncpg.Connection
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runner.fetchrow(_ENQUEUE, "any_queue", "test.echo", "k-x")


# ---------------------------------------------------------------------------
# Enqueue — typed results, index-enforced idempotency (T2-ENQ)
# ---------------------------------------------------------------------------


class TestEnqueue:
    async def test_idempotency_key_returns_same_job(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_enq")

        first = await _enqueue_one(producer, "t2_enq", key="idem-1")
        assert first["created"] is True
        assert first["job_id"] is not None

        second = await _enqueue_one(producer, "t2_enq", key="idem-1")
        assert second["created"] is False  # truthfully reported, never a lie
        assert second["job_id"] == first["job_id"]


# ---------------------------------------------------------------------------
# Claim — H-01 typed claim_batch states (T2-CLAIM)
# ---------------------------------------------------------------------------


class TestClaimStates:
    async def test_unknown_queue(self, pg: asyncpg.Connection, runner: asyncpg.Connection) -> None:
        batch = await runner.fetchrow(_CLAIM, "never_created", "w-1")
        assert batch["state"] == "unknown_queue"
        assert not batch["jobs"]

    async def test_paused_queue(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_paused")
        await operator.fetchrow(
            "SELECT taskq.pause_queue($1, 'harness', 'T2 pause test')", "t2_paused"
        )
        batch = await runner.fetchrow(_CLAIM, "t2_paused", "w-1")
        assert batch["state"] == "paused"
        assert not batch["jobs"]

    async def test_empty_queue(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_empty")
        batch = await runner.fetchrow(_CLAIM, "t2_empty", "w-1")
        assert batch["state"] == "empty"
        assert not batch["jobs"]

    async def test_claimed_after_enqueue(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_claim")
        enqueued = await _enqueue_one(producer, "t2_claim")

        job = await _claim_one(runner, "t2_claim")
        assert job["job_id"] == enqueued["job_id"]
        assert job["attempt_id"] is not None

        row = await pg.fetchrow(
            "SELECT status, worker_id, current_attempt_id FROM taskq.jobs WHERE id = $1",
            enqueued["job_id"],
        )
        assert row["status"] == "running"
        assert row["worker_id"] == "w-1"
        assert row["current_attempt_id"] == job["attempt_id"]


# ---------------------------------------------------------------------------
# Settlement — H-03 verb-aware replays (T2-COMPLETE / T2-FAIL)
# ---------------------------------------------------------------------------


class TestSettleReplays:
    async def test_complete_then_replay_then_cross_verb(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_settle")
        await _enqueue_one(producer, "t2_settle")
        job = await _claim_one(runner, "t2_settle")
        job_id, attempt_id = job["job_id"], job["attempt_id"]

        settled = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1, $2, $3)", job_id, attempt_id, "w-1"
        )
        assert settled["result"] == "ok"
        assert settled["job_status"] == "succeeded"

        # Same verb replayed for the settled attempt → already_settled (H-03).
        replay = await runner.fetchrow(
            "SELECT * FROM taskq.complete_job($1, $2, $3)", job_id, attempt_id, "w-1"
        )
        assert replay["result"] == "already_settled"

        # DIFFERENT verb against the settled attempt → settle_conflict (H-03).
        conflict = await runner.fetchrow(
            "SELECT * FROM taskq.fail_job($1, $2, $3, $4)",
            job_id,
            attempt_id,
            "w-1",
            "late failure after success",
        )
        assert conflict["result"] == "settle_conflict"

        status = await pg.fetchval("SELECT status FROM taskq.jobs WHERE id = $1", job_id)
        assert status == "succeeded"  # the cross-verb replay never mutated the job


class TestReleaseBudget:
    async def test_release_does_not_consume_failure_budget(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_release")
        await _enqueue_one(producer, "t2_release")
        job = await _claim_one(runner, "t2_release")

        released = await runner.fetchrow(
            "SELECT * FROM taskq.release_job($1, $2, $3)",
            job["job_id"],
            job["attempt_id"],
            "w-1",
        )
        assert released["result"] == "ok"
        assert released["job_status"] == "queued"

        row = await pg.fetchrow(
            "SELECT status, failure_count FROM taskq.jobs WHERE id = $1", job["job_id"]
        )
        assert row["status"] == "queued"
        assert row["failure_count"] == 0  # §3.3: releases never consume budget

        attempt_status = await pg.fetchval(
            "SELECT status FROM taskq.job_attempts WHERE id = $1", job["attempt_id"]
        )
        assert attempt_status == "released"  # the ledger IS the verb record (H-03)


class TestCancelWinsFailure:
    async def test_fail_with_pending_cancel_lands_cancelled_budget_untouched(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_cancel")
        await _enqueue_one(producer, "t2_cancel")
        job = await _claim_one(runner, "t2_cancel")

        row = await operator.fetchrow(
            "SELECT * FROM taskq.cancel_job($1, 'harness', 'operator cancel')", job["job_id"]
        )
        # cancel_job returns the typed composite (result, job_status): cooperative
        # cancel of a RUNNING job reports 'cancel_requested' while the job stays
        # 'running' until the worker observes it (manifest §5 / R2 protocol).
        assert row["result"] == "cancel_requested"
        assert row["job_status"] == "running"

        failed = await runner.fetchrow(
            "SELECT * FROM taskq.fail_job($1, $2, $3, $4)",
            job["job_id"],
            job["attempt_id"],
            "w-1",
            "handler aborted on cancel",
        )
        assert failed["result"] == "ok"  # R2-03: cancel branch, not failure accounting
        assert failed["job_status"] == "cancelled"

        row = await pg.fetchrow(
            "SELECT status, failure_count FROM taskq.jobs WHERE id = $1", job["job_id"]
        )
        assert row["status"] == "cancelled"
        assert row["failure_count"] == 0  # budget untouched on the cancel path


# ---------------------------------------------------------------------------
# 0.1 capability gate — followups are contract skew (TQ501)
# ---------------------------------------------------------------------------


class TestFollowupCapabilityGate:
    async def test_non_empty_followups_raise_tq501(
        self,
        pg: asyncpg.Connection,
        operator: asyncpg.Connection,
        producer: asyncpg.Connection,
        runner: asyncpg.Connection,
    ) -> None:
        await _make_queue(operator, "t2_followups")
        await _enqueue_one(producer, "t2_followups")
        job = await _claim_one(runner, "t2_followups")

        with pytest.raises(asyncpg.PostgresError) as excinfo:
            await runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3, p_followups => $4::jsonb)",
                job["job_id"],
                job["attempt_id"],
                "w-1",
                '[{"job_type": "child.step"}]',
            )
        assert excinfo.value.sqlstate == "TQ501"

        # The rejected settle rolled back atomically: the job is still running.
        status = await pg.fetchval("SELECT status FROM taskq.jobs WHERE id = $1", job["job_id"])
        assert status == "running"
