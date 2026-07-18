# FastAPI and OutLabsAuth integration design

This design is based on the checked-out OutLabsAuth `0.1.0a24` implementation and FastAPI's current lifespan/dependency behavior. Its goal is to make secure queue setup simple without making OutLabsAuth a core dependency or moving queue correctness into the web layer.

## Packaging and import boundaries

```toml
[project.optional-dependencies]
http = ["fastapi>=0.139", "httpx>=0.28"]
outlabs = ["outlabs-taskq[http]", "outlabs-auth>=0.1.0a24,<0.2"]
```

The exact minimums should be resolved from the supported application matrix before release. The invariants are:

- `import taskq` does not import FastAPI or OutLabsAuth;
- `taskq.fastapi` owns HTTP models, router/runtime helpers, and HTTP transport;
- `taskq.outlabs` owns permission catalogs, authorization adapters, and provisioning helpers;
- importing an integration without its extra raises a targeted install message;
- the SQL kernel has no knowledge of JWTs, FastAPI requests, users, roles, or entities.

`outlabs-taskq` is the distribution name; `taskq` is the import namespace.

## FastAPI building blocks

Expose small pieces that compose instead of one function that owns the whole application:

```python
from taskq.fastapi import (
    TaskqRuntime,
    create_taskq_router,
    get_taskq_client,
)
```

### `TaskqRuntime`

`TaskqRuntime` is an async context manager responsible for resources TaskQ owns:

- direct transport/pool startup and shutdown;
- optional embedded worker supervisor;
- notification listener and polling loop;
- bounded sync-handler executor;
- worker presence and graceful drain;
- readiness state.

It does not construct the FastAPI app or overwrite the host lifespan. A convenience wrapper may compose a supplied lifespan explicitly:

```python
from taskq.fastapi import compose_lifespans

app = FastAPI(
    lifespan=compose_lifespans(
        existing_lifespan,
        runtime.lifespan,
    )
)
```

