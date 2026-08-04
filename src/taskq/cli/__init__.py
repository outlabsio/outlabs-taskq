"""TaskQ's versioned, resource-oriented command-line surface."""

import importlib

from taskq.sql.transport import SqlTaskqTransport

from ._runtime import (
    _asyncpg_dsn,
    _load_registry,
    _print_auth_report,
    _run_auth_sync,
    _run_migrate,
    _run_scheduler_command,
    _run_target_command,
    _run_verify,
    _run_worker,
)
from .app import COMMAND_SPECS, cli, main

__all__ = [
    "COMMAND_SPECS",
    "SqlTaskqTransport",
    "_asyncpg_dsn",
    "_load_registry",
    "_print_auth_report",
    "_run_auth_sync",
    "_run_migrate",
    "_run_scheduler_command",
    "_run_target_command",
    "_run_verify",
    "_run_worker",
    "cli",
    "importlib",
    "main",
]
