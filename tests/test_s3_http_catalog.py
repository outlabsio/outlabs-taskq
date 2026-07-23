"""Hand-derived Protocol-v1.0.9 catalog and wire-model oracle."""

from __future__ import annotations

import inspect
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from taskq.protocol import (
    HTTP_COMMAND_SPECS,
    AdmissionCancelWireData,
    AttemptRequest,
    ClaimedJob,
    ClaimedJobWire,
    CommandName,
    CompleteWireRequest,
    EnqueueWireData,
    EnqueueWireRequest,
    EnqueueManyWireRequest,
    EnqueueManyItem,
    Followup,
    HeartbeatWireRequest,
    HttpCommandName,
    HttpSurface,
    PROTOCOL_DOCUMENT_REVISION,
    PROTOCOL_MAJOR,
    RetryClass,
    WorkerPresenceWireRequest,
)
from taskq.transport import (
    AuthorizationLookupTransport,
    HousekeeperTransport,
    ObserverTransport,
    OperatorTransport,
    ProducerTransport,
    RunnerTransport,
    non_owning_transport_view,
)
from taskq.http import AsyncTaskqHttpClient, TaskqHttpClient


# Written from the Tier-0 Protocol route tables before consulting generated metadata.
EXPECTED_HTTP_IDENTITIES = {
    "meta": ("GET", "/taskq/v1/meta", "read", "deployment_policy", "active"),
    "ensure_queue": ("PUT", "/taskq/v1/queues/{queue}", "admin", "path", "active"),
    "enqueue": (
        "POST",
        "/taskq/v1/queues/{queue}/jobs",
        "enqueue",
        "path",
        "active",
    ),
    "enqueue_many": (
        "POST",
        "/taskq/v1/queues/{queue}/jobs/batch",
        "enqueue",
        "path",
        "active",
    ),
    "reserve_admission": (
        "POST",
        "/taskq/v1/queues/{queue}/admissions/reserve",
        "enqueue",
        "path",
        "active",
    ),
    "finish_admission": (
        "POST",
        "/taskq/v1/queues/{queue}/admissions/finish",
        "enqueue",
        "path",
        "active",
    ),
    "cancel_admission": (
        "POST",
        "/taskq/v1/queues/{queue}/admissions/cancel",
        "enqueue",
        "path",
        "active",
    ),
    "claim": ("POST", "/taskq/v1/queues/{queue}/claims", "run", "path", "active"),
    "heartbeat": (
        "POST",
        "/taskq/v1/jobs/{job_id}/heartbeat",
        "run",
        "job_lookup",
        "active",
    ),
    "complete": (
        "POST",
        "/taskq/v1/jobs/{job_id}/complete",
        "run",
        "job_lookup",
        "active",
    ),
    "fail": ("POST", "/taskq/v1/jobs/{job_id}/fail", "run", "job_lookup", "active"),
    "release": (
        "POST",
        "/taskq/v1/jobs/{job_id}/release",
        "run",
        "job_lookup",
        "active",
    ),
    "snooze": (
        "POST",
        "/taskq/v1/jobs/{job_id}/snooze",
        "run",
        "job_lookup",
        "active",
    ),
    "cancel_running": (
        "POST",
        "/taskq/v1/jobs/{job_id}/cancel-running",
        "run",
        "job_lookup",
        "active",
    ),
    "worker_heartbeat": (
        "POST",
        "/taskq/v1/workers/heartbeat",
        "run",
        "declared_queues",
        "active",
    ),
    "get_job": ("GET", "/taskq/v1/jobs/{job_id}", "read", "job_lookup", "active"),
    "get_queue_stats": (
        "GET",
        "/taskq/v1/stats/queues/{queue}",
        "read",
        "path",
        "active",
    ),
    "list_queue_stats": (
        "GET",
        "/taskq/v1/stats/queues",
        "read",
        "global",
        "active",
    ),
    "metrics": ("GET", "/taskq/metrics", None, "deployment_policy", "active"),
    "pause_queue": (
        "POST",
        "/taskq/v1/queues/{queue}/pause",
        "control",
        "path",
        "active",
    ),
    "resume_queue": (
        "POST",
        "/taskq/v1/queues/{queue}/resume",
        "control",
        "path",
        "active",
    ),
    "cancel": (
        "POST",
        "/taskq/v1/jobs/{job_id}/cancel",
        "control",
        "job_lookup",
        "active",
    ),
    "redrive": (
        "POST",
        "/taskq/v1/jobs/{job_id}/redrive",
        "control",
        "job_lookup",
        "active",
    ),
    "expire_job": (
        "POST",
        "/taskq/v1/jobs/{job_id}/expire",
        "control",
        "job_lookup",
        "active",
    ),
    "expire_worker_leases": (
        "POST",
        "/taskq/v1/workers/{worker_id}/expire-leases",
        "control",
        "global",
        "active",
    ),
    "purge_queued": (
        "POST",
        "/taskq/v1/queues/{queue}/purge",
        "control",
        "path",
        "active",
    ),
    "run_now": (
        "POST",
        "/taskq/v1/jobs/{job_id}/run-now",
        "control",
        "job_lookup",
        "active",
    ),
    "reprioritize": (
        "POST",
        "/taskq/v1/jobs/{job_id}/reprioritize",
        "control",
        "job_lookup",
        "active",
    ),
    "set_concurrency_limit": (
        "PUT",
        "/taskq/v1/concurrency-limits/{key}",
        "admin",
        "global",
        "active",
    ),
    "request_worker_shutdown": (
        "POST",
        "/taskq/v1/workers/shutdown-requests",
        "control",
        "global",
        "active",
    ),
    "list_workers": ("GET", "/taskq/v1/workers", "read", "global", "gated"),
    "get_queue": ("GET", "/taskq/v1/queues/{queue}", "read", "path", "active"),
    "list_jobs": ("GET", "/taskq/v1/jobs", "read", "query", "active"),
}