Composition ordering and failure cleanup must be tested. FastAPI notes that setting `lifespan` disables legacy startup/shutdown event handlers and that lifespan applies to the main application, not mounted sub-applications; TaskQ should surface those facts in its own guide. See [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/) and [testing lifespan events](https://fastapi.tiangolo.com/advanced/testing-events/).

### Router factory

```python
router = create_taskq_router(
    transport=transport,
    authenticate=auth.current_principal,
    authorizer=TaskqAuthorizer(auth),
    prefix="/taskq/v1",
    operations={"producer", "worker", "observer"},
)
app.include_router(router)
```

The router accepts protocols, not an OutLabsAuth singleton hidden in global state. A host may supply a different authenticator/authorizer with the same contract. Operator/admin routes should be opt-in and can be mounted separately from worker routes.

The factory must produce ordinary FastAPI routes with Pydantic request/response models, meaningful OpenAPI operation IDs, and standard dependency overrides for tests. Class-based dependencies are a suitable fit; see [FastAPI classes as dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/classes-as-dependencies/).

### App access

The runtime stores only its instance on `app.state.taskq`. Request handlers use a documented dependency:

```python
client: Annotated[TaskqClient, Depends(get_taskq_client)]
```

Application code should not reach into private `app.state` fields. Tests can override the dependency with an in-memory protocol model or a database-backed fixture.

### BackgroundTasks boundary

TaskQ should document a clean choice:

- use FastAPI `BackgroundTasks` for small, in-process work that may be lost with the process and does not need cross-node coordination;
- use TaskQ for durable, retryable, scheduled, observable, or resource-controlled work.

This matches FastAPI's own [caveat for heavier background computation](https://fastapi.tiangolo.com/tutorial/background-tasks/).

## Embedded worker safety

Embedding is a convenience, not an invisible default. Every ASGI process starts its own runtime:

```text
effective handlers = asgi_processes × taskq_concurrency
effective pool max  = asgi_processes × per_process_pool_max
```

The runtime should:

- default `embedded_worker=False` when only a router is requested;
- emit the process ID, queues, local concurrency, and pool budget at startup;
- require an explicit `embedded_worker=True` acknowledgement;
- expose a deployment-level expected process count so configuration validation can estimate total connections;
- become unready if it cannot verify the TaskQ schema, start required listeners, or maintain its worker presence;
- stop claiming, drain for a configured grace period, then release unfinished claims on shutdown where safe;
- recommend a dedicated `taskq worker` process for autoscaled or multi-process production deployments.

Connection budgeting should include application SQLAlchemy pools, TaskQ command pools, notification connections, worker concurrency, migration/maintenance connections, replicas, and deployment surge capacity.

## Authorization model

TaskQ has two complementary authorization layers:

1. **Database capability roles** limit which SQL functions a database credential may execute.
2. **OutLabsAuth queue permissions** limit which authenticated principal may act on which queue through HTTP.

Per-queue authorization is intentionally not encoded as one PostgreSQL role per queue. The authenticated facade applies that policy using authoritative TaskQ metadata.

## Permission grammar compatible with `0.1.0a24`

OutLabsAuth permission names use `resource:action`. Both components allow lowercase letters, digits, underscores, and hyphens. Dots are invalid, so `taskq.email:run` must not be used.

Generate permissions as follows:

```text
taskq:{action}                 # global shortcut
taskq_{queue}:{action}         # per queue
```

Actions:

| Action | Commands |
|---|---|
| `enqueue` | Enqueue a job in the queue |
| `run` | Claim, heartbeat, complete, fail, release, snooze, handler-cancel |
| `read` | Read/list queue jobs, attempts, and safe diagnostics |
| `control` | Pause/resume, operator cancel, retry/redrive, drain controls |
| `admin` | Queue definition/policy, destructive retention, privileged maintenance |

Authorization succeeds when the principal has either the specific queue permission or its global counterpart:

```text
taskq_email:run OR taskq:run OR taskq_email:* OR *:*
```

This is an any-of check. OutLabsAuth's permission dependency defaults to any-of unless `require_all=True`; TaskQ should pass that intent explicitly in its adapter rather than rely on a default.

Queue names and permission resources must use one canonicalization function. Prefer rejecting an invalid queue name over transforming it into a permission that can collide with another name.

## Permission catalog and roles

`taskq.outlabs.permission_catalog(queues)` should return OutLabsAuth `PermissionSeed` records for the five global permissions plus the requested per-queue permissions. It can be passed to `seed_system_records(..., permission_catalog=..., include_config=False)` using the host's `permission_service`.

The current OutLabsAuth bootstrap helper seeds permissions, not roles. TaskQ should provide an idempotent role helper built on the public `RoleService` instead of claiming roles are created by `seed_system_records`.

Recommended templates:

| Role template | Permissions |
|---|---|
| `taskq-producer-{queue}` | `taskq_{queue}:enqueue`, optionally `taskq_{queue}:read` |
| `taskq-worker-{queue}` | `taskq_{queue}:run`, `taskq_{queue}:read` |
| `taskq-operator-{queue}` | `taskq_{queue}:read`, `taskq_{queue}:control` |
| `taskq-platform-admin` | `taskq:admin`, `taskq:control`, `taskq:read` |

The helper should return a typed report of created, existing, changed, and conflicting definitions. It must not silently add permissions to a manually modified role unless the caller selects reconciliation mode.

Conceptual bootstrap:

```python
from outlabs_auth import seed_system_records
from taskq.outlabs import permission_catalog, reconcile_roles

catalog = permission_catalog(queues=["email", "exports"])

await seed_system_records(
    session,
    permission_service=auth.permission_service,
    include_permissions=True,
    include_config=False,
    permission_catalog=catalog,
)

report = await reconcile_roles(
    session,
    role_service=auth.role_service,
    queues=["email", "exports"],
    mode="report",  # use "apply" only in explicit bootstrap/migration work
)
```

This code is a target API for TaskQ, not a claim that `reconcile_roles` exists in OutLabsAuth today.

## Service credentials

### SimpleRBAC

Use an OutLabsAuth service token for workers and service-to-service producers. Service tokens embed their permission list and validate without a database permission lookup. Issue narrow tokens:

```python
token = auth.service_token_service.create_service_token(
    service_id="email-worker-prod",
    service_name="Email worker",
    permissions=["taskq_email:run", "taskq_email:read"],
    expires_days=30,
    metadata={"environment": "prod"},
)
```

TaskQ does not store or mint tokens automatically at application startup. Provisioning tooling may print a token once or write it to an explicitly selected secret sink. Rotation and expiry should be visible in deployment runbooks.

### EnterpriseRBAC

Enterprise deployments may use system-integration API keys where OutLabsAuth enables them. In `0.1.0a24`, the default allowed action prefixes already include `read`, `run`, and `control`; they do not include `enqueue` or `admin`. A deployment that wants those actions on system-integration keys must explicitly extend the OutLabsAuth policy. Service tokens remain a valid and simpler worker credential.

Personal API keys should not be used for autonomous workers.

## Authoritative route authorization

Authorization depends on the command:

### Queue-addressed commands

For enqueue, claim, list, and queue controls, the queue is in the canonical path and can be authorized before the SQL call:

```text
POST /queues/email/jobs       -> taskq_email:enqueue or taskq:enqueue
POST /queues/email/claims     -> taskq_email:run or taskq:run
GET  /queues/email/jobs       -> taskq_email:read or taskq:read
```

The SQL function still validates that the queue exists and that the database credential has the relevant function capability.

### Job-addressed commands

For job-ID routes, the client must not choose the queue used for authorization:

```text
authenticate
    -> get_authorization_projection(job_id)
    -> authorize(projection.queue, action)
    -> invoke fenced mutation
```

The projection should reveal only what authorization needs: job ID, queue, task name/lane if lane policy remains, and current status. It should be a `SECURITY DEFINER` function granted to the facade role, not table access.

If the payload includes queue or task name for debugging/compatibility, it is an assertion. Reject a mismatch. Never use it to grant access.

### Bulk commands

Resolve all target queues and authorize the complete set before mutation. Partial authorization must not produce a partially applied bulk operation unless the protocol explicitly defines per-item results and the caller opts into that behavior.

## Worker lanes

Queue permission is the primary authorization boundary. If existing systems need task-type allowlists within a queue, implement them as a second policy after queue authorization:

```python
authorize_queue(principal, queue="billing", action="run")
authorize_lane(principal, queue="billing", task_name="invoice.generate")
```

The task name must also come from the claimed/job row. Lane policy should be a host-supplied pure function or declarative map, not hard-coded into TaskQ's SQL kernel. Prefer separate queues when lanes have materially different trust, concurrency, retry, or retention needs.

## Actor attribution

Every control mutation should accept an actor projection generated by the authenticated facade:

```json
{
  "kind": "service",
  "subject": "email-worker-prod",
  "request_id": "...",
  "auth_method": "service_token"
}
```

The SQL event stores safe actor fields, command, queue/job IDs, and reason. It must not store raw JWTs, API keys, attempt fences, or arbitrary unbounded claims. Direct SQL control commands use the database role/session identity plus an optional bounded application actor label.

## Health and readiness

The router should expose separately controllable endpoints:

| Endpoint | Meaning |
|---|---|
| `/taskq/health` | Process is alive; no database requirement |
| `/taskq/ready` | Schema compatible, database reachable, required queues registered, runtime started |
| `/taskq/v1/diagnostics` | Authenticated read-only operational summary; not public health data |

Readiness should not fail merely because the backlog is nonzero. It should report degraded diagnostics for excessive oldest age, stalled leases, notification failure, or connection exhaustion while preserving a stable machine-readable reason list.

## Integration acceptance tests

- `import taskq` succeeds with neither FastAPI nor OutLabsAuth installed.
- Lifespan startup/shutdown executes exactly once per process and composes with a host lifespan.
- Sync handlers never run on the event-loop thread.
- Four ASGI processes produce the documented fourfold local concurrency/connection budget.
- Every generated permission passes OutLabsAuth's actual validator.
- Specific, global, resource-wildcard, and global-wildcard permissions behave as documented.
- A worker scoped to `email` cannot claim or settle `exports`.
- A caller cannot settle an `exports` job by claiming it belongs to `email`.
- Service-token permissions authorize without a permission database read.
- Enterprise system-key tests cover the configured action-prefix policy.
- Attempt IDs/fences never appear in read/list/OpenAPI examples/logs.
- SQL and HTTP transports return the same typed outcomes for the same scenario.
