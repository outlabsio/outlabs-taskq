"""Deploy-safe availability posture for HTTP-backed workers.

A rolling API deployment or dependency outage must leave a worker alive but
unready under capped jittered backoff, never turn it into a process exit; the
bounded fail-closed posture stays exactly as before for every other claim
error class.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel

from taskq import (
    ClaimedJob,
    Task,
    TaskRegistry,
    WorkerService,
    WorkerServiceOptions,
)
from taskq.errors import (
    TaskqInternalError,
    TaskqUnavailableError,
)
from taskq.http.client import _decode_envelope
from taskq.protocol import (
    HTTP_COMMAND_SPECS,
    ClaimResult,
    ClaimState,
    HttpCommandName,
)
from tests.worker_support import ManualClock, ScriptedTransport


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _registry() -> TaskRegistry:
    async def handler(payload: Input) -> Output:
        return Output(doubled=payload.value * 2)

    return TaskRegistry(
        (
            Task(
                name="alpha.work",
                queue="alpha",
                input_model=Input,
                output_model=Output,
                handler=handler,
            ),
        )
    )


def _claim() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        queue="alpha",
        job_type="alpha.work",
        priority=100,
        payload={"value": 1},
        headers={},
        progress=None,
        attempt_id=uuid4(),
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        lease_expires_at=datetime.now(UTC),
        lease_seconds=15,
    )


def _service(transport: ScriptedTransport, clock: ManualClock, **options: object) -> WorkerService:
    return WorkerService(
        transport,  # type: ignore[arg-type]
        _registry(),
        "worker-1",
        options=WorkerServiceOptions(queues=("alpha",), listen=False, **options),
        clock=clock,
    )


async def _spin(
    predicate: Callable[[], bool],
    clock: ManualClock,
    *,
    step: float = 5.0,
    turns: int = 600,
) -> None:
    for _ in range(turns):
        if predicate():
            return
        clock.advance(step)
        await asyncio.sleep(0)
    assert predicate()


def _claim_calls(transport: ScriptedTransport) -> int:
    return sum(1 for call in transport.calls if call.command == "claim")


async def test_sustained_unavailable_outage_stays_alive_unready_and_recovers() -> None:
    transport = ScriptedTransport()
    transport.script("claim", *(TaskqUnavailableError() for _ in range(12)))
    transport.script("claim", ClaimResult(state=ClaimState.CLAIMED, jobs=(_claim(),)))
    clock = ManualClock()
    service = _service(transport, clock)
    await service.start()

    # Twelve consecutive availability errors sail past claim_fatal_threshold
    # (8) without failing the service: alive, unready, still backing off.
    await _spin(lambda: _claim_calls(transport) >= 12, clock)
    assert not service.snapshot().fatal
    assert not service.stopped

    # The dependency returns: the very next successful claim executes work.
    await _spin(lambda: any(call.command == "complete" for call in transport.calls), clock)
    assert not service.snapshot().fatal
    assert service.snapshot().claimed_jobs == 1
    await service.aclose()


async def test_unavailable_fatal_threshold_restores_the_bounded_posture() -> None:
    transport = ScriptedTransport()
    transport.script("claim", *(TaskqUnavailableError() for _ in range(6)))
    clock = ManualClock()
    service = _service(transport, clock, unavailable_fatal_threshold=5)
    await service.start()
    await _spin(lambda: service.snapshot().fatal, clock)
    assert _claim_calls(transport) == 5
    await service.aclose()


async def test_non_availability_claim_errors_still_fail_closed_at_threshold() -> None:
    transport = ScriptedTransport()
    transport.script("claim", *(TaskqInternalError() for _ in range(10)))
    clock = ManualClock()
    service = _service(transport, clock)
    await service.start()
    await _spin(lambda: service.snapshot().fatal, clock)
    assert _claim_calls(transport) == 8
    await service.aclose()


async def test_long_outage_backoff_exponent_is_guarded() -> None:
    transport = ScriptedTransport()
    clock = ManualClock()
    service = _service(transport, clock)
    for _ in range(1_200):
        service._note_claim_error("alpha", TaskqUnavailableError())
    assert not service.snapshot().fatal
    retry_at = service._claim_retry_at["alpha"]
    cap = service.options.claim_backoff_cap
    assert retry_at - clock.monotonic() <= cap * 1.5
    await service.aclose()


def _enqueue_spec() -> object:
    return HTTP_COMMAND_SPECS[HttpCommandName.ENQUEUE]


def _bare_response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers or {},
        text="Bad Gateway",
        request=httpx.Request("POST", "http://taskq.test/taskq/v1/queues/q/jobs"),
    )


def test_decode_envelope_maps_bare_5xx_to_unavailable() -> None:
    for status in (502, 503, 504):
        with pytest.raises(TaskqUnavailableError) as excinfo:
            _decode_envelope(
                _bare_response(status),
                spec=_enqueue_spec(),  # type: ignore[arg-type]
                sent_request_id="req-1",
            )
        assert excinfo.value.details == {
            "reason": "upstream_unavailable",
            "status": status,
        }
        assert excinfo.value.retryable is True


def test_decode_envelope_honors_retry_after_on_bare_503() -> None:
    with pytest.raises(TaskqUnavailableError) as excinfo:
        _decode_envelope(
            _bare_response(503, headers={"Retry-After": "7"}),
            spec=_enqueue_spec(),  # type: ignore[arg-type]
            sent_request_id="req-1",
        )
    assert excinfo.value.retry_after_seconds == 7.0


def test_decode_envelope_keeps_protocol_mismatch_internal_off_the_5xx_path() -> None:
    for status in (200, 400, 500):
        with pytest.raises(TaskqInternalError) as excinfo:
            _decode_envelope(
                _bare_response(status),
                spec=_enqueue_spec(),  # type: ignore[arg-type]
                sent_request_id="req-1",
            )
        assert excinfo.value.details == {"reason": "missing_or_invalid_protocol_header"}
