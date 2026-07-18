"""outlabs-taskq — Postgres-native durable task queue."""

from __future__ import annotations

__version__ = "0.1.0a0"

from taskq.errors import (
    TaskqBackpressureError,
    TaskqCapabilityError,
    TaskqConfigError,
    TaskqConflictError,
    TaskqError,
    TaskqInternalError,
    TaskqNotFoundError,
    TaskqUnavailableError,
    TaskqValidationError,
    TaskqVersionError,
    UnknownTaskError,
)
from taskq.protocol import ClaimedJob, EnqueueResult, EnqueueStatus, TqCode
from taskq.registry import RetryStrategy, RetryValue, Task, TaskRegistry

__all__ = [
    "ClaimedJob",
    "EnqueueResult",
    "EnqueueStatus",
    "RetryStrategy",
    "RetryValue",
    "Task",
    "TaskRegistry",
    "TaskqBackpressureError",
    "TaskqCapabilityError",
    "TaskqConfigError",
    "TaskqConflictError",
    "TaskqError",
    "TaskqInternalError",
    "TaskqNotFoundError",
    "TaskqUnavailableError",
    "TaskqValidationError",
    "TaskqVersionError",
    "TqCode",
    "UnknownTaskError",
    "__version__",
]
