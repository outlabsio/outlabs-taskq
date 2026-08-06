"""Target and scheduler commands on the resource-oriented CLI."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from uuid import UUID

import pytest

from taskq.cli import main
from taskq.protocol import ContractMeta, SchedulerHealth, TargetIdentityProfile


_INSTALLATION_ID = UUID("018f47b2-a6cd-7c64-8e19-123456789abc")


def _profile(*, environment: str = "staging", version: int = 1) -> TargetIdentityProfile:
    return TargetIdentityProfile(
        installation_id=_INSTALLATION_ID,
        environment=environment,
        binding_version=version,
        bound_at=datetime(2026, 8, 3, tzinfo=UTC),
        bound_by="release-agent",
        contract_version="0.6.1",
        capabilities={"active": ["scheduler_v2", "target_attestation"]},
    )


class _ReadTransport:
    async def target(self) -> TargetIdentityProfile:
        return _profile()

    async def meta(self) -> ContractMeta:
        return ContractMeta(
            contract_version="0.6.1",
            capabilities={"active": ["scheduler_v2", "target_attestation"]},
        )

    async def scheduler_health(self) -> SchedulerHealth:
        return SchedulerHealth(
            database_time=datetime(2026, 8, 3, tzinfo=UTC),
            active_schedules=2,
            due_schedules=0,
            oldest_due_at=None,
            last_decision_at=None,
            auto_paused_schedules=0,
        )


@pytest.fixture
def read_transport(monkeypatch: pytest.MonkeyPatch) -> _ReadTransport:
    value = _ReadTransport()

    @asynccontextmanager
    async def opened(_connection: object) -> AsyncIterator[_ReadTransport]:
        yield value

    monkeypatch.setattr("taskq.cli.app.open_cli_transport", opened)
    return value


def _http_args() -> list[str]:
    return [
        "--http-base-url",
        "https://taskq.example.test",
        "--http-bearer-token",
        "test-secret",
    ]


def test_target_show_uses_the_versioned_machine_envelope(
    read_transport: _ReadTransport,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del read_transport
    assert main(["target", "show", *_http_args(), "-o", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["api_version"] == "taskq.cli/v1"
    assert payload["command"] == "target.show"
    assert payload["data"]["installation_id"] == str(_INSTALLATION_ID)
    assert payload["meta"]["target"]["environment"] == "staging"


def test_scheduler_doctor_is_a_read_only_machine_diagnostic(
    read_transport: _ReadTransport,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del read_transport
    assert main(["scheduler", "doctor", *_http_args(), "-o", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["ready"] is True
    assert payload["data"]["health"]["active_schedules"] == 2


def test_target_bind_refuses_without_acknowledgement_before_database_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened = False

    def unexpected(*_args: Any, **_kwargs: Any) -> object:
        nonlocal opened
        opened = True
        raise AssertionError

    monkeypatch.setattr("taskq.sql.transport.SqlTaskqTransport.from_dsn", unexpected)
    code = main(
        [
            "target",
            "bind",
            "staging",
            "--dsn",
            "postgresql://owner:target-secret@db/taskq",
            "--actor",
            "operator:test",
            "--expected-installation-id",
            str(_INSTALLATION_ID),
            "--expected-binding-version",
            "0",
            "-o",
            "json",
        ]
    )
    assert code == 2 and not opened
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "CLI_SAFETY"
    assert "target-secret" not in captured.err


def test_production_target_bind_requires_literal_allow_production(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened = False

    def unexpected(*_args: Any, **_kwargs: Any) -> object:
        nonlocal opened
        opened = True
        raise AssertionError

    monkeypatch.setattr("taskq.sql.transport.SqlTaskqTransport.from_dsn", unexpected)
    code = main(
        [
            "target",
            "bind",
            "production",
            "--dsn",
            "postgresql://db/taskq",
            "--actor",
            "operator:test",
            "--expected-installation-id",
            str(_INSTALLATION_ID),
            "--expected-binding-version",
            "0",
            "--yes",
        ]
    )
    assert code == 2 and not opened
    assert "--allow-production" in capsys.readouterr().err


def test_unhandled_transport_failure_is_redacted_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @asynccontextmanager
    async def opened(_connection: object) -> AsyncIterator[object]:
        raise RuntimeError("postgresql://owner:do-not-print@db/taskq")
        yield object()

    monkeypatch.setattr("taskq.cli.app.open_cli_transport", opened)
    code = main(["target", "show", *_http_args(), "--request-id", "req-42", "-o", "json"])
    captured = capsys.readouterr()
    assert code == 3 and captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["request_id"] == "req-42"
    assert payload["error"]["retryable"] is True
    assert "do-not-print" not in captured.err


def test_consumer_scheduler_once_closes_preflight_before_owned_runtime_starts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    transport = _ReadTransport()

    @asynccontextmanager
    async def opened(_connection: object) -> AsyncIterator[_ReadTransport]:
        events.append("preflight-open")
        try:
            yield transport
        finally:
            events.append("preflight-closed")

    async def run_scheduler(
        settings: object,
        *,
        once: bool,
        max_batches: int,
        max_runtime_seconds: float,
    ) -> dict[str, object]:
        del settings, max_batches, max_runtime_seconds
        events.append("runtime-started")
        return {"once": once, "batches": 0}

    monkeypatch.setenv("OUTLABS_TASKQ_DSN", "postgresql://owner:secret@db/taskq")
    monkeypatch.setattr("taskq.cli.app.open_cli_transport", opened)
    monkeypatch.setattr("taskq.cli.app._run_scheduler", run_scheduler)

    code = main(
        [
            "scheduler",
            "once",
            "--dsn-env",
            "OUTLABS_TASKQ_DSN",
            "--expected-environment",
            "staging",
            "--actor",
            "operator:test",
            "-o",
            "json",
        ]
    )
    assert code == 0
    assert events == ["preflight-open", "preflight-closed", "runtime-started"]
    assert json.loads(capsys.readouterr().out)["data"] == {"batches": 0, "once": True}
