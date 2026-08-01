"""S2-05C settings, registry import, CLI, and diagnostics."""

from __future__ import annotations

import logging
import sys
from types import ModuleType

import pytest
from pydantic import ValidationError

from taskq import TaskRegistry, WorkerService, WorkerServiceOptions
from taskq import cli as cli_module
from taskq.cli import _load_registry, main
from taskq.errors import TaskqConfigError
from taskq.settings import WorkerSettings
from tests.test_s2_worker_service_poll import _claim, _registry, _spin_until
from tests.worker_support import ManualClock, ScriptedTransport
from taskq.protocol import ClaimResult, ClaimState


_ENV_NAMES = (
    "TASKQ_DSN",
    "TASKQ_HTTP_BASE_URL",
    "TASKQ_HTTP_BEARER_TOKEN",
    "TASKQ_HTTP_HEADER_NAME",
    "TASKQ_HTTP_HEADER_VALUE",
    "TASKQ_HTTP_CLAIM_WAIT_SECONDS",
    "TASKQ_REGISTRY",
    "TASKQ_QUEUES",
    "TASKQ_ENVIRONMENT",
    "TASKQ_WORKER_ID",
    "TASKQ_CONCURRENCY",
    "TASKQ_SYNC_WORKERS",
    "TASKQ_BATCH",
    "TASKQ_POLL_INTERVAL",
    "TASKQ_LISTEN",
    "TASKQ_PRESENCE_INTERVAL",
    "TASKQ_SOFT_STOP_TIMEOUT",
    "TASKQ_EXPECTED_ENV",
    "TASKQ_ALLOW_PRODUCTION",
    "TASKQ_POOL_SIZE",
)


@pytest.fixture(autouse=True)
def _clean_worker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: object) -> WorkerSettings:
    values = {
        "dsn": "postgresql://runner:very-secret@db/taskq",
        "registry": "app.tasks:registry",
        "queues": ("alpha",),
        "environment": "test",
        "listen": False,
    }
    values.update(overrides)
    return WorkerSettings(**values)


def test_settings_are_frozen_bounded_and_secret_safe() -> None:
    settings = _settings(concurrency=9)
    assert settings.pool_size == 11
    assert "very-secret" not in repr(settings)
    with pytest.raises(ValidationError):
        _settings(batch=2, concurrency=1)
    with pytest.raises(ValidationError):
        _settings(unexpected=True)
    with pytest.raises(ValidationError):
        settings.concurrency = 2  # type: ignore[misc]


def test_settings_parse_json_queues_and_expected_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKQ_DSN", "postgresql://runner:env-secret@db/taskq")
    monkeypatch.setenv("TASKQ_REGISTRY", "app.tasks:registry")
    monkeypatch.setenv("TASKQ_QUEUES", '["alpha", "beta"]')
    monkeypatch.setenv("TASKQ_ENVIRONMENT", "staging")
    monkeypatch.setenv("TASKQ_EXPECTED_ENV", "staging")
    settings = WorkerSettings()
    assert settings.queues == ("alpha", "beta")
    assert settings.expected_environment == "staging"
    assert settings.pool_size == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "production"},
        {"environment": "staging", "expected_environment": "production"},
    ],
)
def test_environment_interlocks_refuse_unsafe_start(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)
    assert _settings(environment="production", allow_production=True).environment == "production"


def test_registry_loader_accepts_instance_and_zero_arg_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("taskq_test_registry")
    instance = _registry("alpha")
    module.instance = instance
    module.factory = lambda: instance
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert _load_registry(f"{module.__name__}:instance") is instance
    assert _load_registry(f"{module.__name__}:factory") is instance


@pytest.mark.parametrize("target", [object(), lambda required: TaskRegistry(), lambda: object()])
def test_registry_loader_rejects_wrong_targets(
    monkeypatch: pytest.MonkeyPatch, target: object
) -> None:
    module = ModuleType("taskq_bad_registry")
    module.target = target
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(TaskqConfigError):
        _load_registry(f"{module.__name__}:target")


