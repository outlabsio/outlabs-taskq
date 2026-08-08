"""Deterministic human and machine rendering for the CLI."""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any, TextIO
from uuid import UUID

import yaml
from pydantic import BaseModel

from .models import CliErrorEnvelope, CliSuccessEnvelope, OutputFormat


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if isinstance(value, (datetime, date, UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    return value


def _compact(value: Any, *, limit: int = 72) -> str:
    if value is None:
        rendered = "-"
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (Mapping, list, tuple)):
        rendered = json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"))
    else:
        rendered = str(value)
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _table_rows(data: Any) -> tuple[list[str], list[list[str]]]:
    value = jsonable(data)
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        value = value["items"]
    if isinstance(value, list):
        mappings = [item for item in value if isinstance(item, Mapping)]
        if len(mappings) != len(value):
            return ["VALUE"], [[_compact(item)] for item in value]
        columns: list[str] = []
        for item in mappings:
            for key in item:
                if key not in columns:
                    columns.append(str(key))
        return [item.upper() for item in columns], [
            [_compact(row.get(column)) for column in columns] for row in mappings
        ]
    if isinstance(value, Mapping):
        return ["FIELD", "VALUE"], [[str(key), _compact(item)] for key, item in value.items()]
    return ["VALUE"], [[_compact(value)]]


def _render_table(data: Any) -> str:
    headers, rows = _table_rows(data)
    if not rows:
        return "No resources found.\n"
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    rendered = [line]
    rendered.extend(
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        for row in rows
    )
    return "\n".join(rendered) + "\n"


def _names(data: Any) -> str:
    value = jsonable(data)
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        value = value["items"]
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            for key in (
                "job_id",
                "workflow_id",
                "worker_id",
                "schedule_id",
                "installation_id",
                "queue",
                "name",
                "id",
            ):
                if item.get(key) is not None:
                    result.append(str(item[key]))
                    break
            else:
                raise ValueError("resource has no canonical name field")
        else:
            result.append(str(item))
    return "".join(f"{item}\n" for item in result)


def render_success(
    envelope: CliSuccessEnvelope,
    output: OutputFormat,
    *,
    stream: TextIO | None = None,
) -> None:
    stream = stream or sys.stdout
    payload = envelope.model_dump(mode="json")
    if output == "json":
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    elif output == "yaml":
        stream.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    elif output == "jsonl":
        data = payload["data"]
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            for sequence, item in enumerate(items, start=1):
                stream.write(
                    json.dumps(
                        {
                            "api_version": "taskq.cli/v1",
                            "kind": f"{envelope.kind}Item",
                            "command": envelope.command,
                            "sequence": sequence,
                            "data": item,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            stream.write(
                json.dumps(
                    {
                        "api_version": "taskq.cli/v1",
                        "kind": "PageEnd",
                        "command": envelope.command,
                        "meta": payload["meta"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        else:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    elif output == "name":
        stream.write(_names(envelope.data))
    else:
        stream.write(_render_table(envelope.data))
        for warning in envelope.warnings:
            print(f"warning: {warning}", file=sys.stderr)


def render_error(
    envelope: CliErrorEnvelope,
    output: OutputFormat,
    *,
    stream: TextIO | None = None,
) -> None:
    stream = stream or sys.stderr
    payload = envelope.model_dump(mode="json")
    if output in {"json", "jsonl"}:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    elif output == "yaml":
        stream.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    else:
        error = envelope.error
        stream.write(f"error[{error.code}]: {error.message}\n")
        if error.hint:
            stream.write(f"hint: {error.hint}\n")


__all__ = ["jsonable", "render_error", "render_success"]
