"""Secret-safe configuration for the core worker command."""

from __future__ import annotations

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        extra="forbid",
        env_prefix="TASKQ_",
        env_file=None,
    )

    dsn: SecretStr | None = None
    http_base_url: str | None = Field(default=None, min_length=1)
    http_bearer_token: SecretStr | None = None
    http_header_name: str | None = Field(default=None, min_length=1, max_length=128)
    http_header_value: SecretStr | None = None
    http_claim_wait_seconds: float = Field(default=25.0, ge=0, le=30)
    registry: str = Field(min_length=3)
    queues: tuple[str, ...]
    environment: str = Field(min_length=1)
    worker_id: str | None = Field(default=None, min_length=1, max_length=200)
    concurrency: int = Field(default=1, ge=1, le=1000)
    sync_workers: int | None = Field(default=None, ge=1, le=1000)
    batch: int = Field(default=1, ge=1, le=50)
    poll_interval: float = Field(default=5.0, ge=0.1, le=3600)
    listen: bool = True
    presence_interval: float = Field(default=60.0, ge=5, le=3600)
    soft_stop_timeout: float | None = Field(default=None, ge=0)
    expected_environment: str | None = Field(
        default=None,
        validation_alias=AliasChoices("expected_environment", "TASKQ_EXPECTED_ENV"),
    )
    allow_production: bool = False
    pool_size: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def _validate_worker(self) -> WorkerSettings:
        if (self.dsn is None) == (self.http_base_url is None):
            raise ValueError("configure exactly one of dsn or http_base_url")
        header_pair = self.http_header_name is not None or self.http_header_value is not None
        if (self.http_header_name is None) != (self.http_header_value is None):
            raise ValueError("HTTP header name and value must be configured together")
        if self.http_base_url is not None:
            if (self.http_bearer_token is None) == (not header_pair):
                raise ValueError("configure exactly one HTTP credential source")
            if self.listen:
                raise ValueError("HTTP workers cannot use PostgreSQL LISTEN")
            if len(self.queues) > 1 and self.http_claim_wait_seconds > 0:
                raise ValueError("multi-queue HTTP workers require claim wait zero")
            if self.pool_size is not None:
                raise ValueError("pool_size applies only to DSN workers")
        elif self.http_bearer_token is not None or header_pair:
            raise ValueError("HTTP credentials require http_base_url")
        if self.sync_workers is not None and self.sync_workers > self.concurrency:
            raise ValueError("sync_workers cannot exceed concurrency")
        if self.batch > self.concurrency:
            raise ValueError("batch cannot exceed concurrency")
        if self.expected_environment is not None and self.expected_environment != self.environment:
            raise ValueError("declared environment does not match expected_environment")
        if self.environment == "production" and not self.allow_production:
            raise ValueError("production requires allow_production=True")
        if self.dsn is not None and self.pool_size is None:
            object.__setattr__(self, "pool_size", min(self.concurrency + 2, 1000))
        return self


__all__ = ["WorkerSettings"]
