# taskq — Stage 3 FastAPI and Authorization Specification

> **Status:** S3-00 implementation specification — frozen 2026-07-18; round-5 documentation remediation applied the same day
> **Authority:** Tier 3. Transport Protocol v1 document revision 1.0.4, Function Manifest 0.1.2, and ADR-001..017 win every conflict.
> **Depends on:** completed Stage 2A–2D, including the audited worker service, consumer testing surface, and S3-PREP protocol hardening.

## 1. Purpose and boundary

Stage 3 adds the optional HTTP and authorization layers around the completed SQL and Python core:

- a generated Protocol-v1 FastAPI facade;
- first-class asynchronous and synchronous HTTP clients;
- queue-scoped authorization with authoritative job lookup;
- a composable FastAPI lifespan runtime with housekeeper and opt-in embedded worker;
- the optional OutLabs authorization adapter, catalog, and explicit provisioning tools; and
- permanent SQL-versus-HTTP parity, packaging, lifecycle, and security evidence.

The design goal is a one-line small deployment without hiding production topology. A host mounts
the isolated Taskq sub-application with a simple static authorizer, or supplies the OutLabs adapter
and per-queue permissions.
An embedded worker is always opt-in; a dedicated worker remains the default for multi-process or
autoscaled deployments.

This specification adds no SQL function, migration, role, grant, table, composite, outcome, route,
or permission action. SQL contract stays 0.1.2; Protocol wire major stays 1 and document revision
is 1.0.4. It does not implement Stage-4 host adoption, 0.2/0.3 capabilities, an admin UI, SSE,
general job listing, queue-detail reads, or a public tick/janitor route.

## 2. Package and import boundary

The public modules are:

```text
taskq                         core; never imports FastAPI, HTTPX, or OutLabs authorization
taskq.http                    FastAPI facade, wire models, clients, runtime, generic authorizers
taskq.http.outlabs            OutLabs adapter and provisioning; imported only on explicit request
```

Extras remain:

- `taskq[http]`: FastAPI and HTTPX;
- `taskq[outlabs]`: `taskq[http]` plus the pinned supported OutLabs authorization range.

`import taskq` and the Stage-2 worker/testing imports succeed without either extra. `import
taskq.http` succeeds without OutLabs authorization. Only importing `taskq.http.outlabs` requires the
OutLabs extra. No import constructs an app, reads environment, creates a pool/client, starts a task,
registers an event handler, opens a socket, seeds IAM, or mutates global FastAPI state.

The root package may lazily re-export stable HTTP names only through guarded optional imports; it
must not make the core import depend on an extra. Error messages for missing extras name the exact
extra to install and contain no import traceback or secret.

## 3. Capability-sized transport protocols

The existing all-capabilities `TaskqTransport` remains the SQL transport intersection. Stage 3
splits structural typing into capability protocols without changing methods or runtime semantics:

```python
class ProducerTransport(Protocol): ...      # enqueue, enqueue_many, aclose
class RunnerTransport(Protocol): ...        # claim, job heartbeat/settles, presence, aclose
class ObserverTransport(Protocol): ...      # safe projections/meta/stats/metrics, aclose
class OperatorTransport(Protocol): ...      # operator commands, aclose
class HousekeeperTransport(Protocol): ...   # tick, janitor, aclose
class TaskqTransport(
    ProducerTransport,
    RunnerTransport,
    ObserverTransport,
    OperatorTransport,
    HousekeeperTransport,
    Protocol,
): ...
```

`TaskQ` depends on `ProducerTransport`; `WorkerSupervisor` and `WorkerService` depend on
`RunnerTransport`; facade components accept only the capability they use. The SQL transport still
implements the intersection. An HTTP runner client does not grow fake tick, janitor, authorization
projection, or raw-view methods merely to satisfy an oversized protocol. Unsupported capability
access fails at construction/type-check boundaries, not after a job is claimed.

Closing a composite transport closes each owned resource exactly once. Capability views over one
underlying transport are non-owning. Existing callers typed against `TaskqTransport` remain valid.

## 4. Single generated Protocol-v1 source

### 4.1 Generation rule

