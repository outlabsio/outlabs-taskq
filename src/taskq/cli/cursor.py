"""Opaque, target-bound CLI cursors shared by SQL and HTTP list commands."""

from __future__ import annotations

import base64
import json
from typing import Any

from taskq.errors import TaskqConfigError


def encode_cursor(
    *, command: str, transport: str, target: str | None, filters: dict[str, Any], value: Any
) -> str | None:
    if value is None:
        return None
    payload = json.dumps(
        {
            "v": 1,
            "command": command,
            "transport": transport,
            "target": target,
            "filters": filters,
            "value": value,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(
    token: str | None,
    *,
    command: str,
    transport: str,
    target: str | None,
    filters: dict[str, Any],
) -> Any:
    if token is None:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception as exc:
        raise TaskqConfigError("cursor is malformed") from exc
    expected = {
        "v": 1,
        "command": command,
        "transport": transport,
        "target": target,
        "filters": filters,
    }
    if not isinstance(payload, dict) or any(
        payload.get(key) != value for key, value in expected.items()
    ):
        raise TaskqConfigError("cursor does not belong to this command, target, or filter set")
    return payload.get("value")


__all__ = ["decode_cursor", "encode_cursor"]