def test_registry_loader_rejects_async_and_raising_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("taskq_factory_failures")

    async def asynchronous() -> TaskRegistry:
        return TaskRegistry()

    def raising() -> TaskRegistry:
        raise RuntimeError("sensitive factory detail")

    module.asynchronous = asynchronous
    module.raising = raising
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(TaskqConfigError, match="sync factory"):
        _load_registry(f"{module.__name__}:asynchronous")
    with pytest.raises(TaskqConfigError, match="factory failed") as exc_info:
        _load_registry(f"{module.__name__}:raising")
    assert "sensitive factory detail" not in str(exc_info.value)


def test_production_refusal_happens_before_registry_import_or_database_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    imported = False
    opened = False

    def unexpected_import(_name: str) -> ModuleType:
        nonlocal imported
        imported = True
        raise AssertionError

    def unexpected_open(*_args: object, **_kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError

    monkeypatch.setattr(cli_module.importlib, "import_module", unexpected_import)
    monkeypatch.setattr(cli_module.SqlTaskqTransport, "from_dsn", unexpected_open)
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "worker",
                "--dsn",
                "postgresql://runner:cli-secret@db/taskq",
                "--registry",
                "app.tasks:registry",
                "--queue",
                "alpha",
                "--environment",
                "production",
            ]
        )
    assert exc_info.value.code == 2
    assert not imported and not opened
    assert "cli-secret" not in capsys.readouterr().err


def test_cli_values_override_environment_without_opening_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("taskq_cli_registry")
    module.registry = _registry("alpha")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("TASKQ_DSN", "postgresql://runner:env-secret@db/taskq")
    monkeypatch.setenv("TASKQ_REGISTRY", f"{module.__name__}:registry")
    monkeypatch.setenv("TASKQ_QUEUES", '["beta"]')
    monkeypatch.setenv("TASKQ_ENVIRONMENT", "test")
    captured: WorkerSettings | None = None

    async def fake_run(settings: WorkerSettings, _registry: TaskRegistry) -> int:
        nonlocal captured
        captured = settings
        return 0

    monkeypatch.setattr("taskq.cli._run_worker", fake_run)
    main(["worker", "--queue", "alpha", "--concurrency", "4", "--no-listen"])
    assert captured is not None
    assert captured.queues == ("alpha",)
    assert captured.concurrency == 4
    assert captured.listen is False
    assert captured.pool_size == 6


def test_runtime_failure_exits_nonzero_without_secret_or_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = ModuleType("taskq_runtime_failure_registry")
    module.registry = _registry("alpha")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    async def fail_run(_settings: WorkerSettings, _registry: TaskRegistry) -> int:
        raise RuntimeError("postgresql://runner:runtime-secret@db/taskq")

    monkeypatch.setattr(cli_module, "_run_worker", fail_run)
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "worker",
                "--dsn",
                "postgresql://runner:runtime-secret@db/taskq",
                "--registry",
                f"{module.__name__}:registry",
                "--queue",
                "alpha",
                "--environment",
                "test",
                "--no-listen",
            ]
        )
    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "RuntimeError" in error
    assert "runtime-secret" not in error


async def test_structured_diagnostics_are_stable_and_fence_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = ScriptedTransport()
    claim = _claim("alpha", value=739)
    transport.script("claim", ClaimResult(state=ClaimState.CLAIMED, jobs=(claim,)))
    service = WorkerService(
        transport,  # type: ignore[arg-type]
        _registry("alpha"),
        "worker-safe",
        options=WorkerServiceOptions(queues=("alpha",), listen=False),
        clock=ManualClock(),
    )
    with caplog.at_level(logging.DEBUG, logger="taskq.worker"):
        await service.start()
        await _spin_until(lambda: service.snapshot().submitted_jobs == 1)
        await service.aclose()
    snapshot = service.snapshot()
    assert snapshot.claimed_jobs == snapshot.submitted_jobs == 1
    assert snapshot.active_slots == 0
    assert snapshot.started_monotonic == 0
    messages = {record.message for record in caplog.records}
    assert {"worker.starting", "worker.ready", "poll.sweep", "job.submitted"} <= messages
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert str(claim.attempt_id) not in rendered
    assert "739" not in rendered
    assert "postgresql://" not in rendered
