"""WFC-I03 runtime, fake transport, and worker policy-negotiation proof."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from pydantic import ValidationError
import pytest

from taskq import (
    Complete,
    Followup,
    FollowupTarget,
    JobRunOutcome,
    Task,
    TaskQ,
    TaskRegistry,
    WorkerSupervisor,
    WorkerService,
    WorkerServiceOptions,
)
from taskq.testing import FakeTaskQClient
from taskq.protocol import (
    ClaimWireRequest,
    PROTOCOL_DOCUMENT_REVISION,
    ClaimedJob,
    ClaimResult,
    ClaimState,
    EnqueueCommand,
    JobStatus,
    SettleOkResult,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)
from taskq.errors import TaskqValidationError
from taskq.sql.transport import _continuation_reason
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


def test_runtime_binds_current_protocol_document_revision() -> None:
    assert PROTOCOL_DOCUMENT_REVISION == "1.0.16"


def test_policy_advertisement_wire_bounds_and_canonicalizes() -> None:
    request = ClaimWireRequest(
        worker_id="worker",
        supported_policy_hashes=("b" * 64, "a" * 64),
    )
    assert request.supported_policy_hashes == ("a" * 64, "b" * 64)
    with pytest.raises(ValidationError):
        ClaimWireRequest(
            worker_id="worker",
            supported_policy_hashes=tuple(f"{index:064x}" for index in range(33)),
        )


def test_only_frozen_continuation_reasons_survive_driver_normalization() -> None:
    class DriverError(Exception):
        def __init__(self, detail: str) -> None:
            self.detail = detail

    safe = _continuation_reason(
        TaskqValidationError(cause=DriverError('{"reason":"continuation_policy_required"}'))
    )
    unsafe = _continuation_reason(
        TaskqValidationError(cause=DriverError('{"reason":"secret_sql_detail"}'))
    )
    assert safe.details == {"reason": "continuation_policy_required"}
    assert unsafe.details == {}


async def test_omitted_continuation_fields_preserve_legacy_custom_transport() -> None:
    class LegacyWorkflowTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create_workflow(
            self,
            workflow_key: str,
            kind: WorkflowKind | str,
            *,
            params: dict[str, Any] | None,
            declared_queues: tuple[str, ...],
            actor: str,
        ) -> WorkflowResult:
            self.calls.append(
                {
                    "workflow_key": workflow_key,
                    "kind": kind,
                    "params": params,
                    "declared_queues": declared_queues,
                    "actor": actor,
                }
            )
            return WorkflowResult(
                outcome="created",
                workflow_id=uuid4(),
                status=WorkflowStatus.RUNNING,
            )

        async def seal_workflow(
            self,
            workflow_id: UUID,
            actor: str,
        ) -> WorkflowResult:
            raise AssertionError("not used")

        async def aclose(self) -> None:
            return None

    transport = LegacyWorkflowTransport()
    client = TaskQ(transport)  # type: ignore[arg-type]

    result = await client.create_workflow(
        "legacy",
        WorkflowKind.DAG,
        params={"stable": True},
        declared_queues=("parent",),
        actor="producer",
    )

    assert result.status is WorkflowStatus.RUNNING
    assert transport.calls == [
        {
            "workflow_key": "legacy",
            "kind": WorkflowKind.DAG,
            "params": {"stable": True},
            "declared_queues": ("parent",),
            "actor": "producer",
        }
    ]


async def _parent_handler(payload: Input) -> Complete:
    return Complete(
        result={"value": payload.value},
        followups=(
            Followup(
                step="branch",
                job_type="graph.child",
                queue="child",
                payload={"value": payload.value + 1},
                workflow_member=True,
            ),
        ),
    )


def _registry() -> tuple[TaskRegistry, Task[Input, Output]]:
    child = Task(
        name="graph.child",
        queue="child",
        input_model=Input,
        output_model=Output,
    )
    parent = Task(
        name="graph.parent",
        queue="parent",
        input_model=Input,
        output_model=Output,
        followup_targets=(
            FollowupTarget(
                queue="child",
                job_type="graph.child",
                workflow_member=True,
                continuation_revision="1",
            ),
        ),
        handler=_parent_handler,
    )
    return TaskRegistry((parent, child)), parent


async def test_fake_policy_claim_and_atomic_member_handoff_are_coherent() -> None:
    registry, parent = _registry()
    policy = registry.compile_continuation_policy((parent,))
    fake = FakeTaskQClient(queues=("parent", "child", "detached"))
    workflow = await fake.create_workflow(
        "fake-policy",
        WorkflowKind.DAG,
        params={},
        declared_queues=("parent", "child"),
        actor="producer",
        member_limit=3,
        continuation_policy_hash=policy.continuation_policy_hash,
    )
    initial = await fake.enqueue(
        EnqueueCommand(
            queue="parent",
            job_type="graph.parent",
            payload={"value": 1},
            workflow_id=workflow.workflow_id,
            step_key="root",
        )
    )

    assert (await fake.claim("parent", "legacy")).jobs == ()
    claim = (
        await fake.claim(
            "parent",
            "policy-worker",
            supported_policy_hashes=(policy.continuation_policy_hash,),
        )
    ).jobs[0]
    assert claim.job_id == initial.job_id
    assert claim.continuation_policy_hash == policy.continuation_policy_hash

    settled = await fake.complete(
        claim.job_id,
        claim.attempt_id,
        "policy-worker",
        result={"value": 1},
        followups=(
            Followup(
                step="member",
                job_type="graph.child",
                queue="child",
                payload={"value": 2},
                workflow_member=True,
            ),
            Followup(
                step="detached",
                job_type="graph.audit",
                queue="detached",
                payload={},
            ),
        ),
        continuation_policy_hash=claim.continuation_policy_hash,
    )
    assert settled.result.value == "ok"

    member = (
        await fake.claim(
            "child",
            "policy-worker",
            supported_policy_hashes=(policy.continuation_policy_hash,),
        )
    ).jobs[0]
    detached = (await fake.claim("detached", "legacy")).jobs[0]
    assert member.workflow_id == workflow.workflow_id
    assert member.step_key == f"c:{claim.job_id}:member"
    assert member.continuation_policy_hash == policy.continuation_policy_hash
    assert detached.workflow_id is None
    assert detached.continuation_policy_hash is None


class _SettlementTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def release(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        cause: str,
        **kwargs: Any,
    ) -> SettleOkResult:
        self.calls.append(("release", {"cause": cause, **kwargs}))
        return SettleOkResult(result="ok", job_status=JobStatus.QUEUED, scheduled_at=None)

    async def complete(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        **kwargs: Any,
    ) -> SettleOkResult:
        self.calls.append(("complete", kwargs))
        return SettleOkResult(result="ok", job_status=JobStatus.SUCCEEDED, scheduled_at=None)

    async def fail(
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        error: str,
        **kwargs: Any,
    ) -> SettleOkResult:
        self.calls.append(("fail", {"error": error, **kwargs}))
        return SettleOkResult(result="ok", job_status=JobStatus.FAILED, scheduled_at=None)

    async def heartbeat(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("short handler must not reach a heartbeat")


def _claim(policy_hash: str) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue="parent",
        job_type="graph.parent",
        priority=0,
        payload={"value": 1},
        headers={},
        progress=None,
        attempt_id=uuid4(),
        attempt_number=1,
        failure_count=0,
        max_attempts=3,
        lease_expires_at=datetime.now(UTC),
        lease_seconds=15,
        workflow_id=uuid4(),
        step_key="root",
        continuation_policy_hash=policy_hash,
    )


async def test_worker_releases_unknown_policy_before_handler_and_soft_stops() -> None:
    transport = _SettlementTransport()
    registry, _ = _registry()
    supervisor = WorkerSupervisor(transport, registry, "worker")  # type: ignore[arg-type]
    report = await supervisor.run_job(_claim("a" * 64))

    assert report.outcome is JobRunOutcome.UNSUPPORTED_CONTINUATION_POLICY
    assert not supervisor.accepting
    for _ in range(10):
        if supervisor.stopped:
            break
        await asyncio.sleep(0)
    assert supervisor.stopped
    assert transport.calls == [
        (
            "release",
            {
                "cause": "released",
                "delay_seconds": 60,
                "progress": None,
            },
        )
    ]


async def test_worker_uses_claimed_policy_as_runtime_owned_complete_witness() -> None:
    transport = _SettlementTransport()
    registry, parent = _registry()
    policy = registry.compile_continuation_policy((parent,))
    supervisor = WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        registry,
        "worker",
        continuation_policies=(policy,),
    )
    report = await supervisor.run_job(_claim(policy.continuation_policy_hash))

    assert report.outcome is JobRunOutcome.SETTLED
    assert transport.calls[0][0] == "complete"
    assert transport.calls[0][1]["continuation_policy_hash"] == policy.continuation_policy_hash
    assert transport.calls[0][1]["followups"][0].workflow_member is True


async def test_worker_retains_old_policy_and_filters_new_registry_edges() -> None:
    old_registry, old_parent = _registry()
    old_policy = old_registry.compile_continuation_policy((old_parent,))

    async def new_edge_handler(payload: Input) -> Complete:
        return Complete(
            result={"value": payload.value},
            followups=(
                Followup(
                    step="new-branch",
                    job_type="graph.new_child",
                    queue="new_child",
                    payload={"value": payload.value + 1},
                    workflow_member=True,
                ),
            ),
        )

    child = Task(
        name="graph.child",
        queue="child",
        input_model=Input,
        output_model=Output,
    )
    new_child = Task(
        name="graph.new_child",
        queue="new_child",
        input_model=Input,
        output_model=Output,
    )
    retained_parent = Task(
        name="graph.parent",
        queue="parent",
        input_model=Input,
        output_model=Output,
        followup_targets=(
            FollowupTarget(
                queue="child",
                job_type="graph.child",
                workflow_member=True,
                continuation_revision="2",
            ),
            FollowupTarget(
                queue="new_child",
                job_type="graph.new_child",
                workflow_member=True,
                continuation_revision="2",
            ),
        ),
        handler=_parent_handler,
    )
    retained_registry = TaskRegistry((retained_parent, child, new_child))
    new_policy = retained_registry.compile_continuation_policy((retained_parent,))
    transport = _SettlementTransport()
    supervisor = WorkerSupervisor(
        transport,  # type: ignore[arg-type]
        retained_registry,
        "worker",
        continuation_policies=(old_policy, new_policy),
    )

    report = await supervisor.run_job(_claim(old_policy.continuation_policy_hash))

    assert report.outcome is JobRunOutcome.SETTLED
    assert transport.calls[0][0] == "complete"
    assert transport.calls[0][1]["followups"][0].job_type == "graph.child"
    assert transport.calls[0][1]["continuation_policy_hash"] == old_policy.continuation_policy_hash

    new_parent = replace(retained_parent, handler=new_edge_handler)
    new_registry = TaskRegistry((new_parent, child, new_child))
    rejected_transport = _SettlementTransport()
    rejecting_supervisor = WorkerSupervisor(
        rejected_transport,  # type: ignore[arg-type]
        new_registry,
        "worker",
        continuation_policies=(old_policy, new_policy),
    )
    rejected = await rejecting_supervisor.run_job(_claim(old_policy.continuation_policy_hash))

    assert rejected.outcome is JobRunOutcome.FOLLOWUP_REJECTED
    assert rejected_transport.calls == [
        (
            "fail",
            {
                "error": "invalid_followup: rejected by active worker or SQL contract",
                "retryable": False,
                "progress": None,
            },
        )
    ]


async def test_worker_service_advertises_policy_in_claim_and_presence() -> None:
    registry, parent = _registry()
    policy = registry.compile_continuation_policy((parent,))
    transport = ScriptedTransport()
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        registry,
        "policy-worker",
        options=WorkerServiceOptions(queues=("parent",), listen=False),
        continuation_policies=(policy,),
        clock=ManualClock(),
    )

    await service.start()
    for _ in range(100):
        commands = {call.command for call in transport.calls}
        if {"claim", "worker_heartbeat"} <= commands:
            break
        await asyncio.sleep(0)
    await service.aclose()

    claim = next(call for call in transport.calls if call.command == "claim")
    presence = next(call for call in transport.calls if call.command == "worker_heartbeat")
    assert claim.arguments["supported_policy_hashes"] == (policy.continuation_policy_hash,)
    assert presence.arguments["meta"]["continuation_policy_hashes"] == [
        policy.continuation_policy_hash
    ]


async def test_worker_service_soft_stops_on_claim_filter_capability_skew() -> None:
    transport = ScriptedTransport()
    transport.script(
        "claim",
        ClaimResult(
            state=ClaimState.CLAIMED,
            jobs=(_claim("f" * 64),),
        ),
    )
    registry, _ = _registry()
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        registry,
        "skewed-worker",
        options=WorkerServiceOptions(queues=("parent",), listen=False),
        clock=ManualClock(),
    )

    await service.start()
    await asyncio.wait_for(service.run(), timeout=1)

    release = next(call for call in transport.calls if call.command == "release")
    assert release.arguments["cause"] == "released"
    assert release.arguments["delay_seconds"] == 60
    assert not any(call.command == "complete" for call in transport.calls)
    assert service.stopped