`taskq.protocol` remains the only Python source for closed command names, value models, SQL
identities, capability roles, outcomes, TQ errors, and replay rules. Stage 3 extends that source with
immutable HTTP metadata: method, canonical path, action, queue source, active/gated/deferred state,
request/result adapters, status mapping, and a closed per-command retry classification sufficient to
distinguish never-retry, keyed enqueue, safe idempotent command, and worker-owned fenced settlement.

That one source generates or drives:

- FastAPI route registration and OpenAPI;
- asynchronous and synchronous HTTP client methods;
- request/response adapters and envelope decoders;
- protocol-header and capability negotiation;
- SQL-reference versus HTTP parity vectors; and
- a deterministic generated artifact compared with a catalog oracle hand-derived independently from
  the Tier-0 Protocol and Function Manifest.

Hand-copied route dictionaries, per-client status tables, duplicate TQ registries, and handwritten
OpenAPI response unions are forbidden. The independent oracle never imports generated metadata for
expected values. Generation is deterministic and has a checked-in or test-compared normalized
output so drift is reviewable.

### 4.2 Active, gated, and excluded 0.1 surface

The canonical paths and mappings come from Protocol document revision 1.0.4. The generated active
surface is:

| Area | Commands/routes |
|---|---|
| Meta | `GET /taskq/v1/meta` |
| Producer | single and batch enqueue under `/taskq/v1/queues/{queue}/jobs` |
| Runner | queue claim; job heartbeat; complete, fail, release, snooze, cancel-running; worker-presence heartbeat |
| Reads | job detail; per-queue and global queue stats; `/taskq/metrics` |
| Operator | queue ensure (no version/ETag), pause/resume, cancel/redrive/expire, purge, run-now, reprioritize, concurrency limit, worker shutdown request, worker lease expiry |

`GET /taskq/v1/workers` is **declared and generated but gated**: its method/OpenAPI error/client
surface exists and returns typed `TQ501`; it has no success model. `GET
/taskq/v1/queues/{queue}` and `GET /taskq/v1/jobs` are **deferred out** under ADR-015/017 and have no
generated operation, OpenAPI operation, success schema, or client method. Each reserved path is
still mounted as a hidden negative responder returning typed `TQ501`.
`get_authorization_projection` is facade-internal;
`redrive_failed`, `tick`, and `janitor` remain DB/CLI-only because Protocol v1 defines no public HTTP
route. Inactive dependency, workflow, schedule, archive, SSE, and uniqueness-mode fields are rejected
with `TQ501`, never ignored or prematurely represented.

Queue ensure and every other operator route are generated only when both a separate operator
transport/pool and `operator_authorizer` are configured; supplying only one is a construction error.
Their absence is deployment surface reduction, not fallback to the ordinary facade credential or
primary authorizer. Hidden deferred-route responders remain mounted because they execute no SQL.

## 5. Wire models, envelopes, and error normalization

### 5.1 Direction and size

Inbound command models use `extra="forbid"`; unknown fields, malformed JSON, explicit null outside
a nullable domain, invalid path values, and inactive fields normalize to the Protocol registry.
Outbound models use `extra="ignore"` for additive v1 compatibility. All H-09 byte/item/count bounds
are checked before SQL except ADR-012's diagnostic carve-out: settlement error/cancel text may pass
through within the request ceiling and SQL performs the authoritative byte-safe truncation so
diagnostics can never block settlement. Batch validation is one atomic model pass and reports a
1-based input index, matching SQL, without partially authorizing or mutating.

The claim wire projection is distinct from the fence-redacted public Python `ClaimedJob`
serialization: it includes `attempt_id` because claim is the sole fence-delivery channel. Settlement
request models mark the attempt id write-only/sensitive. Attempt ids never appear in URLs, response
examples, read models, error details, request representations, structured logs, metrics, tracing
attributes, or validation messages.

### 5.2 Common envelope

Every JSON result is a discriminated model containing exactly the Protocol fields:

```python
class CommandEnvelope[T]:
    protocol_version: Literal[1]
    request_id: str
    outcome: str
    data: T

class ErrorEnvelope:
    protocol_version: Literal[1]
    request_id: str
    error: ProtocolError
```

`Taskq-Protocol-Version: 1` is echoed on every response. Missing input is accepted; unsupported input
returns `TQ426`. `Taskq-Request-Id` follows ADR-016/017 exactly: validated 1–128 safe ASCII, or a
server-minted lowercase UUID, echoed in body and header, and never stored unbounded. Invalid supplied
values are correlation-absent: authenticate first, then reject with `TQ422` using the minted value;
the invalid bytes are never echoed or logged.