# Independently transcribed from the Tier-0 outcome tables, including negative-only rows.
EXPECTED_HTTP_OUTCOMES = {
    "meta": {"ok": 200},
    "ensure_queue": {"created": 201, "updated": 200, "unchanged": 200},
    "enqueue": {"created": 201, "existed": 200},
    "enqueue_many": {"ok": 200},
    "reserve_admission": {"reserved": 200, "pending": 202, "admitted": 200},
    "finish_admission": {"created": 201, "existed": 200},
    "cancel_admission": {
        "cancelled": 200,
        "already_cancelled": 200,
        "expired": 200,
        "already_admitted": 200,
    },
    "claim": {"claimed": 200, "empty": 200, "timeout": 200, "paused": 200, "unavailable": 200},
    "heartbeat": {"ok": 200, "lost": 409},
    "complete": {"ok": 200, "already_settled": 200, "settle_conflict": 409, "lost": 409},
    "fail": {
        "retry_scheduled": 200,
        "dead": 200,
        "already_settled": 200,
        "settle_conflict": 409,
        "lost": 409,
    },
    "release": {"ok": 200, "already_settled": 200, "settle_conflict": 409, "lost": 409},
    "snooze": {"ok": 200, "already_settled": 200, "settle_conflict": 409, "lost": 409},
    "cancel_running": {"ok": 200, "already_settled": 200, "settle_conflict": 409, "lost": 409},
    "worker_heartbeat": {"continue": 200, "shutdown_requested": 200},
    "get_job": {"ok": 200},
    "get_queue_stats": {"ok": 200},
    "list_queue_stats": {"ok": 200},
    "metrics": {"ok": 200},
    "pause_queue": {"paused": 200, "already_paused": 200},
    "resume_queue": {"resumed": 200, "already_resumed": 200},
    "cancel": {"cancelled": 200, "cancel_requested": 202, "already_terminal": 200},
    "redrive": {"redriven": 200},
    "expire_job": {"expired_and_reaped": 200, "not_running": 409},
    "expire_worker_leases": {"ok": 200},
    "purge_queued": {"ok": 200},
    "run_now": {"ok": 200},
    "reprioritize": {"ok": 200},
    "set_concurrency_limit": {"created": 201, "updated": 200, "unchanged": 200},
    "request_worker_shutdown": {"accepted": 202},
    "list_workers": {},
    "get_queue": {"ok": 200},
    "list_jobs": {"ok": 200},
}


