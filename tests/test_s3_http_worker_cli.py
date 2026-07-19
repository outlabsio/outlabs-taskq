"""S3-03 HTTP worker configuration and CLI transport integration."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from taskq import TaskRegistry
from taskq import cli as cli_module
from taskq.cli import _run_worker
from taskq.settings import WorkerSettings


def _settings(**overrides: object) -> WorkerSettings:
    values = {
        "http_base_url": "https://queue.example/taskq",
        "http_bearer_token": "worker-secret",
        "registry": "app.tasks:registry",
        "queues": ("alpha",),
        "environment": "test",
        "listen": False,
    }
    values.update(overrides)
    return WorkerSettings(**values)


def test_http_worker_settings_are_secret_safe_and_transport_exclusive() -> None:
    settings = _settings()
    assert settings.dsn is None and settings.pool_size is None
    assert "worker-secret" not in repr(settings)
    with pytest.raises(ValidationError, match="exactly one of dsn or http_base_url"):
        _settings(dsn="postgresql://db/taskq")
    with pytest.raises(ValidationError, match="cannot use PostgreSQL LISTEN"):
        _settings(listen=True)
    with pytest.raises(ValidationError, match="claim wait zero"):
        _settings(queues=("alpha", "beta"))
    assert _settings(queues=("alpha", "beta"), http_claim_wait_seconds=0).queues == (
        "alpha",
        "beta",
    )
    with pytest.raises(ValidationError, match="credential source"):
        _settings(http_bearer_token=None)
    header = _settings(
        http_bearer_token=None,
        http_header_name="X-Taskq-Key",
        http_header_value="header-secret",
    )
    assert isinstance(header.http_header_value, SecretStr)
    assert "header-secret" not in repr(header)


async def test_run_worker_uses_async_http_transport_and_cancels_long_poll_on_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, base_url: str, **kwargs: Any) -> None:
            captured["base_url"] = base_url
            captured.update(kwargs)

        async def start(self) -> None:
            events.append("client-start")

        async def aclose(self) -> None:
            events.append("client-close")

    class Snapshot:
        fatal = False

    class Service:
        requires_process_exit = False
        stopped = True

        def __init__(
            self, transport: object, registry: TaskRegistry, worker_id: str, **kwargs: Any
        ):
            del registry, worker_id
            captured["transport"] = transport
            captured.update(kwargs)

        async def run(self) -> None:
            events.append("service-run")

        def snapshot(self) -> Snapshot:
            return Snapshot()

        async def aclose(self) -> None:
            events.append("service-close")

    monkeypatch.setattr("taskq.http.client.AsyncTaskqHttpClient", Client)
    monkeypatch.setattr(cli_module, "WorkerService", Service)
    result = await _run_worker(_settings(), TaskRegistry())
    assert result == 0
    assert captured["base_url"] == "https://queue.example/taskq"
    assert captured["claim_wait_seconds"] == 25
    assert captured["notifications"] is None
    assert captured["options"].cancel_inflight_claim_on_stop is True
    assert events == ["client-start", "service-run", "service-close", "client-close"]