Expected races (`lost`, `already_settled`, `settle_conflict`, paused/empty/timeout/unavailable) are
typed command outcomes even where their HTTP status is non-2xx. Native or unregistered database
errors become opaque `TQ500`; protected logs retain the source class/SQLSTATE but no SQL text,
arguments, payload, secret, or fence. Authentication failures use `AUTH401`/`AUTH403` inside the
same outer envelope. Every **error** response reports registry-owned `retryable`; `Retry-After` is emitted only
where the Protocol permits it.

### 5.3 Actor and request data

No public request accepts `actor`. `AuthContext.actor` is the authenticated subject and supplies
operator SQL actor arguments plus facade audit fields. Runner SQL functions still record their
contract-owned worker label; the protected facade audit records both subject and advisory label
without pretending they are the same identity. Caller-supplied queue/job type on legacy aliases are
assertions only and are excluded from canonical job-id routes.

## 6. HTTP clients and remote workers

`taskq.http` exports `AsyncTaskqHttpClient` and `TaskqHttpClient`. Both are generated from the same
HTTP command metadata and return the same core typed domain values as SQL after envelope removal.
The asynchronous client implements the capability protocols required by `TaskQ` and
`WorkerService`; the synchronous client exposes the same active public commands for synchronous
hosts. Neither claims DB-only capabilities.

Construction is side-effect free. Clients support:

- a normalized base URL and fixed `/taskq/v1` major;
- exactly one credential source: bearer, named static header, or caller-supplied HTTPX auth;
- an owned or borrowed HTTPX client with explicit close ownership;
- connect/read/write/pool timeouts, with claim-read timeout greater than configured long poll;
- optional `Taskq-Request-Id` generation/provider;
- explicit `start()`/`ensure_compatible()` that reads `/meta` before first claim; and
- bounded, secret-safe diagnostics and snapshots.

Credentials are secret values and never appear in repr, exceptions, logs, or retry records.
Borrowed HTTPX clients are never closed or reconfigured. Sync/async clients do not share a mutable
session across process forks.

Automatic retries are generated from each command's immutable retry classification and follow only
Protocol §5. Enqueue retries require the same non-empty idempotency
key; a mixed/non-keyed batch is never retried automatically. Claim is never replayed after an
unknown response. Fenced settlement retry ownership remains exclusively in the Stage-2 worker
settlement layer; the HTTP client performs one settlement request per call and never adds an inner
retry loop.
`TQ501`, `TQ422`, `lost`, and `settle_conflict` never loop. Server `Retry-After` plus bounded jitter
is honored for permitted retryable commands.

An HTTP-backed `WorkerService` uses the same Stage-2 supervisor, cancellation, heartbeat, replay,
and soft-stop semantics. Server-side long polling (`claim_wait_seconds`, default 25, maximum 30) is
permitted only for a worker configured with exactly one queue. Multi-queue HTTP workers must use
immediate claims (`wait_seconds=0`) through the existing fair sweep plus a bounded monotonic poll
interval; configuration rejects a positive wait with multiple queues. On stop, only an in-flight
**HTTP long-poll claim** is cancelled so the Stage-2 grace window is not consumed by the server wait.
That cancellation may lose a committed claim response but never fabricates a fence or settlement;
lease recovery is authoritative. SQL-mode worker cancellation, defaults, listener behavior, and
sweep semantics remain byte-for-byte unchanged. Presence uses ADR-014 and a remote drain signal is
sticky. HTTP wire `timeout` normalizes to core `ClaimState.EMPTY`; elapsed/deadline information may
remain on a raw HTTP result wrapper but never grows the core state enum. Sync handlers remain in the
audited bounded executor; the synchronous HTTP client does not imply event-loop blocking.

The existing worker CLI gains an explicit HTTP transport mode only in the implementation slice. DSN
and HTTP base URL are mutually exclusive; credentials are environment/secret inputs; HTTP workers
cannot tick or janitor. Existing SQL mode remains byte-for-byte compatible in defaults and safety
interlocks.

## 7. Generic authorization facade

### 7.1 Public types

`taskq.http` defines no OutLabs imports:

