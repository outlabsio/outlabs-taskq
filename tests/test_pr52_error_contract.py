"""Main-session acceptance contract for PR #52 permanent authorization errors."""

from uuid import uuid4

import pytest

from taskq.errors import TaskqError, taskq_error_from_exception
from taskq.protocol import COMMAND_SPECS, TQ_ERROR_REGISTRY, CommandName, TqCode
from taskq.sql.manifest import PUBLIC_ERRORS
from taskq.sql.transport import SqlTaskqTransport
from tests.test_s2_worker_settlement import _supervisor, complete_handler
from tests.worker_support import ManualClock, ScriptedTransport


class Denial(Exception):
    def __init__(self, state):
        self.sqlstate = state
        super().__init__("private SQL and payload must not escape")


@pytest.mark.parametrize("state", ["TQ403", "TQ425"])
def test_issue4_denial_codes_registered_nonretryable_and_message_free(state):
    error = taskq_error_from_exception(Denial(state))
    assert error.code.value == state
    assert error.retryable is False
    assert TQ_ERROR_REGISTRY[TqCode(state)].http_status == 403
    assert "private" not in str(error)
    assert "private" not in repr(error)


@pytest.mark.parametrize("state", ["TQ403", "TQ425"])
async def test_issue4_sql_transport_preserves_wrapped_denial(state):
    source = Denial(state)
    wrapper = RuntimeError("driver wrapper")
    wrapper.__cause__ = source

    async def operation(connection):
        raise wrapper

    transport = SqlTaskqTransport(object())
    with pytest.raises(TaskqError) as denied:
        await transport._run(operation, connection=object())
    assert denied.value.code.value == state
    assert denied.value.retryable is False


@pytest.mark.parametrize("state", ["TQ403", "TQ425"])
async def test_issue4_settlement_does_not_retry_authorization_denial(state, monkeypatch):
    clock = ManualClock()

    async def immediate_sleep(delay):
        clock.advance(delay)

    monkeypatch.setattr(clock, "sleep", immediate_sleep)
    supervisor = _supervisor(ScriptedTransport(), clock, complete_handler, attempts=3)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise taskq_error_from_exception(Denial(state))

    try:
        await supervisor._settle_with_retry(uuid4(), CommandName.COMPLETE, operation)
        assert calls == 1
    finally:
        await supervisor.aclose()


@pytest.mark.parametrize(
    "command",
    [
        CommandName.RESERVE_ADMISSION,
        CommandName.FINISH_ADMISSION,
        CommandName.CANCEL_ADMISSION,
        CommandName.ENQUEUE,
        CommandName.ENQUEUE_MANY,
        CommandName.COMPLETE,
    ],
)
def test_issue4_affected_command_and_sql_errors_include_owner_denial(command):
    spec = COMMAND_SPECS[command]
    assert "TQ425" in {code.value for code in spec.errors}
    assert "TQ425" in PUBLIC_ERRORS[spec.sql_function]


def test_issue4_every_manifest_error_has_public_exception_mapping():
    for state in set().union(*PUBLIC_ERRORS.values()):
        assert taskq_error_from_exception(Denial(state)).code.value == state
    assert (
        "TQ425" in PUBLIC_ERRORS["taskq.lock_terminal_effect_job(uuid,text,text,text,uuid,boolean)"]
    )
    assert "TQ403" in PUBLIC_ERRORS["taskq.bind_queue_admission_owner(text,text,text,uuid,boolean)"]
    assert (
        "TQ403"
        in PUBLIC_ERRORS["taskq.adopt_queue_admission_owner(text,text,text,text,text,uuid,boolean)"]
    )
    assert (
        "TQ403"
        in PUBLIC_ERRORS[
            "taskq.rotate_queue_admission_owner(text,text,text,text,text,uuid,boolean)"
        ]
    )
