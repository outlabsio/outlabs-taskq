"""Standalone scheduler target-identity CLI contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from taskq import cli as cli_module
from taskq.cli import main
from taskq.protocol import SchedulerHealth, TargetIdentityProfile
from taskq.scheduler import SchedulerDoctorReport


_INSTALLATION_ID = UUID("018f47b2-a6cd-7c64-8e19-123456789abc")


def _profile(*, environment: str = "staging", version: int = 1) -> TargetIdentityProfile:
    return TargetIdentityProfile(
        installation_id=_INSTALLATION_ID,
        environment=environment,
        binding_version=version,
        bound_at=datetime(2026, 8, 3, tzinfo=UTC),
        bound_by="release-agent",
        contract_version="0.2.7",
        capabilities={"active": ["schedules"]},
    )


def test_target_show_json_is_machine_readable_and_human_output_abbreviates_uuid(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def run(_args: object) -> TargetIdentityProfile:
        return _profile()

    monkeypatch.setattr(cli_module, "_run_target_command", run)
    main(["target", "show", "--dsn", "postgresql://db/taskq", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["installation_id"] == str(_INSTALLATION_ID)
    assert payload["environment"] == "staging"

    main(["target", "show", "--dsn", "postgresql://db/taskq"])
    output = capsys.readouterr().out
    assert "018f47b2…" in output
    assert str(_INSTALLATION_ID) not in output


def test_target_bind_passes_explicit_cas_and_rotation_inputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: object | None = None

    async def run(args: object) -> TargetIdentityProfile:
        nonlocal captured
        captured = args
        return _profile(environment="development", version=2)

    monkeypatch.setattr(cli_module, "_run_target_command", run)
    main(
        [
            "target",
            "bind",
            "development",
            "--dsn",
            "postgresql://db/taskq",
            "--actor",
            "clone-agent",
            "--expected-installation-id",
            str(_INSTALLATION_ID),
            "--expected-binding-version",
            "1",
            "--rotate",
            "--reason",
            "staging clone",
        ]
    )
    assert captured is not None
    assert getattr(captured, "environment") == "development"
    assert getattr(captured, "rotate") is True
    assert getattr(captured, "expected_binding_version") == 1
    assert "environment: development" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv,expected",
    [
        (
            [
                "target",
                "bind",
                "production",
                "--actor",
                "agent",
                "--expected-installation-id",
                str(_INSTALLATION_ID),
                "--expected-binding-version",
                "0",
            ],
            "--allow-production",
        ),
        (
            [
                "target",
                "bind",
                "staging",
                "--actor",
                "agent",
                "--expected-installation-id",
                str(_INSTALLATION_ID),
                "--expected-binding-version",
                "0",
                "--rotate",
            ],
            "--reason",
        ),
    ],
)
def test_target_bind_refuses_unsafe_local_inputs_before_opening_database(
    argv: list[str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened = False

    async def run(_args: object) -> TargetIdentityProfile:
        nonlocal opened
        opened = True
        return _profile()

    monkeypatch.setenv("TASKQ_DSN", "postgresql://db/taskq")
    monkeypatch.setattr(cli_module, "_run_target_command", run)
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    assert exc_info.value.code == 2
    assert not opened
    assert expected in capsys.readouterr().err


def test_target_cli_password_warning_never_repeats_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def run(_args: object) -> TargetIdentityProfile:
        return _profile()

    monkeypatch.setattr(cli_module, "_run_target_command", run)
    main(
        [
            "target",
            "show",
            "--dsn",
            "postgresql://owner:target-secret@db/taskq",
        ]
    )
    error = capsys.readouterr().err
    assert "TASKQ_DSN" in error
    assert "target-secret" not in error


def test_scheduler_doctor_is_read_only_machine_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def run(args: object) -> SchedulerDoctorReport:
        assert getattr(args, "scheduler_command") == "doctor"
        assert getattr(args, "once") is False
        return SchedulerDoctorReport(
            ready=True,
            target=_profile(),
            health=SchedulerHealth(
                database_time=datetime(2026, 8, 3, tzinfo=UTC),
                active_schedules=2,
                due_schedules=0,
                oldest_due_at=None,
                last_decision_at=None,
                auto_paused_schedules=0,
            ),
        )

    monkeypatch.setattr(cli_module, "_run_scheduler_command", run)
    main(["scheduler", "doctor", "--dsn", "postgresql://db/taskq", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["health"]["active_schedules"] == 2