```python
class TaskqAction(StrEnum):
    ENQUEUE = "enqueue"
    RUN = "run"
    READ = "read"
    CONTROL = "control"
    ADMIN = "admin"

class AuthContext(BaseModel):
    actor: str
    principal: object

class QueueAuthorizer(Protocol):
    async def authenticate(self, request: Request) -> AuthContext: ...
    async def authorize_context(
        self, request: Request, context: AuthContext,
        action: TaskqAction, queue: str | None
    ) -> None: ...
    async def authorize(
        self, request: Request, action: TaskqAction, queue: str | None
    ) -> AuthContext: ...
```

The five actions are closed. `queue=None` means a global permission. Generic adapters include
static API-key, bearer-token, callable, legacy read/write/operator shim, and an explicitly named
test-only no-auth adapter. There is no production default authorizer: `create_taskq_app` refuses to
construct without one. Queue-blind simple adapters authorize all queues after credential success;
that simplicity is explicit, never represented as queue isolation.
The two phased methods are the facade's security boundary: authenticate produces one context before
body-error disclosure or job lookup, then `authorize_context` applies each canonical queue check
without re-authenticating. `authorize` is the one-call convenience for consumers that already know
their queue. Construction rejects adapters missing either phase rather than weakening the ordering.

### 7.2 Ordering and queue source

Every request executes this security order:

1. select a safe response correlation value: use a valid supplied request id or mint one, but defer
   rejection of an invalid supplied value;
2. authenticate before database lookup or detailed validation disclosure (a failed authentication
   uses the safe minted correlation value when the supplied header is invalid);
3. validate the supplied request-id/protocol headers and the complete strict command, then derive its
   canonical queue source;
4. authorize every required `(action, queue)` pair before mutation;
5. execute through the least-capability transport; and
6. normalize the typed result/envelope and emit a fence-free audit event.

Path-addressed commands authorize the canonical path queue. Protocol 0.1 bulk has one path queue, so
one check covers the whole atomic batch. ADR-014 worker presence validates a non-empty distinct list
and authorizes `run` for **every** declared queue before one SQL call. Global stats and globally
scoped operator commands require a global action.

For every job-id route the order is immutable:

```text
authenticate
→ get_authorization_projection(job_id) through observer capability
→ missing = TQ001/404
→ authorize(action, projection.queue)
→ execute/read using job id and any fence
```

The caller never supplies the authorization queue. The mutation rechecks ownership atomically.
Default denial is 403 naming the queue; `not_found_on_forbidden=True` maps denial to a response
field-identical to the genuine `TQ001` missing-job envelope except for `request_id`, so it cannot
become an existence oracle. Projection and mutation use short independent transactions and never
hold a connection while authorization performs external work.

### 7.3 Database credential split

The ordinary facade login has producer + runner + observer + housekeeper memberships and **never**
operator. Queue ensure is an operator/admin command, not ordinary bootstrap. It and all other
operator routes require an opt-in separate pool/login with observer + operator plus the explicit
`operator_authorizer`. The
operator pool never executes producer/runner/housekeeper commands; the ordinary pool never retries
an operator command after permission denial. Omitting the operator pool omits those routes.

Internal housekeeper calls use only `HousekeeperTransport`; there is no public tick route. Metrics
uses observer capability and an independently configurable scrape authorizer; when omitted, the
primary authorizer performs global `read` authorization. `/meta` is
authenticated `read` by default, with an explicit deployment-policy option for public metadata as
per Protocol v1.

The gated 0.1 worker-list operation authorizes global `read` before returning `TQ501`; its possible
future operator/control semantics do not create an any-of exception while the safe projection is
inactive. Per-queue stats with no snapshot return typed `ok` plus an empty `items` list. In 0.1 that
honestly covers both snapshot lag and an unknown configuration because the observer contract exposes
no separate queue-profile oracle; it never borrows operator access to distinguish them.

## 8. FastAPI sub-application and long polling

`create_taskq_app(runtime, *, authorizer, operator_transport=None,
operator_authorizer=None, not_found_on_forbidden=False, meta_public=False,
metrics_authorizer=None)` returns a lifespan-free `FastAPI` sub-application and performs no I/O.
The stable integration mounts it at `/taskq`; the sub-application registers `/v1/...` internally so
the external paths remain the canonical `/taskq/v1/...`. Mounting a bare generated `APIRouter` is
unsupported. Host compatibility aliases are separate adapters that call the same generated handlers
and never define a second model or outcome table.

