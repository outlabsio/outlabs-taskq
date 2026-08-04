"""Stable models for the human and coding-agent CLI contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, SecretStr, model_validator


OutputFormat = Literal["table", "json", "yaml", "jsonl", "name"]
TransportName = Literal["sql", "http"]


class CliMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: str | None = None
    transport: TransportName | None = None
    target: dict[str, Any] | None = None
    next_cursor: str | None = None
    sensitive_fields_included: bool = False
    request_id: str | None = None


class CliSuccessEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_version: Literal["taskq.cli/v1"] = "taskq.cli/v1"
    kind: str
    command: str
    ok: Literal[True] = True
    data: Any
    meta: CliMeta = Field(default_factory=CliMeta)
    warnings: tuple[str, ...] = ()


class CliErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    category: str
    message: str
    retryable: bool
    hint: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class CliErrorEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_version: Literal["taskq.cli/v1"] = "taskq.cli/v1"
    kind: Literal["Error"] = "Error"
    command: str
    ok: Literal[False] = False
    error: CliErrorBody


class ContextDefinition(BaseModel):
    """One secret-free named CLI context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transport: TransportName
    dsn_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    base_url: str | None = None
    bearer_token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    header_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]+$")
    header_value_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    auth_dsn_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    expected_environment: str | None = Field(default=None, min_length=1, max_length=63)
    expected_installation_id: UUID | None = None
    actor: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _closed_transport(self) -> ContextDefinition:
        if self.transport == "sql":
            if not self.dsn_env:
                raise ValueError("SQL context requires dsn_env")
            if any(
                value is not None
                for value in (
                    self.base_url,
                    self.bearer_token_env,
                    self.header_name,
                    self.header_value_env,
                )
            ):
                raise ValueError("SQL context cannot contain HTTP fields")
        else:
            if not self.base_url:
                raise ValueError("HTTP context requires base_url")
            bearer = self.bearer_token_env is not None
            header = self.header_name is not None or self.header_value_env is not None
            if bearer == header:
                raise ValueError("HTTP context requires exactly one credential source")
            if (self.header_name is None) != (self.header_value_env is None):
                raise ValueError("HTTP header name and value environment must be paired")
            if self.dsn_env is not None:
                raise ValueError("HTTP context cannot contain dsn_env")
            if self.actor is not None:
                raise ValueError("HTTP context cannot configure an actor")
        return self


class ContextFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    contexts: dict[str, ContextDefinition]


class ResolvedConnection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: str | None = None
    transport: TransportName
    dsn: SecretStr | None = None
    base_url: str | None = None
    bearer_token: SecretStr | None = None
    header_name: str | None = None
    header_value: SecretStr | None = None
    auth_dsn: SecretStr | None = None
    expected_environment: str | None = None
    expected_installation_id: UUID | None = None
    actor: str | None = None


class CliQueueProfileInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_priority: int | None = Field(default=None, ge=0, le=1000)
    default_lease_seconds: int | None = Field(default=None, ge=15, le=86400)
    default_max_attempts: int | None = Field(default=None, ge=1, le=100)
    default_backoff_mode: Literal["fixed", "exponential"] | None = None
    default_backoff_base: int | None = Field(default=None, ge=1, le=86400)
    default_backoff_cap: int | None = Field(default=None, ge=1, le=86400)
    retention_hours: int | None = Field(default=None, ge=1)
    failed_retention_hours: int | None = Field(default=None, ge=1)
    max_depth: int | None = Field(default=None, ge=1)
    notify_enabled: bool | None = None


class CliWorkflowCreateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_key: str = Field(min_length=1, max_length=255)
    kind: Literal["dag", "batch"]
    params: dict[str, Any] = Field(default_factory=dict)
    declared_queues: tuple[str, ...] = Field(min_length=1, max_length=32)
    member_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    continuation_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CliEmptyInput(BaseModel):
    """Schema marker for commands whose input is entirely positional/options."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CliDataOutput(RootModel[Any]):
    """Schema marker for command data carried inside the stable CLI envelope."""


@dataclass(frozen=True, slots=True)
class CliCommandSpec:
    path: str
    summary: str
    transports: tuple[TransportName, ...] = ()
    capability: str | None = None
    role: str | None = None
    mutates: bool = False
    destructive: bool = False
    input_model: type[BaseModel] = CliEmptyInput
    output_model: type[BaseModel] = CliDataOutput
    examples: tuple[str, ...] = ()

    @property
    def danger_level(self) -> Literal["read-only", "mutation", "destructive"]:
        if self.destructive:
            return "destructive"
        return "mutation" if self.mutates else "read-only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "summary": self.summary,
            "transports": list(self.transports),
            "capability": self.capability,
            "role": self.role,
            "mutates": self.mutates,
            "destructive": self.destructive,
            "danger_level": self.danger_level,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "examples": list(self.examples or (f"taskq {self.path.replace('.', ' ')} --help",)),
            "exit_codes": {
                "0": "success",
                "1": "non-retryable operation failure",
                "2": "usage, configuration, or safety refusal",
                "3": "retryable, unavailable, or runtime failure",
                "4": "wait timeout",
                "5": "partial or degraded result",
                "130": "watch interrupted",
            },
        }


__all__ = [
    "CliCommandSpec",
    "CliDataOutput",
    "CliEmptyInput",
    "CliErrorBody",
    "CliErrorEnvelope",
    "CliMeta",
    "CliQueueProfileInput",
    "CliSuccessEnvelope",
    "CliWorkflowCreateInput",
    "ContextDefinition",
    "ContextFile",
    "OutputFormat",
    "ResolvedConnection",
    "TransportName",
]