def _method_names(protocol: type[object]) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(protocol)
        if not name.startswith("_") and inspect.isfunction(value)
    }


def test_capability_protocol_method_sets_are_exact() -> None:
    assert _method_names(ProducerTransport) == {
        "reserve_admission",
        "finish_admission",
        "cancel_admission",
        "enqueue",
        "enqueue_many",
        "aclose",
    }
    assert _method_names(RunnerTransport) == {
        "claim",
        "heartbeat",
        "complete",
        "fail",
        "snooze",
        "release",
        "cancel_running",
        "worker_heartbeat",
        "aclose",
    }
    assert _method_names(ObserverTransport) == {
        "get_job",
        "get_queue_stats",
        "get_queue_profile",
        "list_jobs",
        "get_contract_meta",
        "metrics",
        "aclose",
    }
    assert _method_names(AuthorizationLookupTransport) == {
        "get_authorization_projection",
        "aclose",
    }
    assert _method_names(HousekeeperTransport) == {"tick", "janitor", "aclose"}
    assert "redrive_failed" in _method_names(OperatorTransport)


def test_cancel_admission_wire_projection_is_exact() -> None:
    spec = HTTP_COMMAND_SPECS[HttpCommandName.CANCEL_ADMISSION]
    assert spec.data_model is AdmissionCancelWireData
    assert set(AdmissionCancelWireData.model_json_schema()["properties"]) == {
        "job_id",
        "receipt",
        "receipt_expires_at",
    }


async def test_non_owning_capability_view_close_is_a_noop() -> None:
    class Underlying:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

        def marker(self) -> str:
            return "delegated"

    underlying = Underlying()
    view = non_owning_transport_view(underlying)
    assert view.marker() == "delegated"
    await view.aclose()
    assert not underlying.closed


def _assert_catalog_matches_hand_derived_oracle(
    specs: object = HTTP_COMMAND_SPECS,
) -> None:
    observed = specs  # keep the oracle independent from the generator implementation
    actual = {
        name.value: (
            spec.method,
            spec.path,
            spec.action.value if spec.action is not None else None,
            spec.queue_source.value,
            spec.surface.value,
        )
        for name, spec in observed.items()
    }
    assert actual == EXPECTED_HTTP_IDENTITIES
    assert {
        name.value: dict(spec.outcomes) for name, spec in observed.items()
    } == EXPECTED_HTTP_OUTCOMES
    assert set(observed) == set(HttpCommandName)


def test_http_catalog_matches_hand_derived_tier0_oracle() -> None:
    assert PROTOCOL_MAJOR == 1
    assert PROTOCOL_DOCUMENT_REVISION == "1.0.9"
    _assert_catalog_matches_hand_derived_oracle()


def test_hand_derived_catalog_oracle_rejects_a_deliberate_generator_mutation() -> None:
    mutated = dict(HTTP_COMMAND_SPECS)
    enqueue = mutated[HttpCommandName.ENQUEUE]
    mutated[HttpCommandName.ENQUEUE] = replace(enqueue, path="/taskq/v1/jobs")
    with pytest.raises(AssertionError):
        _assert_catalog_matches_hand_derived_oracle(mutated)


def test_http_catalog_excludes_db_only_commands_and_has_honest_gates() -> None:
    active_sql = {
        spec.sql_command
        for spec in HTTP_COMMAND_SPECS.values()
        if spec.surface is HttpSurface.ACTIVE and spec.sql_command is not None
    }
    assert {
        CommandName.GET_AUTHORIZATION_PROJECTION,
        CommandName.REDRIVE_FAILED,
        CommandName.TICK,
        CommandName.JANITOR,
    }.isdisjoint(active_sql)
    for name in (HttpCommandName.GET_QUEUE, HttpCommandName.LIST_JOBS):
        assert HTTP_COMMAND_SPECS[name].surface is HttpSurface.ACTIVE
        assert HTTP_COMMAND_SPECS[name].outcomes == {"ok": 200}
    worker_list = HTTP_COMMAND_SPECS[HttpCommandName.LIST_WORKERS]
    assert worker_list.surface is HttpSurface.GATED
    assert not worker_list.outcomes

    generated = {
        name.value
        for name, spec in HTTP_COMMAND_SPECS.items()
        if spec.surface is not HttpSurface.DEFERRED
    }
    for client_type in (AsyncTaskqHttpClient, TaskqHttpClient):
        assert all(hasattr(client_type, method) for method in generated)
        assert hasattr(client_type, HttpCommandName.GET_QUEUE.value)
        assert hasattr(client_type, HttpCommandName.LIST_JOBS.value)