The sub-application uses a custom `TaskqRoute` to own parsing, validation, dependency, handler, and
response exceptions for matched commands, with input echo suppressed. Sub-application exception
handlers/middleware own unmatched 404, method-mismatch 405, and uncaught 500 normalization. Thus
FastAPI's native error bodies never escape the `/taskq` boundary and settlement fences appear
nowhere in error bodies, headers, or logs. The two deferred reserved GET paths are explicit hidden
responders, not accidental 404/405 behavior.

The mounted app publishes its generated schema at `/taskq/openapi.json` with `/v1/...` paths and a
`/taskq` server/base entry; mounted operations are not silently merged into the host's
`/openapi.json`. Hosts wanting one schema explicitly merge the deterministic schema through the
provided composition helper, which prefixes those paths to `/taskq/v1/...`. In either form,
OpenAPI advertises exact discriminated success/error unions, limits, protocol/request headers,
authorization, retry statuses, and write-only fences; it contains no credentials or attempt
examples. Documentation UIs remain host policy.

`TaskqRuntime`—never the sub-application—is the sole Taskq lifecycle/resource owner. The facade
runtime owns one reconnectable PostgreSQL LISTEN connection per process for long-poll
hints, not one per request. A generation-based `ClaimWaitHub` coalesces notifications and wakes
subscribers; payload content is never authoritative. Each claim request performs the normative race
closure in this order:

1. capture the current hub generation, then run one short authoritative claim transaction;
2. return immediately for claimed/paused/unknown/target-unavailable;
3. if empty and time remains, subscribe against the captured generation after releasing every SQL
   connection; a changed generation arms the subscription immediately;
4. run an immediate second authoritative claim after subscription, before waiting;
5. if still empty, await only until the next notification or bounded monotonic poll deadline, close
   the subscription, and repeat from generation capture; and
6. return typed timeout at the caller deadline.

The mandatory poll interval bounds missed-notification and future-due latency. No database
transaction or pooled connection spans the wait. Subscribe-after-check races are closed by the
generation token plus an immediate recheck. Disconnect cancels only the waiter; if a claim may have
committed, the facade/client never guesses the fence and lease recovery is authoritative. Runtime
shutdown rejects new waits, wakes/drains every subscriber, then closes the listener and pools.

Listener loss marks runtime unready and degraded while authoritative polling remains correct.
Reconnect performs an immediate catch-up sweep. Listener callbacks never execute SQL or user code.
Disconnect/cancellation closes only that subscription; shutdown rejects new subscriptions, wakes all
current waiters, and leaves listener/pool ownership to `TaskqRuntime`.

## 9. TaskqRuntime and lifespan composition

### 9.1 Public lifecycle

`TaskqRuntime` is an idempotent async context manager with states `constructed → starting → running
| degraded → stopping → stopped | failed`. It owns only resources it creates. Supplied `TaskQ`,
transports, HTTPX clients, SQLAlchemy engines, and host lifespans are borrowed unless an explicit
ownership flag/factory says otherwise. Construction starts nothing.

The stable integration is:

```python
runtime = TaskqRuntime.from_dsn(..., options=TaskqRuntimeOptions(...))
taskq_app = create_taskq_app(runtime, authorizer=authorizer)

app = FastAPI(lifespan=compose_lifespans(host_lifespan, runtime))
app.mount("/taskq", taskq_app)
```

Composition order is host startup → taskq startup → serve → taskq shutdown → host shutdown, so
taskq may use host-provided dependencies and stops before they disappear. If either startup fails,
already-entered contexts unwind exactly once. `taskq_lifespan(runtime)` supplies the no-host
one-liner but never replaces an existing lifespan silently.

During its context, runtime installs the public producer `TaskQ` at `app.state.taskq`, preserving
and restoring any previous value exactly. `get_taskq_client(request)` returns that value and raises
a typed configuration error outside an active lifespan. Tests override this dependency through
normal FastAPI dependency overrides; no private state access is required.

### 9.2 Housekeeper

When enabled, one monotonic loop calls `tick` approximately every five seconds with bounded jitter
through the housekeeper-only transport. Calls are short and never share request transactions. The
SQL advisory lock makes multiple processes safe, but each process's connection budget is still
reported honestly. Tick errors degrade readiness, back off within a bound, and recover visibly;
non-retryable contract/version errors fail startup or the runtime. The 0.1 due-gated janitor remains
inside SQL tick. HTTP workers never run this loop.

