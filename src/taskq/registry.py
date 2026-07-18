"""Immutable typed task metadata and collision-safe registration."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import NoneType, UnionType
from typing import Any, Generic, TypeAlias, TypeVar, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from taskq.errors import TaskqConfigError, UnknownTaskError
from taskq.execution import HANDLER_RESULT_TYPES, JobContext

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)

_WIRE_NAME = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")
_QUEUE_NAME = re.compile(r"[a-z0-9_]{1,57}\Z")


class RetryStrategy(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    max_attempts: int | None = Field(default=None, ge=1, le=100)
    mode: str = "exponential"
    base_seconds: int = Field(default=30, ge=1, le=86400)
    cap_seconds: int = Field(default=3600, ge=1)
    retry_exceptions: tuple[type[BaseException], ...] | None = None

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        if value not in {"fixed", "exponential"}:
            raise ValueError("mode must be fixed or exponential")
        return value

    @field_validator("retry_exceptions")
    @classmethod
    def _valid_exception_types(
        cls, value: tuple[type[BaseException], ...] | None
    ) -> tuple[type[BaseException], ...] | None:
        if value is not None and any(
            not isinstance(item, type) or not issubclass(item, BaseException) for item in value
        ):
            raise ValueError("retry_exceptions must contain exception types")
        return value

    @field_validator("cap_seconds")
    @classmethod
    def _cap_is_bounded(cls, value: int) -> int:
        if value > 2_147_483_647:
            raise ValueError("cap_seconds exceeds PostgreSQL integer range")
        return value

    @model_validator(mode="after")
    def _cap_covers_base(self) -> RetryStrategy:
        if self.cap_seconds < self.base_seconds:
            raise ValueError("cap_seconds must be greater than or equal to base_seconds")
        return self


RetryValue: TypeAlias = bool | int | RetryStrategy
Handler: TypeAlias = Callable[..., Any]


def _validate_wire_name(value: str, *, field: str) -> None:
    if not 1 <= len(value) <= 120 or _WIRE_NAME.fullmatch(value) is None:
        raise TaskqConfigError(f"{field} must match the durable wire-name grammar")


def _validate_handler(
    handler: Handler, input_model: type[BaseModel], output_model: type[BaseModel]
) -> None:
    try:
        hints = get_type_hints(handler)
    except Exception as exc:
        raise TaskqConfigError("handler annotations could not be resolved") from exc
    signature = inspect.signature(handler)
    if any(
        parameter.kind is parameter.VAR_POSITIONAL for parameter in signature.parameters.values()
    ):
        raise TaskqConfigError("handler cannot declare variadic positional parameters")
    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(parameters) not in (1, 2):
        raise TaskqConfigError("handler must declare payload or context plus payload")
    input_name = parameters[-1].name
    if hints.get(input_name) is not input_model:
        raise TaskqConfigError("handler input annotation must match the task input model")
    if len(parameters) == 2 and hints.get(parameters[0].name) is not JobContext:
        raise TaskqConfigError("two-argument handler context must be annotated as JobContext")

    result_hint = hints.get("return")
    allowed = {output_model, NoneType, *HANDLER_RESULT_TYPES}
    if get_origin(result_hint) in (Union, UnionType):
        result_types = set(get_args(result_hint))
    else:
        result_types = {result_hint}
    if None in result_types or not result_types or not result_types <= allowed:
        raise TaskqConfigError("handler return annotation must use the task output or result types")


@dataclass(frozen=True, slots=True)
class Task(Generic[InT, OutT]):
    name: str
    queue: str
    input_model: type[InT]
    output_model: type[OutT]
    aliases: tuple[str, ...] = ()
    retry: RetryValue = True
    priority: int | None = None
    lease_seconds: int | None = None
    handler: Handler | None = None

    def __post_init__(self) -> None:
        _validate_wire_name(self.name, field="name")
        if _QUEUE_NAME.fullmatch(self.queue) is None:
            raise TaskqConfigError("queue must match [a-z0-9_]{1,57}")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TaskqConfigError("input_model must be a Pydantic BaseModel subclass")
        if not isinstance(self.output_model, type) or not issubclass(self.output_model, BaseModel):
            raise TaskqConfigError("output_model must be a Pydantic BaseModel subclass")
        aliases = tuple(self.aliases)
        object.__setattr__(self, "aliases", aliases)
        for alias in aliases:
            _validate_wire_name(alias, field="alias")
        if len(set(aliases)) != len(aliases) or self.name in aliases:
            raise TaskqConfigError("aliases must be distinct and cannot repeat the canonical name")
        if type(self.retry) is int and not 1 <= self.retry <= 100:
            raise TaskqConfigError("integer retry must be between 1 and 100")
        if not isinstance(self.retry, (bool, int, RetryStrategy)):
            raise TaskqConfigError("retry must be bool, int, or RetryStrategy")
        if self.priority is not None and not 0 <= self.priority <= 1000:
            raise TaskqConfigError("priority must be between 0 and 1000")
        if self.lease_seconds is not None and not 15 <= self.lease_seconds <= 86400:
            raise TaskqConfigError("lease_seconds must be between 15 and 86400")
        if self.handler is not None:
            _validate_handler(self.handler, self.input_model, self.output_model)

    def validate_payload(self, value: InT | Mapping[str, object]) -> dict[str, Any]:
        payload = self.input_model.model_validate(value).model_dump(mode="json")
        if not isinstance(payload, dict):  # defensive: BaseModel currently always dumps an object
            raise TaskqConfigError("task input must serialize to a JSON object")
        return payload

    @property
    def handler_is_async(self) -> bool:
        return self.handler is not None and inspect.iscoroutinefunction(self.handler)


class TaskRegistry:
    def __init__(self, tasks: Iterable[Task[Any, Any]] = ()) -> None:
        self._canonical: dict[str, Task[Any, Any]] = {}
        self._lookup: dict[str, Task[Any, Any]] = {}
        self.register_many(tasks)

    def __iter__(self) -> Iterator[Task[Any, Any]]:
        return iter(self._canonical.values())

    def __len__(self) -> int:
        return len(self._canonical)

    def register(self, task: Task[Any, Any]) -> Task[Any, Any]:
        self.register_many((task,))
        return task

    def register_many(self, tasks: Iterable[Task[Any, Any]]) -> None:
        canonical = dict(self._canonical)
        lookup = dict(self._lookup)
        for task in tasks:
            keys = (task.name, *task.aliases)
            collisions = sorted(set(keys) & lookup.keys())
            if collisions:
                raise TaskqConfigError(f"task name collision: {collisions[0]!r}")
            canonical[task.name] = task
            for key in keys:
                lookup[key] = task
        self._canonical = canonical
        self._lookup = lookup

    def resolve(self, name: str) -> Task[Any, Any] | None:
        return self._lookup.get(name)

    def canonical(self, name: str) -> str:
        task = self.resolve(name)
        if task is None:
            raise UnknownTaskError(name)
        return task.name

    def require(self, task_or_name: Task[Any, Any] | str) -> Task[Any, Any]:
        if isinstance(task_or_name, str):
            task = self.resolve(task_or_name)
            if task is None:
                raise UnknownTaskError(task_or_name)
            return task
        registered = self._canonical.get(task_or_name.name)
        if registered is not task_or_name:
            raise UnknownTaskError(task_or_name.name)
        return registered


__all__ = ["RetryStrategy", "RetryValue", "Task", "TaskRegistry"]