def test_retry_classes_are_command_specific_and_settlement_is_worker_owned() -> None:
    assert HTTP_COMMAND_SPECS[HttpCommandName.ENQUEUE].retry_class is RetryClass.KEYED_ENQUEUE
    assert HTTP_COMMAND_SPECS[HttpCommandName.ENQUEUE_MANY].retry_class is RetryClass.KEYED_BATCH
    assert HTTP_COMMAND_SPECS[HttpCommandName.CLAIM].retry_class is RetryClass.NEVER
    for name in (
        HttpCommandName.COMPLETE,
        HttpCommandName.FAIL,
        HttpCommandName.RELEASE,
        HttpCommandName.SNOOZE,
        HttpCommandName.CANCEL_RUNNING,
    ):
        assert HTTP_COMMAND_SPECS[name].retry_class is RetryClass.WORKER_SETTLEMENT


def test_enqueue_wire_fields_and_bulk_index_are_exact() -> None:
    assert set(EnqueueWireData.model_json_schema()["properties"]) == {"job_id"}
    assert "queue" not in EnqueueWireRequest.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        EnqueueWireRequest(job_type="tests.echo", payload={}, typo=True)


def test_h09_json_and_collection_bounds_are_enforced_by_wire_models() -> None:
    with pytest.raises(ValidationError, match="payload exceeds"):
        EnqueueWireRequest(job_type="tests.echo", payload={"value": "x" * 65536})
    with pytest.raises(ValidationError, match="headers exceeds"):
        EnqueueWireRequest(job_type="tests.echo", payload={}, headers={"value": "x" * 8192})
    with pytest.raises(ValidationError, match="progress exceeds"):
        HeartbeatWireRequest(
            attempt_id=uuid4(),
            worker_id="worker-1",
            progress={"value": "x" * 2048},
        )
    with pytest.raises(ValidationError):
        EnqueueManyWireRequest(
            items=tuple(EnqueueManyItem(job_type="tests.echo") for _ in range(1001))
        )
    with pytest.raises(ValidationError, match="distinct"):
        WorkerPresenceWireRequest(worker_id="worker-1", queues=("a", "a"))


def test_complete_followup_wire_model_is_exactly_the_closed_protocol_shape() -> None:
    assert set(Followup.model_json_schema()["properties"]) == {
        "step",
        "job_type",
        "queue",
        "payload",
        "headers",
        "priority",
        "max_attempts",
        "lease_seconds",
        "scheduled_at",
    }
    request = CompleteWireRequest(
        attempt_id=uuid4(),
        worker_id="worker-1",
        followups=({"step": "next", "job_type": "tests.child"},),
    )
    assert request.followups == (Followup(step="next", job_type="tests.child"),)
    with pytest.raises(ValidationError):
        CompleteWireRequest(
            attempt_id=uuid4(),
            worker_id="worker-1",
            followups=({"step": "next", "job_type": "tests.child", "typo": True},),
        )


def test_fence_is_write_only_in_requests_and_only_claim_serializes_it() -> None:
    request_schema = AttemptRequest.model_json_schema()
    assert request_schema["properties"]["attempt_id"]["writeOnly"] is True
    fence = uuid4()
    core = ClaimedJob(
        job_id=uuid4(),
        queue="emails",
        job_type="tests.echo",
        priority=100,
        payload={},
        headers={},
        progress=None,
        attempt_id=fence,
        attempt_number=1,
        failure_count=0,
        max_attempts=3,
        lease_expires_at="2026-07-19T12:00:00Z",
        lease_seconds=30,
    )
    assert "attempt_id" not in core.model_dump(mode="json")
    wire = ClaimedJobWire.model_validate({**core.model_dump(), "attempt_id": fence})
    assert wire.model_dump(mode="json")["attempt_id"] == str(fence)
    assert "attempt_id" not in repr(wire)