### 9.3 Readiness and shutdown

Ready means compatible schema, started required pools, healthy required listener, healthy
housekeeper, and—when embedded—healthy worker presence/service. Backlog never makes readiness fail.
Snapshots are monotonic-age-based and fence/secret-free.

Shutdown ordering is fixed:

1. mark unready, reject new claims/long polls, and wake waiters;
2. stop embedded intake and await the Stage-2 supervisor soft stop;
3. stop/join housekeeper and presence loops;
4. drain the long-poll hub and close its listener;
5. close owned operator and ordinary pools/clients; and
6. restore app state and report stopped.

External cancellation shields cleanup then re-raises. Concurrent stop callers share one operation;
escalation is monotonic. A live synchronous handler preserves Stage-2's process-exit requirement and
is never released while its thread may still produce side effects. ASGI graceful timeout must exceed
`soft_stop_timeout`; an inverted known value warns at startup.

## 10. Embedded worker and process budgets

Embedded execution is disabled by default. Enabling it requires `EmbeddedWorkerOptions` with
non-empty distinct queues and an explicit `acknowledge_process_multiplication=True`. It composes the
existing `WorkerService` and `WorkerSupervisor` unchanged, uses a separate runner pool plus dedicated
listener connection, registers ordinary presence, and defaults to concurrency 1. Registered sync
handlers use the existing bounded executor; there is no new `blocking=True` semantics.

`TaskqRuntimeOptions` records at least:

- request, operator, housekeeper, and embedded-worker pool maxima;
- embedded concurrency and sync-worker count;
- expected ASGI process count;
- optional database connection ceiling/reserve;
- housekeeper/poll/presence intervals and soft-stop timeout; and
- expected environment plus explicit production acknowledgement.

Startup logs process-local and deployment-wide arithmetic, not merely configured pool numbers:

```text
total_handler_capacity = expected_asgi_processes × embedded_concurrency
total_pool_capacity = expected_asgi_processes × sum(process_pool_maxima)
total_listener_connections = expected_asgi_processes × enabled_dedicated_listeners
```

Validation refuses an estimate above the configured database ceiling after reserve. If process
count or ceiling is unknown, startup emits a structured warning and the snapshot marks the estimate
unknown; it never claims safety. Dedicated workers remain recommended for heavy, blocking,
multi-process, or autoscaled lanes. Embedded graduation changes deployment only, not task or handler
definitions.

## 11. OutLabs authorization integration

### 11.1 Adapter

`taskq.http.outlabs.OutlabsQueueAuthorizer` implements `QueueAuthorizer` against the supported real
OutLabs authorization API. For `(action, queue)` it requires an explicit any-of over:

```text
taskq_{queue}:{action}
taskq:{action}
```

Global routes check only `taskq:{action}`. Checker construction is lazy and cached by immutable
candidate tuple; concurrent first use converges to one cache entry. Every generated permission name
passes the dependency's real validator. There is no local imitation regex, configurable namespace,
implicit auth initialization, or Redis assumption.

The adapter accepts an explicit auth object/session dependency and optional legacy candidates for a
strangler period. Principal-to-actor conversion is deterministic and bounded. Authorization errors
normalize to AUTH envelopes without exposing permission catalogs, token claims, session data, or
checker internals. Service tokens are the default fleet credential; system integration keys require
the documented allowed-action-prefix configuration for enqueue/admin. Personal-key `run` exclusion
is package-enforced only under EnterpriseRBAC. Under SimpleRBAC it is a host role/grant invariant:
human roles must not receive `taskq:run` or queue-scoped `run`, and worker credentials should be
service tokens.

### 11.2 Catalog and provisioning

`taskq_permission_catalog(queues)` is pure and deterministic: five global permissions plus five per
distinct canonical queue, sorted and validated through the real OutLabs validator. IAM roles remain
separate from PostgreSQL capability roles.

`provision_taskq_auth(...)` is explicit host bootstrap, never import/mount/lifespan behavior. It
supports:

- `mode="report"` by default: typed created/existing/changed/conflicting diff, no mutation;
- `mode="apply"`: idempotent permission and optional standard-role creation;
- `reconcile=False` by default: manually changed roles are conflicts, not silently overwritten;
- explicit `reconcile=True` for authorized convergence; and
- optional per-queue worker roles.

