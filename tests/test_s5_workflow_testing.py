"""Native consumer fake and high-level workflow API evidence."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from taskq import TaskQ
from taskq.errors import TaskqConflictError, TaskqValidationError
from taskq.http import TaskqHttpClient
from taskq.protocol import EnqueueCommand, JobStatus, WorkflowStatus
from taskq.testing import FakeTaskQClient
from taskq.transport import (
    ProducerTransport,
    WorkflowAuthorizationLookupTransport,
    WorkflowOperatorTransport,
    WorkflowProducerTransport,
)


async def test_fake_is_a_native_structural_workflow_transport() -> None:
    fake = FakeTaskQClient(queues=("fake_a", "fake_b"))
    assert isinstance(fake, ProducerTransport)
    assert isinstance(fake, WorkflowProducerTransport)
    assert isinstance(fake, WorkflowAuthorizationLookupTransport)
    assert isinstance(fake, WorkflowOperatorTransport)

    created = await fake.create_workflow(
        "fake-flow",
        "dag",
        params={"stable": True},
        declared_queues=("fake_b", "fake_a"),
        actor="unit",
    )
    existed = await fake.create_workflow(
        "fake-flow",
        "dag",
        params={"stable": True},
        declared_queues=("fake_a", "fake_b"),
        actor="unit",
    )
    assert created.workflow_id == existed.workflow_id
    assert (created.outcome, existed.outcome) == ("created", "existed")
    projection = await fake.get_workflow_authorization_projection(created.workflow_id)
    assert projection.declared_queues == ("fake_a", "fake_b")
    with pytest.raises(TaskqValidationError):
        await fake.enqueue(
            EnqueueCommand(
                queue="fake_outside",
                job_type="tests.outside",
                payload={},
                workflow_id=created.workflow_id,
                step_key="outside",
            )
        )

    parent = await fake.enqueue(
        EnqueueCommand(
            queue="fake_a",
            job_type="tests.parent",
            payload={},
            workflow_id=created.workflow_id,
            step_key="parent",
        )
    )
    sibling = await fake.enqueue(
        EnqueueCommand(
            queue="fake_a",
            job_type="tests.sibling",
            payload={},
            workflow_id=created.workflow_id,
            step_key="sibling",
        )
    )
    child_command = EnqueueCommand(
        queue="fake_b",
        job_type="tests.child",
        payload={},
        workflow_id=created.workflow_id,
        step_key="child",
        depends_on=(parent.job_id, sibling.job_id),
    )
    child = await fake.enqueue(child_command)
    replay = await fake.enqueue(
        child_command.model_copy(update={"depends_on": (sibling.job_id, parent.job_id)})
    )
    assert child.job_id == replay.job_id
    assert {job.job_id: job.status for job in fake._jobs.values()}[
        child.job_id
    ] is JobStatus.BLOCKED

    claim = await fake.claim("fake_a", "fake-worker", job_id=parent.job_id)
    attempt = claim.jobs[0]
    await fake.complete(parent.job_id, attempt.attempt_id, "fake-worker")
    assert {job.job_id: job.status for job in fake._jobs.values()}[
        child.job_id
    ] is JobStatus.BLOCKED
    sibling_claim = await fake.claim("fake_a", "fake-worker", job_id=sibling.job_id)
    sibling_attempt = sibling_claim.jobs[0]
    await fake.complete(sibling.job_id, sibling_attempt.attempt_id, "fake-worker")
    assert {job.job_id: job.status for job in fake._jobs.values()}[child.job_id] is JobStatus.QUEUED
    sealed = await fake.seal_workflow(created.workflow_id, "unit")
    assert sealed.outcome == "sealed"


async def test_fake_cancellation_cascades_and_high_level_facade_is_native() -> None:
    fake = FakeTaskQClient(queues=("fake_cancel",))
    facade = TaskQ(fake, validate_job_types=False)
    workflow = await facade.create_workflow(
        "cancel-flow",
        "batch",
        declared_queues=("fake_cancel",),
        actor="unit",
    )
    parent = await facade.enqueue_raw(
        queue="fake_cancel",
        job_type="tests.parent",
        payload={},
        workflow_id=workflow.workflow_id,
        step_key="parent",
    )
    child = await facade.enqueue_raw(
        queue="fake_cancel",
        job_type="tests.child",
        payload={},
        workflow_id=workflow.workflow_id,
        step_key="child",
        depends_on=(parent.job_id,),
    )
    cancelled = await fake.cancel_workflow(workflow.workflow_id, "operator", "test")
    assert cancelled.outcome == "cancel_requested"
    assert cancelled.status is WorkflowStatus.CANCELLED
    assert fake._jobs[parent.job_id].status is JobStatus.CANCELLED
    assert fake._jobs[child.job_id].status is JobStatus.CANCELLED

    with pytest.raises(TaskqConflictError) as mismatch:
        await fake.enqueue(
            EnqueueCommand(
                queue="fake_cancel",
                job_type="tests.changed",
                payload={},
                workflow_id=workflow.workflow_id,
                step_key="parent",
            )
        )
    assert mismatch.value.details == {"reason": "workflow_step_mismatch"}


def test_workflow_models_reject_ambiguous_or_invalid_dependency_shapes() -> None:
    with pytest.raises(ValueError):
        EnqueueCommand(
            queue="fake",
            job_type="tests.invalid",
            payload={},
            workflow_id=uuid4(),
        )
    duplicate = uuid4()
    with pytest.raises(ValueError):
        EnqueueCommand(
            queue="fake",
            job_type="tests.invalid",
            payload={},
            workflow_id=uuid4(),
            step_key="step",
            depends_on=(duplicate, duplicate),
        )


def test_sync_http_client_exposes_typed_workflow_surface_without_actor_on_wire() -> None:
    workflow_id = uuid4()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        request_id = request.headers["Taskq-Request-Id"]
        path = request.url.path
        outcome, status = (
            ("created", 201)
            if path.endswith("/workflows")
            else ("sealed", 200)
            if path.endswith("/seal")
            else ("cancel_requested", 202)
        )
        return httpx.Response(
            status,
            headers={
                "Taskq-Protocol-Version": "1",
                "Taskq-Request-Id": request_id,
            },
            json={
                "protocol_version": 1,
                "request_id": request_id,
                "outcome": outcome,
                "data": {"workflow_id": str(workflow_id), "status": "running"},
            },
        )

    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = TaskqHttpClient(
        "http://test",
        bearer_token="secret",
        client=raw,
        request_id_provider=lambda: str(uuid4()),
    )
    try:
        assert (
            client.create_workflow(
                "sync-flow",
                "dag",
                declared_queues=("queue_a",),
                actor="must-not-cross",
            ).outcome
            == "created"
        )
        assert client.seal_workflow(workflow_id, "must-not-cross").outcome == "sealed"
        assert (
            client.cancel_workflow(workflow_id, "must-not-cross", "bounded").outcome
            == "cancel_requested"
        )
        assert all(b"must-not-cross" not in request.content for request in requests)
        assert requests[-1].content == b'{"reason":"bounded"}'
    finally:
        client.close()
        raw.close()
