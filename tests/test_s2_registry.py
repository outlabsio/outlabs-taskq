"""S2-01 typed task, protocol value, error, and registry contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from hypothesis import given, strategies as st
from pydantic import BaseModel, ValidationError

from taskq import (
    JobContext,
    ClaimedJob,
    EnqueueCreatedResult,
    EnqueueExistedResult,
    EnqueueStatus,
    RetryStrategy,
    Task,
    TaskRegistry,
    TaskqConfigError,
    UnknownTaskError,
)
from taskq.errors import (
    TaskqBackpressureError,
    TaskqCapabilityError,
    TaskqConflictError,
    TaskqInternalError,
    TaskqNotFoundError,
    TaskqUnavailableError,
    TaskqValidationError,
    TaskqVersionError,
    taskq_error_from_exception,
)
from taskq.protocol import (
    COMMAND_SPECS,
    CLAIM_BATCH_ADAPTER,
    ENQUEUE_MANY_ITEMS_ADAPTER,
    ENQUEUE_RESULT_ADAPTER,
    SETTLE_RESULT_ADAPTER,
    CommandName,
    EnqueueCommand,
    EnqueueManyItem,
    SettleOutcome,
    TQ_ERROR_REGISTRY,
    TqCode,
)


class Input(BaseModel):
    value: int


class Output(BaseModel):
    doubled: int


def _task(
    name: str = "math.double",
    *,
    aliases: tuple[str, ...] = (),
    queue: str = "default",
) -> Task[Input, Output]:
    return Task(
        name=name,
        queue=queue,
        input_model=Input,
        output_model=Output,
        aliases=aliases,
    )


@pytest.mark.parametrize(
    "name",
    ["a", "emails.send", "v2.email_2.send_now", "a" * 120],
)
def test_valid_wire_names(name: str) -> None:
    assert _task(name).name == name


@pytest.mark.parametrize(
    "name",
    ["", ".email", "email.", "Email.send", "email-send", "1email.send", "a..b", "a" * 121],
)
def test_invalid_wire_names(name: str) -> None:
    with pytest.raises(TaskqConfigError, match="wire-name"):
        _task(name)


@pytest.mark.parametrize("queue", ["a", "queue_2", "0", "q" * 57])
def test_valid_queue_names(queue: str) -> None:
    assert _task(queue=queue).queue == queue


@pytest.mark.parametrize("queue", ["", "Email", "email.send", "email-send", "q" * 58])
def test_invalid_queue_names(queue: str) -> None:
    with pytest.raises(TaskqConfigError, match="queue"):
        _task(queue=queue)


def test_task_metadata_is_immutable_and_payload_is_json_mode() -> None:
    task = _task()
    assert task.validate_payload({"value": 4}) == {"value": 4}
    with pytest.raises(ValidationError):
        task.validate_payload({"value": "not-an-int"})
    with pytest.raises((AttributeError, TypeError)):
        task.queue = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"aliases": ("old", "old")}, "aliases"),
        ({"aliases": ("math.double",)}, "aliases"),
        ({"retry": 0}, "retry"),
        ({"retry": 101}, "retry"),
        ({"priority": -1}, "priority"),
        ({"priority": 1001}, "priority"),
        ({"lease_seconds": 14}, "lease_seconds"),
        ({"lease_seconds": 86401}, "lease_seconds"),
    ],
)
def test_invalid_task_policy(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(TaskqConfigError, match=message):
        Task(
            name="math.double",
            queue="default",
            input_model=Input,
            output_model=Output,
            **kwargs,
        )


def test_retry_strategy_is_frozen_and_closed() -> None:
    strategy = RetryStrategy(max_attempts=5, mode="fixed", retry_exceptions=(TimeoutError,))
    assert strategy.max_attempts == 5
    with pytest.raises(ValidationError):
        RetryStrategy(mode="random")
    with pytest.raises(ValidationError):
        RetryStrategy(base_seconds=60, cap_seconds=30)
    with pytest.raises(ValidationError):
        strategy.max_attempts = 4  # type: ignore[misc]


async def valid_handler(payload: Input) -> Output:
    return Output(doubled=payload.value * 2)


def sync_handler(payload: Input) -> Output:
    return Output(doubled=payload.value * 2)


async def contextual_handler(ctx: JobContext, payload: Input) -> Output:
    ctx.raise_if_cancelled()
    return Output(doubled=payload.value * 2)


async def wrong_handler(payload: Output) -> Input:
    return Input(value=payload.doubled)


def test_optional_handler_annotations_are_validated() -> None:
    task = Task(
        name="math.double",
        queue="default",
        input_model=Input,
        output_model=Output,
        handler=valid_handler,
    )
    assert task.handler is valid_handler
    sync_task = Task(
        name="math.sync",
        queue="default",
        input_model=Input,
        output_model=Output,
        handler=sync_handler,
    )
    contextual_task = Task(
        name="math.contextual",
        queue="default",
        input_model=Input,
        output_model=Output,
        handler=contextual_handler,
    )
    assert sync_task.handler_is_async is False
    assert contextual_task.handler_is_async is True
    with pytest.raises(TaskqConfigError, match="input annotation"):
        Task(
            name="math.wrong",
            queue="default",
            input_model=Input,
            output_model=Output,
            handler=wrong_handler,
        )


def test_alias_resolution_and_deterministic_iteration() -> None:
    first = _task("math.double", aliases=("math.double_v1",))
    second = _task("math.triple")
    registry = TaskRegistry([first, second])
    assert list(registry) == [first, second]
    assert registry.resolve("math.double_v1") is first
    assert registry.canonical("math.double_v1") == "math.double"
    assert registry.require(first) is first
    assert registry.require("math.triple") is second


@pytest.mark.parametrize(
    "candidate",
    [
        _task("math.double"),
        _task("math.double_v1"),
        _task("math.other", aliases=("math.double",)),
        _task("math.other", aliases=("math.double_v1",)),
    ],
)
def test_every_registry_collision_direction_is_atomic(candidate: Task[Input, Output]) -> None:
    existing = _task("math.double", aliases=("math.double_v1",))
    registry = TaskRegistry([existing])
    before = list(registry)
    with pytest.raises(TaskqConfigError, match="collision"):
        registry.register(candidate)
    assert list(registry) == before
    assert registry.resolve("math.other") is None


def test_register_many_is_atomic_across_the_whole_batch() -> None:
    existing = _task("math.double")
    registry = TaskRegistry([existing])
    with pytest.raises(TaskqConfigError, match="collision"):
        registry.register_many([_task("math.triple"), _task("math.triple")])
    assert list(registry) == [existing]
    assert registry.resolve("math.triple") is None


def test_require_rejects_unknown_name_and_unregistered_lookalike() -> None:
    registered = _task()
    registry = TaskRegistry([registered])
    with pytest.raises(UnknownTaskError):
        registry.require("missing")
    with pytest.raises(UnknownTaskError):
        registry.require(_task())


_segment = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)
_wire_name = st.lists(_segment, min_size=1, max_size=4).map(".".join)


@given(_wire_name)
def test_generated_valid_names_round_trip_as_aliases(name: str) -> None:
    canonical = "root.task" if name != "root.task" else "other.task"
    task = _task(canonical, aliases=(name,))
    registry = TaskRegistry([task])
    assert registry.resolve(name) is task
    assert registry.canonical(name) == canonical


@pytest.mark.parametrize("status", list(EnqueueStatus))
def test_enqueue_result_is_closed_consistent_and_frozen(status: EnqueueStatus) -> None:
    result = ENQUEUE_RESULT_ADAPTER.validate_python(
        {
            "status": status,
            "job_id": uuid4(),
            "created": status is EnqueueStatus.CREATED,
            "queue": "default",
            "job_type": "math.double",
        }
    )
    assert result.ok
    expected_type = (
        EnqueueCreatedResult if status is EnqueueStatus.CREATED else EnqueueExistedResult
    )
    assert isinstance(result, expected_type)
    with pytest.raises(ValidationError):
        ENQUEUE_RESULT_ADAPTER.validate_python(
            {
                "status": status,
                "job_id": uuid4(),
                "created": status is not EnqueueStatus.CREATED,
                "queue": "default",
                "job_type": "math.double",
            }
        )
    with pytest.raises(ValidationError):
        ENQUEUE_RESULT_ADAPTER.validate_python(
            {
                "status": "replaced",
                "job_id": uuid4(),
                "created": False,
                "queue": "default",
                "job_type": "math.double",
            }
        )


def test_inbound_command_typo_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EnqueueCommand.model_validate(
            {
                "queue": "default",
                "job_type": "math.double",
                "payload": {"value": 3},
                "prioroty": 10,
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EnqueueManyItem.model_validate(
            {"job_type": "math.double", "payload": {"value": 3}, "prioroty": 10}
        )


def test_outbound_result_ignores_unknown_additive_field() -> None:
    result = ENQUEUE_RESULT_ADAPTER.validate_python(
        {
            "status": "created",
            "job_id": uuid4(),
            "created": True,
            "queue": "default",
            "job_type": "math.double",
            "future_server_field": {"added": True},
        }
    )
    assert "future_server_field" not in result.model_dump()


def test_bulk_item_adapter_validates_the_sequence_in_one_boundary_call() -> None:
    items = ENQUEUE_MANY_ITEMS_ADAPTER.validate_python(
        [
            {"job_type": "math.double", "payload": {"value": 1}},
            EnqueueManyItem(job_type="math.double", payload={"value": 2}),
        ]
    )
    assert [item.payload for item in items] == [{"value": 1}, {"value": 2}]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ENQUEUE_MANY_ITEMS_ADAPTER.validate_python(
            [{"job_type": "math.double", "payload": {}, "prioroty": 10}]
        )


def test_claim_batch_adapter_decodes_projection_and_enforces_state_shape() -> None:
    fence = uuid4()
    batch = CLAIM_BATCH_ADAPTER.validate_python(
        {
            "state": "claimed",
            "future_batch_field": True,
            "jobs": [
                {
                    "job_id": uuid4(),
                    "queue": "default",
                    "job_type": "math.double",
                    "priority": 100,
                    "payload": {"value": 3},
                    "headers": {},
                    "progress": None,
                    "attempt_id": fence,
                    "attempt_number": 1,
                    "failure_count": 0,
                    "max_attempts": 5,
                    "lease_expires_at": datetime.now(UTC),
                    "lease_seconds": 300,
                    "future_job_field": True,
                }
            ],
        }
    )
    assert batch.state.value == "claimed" and len(batch.jobs) == 1
    assert batch.jobs[0].attempt_id == fence
    assert str(fence) not in batch.model_dump_json()
    with pytest.raises(ValidationError, match="claimed state"):
        CLAIM_BATCH_ADAPTER.validate_python({"state": "claimed", "jobs": []})


def test_claimed_job_fence_is_excluded_from_repr_and_dump() -> None:
    fence = uuid4()
    job = ClaimedJob(
        job_id=uuid4(),
        queue="default",
        job_type="math.double",
        priority=100,
        payload={"value": 3},
        headers={},
        progress=None,
        attempt_id=fence,
        attempt_number=1,
        failure_count=0,
        max_attempts=5,
        lease_expires_at=datetime.now(UTC),
        lease_seconds=300,
    )
    assert job.attempt_id == fence
    assert str(fence) not in repr(job)
    assert "attempt_id" not in job.model_dump()
    assert str(fence) not in job.model_dump_json()


class DriverError(Exception):
    def __init__(self, state: str | None, message: str = "driver secret") -> None:
        self.sqlstate = state
        super().__init__(message)


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        (TqCode.NOT_FOUND, TaskqNotFoundError),
        (TqCode.CONFLICT, TaskqConflictError),
        (TqCode.VALIDATION, TaskqValidationError),
        (TqCode.VERSION, TaskqVersionError),
        (TqCode.BACKPRESSURE, TaskqBackpressureError),
        (TqCode.INTERNAL, TaskqInternalError),
        (TqCode.CAPABILITY, TaskqCapabilityError),
        (TqCode.UNAVAILABLE, TaskqUnavailableError),
    ],
)
def test_closed_tq_registry_normalizes_from_sqlstate_only(
    code: TqCode, error_type: type[Exception]
) -> None:
    source = DriverError(code.value)
    error = taskq_error_from_exception(source)
    assert isinstance(error, error_type)
    assert error.code is code
    assert error.retryable is TQ_ERROR_REGISTRY[code].retryable
    assert error.cause is source
    assert "driver secret" not in str(error)
    assert "driver secret" not in repr(error)


def test_protocol_command_registry_is_closed_and_self_consistent() -> None:
    assert set(COMMAND_SPECS) == set(CommandName)
    assert len(COMMAND_SPECS) == 30
    for spec in COMMAND_SPECS.values():
        assert spec.outcomes
        assert spec.retryable_errors == frozenset(
            code for code in spec.errors if TQ_ERROR_REGISTRY[code].retryable
        )


def test_settle_outcomes_are_closed_protocol_values() -> None:
    result = SETTLE_RESULT_ADAPTER.validate_python(
        {"result": "lost", "job_status": None, "scheduled_at": None}
    )
    assert result.result is SettleOutcome.LOST
    with pytest.raises(ValidationError):
        SETTLE_RESULT_ADAPTER.validate_python(
            {"result": "invented", "job_status": None, "scheduled_at": None}
        )


def test_tagged_result_serialization_is_byte_identical_to_legacy_wire_shape() -> None:
    enqueue_vectors = [
        (
            {
                "status": "created",
                "job_id": UUID("00000000-0000-0000-0000-000000000001"),
                "created": True,
                "queue": "default",
                "job_type": "math.double",
            },
            b'{"status":"created","job_id":"00000000-0000-0000-0000-000000000001","created":true,"queue":"default","job_type":"math.double","idempotency_key":null,"scheduled_at":null}',
        ),
        (
            {
                "status": "existed",
                "job_id": UUID("00000000-0000-0000-0000-000000000002"),
                "created": False,
                "queue": "default",
                "job_type": "math.double",
                "idempotency_key": "same",
                "scheduled_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            },
            b'{"status":"existed","job_id":"00000000-0000-0000-0000-000000000002","created":false,"queue":"default","job_type":"math.double","idempotency_key":"same","scheduled_at":"2026-01-02T03:04:05Z"}',
        ),
    ]
    for value, expected in enqueue_vectors:
        parsed = ENQUEUE_RESULT_ADAPTER.validate_python(value)
        assert ENQUEUE_RESULT_ADAPTER.dump_json(parsed) == expected

    settle_vectors = [
        (
            {"result": "ok", "job_status": "succeeded", "scheduled_at": None},
            b'{"result":"ok","job_status":"succeeded","scheduled_at":null}',
        ),
        (
            {
                "result": "retry_scheduled",
                "job_status": "queued",
                "scheduled_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            },
            b'{"result":"retry_scheduled","job_status":"queued","scheduled_at":"2026-01-02T03:04:05Z"}',
        ),
        (
            {"result": "dead", "job_status": "failed", "scheduled_at": None},
            b'{"result":"dead","job_status":"failed","scheduled_at":null}',
        ),
        (
            {"result": "already_settled", "job_status": "succeeded", "scheduled_at": None},
            b'{"result":"already_settled","job_status":"succeeded","scheduled_at":null}',
        ),
        (
            {"result": "settle_conflict", "job_status": "failed", "scheduled_at": None},
            b'{"result":"settle_conflict","job_status":"failed","scheduled_at":null}',
        ),
        (
            {"result": "lost", "job_status": None, "scheduled_at": None},
            b'{"result":"lost","job_status":null,"scheduled_at":null}',
        ),
    ]
    for value, expected in settle_vectors:
        parsed = SETTLE_RESULT_ADAPTER.validate_python(value)
        assert SETTLE_RESULT_ADAPTER.dump_json(parsed) == expected


@pytest.mark.parametrize("state", ["08006", "53300", "57P01", "57P03"])
def test_known_availability_states_normalize_to_tq503(state: str) -> None:
    assert isinstance(taskq_error_from_exception(DriverError(state)), TaskqUnavailableError)


@pytest.mark.parametrize("state", [None, "23505", "XX000", "TQ999"])
def test_native_or_unknown_states_normalize_to_tq500(state: str | None) -> None:
    assert isinstance(taskq_error_from_exception(DriverError(state)), TaskqInternalError)


def test_nested_driver_error_is_found_without_reading_messages() -> None:
    wrapper = RuntimeError("public wrapper secret")
    wrapper.__cause__ = DriverError("TQ422", "inner secret")
    error = taskq_error_from_exception(wrapper)
    assert isinstance(error, TaskqValidationError)
    assert "secret" not in str(error)


def test_sensitive_error_details_are_removed() -> None:
    fence = uuid4()
    error = TaskqInternalError(
        details={"attempt_id": fence, "dsn": "postgresql://secret", "incident": "safe-ref"}
    )
    assert error.details == {"incident": "safe-ref"}
    assert str(fence) not in str(error)
    assert "secret" not in repr(error)


def test_public_exports_do_not_import_optional_frameworks() -> None:
    import sys

    assert "fastapi" not in sys.modules
    assert "outlabs_auth" not in sys.modules
    assert UUID is not None  # keep the public UUID-typed assertions explicit