Standard roles and grants remain exactly those in the Authorization doc: the admin role enumerates
the five global permissions and standard roles use `is_system_role=False` so the public role service
can reconcile them. Permission seeding uses the public system-record API; role changes use the
public role service. Caller transaction/session
ownership is preserved. Failures roll back the provisioning transaction and return no partial
success claim.

The optional `taskq auth sync-permissions` CLI lazily imports the adapter, calls the same service,
defaults to report mode, prints a secret-free deterministic diff, and prints required host API-key
policy changes it cannot make. `ensure_queue(..., provision_auth=True)` is an explicit composition
that runs queue ensure and IAM provisioning under documented non-atomic cross-system semantics; it
reports each side separately and is never implied by ordinary ensure.

## 12. Configuration, diagnostics, and safety

HTTP/runtime settings use frozen `pydantic-settings` models with `extra="forbid"`, explicit
environment prefixes, no implicit `.env`, deterministic CLI > environment > defaults precedence,
secret types, and existing production interlocks. Configuration representations and validation
errors redact DSNs, tokens, keys, authorization headers, and custom secret headers.

Structured events use stable names and bounded scalar fields for request start/finish, command,
outcome/code, duration, queue when authorized, actor identity, listener/housekeeper/runtime state,
pool budget, and retries. They never include payload, headers, progress, result, error text, SQL,
credential, raw principal, attempt id, or arbitrary presence metadata. Metrics use the SQL metrics
contract and bounded facade counters; request id and actor are never metric labels.
Public error diagnostics admit at most 16 scalar entries, with keys capped at 64 characters and
string values at 256 characters; sensitive field names are dropped before serialization.

Rate/concurrency limits at the facade are deployment policy, not queue correctness. Proxy/body
limits must be at least as strict as H-09. CORS, TLS, network exposure, and host authentication
configuration remain host-owned and are documented without insecure defaults.

## 13. Deterministic tests and acceptance matrix

Tests reuse Stage-2 manual clocks, scripted failures, barrier choreography, and resource ledgers.
HTTP tests use a real ASGI transport for model/exception behavior and real network/subprocess cases
for disconnect, timeout, sync client, signal, and socket ownership. Mock-only auth or SQL tests are
not accepted as parity evidence.

### S3-01 — protocol metadata and clients

- capability protocols preserve existing SQL/core behavior and reject unsupported capability use;
- independently hand-derived route/model/status/error/retry catalog matches Protocol document
  revision 1.0.4 exactly without importing generated expected values;
- generated OpenAPI, async client, sync client, and parity vectors share one source;
- direction-aware extras, every H-09 bound, Protocol/Request-Id headers, envelopes, and native-error
  normalization are exact;
- absent/valid/oversize/non-ASCII request IDs prove mint/echo behavior, non-reflection, and
  authenticate-before-`TQ422`; bad credentials plus an invalid ID return auth failure with a minted ID;
- single enqueue exposes exact outcome plus `data.job_id` only for both dispositions, with no
  `created_at` or request echoes;
- claim is the sole fence output; OpenAPI/examples/repr/log/error/metrics remain fence-free elsewhere;
- the generated per-command retry classification matches an independently Tier-0-derived Protocol
  §5 oracle; borrowed/owned client cleanup, cancellation, connection failure, and compatibility
  negotiation are deterministic; and
- worker list produces typed `TQ501`; queue detail/list jobs have no generated method or OpenAPI
  operation; and no inactive field is accepted.

### S3-02 — facade, authorization, and long poll

- every active generic route maps 1:1 to the SQL reference outcome and error registry;
- static, bearer, callable, legacy, and explicit test authorizers cover 401/403/global/queue cases;
- authoritative job lookup rejects lied queue/type and never sends a fence before authorization;
- worker presence preflights every distinct queue, preserves subject/label separation, and proves
  both 200 outcomes without extending a lease;
- ordinary and operator pools execute only their permitted commands; missing operator config exposes
  no operator route (including queue ensure) and no fallback;
- invalid settlement input, unknown path, wrong method, and raising authorizer all return envelopes
  with no fence/native body; queue detail and list jobs return hidden typed `TQ501` responses;
