"""DB-free acceptance contract for the UI-independent CLI."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import click
import pytest
import yaml
from pydantic import BaseModel

from taskq.cli import COMMAND_SPECS, cli, main
from taskq.cli.context import resolve_connection
from taskq.cli.cursor import decode_cursor, encode_cursor
from taskq.cli.errors import normalize_error
from taskq.cli.models import CliMeta, CliSuccessEnvelope
from taskq.cli.output import render_success
from taskq.errors import TaskqValidationError
from taskq.protocol import ContractMeta, JobStatus, TargetIdentityProfile


INSTALLATION_ID = UUID("018f47b2-a6cd-7c64-8e19-123456789abc")


def _target(environment: str = "staging") -> TargetIdentityProfile:
    return TargetIdentityProfile(
        installation_id=INSTALLATION_ID,
        environment=environment,
        binding_version=3,
        bound_at=datetime(2026, 8, 4, tzinfo=UTC),
        bound_by="operator:test",
        contract_version="0.6.5",
        capabilities={"active": []},
    )


def _http_args(*, target: bool = False, environment: str = "staging") -> list[str]:
    values = [
        "--http-base-url",
        "https://taskq.example.test",
        "--http-bearer-token",
        "secret-value",
        "--expected-environment",
        environment,
    ]
    if target:
        values.extend(["--expected-installation-id", str(INSTALLATION_ID)])
    return values


def _leaf_paths(group: click.Group, prefix: tuple[str, ...] = ()) -> set[str]:
    result: set[str] = set()
    for name, command in group.commands.items():
        path = (*prefix, name)
        if isinstance(command, click.Group):
            result.update(_leaf_paths(command, path))
        else:
            result.add(".".join(path))
    return result


def test_command_registry_exactly_matches_click_leaves_and_is_agent_complete() -> None:
    assert _leaf_paths(cli) == set(COMMAND_SPECS)
    assert len(COMMAND_SPECS) == 73
    for path, spec in COMMAND_SPECS.items():
        metadata = spec.as_dict()
        assert metadata["path"] == path
        assert metadata["danger_level"] in {"read-only", "mutation", "destructive"}
        assert metadata["input_schema"] is not None
        assert metadata["output_schema"] is not None
        assert metadata["examples"]
        assert set(metadata["exit_codes"]) == {"0", "1", "2", "3", "4", "5", "130"}


def test_discovery_and_schema_are_versioned_and_need_no_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["commands", "-o", "json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["api_version"] == "taskq.cli/v1"
    assert len(catalog["data"]["items"]) == len(COMMAND_SPECS)

    assert main(["schema", "job.enqueue", "-o", "json"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["data"]["path"] == "job.enqueue"
    assert schema["data"]["input_schema"]["title"] == "EnqueueCommand"
    assert schema["data"]["danger_level"] == "mutation"


def test_removed_alpha_grammar_has_no_compatibility_alias(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["migrate", "-o", "json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "CLI_CONFIG"


def test_context_file_is_secret_free_and_has_no_implicit_current_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
version = 1
[contexts.staging]
transport = "sql"
dsn_env = "TASKQ_STAGING_DSN"
expected_environment = "staging"
expected_installation_id = "018f47b2-a6cd-7c64-8e19-123456789abc"
actor = "operator:test"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TASKQ_STAGING_DSN", "postgresql://owner:secret@db/taskq")
    resolved = resolve_connection(context_name="staging", config_path=config)
    assert resolved.context == "staging" and resolved.transport == "sql"
    assert "secret" not in repr(resolved)

    monkeypatch.setenv("TASKQ_DSN", "postgresql://implicit@db/taskq")
    with pytest.raises(ValueError, match="--context"):
        resolve_connection(context_name=None, config_path=None)
    monkeypatch.setenv("TASKQ_EXPECTED_ENV", "production")
    explicit = resolve_connection(
        context_name=None,
        config_path=None,
        http_base_url="https://taskq.example.test",
        http_bearer_token="secret",
    )
    assert explicit.expected_environment is None

    literal = tmp_path / "literal.toml"
    literal.write_text(
        'version = 1\n[contexts.bad]\ntransport = "sql"\ndsn = "postgresql://secret"\n',
        encoding="utf-8",
    )
    assert main(["context", "validate", "--config", str(literal), "-o", "json"]) == 2


def test_explicit_dsn_environment_name_is_secret_safe_and_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OUTLABS_TASKQ_DSN", "postgresql://owner:secret@db/taskq")
    resolved = resolve_connection(
        context_name=None,
        config_path=None,
        dsn_env="OUTLABS_TASKQ_DSN",
        expected_environment="staging",
        actor="operator:test",
    )
    assert resolved.transport == "sql"
    assert resolved.dsn is not None
    assert resolved.dsn.get_secret_value().endswith("@db/taskq")
    assert "secret" not in repr(resolved)

    with pytest.raises(ValueError, match="environment-variable name"):
        resolve_connection(
            context_name=None,
            config_path=None,
            dsn_env="OUTLABS-TASKQ-DSN",
        )
    with pytest.raises(ValueError, match="exactly one of --dsn or --dsn-env"):
        resolve_connection(
            context_name=None,
            config_path=None,
            dsn="postgresql://db/taskq",
            dsn_env="OUTLABS_TASKQ_DSN",
        )


def test_context_identity_conflicts_and_http_actor_spoofing_are_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
version = 1
[contexts.remote]
transport = "http"
base_url = "https://taskq.example.test"
bearer_token_env = "TASKQ_TOKEN"
expected_environment = "staging"
expected_installation_id = "018f47b2-a6cd-7c64-8e19-123456789abc"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TASKQ_TOKEN", "secret")
    with pytest.raises(ValueError, match="environment conflicts"):
        resolve_connection(
            context_name="remote",
            config_path=config,
            expected_environment="production",
        )
    with pytest.raises(ValueError, match="actor spoofing"):
        resolve_connection(context_name="remote", config_path=config, actor="operator:spoof")


class _PagedTransport:
    def __init__(
        self,
        *,
        target: TargetIdentityProfile | None = None,
        capabilities: tuple[str, ...] = (),
        total: int = 0,
        job_status: JobStatus = JobStatus.QUEUED,
    ) -> None:
        self.target_value = target or _target()
        self.capabilities = capabilities
        self.total = total
        self.job_status = job_status
        self.page_calls: list[tuple[int, Any]] = []
        self.mutations = 0
        self.enqueued: Any = None

    async def target(self) -> TargetIdentityProfile:
        return self.target_value

    async def meta(self) -> ContractMeta:
        return ContractMeta(
            contract_version=self.target_value.contract_version,
            capabilities={"active": list(self.capabilities)},
        )

    async def job_list(self, queue: str, view: str, limit: int, cursor: Any) -> Any:
        del queue, view
        start = int(cursor or 0)
        stop = min(start + limit, self.total)
        self.page_calls.append((limit, cursor))
        return SimpleNamespace(
            as_of=datetime(2026, 8, 4, tzinfo=UTC),
            items=tuple({"job_id": str(index), "status": "ready"} for index in range(start, stop)),
            next_after=stop if stop < self.total else None,
        )

    async def workflow_list(self, view: str, limit: int, cursor: Any) -> Any:
        del view, limit, cursor
        self.mutations += 1
        raise AssertionError("capability gate must run before operation")

    async def enqueue(self, command: Any) -> Any:
        self.mutations += 1
        self.enqueued = command
        return {"job_id": str(uuid4()), "created": True}

    async def job_show(self, _job_id: UUID) -> Any:
        return _JobView(job_id=_job_id, status=self.job_status)


class _JobView(BaseModel):
    job_id: UUID
    status: JobStatus


@pytest.fixture
def open_transport(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(value: _PagedTransport) -> None:
        @asynccontextmanager
        async def opened(_connection: object) -> AsyncIterator[_PagedTransport]:
            yield value

        monkeypatch.setattr("taskq.cli.app.open_cli_transport", opened)

    return install


def test_list_limit_collects_server_pages_and_all_explicitly_exhausts(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _PagedTransport(total=300)
    open_transport(transport)
    assert (
        main(
            [
                "job",
                "list",
                "--queue",
                "alpha",
                "--view",
                "ready",
                "--limit",
                "205",
                *_http_args(),
                "-o",
                "json",
            ]
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert len(first["data"]["items"]) == 205
    assert [call[0] for call in transport.page_calls] == [100, 100, 5]
    assert first["meta"]["next_cursor"]
    assert "next_cursor_value" not in first["data"]

    transport.page_calls.clear()
    assert (
        main(
            [
                "job",
                "list",
                "--queue",
                "alpha",
                "--view",
                "ready",
                "--limit",
                "50",
                "--all",
                *_http_args(),
                "-o",
                "json",
            ]
        )
        == 0
    )
    exhausted = json.loads(capsys.readouterr().out)
    assert len(exhausted["data"]["items"]) == 300
    assert exhausted["meta"]["next_cursor"] is None
    assert len(transport.page_calls) == 6


def test_cursor_is_bound_to_command_transport_target_and_filters() -> None:
    token = encode_cursor(
        command="job.list",
        transport="sql",
        target=str(INSTALLATION_ID),
        filters={"queue": "alpha", "view": "ready"},
        value={"scheduled_at": "2026-08-04T00:00:00Z", "job_id": str(uuid4())},
    )
    assert token is not None
    assert decode_cursor(
        token,
        command="job.list",
        transport="sql",
        target=str(INSTALLATION_ID),
        filters={"queue": "alpha", "view": "ready"},
    )["job_id"]
    with pytest.raises(ValueError, match="does not belong"):
        decode_cursor(
            token,
            command="job.list",
            transport="http",
            target=str(INSTALLATION_ID),
            filters={"queue": "alpha", "view": "ready"},
        )


def test_dormant_read_model_command_returns_capability_error_before_query(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _PagedTransport()
    open_transport(transport)
    code = main(["workflow", "list", "--view", "running", *_http_args(), "-o", "json"])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == "" and transport.mutations == 0
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "TQ501"
    assert payload["error"]["details"]["capability"] == "read_model_workflow_list"


def test_production_mutation_gates_run_after_target_read_and_before_write(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _PagedTransport(target=_target("production"))
    open_transport(transport)
    code = main(
        [
            "job",
            "enqueue",
            "--queue",
            "alpha",
            "--type",
            "tests.echo",
            "--idempotency-key",
            "key-1",
            *_http_args(target=True, environment="production"),
            "-o",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2 and captured.out == "" and transport.mutations == 0
    assert json.loads(captured.err)["error"]["code"] == "CLI_SAFETY"


def test_lease_expiry_requires_yes_before_write(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _PagedTransport()
    open_transport(transport)
    code = main(["job", "expire", str(uuid4()), *_http_args(), "-o", "json"])
    captured = capsys.readouterr()
    assert code == 2 and captured.out == "" and transport.mutations == 0
    assert json.loads(captured.err)["error"]["code"] == "CLI_SAFETY"


def test_direct_sql_mutation_requires_actor_before_database_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened = False

    @asynccontextmanager
    async def unexpected(_connection: object) -> AsyncIterator[object]:
        nonlocal opened
        opened = True
        yield object()

    monkeypatch.setattr("taskq.cli.app.open_cli_transport", unexpected)
    code = main(
        [
            "job",
            "enqueue",
            "--queue",
            "alpha",
            "--type",
            "tests.echo",
            "--idempotency-key",
            "key-1",
            "--dsn",
            "postgresql://owner:secret@db/taskq",
            "-o",
            "json",
        ]
    )
    assert code == 2 and not opened
    captured = capsys.readouterr()
    assert captured.out == "" and "secret" not in captured.err


def test_consumer_worker_wrapper_closes_preflight_before_owned_runtime_starts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []
    transport = _PagedTransport()

    @asynccontextmanager
    async def opened(_connection: object) -> AsyncIterator[_PagedTransport]:
        events.append("preflight-open")
        try:
            yield transport
        finally:
            events.append("preflight-closed")

    async def run_worker(settings: object, registry: object) -> int:
        del settings, registry
        events.append("runtime-started")
        return 0

    monkeypatch.setattr("taskq.cli.app.open_cli_transport", opened)
    monkeypatch.setattr("taskq.cli.app._runtime._load_registry", lambda _reference: object())
    monkeypatch.setattr(
        "taskq.cli.app._runtime._validate_subscriptions", lambda _registry, _queues: None
    )
    monkeypatch.setattr("taskq.cli.app._runtime._run_worker", run_worker)

    code = main(
        [
            "worker",
            "run",
            "--registry",
            "app.tasks:registry",
            "--queue",
            "auth_maintenance",
            "--environment",
            "staging",
            "--concurrency",
            "1",
            "--batch",
            "1",
            "--no-listen",
            *_http_args(environment="staging"),
            "-o",
            "json",
        ]
    )
    assert code == 0
    assert events == ["preflight-open", "preflight-closed", "runtime-started"]
    assert json.loads(capsys.readouterr().out)["data"]["outcome"] == "stopped"


def test_enqueue_accepts_stdin_rejects_mixed_input_and_marks_unkeyed_explicitly(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _PagedTransport()
    open_transport(transport)
    monkeypatch.setattr(
        "sys.stdin",
        StringIO(
            '{"queue":"alpha","job_type":"tests.echo","payload":{"value":7},'
            '"idempotency_key":"stdin-key"}'
        ),
    )
    assert main(["job", "enqueue", "--input", "-", *_http_args(), "-o", "json"]) == 0
    assert transport.enqueued.payload == {"value": 7}
    assert json.loads(capsys.readouterr().out)["ok"] is True

    code = main(
        [
            "job",
            "enqueue",
            "--input",
            "-",
            "--queue",
            "alpha",
            *_http_args(),
            "-o",
            "json",
        ]
    )
    assert code == 2 and json.loads(capsys.readouterr().err)["error"]["code"] == "CLI_CONFIG"

    code = main(
        [
            "job",
            "enqueue",
            "--queue",
            "alpha",
            "--type",
            "tests.echo",
            *_http_args(),
            "-o",
            "json",
        ]
    )
    assert code == 2 and "allow-unkeyed" in capsys.readouterr().err


def test_wait_timeout_is_exit_4_and_jsonl_error_is_in_band(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transport = _PagedTransport()
    open_transport(transport)
    code = main(
        [
            "job",
            "wait",
            str(uuid4()),
            "--timeout",
            "0.1",
            "--poll-interval",
            "0.5",
            *_http_args(),
            "-o",
            "jsonl",
        ]
    )
    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert code == 4 and captured.err == "", (code, captured.err, records)
    assert records[0]["data"]["type"] == "initial"
    assert records[-1]["ok"] is False
    assert records[-1]["error"]["code"] == "CLI_TIMEOUT"


def test_wait_and_watch_json_are_single_documents_and_timeout_keeps_stdout_empty(
    open_transport: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    job_id = str(uuid4())
    open_transport(_PagedTransport(job_status=JobStatus.CANCELLED))
    assert main(["job", "wait", job_id, "--for", "cancelled", *_http_args(), "-o", "json"]) == 0
    wait = json.loads(capsys.readouterr().out)
    assert wait["kind"] == "WaitResult"
    assert wait["data"]["resource"]["status"] == "cancelled"

    assert main(["job", "watch", job_id, *_http_args(), "-o", "json"]) == 0
    watch = json.loads(capsys.readouterr().out)
    assert watch["kind"] == "WatchResult"
    assert [item["type"] for item in watch["data"]["items"]] == ["initial", "terminal"]

    open_transport(_PagedTransport())
    code = main(
        [
            "job",
            "wait",
            job_id,
            "--timeout",
            "0.1",
            "--poll-interval",
            "0.5",
            *_http_args(),
            "-o",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 4 and captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "CLI_TIMEOUT"


@pytest.mark.parametrize("output", ["table", "json", "yaml", "jsonl", "name"])
def test_renderers_are_deterministic_for_resource_lists(output: str) -> None:
    envelope = CliSuccessEnvelope(
        kind="JobList",
        command="job.list",
        data={"items": [{"job_id": "job-1", "status": "ready"}]},
        meta=CliMeta(transport="sql"),
    )
    stream = StringIO()
    render_success(envelope, output, stream=stream)  # type: ignore[arg-type]
    rendered = stream.getvalue()
    assert "job-1" in rendered
    if output == "json":
        assert json.loads(rendered)["api_version"] == "taskq.cli/v1"
    if output == "yaml":
        assert yaml.safe_load(rendered)["api_version"] == "taskq.cli/v1"


def test_structured_error_details_recursively_redact_secrets_and_payloads() -> None:
    _, envelope = normalize_error(
        TaskqValidationError(
            details={
                "field": "input",
                "payload": {"email": "person@example.test"},
                "nested": {
                    "bearer_token": "do-not-print",
                    "location": "postgresql://owner:do-not-print@db/taskq",
                },
            }
        ),
        command="job.enqueue",
        request_id="req-redaction",
    )
    rendered = envelope.model_dump_json()
    assert envelope.error.details["field"] == "input"
    assert "payload" not in envelope.error.details
    assert "do-not-print" not in rendered
