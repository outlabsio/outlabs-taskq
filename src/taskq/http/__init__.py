"""Optional Protocol-v1 HTTP clients and wire metadata.

Install ``outlabs-taskq[http]`` to import this module. Importing it performs no
I/O and constructs no client, application, pool, or background task.
"""

try:
    import fastapi as _fastapi  # noqa: F401
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
from taskq.http.deps import (
    AuthContext,
    QueueAuthorizer,
    bearer_token_auth,
    callable_auth,
    legacy_taskq_auth,
    no_auth_for_tests,
    static_api_key_auth,
)
from taskq.http.facade import TaskqFacadeTransports, create_taskq_app, merge_taskq_openapi
from taskq.http.hub import ClaimWaitHub, ClaimWaitSubscription
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
    "AuthContext",
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
    "ClaimWaitHub",
    "ClaimWaitSubscription",
    "QueueAuthorizer",
    "TaskqFacadeTransports",
    "bearer_token_auth",
    "callable_auth",
    "create_taskq_app",
    "legacy_taskq_auth",
    "merge_taskq_openapi",
    "no_auth_for_tests",
    "static_api_key_auth",
]
