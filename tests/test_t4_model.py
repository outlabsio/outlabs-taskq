"""T4 — Hypothesis stateful model against the real PostgreSQL kernel.

The model exercises the 0.1 state machine and budget table through exact
capability roles. Every step reconciles model state to durable rows; lease
time travel uses only the scratch-only ``taskq_test`` helper from conftest.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from conftest import _plain_dsn, _truncate_taskq

pytestmark = pytest.mark.taskq_sql

_ACTIVE = {"blocked", "queued", "running"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_ENQUEUE = (
    "SELECT * FROM taskq.enqueue($1, 'test.model', '{}'::jsonb, "
    "p_idempotency_key => $2, p_max_attempts => 4::smallint, "
    "p_backoff_mode => 'fixed', p_backoff_base => 1, p_backoff_cap => 1)"
)


@dataclass(slots=True)
class ModelJob:
    key: str | None
    status: str = "queued"
    failure_count: int = 0
    expiry_streak: int = 0
    claim_count: int = 0
    attempt_id: UUID | None = None
    worker_id: str | None = None
    cancel_pending: bool = False
    due: bool = True


class TaskqStateMachine(RuleBasedStateMachine):
    jobs = Bundle("jobs")

    def __init__(self, dsn: str) -> None:
        super().__init__()
        self.dsn = dsn
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.model: dict[UUID, ModelJob] = {}
        self.worker_sequence = 0
        self.connections: list[asyncpg.Connection] = []
        self._run(self._setup())

    def _run(self, awaitable: Awaitable[Any]) -> Any:
        return self.loop.run_until_complete(awaitable)

    async def _setup(self) -> None:
        self.admin = await asyncpg.connect(self.dsn)
        self.connections.append(self.admin)
        await _truncate_taskq(self.admin)

        async def role(name: str) -> asyncpg.Connection:
            conn = await asyncpg.connect(self.dsn)
            self.connections.append(conn)
            await conn.execute(f"SET ROLE {name}")
            return conn

        self.operator = await role("taskq_operator")
        self.producer = await role("taskq_producer")
        self.runner = await role("taskq_runner")
        self.housekeeper = await role("taskq_housekeeper")
        row = await self.operator.fetchrow(
            "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 't4')",
            "t4_model",
            '{"default_max_attempts":4,"default_backoff_mode":"fixed",'
            '"default_backoff_base":1,"default_backoff_cap":1}',
        )
        assert row is not None

    def teardown(self) -> None:
        async def close_all() -> None:
            for conn in reversed(self.connections):
                await conn.close()

        self._run(close_all())
        self.loop.close()
        asyncio.set_event_loop(None)

    @rule(target=jobs, key_slot=st.one_of(st.none(), st.integers(min_value=0, max_value=7)))
    def enqueue(self, key_slot: int | None) -> UUID:
        key = None if key_slot is None else f"model-key-{key_slot}"
        active_existing = next(
            (
                job_id
                for job_id, model in self.model.items()
                if key is not None and model.key == key and model.status in _ACTIVE
            ),
            None,
        )
        row = self._run(self.producer.fetchrow(_ENQUEUE, "t4_model", key))
        assert row is not None
        job_id = row["job_id"]
        if active_existing is not None:
            assert row["created"] is False
            assert job_id == active_existing
        else:
            assert row["created"] is True
            assert job_id not in self.model
            self.model[job_id] = ModelJob(key=key)
        return job_id

    @rule(job_id=jobs)
    def claim(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "queued" or not model.due:
            return
        self.worker_sequence += 1
        worker_id = f"model-worker-{self.worker_sequence}"
        batch = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.claim_jobs($1, $2, p_job_id => $3)",
                "t4_model",
                worker_id,
                job_id,
            )
        )
        assert batch is not None and batch["state"] == "claimed"
        assert len(batch["jobs"]) == 1
        claimed = batch["jobs"][0]
        assert claimed["job_id"] == job_id
        model.status = "running"
        model.claim_count += 1
        model.attempt_id = claimed["attempt_id"]
        model.worker_id = worker_id

    @rule(job_id=jobs)
    def heartbeat(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        row = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.heartbeat($1, $2, $3, p_progress => '{\"model\":true}'::jsonb)",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert row["ok"] is True
        assert row["cancel_requested"] is model.cancel_pending

    @rule(job_id=jobs)
    def cancel_running(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        row = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.cancel_running_job($1, $2, $3, 'model worker cancel')",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert row["result"] == "ok" and row["job_status"] == "cancelled"
        replay = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.cancel_running_job($1, $2, $3, 'model replay')",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert replay["result"] == "already_settled"
        conflict = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert conflict["result"] == "settle_conflict"
        model.status = "cancelled"
        model.attempt_id = None
        model.worker_id = None
        model.cancel_pending = False
        model.due = False

    @rule(job_id=jobs)
    def complete(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        row = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.complete_job($1, $2, $3)",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert row["result"] == "ok" and row["job_status"] == "succeeded"
        model.status = "succeeded"
        model.expiry_streak = 0
        model.attempt_id = None
        model.worker_id = None
        model.cancel_pending = False

    @rule(job_id=jobs, retryable=st.booleans())
    def fail(self, job_id: UUID, retryable: bool) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        before_budget = model.failure_count
        row = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.fail_job($1, $2, $3, 'model failure', "
                "p_retryable => $4, p_retry_after_seconds => 0)",
                job_id,
                model.attempt_id,
                model.worker_id,
                retryable,
            )
        )
        if model.cancel_pending:
            assert row["result"] == "ok" and row["job_status"] == "cancelled"
            model.status = "cancelled"
            assert model.failure_count == before_budget
        else:
            model.failure_count += 1
            model.expiry_streak = 0
            if retryable and model.failure_count < 4:
                assert row["result"] == "retry_scheduled"
                model.status = "queued"
                model.due = True
            else:
                assert row["result"] == "dead" and row["job_status"] == "failed"
                model.status = "failed"
        model.attempt_id = None
        model.worker_id = None
        model.cancel_pending = False

    @rule(job_id=jobs)
    def release(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        before_budget = model.failure_count
        row = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.release_job($1, $2, $3, p_delay_seconds => 0)",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert row["result"] == "ok"
        model.status = "cancelled" if model.cancel_pending else "queued"
        model.due = not model.cancel_pending
        assert model.failure_count == before_budget
        model.attempt_id = None
        model.worker_id = None
        model.cancel_pending = False

    @rule(job_id=jobs)
    def snooze(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        before_budget = model.failure_count
        row = self._run(
            self.runner.fetchrow(
                "SELECT * FROM taskq.snooze_job($1, $2, $3, 0, 'model snooze')",
                job_id,
                model.attempt_id,
                model.worker_id,
            )
        )
        assert row["result"] == "ok"
        model.status = "cancelled" if model.cancel_pending else "queued"
        model.due = not model.cancel_pending
        model.expiry_streak = 0
        assert model.failure_count == before_budget
        model.attempt_id = None
        model.worker_id = None
        model.cancel_pending = False

    @rule(job_id=jobs)
    def cancel(self, job_id: UUID) -> None:
        model = self.model[job_id]
        row = self._run(
            self.operator.fetchrow(
                "SELECT * FROM taskq.cancel_job($1, 't4', 'model cancel')", job_id
            )
        )
        assert row is not None
        if model.status in _TERMINAL:
            assert row["result"] == "already_terminal"
        elif model.status == "running":
            assert row["result"] == "cancel_requested"
            model.cancel_pending = True
        else:
            assert row["result"] == "cancelled"
            model.status = "cancelled"
            model.due = False

    @rule(job_id=jobs)
    def rewind_lease_and_tick(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "running":
            return
        before_budget = model.failure_count
        rewound = self._run(
            self.housekeeper.fetchval(
                "SELECT taskq_test.rewind_lease($1, interval '2 days')", job_id
            )
        )
        assert rewound is True
        tick = self._run(self.housekeeper.fetchval("SELECT taskq.tick(200)"))
        assert isinstance(tick, str)
        if model.cancel_pending:
            model.status = "cancelled"
            assert model.failure_count == before_budget
        else:
            model.failure_count += 1
            model.expiry_streak += 1
            if model.expiry_streak >= 3 or model.failure_count >= 4:
                model.status = "failed"
            else:
                model.status = "queued"
                model.due = False
        model.attempt_id = None
        model.worker_id = None
        model.cancel_pending = False

    @rule(job_id=jobs)
    def redrive(self, job_id: UUID) -> None:
        model = self.model[job_id]
        if model.status != "failed":
            return
        collision = model.key is not None and any(
            other_id != job_id and other.key == model.key and other.status in _ACTIVE
            for other_id, other in self.model.items()
        )
        try:
            redriven = self._run(
                self.operator.fetchval("SELECT taskq.redrive_job($1, 't4', false)", job_id)
            )
        except asyncpg.PostgresError as exc:
            assert collision and exc.sqlstate == "TQ409"
            return
        assert not collision and redriven is True
        model.status = "queued"
        model.failure_count = 0
        model.expiry_streak = 0
        model.cancel_pending = False
        model.due = True

    @invariant()
    def durable_state_matches_model(self) -> None:
        if not self.model:
            return
        rows = self._run(
            self.admin.fetch(
                "SELECT id, status, failure_count, expiry_streak, attempt_count, "
                "finished_at, current_attempt_id FROM taskq.jobs WHERE id = ANY($1::uuid[])",
                list(self.model),
            )
        )
        durable = {row["id"]: row for row in rows}
        assert set(durable) == set(self.model)
        for job_id, model in self.model.items():
            row = durable[job_id]
            assert row["status"] == model.status
            assert row["failure_count"] == model.failure_count
            assert row["expiry_streak"] == model.expiry_streak
            assert row["attempt_count"] == model.claim_count
            assert (row["finished_at"] is not None) == (model.status in _TERMINAL)
            assert (row["current_attempt_id"] is not None) == (model.status == "running")
            if model.status == "running":
                assert row["current_attempt_id"] == model.attempt_id

        duplicate_running = self._run(
            self.admin.fetchval(
                "SELECT count(*) FROM ("
                "SELECT job_id FROM taskq.job_attempts WHERE status = 'running' "
                "GROUP BY job_id HAVING count(*) > 1) duplicates"
            )
        )
        duplicate_active_keys = self._run(
            self.admin.fetchval(
                "SELECT count(*) FROM ("
                "SELECT queue, idempotency_key FROM taskq.jobs "
                "WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running') "
                "GROUP BY queue, idempotency_key HAVING count(*) > 1) duplicates"
            )
        )
        assert duplicate_running == 0
        assert duplicate_active_keys == 0


def test_stateful_sql_model(
    taskq_dsn: str,
    stateful_time_travel: None,
) -> None:
    examples = int(os.environ.get("TASKQ_MODEL_EXAMPLES", "20"))
    steps = int(os.environ.get("TASKQ_MODEL_STEPS", "40"))
    assert examples > 0 and steps > 0
    print(f"T4 examples={examples} steps={steps}; set HYPOTHESIS_SEED to replay a generated run")
    run_state_machine_as_test(
        lambda: TaskqStateMachine(_plain_dsn(taskq_dsn)),
        settings=settings(
            max_examples=examples,
            stateful_step_count=steps,
            deadline=None,
            suppress_health_check=(HealthCheck.too_slow,),
        ),
    )
