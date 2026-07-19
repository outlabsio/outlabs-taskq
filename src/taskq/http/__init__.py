"""Optional Protocol-v1 HTTP clients and wire metadata.

Install ``outlabs-taskq[http]`` to import this module. Importing it performs no
I/O and constructs no client, application, pool, or background task.
"""

try:
    import httpx as _httpx  # noqa: F401
except ModuleNotFoundError:
    raise ModuleNotFoundError(
        "taskq.http requires the HTTP extra: install 'outlabs-taskq[http]'"
    ) from None

from taskq.http.client import (
    AsyncTaskqHttpClient,
    TaskqAuthenticationError,
    TaskqAuthorizationError,
    TaskqHttpClient,
)
from taskq.protocol import (
    HTTP_COMMAND_SPECS,
    CommandEnvelope,
    ErrorEnvelope,
    HttpCommandName,
    HttpCommandSpec,
    HttpSurface,
    ProtocolError,
    RetryClass,
)

__all__ = [
    "AsyncTaskqHttpClient",
    "CommandEnvelope",
    "ErrorEnvelope",
    "HTTP_COMMAND_SPECS",
    "HttpCommandName",
    "HttpCommandSpec",
    "HttpSurface",
    "ProtocolError",
    "RetryClass",
    "TaskqHttpClient",
    "TaskqAuthenticationError",
    "TaskqAuthorizationError",
]