- HTTP timeout and SQL empty normalize identically to `ClaimState.EMPTY`; single-queue long poll and
  multi-queue immediate sweeps meet claim/stop latency bounds without changing SQL-mode behavior;
- long poll proves subscribe/check, notification-before/after-wait, missed hint, future due, timeout,
  disconnect-before/after-commit, listener reconnect, and shutdown-drain winner orders; and
- no transaction/connection spans a wait; every waiter/listener/task returns to baseline.

### S3-03 — runtime, housekeeper, embedded worker, and worker CLI

- host/taskq lifespan ordering, both startup-failure directions, cancellation, concurrent stop, app
  state restoration, and dependency override behavior are exact;
- five-second jittered housekeeper, transient recovery, fatal version skew, advisory-lock duplicate
  safety, and no public tick route pass against live SQL;
- embedded worker is default-off, acknowledgement-gated, pool/listener-split, presence-identical, and
  uses the existing cancellation/settlement kernel without a special path;
- single/multi-process budget arithmetic, ceiling refusal, unknown-budget warnings, readiness, ASGI
  grace warning, and unsafe-sync process-exit evidence are permanent;
- HTTP worker long poll, remote drain, response loss, signal/cancellation, sync handler, and CLI
  subprocess cleanup preserve Stage-2 invariants; and
- task, exception, thread, pool, HTTP client, listener, subscriber, and app-state ledgers return to
  baseline under repeated lifecycle races.

### S3-04 — OutLabs adapter and provisioning

- real supported-package validator and permission dependency prove per-queue, global fallback,
  explicit any-of, global route, denial, cache concurrency, actor mapping, and legacy candidates;
- the required matrix includes an `emails` run token denied on `tools`, lied-queue settlement, shared
  fleet label, service-token embedded scopes, system-key policy guidance, Enterprise-fixture
  personal-worker denial, and a SimpleRBAC vector proving the documented grant-policy residual;
- catalog counts/order/validation, report/apply/reconcile, second-run idempotency, conflict handling,
  role grants, rollback, and caller session ownership are exact, including first apply on empty IAM
  and convergence of a drifted non-system standard role through the public service;
- CLI and ensure/provision composition are secret-free and honest about cross-system partial failure;
  and
- core and HTTP imports remain independent of the OutLabs extra and of any external backing service.

### S3-AUDIT — permanent completion evidence

- T6 runs the same contract-derived scenario vectors through SQL and a live FastAPI facade and
  compares normalized outcomes, errors, retryability, actor/queue decisions, and durable state;
- the full auth, request-size, malformed-input, fence-redaction, long-poll race, lifespan, process
  budget, cancellation, and resource-leak matrices repeat without correctness sleeps;
- PostgreSQL 16.14 and 18.3 run the identical full suite; Python 3.12/3.13 run source isolation;
- wheel and sdist each install/smoke core, HTTP, and OutLabs combinations outside the checkout;
- B14 measures the actual generated client → ASGI → SQL path and query plans; B11 measures embedded
  request-latency/resource overhead. Both are report-only until a reviewed baseline exists, and
  environmental noise is never claimed as a win;
- CI collects the million-row plan gate, all Stage-3 races, artifact matrix, and protocol generation
  drift; and
- exact diff proves no SQL contract/migration, Tier-4, Stage-4 host, or 0.2/0.3 implementation landed.

## 14. Explicit non-goals

- Per-queue PostgreSQL roles, RLS, or multi-tenant isolation
- General job listing, queue-detail GET, active worker-list success, timelines, SSE, or dashboard
- Public HTTP tick/janitor, bulk redrive not defined by Protocol, or raw SQL/view passthrough
- Workflow, dependency, followup, schedule, archive, or future uniqueness capabilities
- Automatic app construction, lifespan replacement, IAM seeding, queue creation, or auth startup
- Multiprocessing/fork management inside the runtime
- A second worker state machine, settlement path, fake SQL kernel, or broker abstraction
- Stage-4 host route migrations or credential rollout

## 15. Exit gate

S3-00 is complete when this Tier-3 specification and the immutable round-5 request are registered,
the full existing suite and Ruff remain green, the board lists S3-01..04/AUDIT behind round-5
acceptance, and no Stage-3 implementation exists. Stage-3 coding opens only after the external
response is recorded and every blocking finding or Contract question is adjudicated docs-first.
