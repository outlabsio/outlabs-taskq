# taskq — Library Extraction Design Brief

> **Status:** Design brief — 2026-07-18; **ADR fold-in applied same day** ([ADR-001..010](./adr/README.md) accepted — where this brief and an ADR disagree, the ADR wins)
> **Companion:** [`Task Queue — Unified Design Spec.md`](./Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md) (canonical protocol)
> **Borrowed product features (normative):** [`taskq-borrowed-features/`](./taskq-borrowed-features/README.md)
> **Peer provenance:** [`Task Queue Peer Patterns Research.md`](./Task%20Queue%20Peer%20Patterns%20Research.md)
> **Audience:** Extract `taskq` from Diverse + QDarte into one publishable Python package, modeled on how `outlabs-auth` is already shared — without making `outlabs-auth` a hard dependency of the queue engine.

---

## 0. Verdict

Extract `taskq` into its own package. The protocol is already designed as SQL-first, multi-consumer, and “published like outlabs-auth.” Both production systems already prove the same transport shape (HTTP workers, no worker DB credentials, host-app auth in front of the facade). The extraction’s job is to make **one installer + one client + one contract** the source of truth, while treating **HTTP auth as a host-injected adapter** — first-class with outlabs-auth when the host uses it, fully usable without it.

Correctness stays in Postgres (`taskq.*` functions + the capability roles, ADR-010/011). Identity stays in the host app (or nowhere, for CLI/psql). Those layers must never be fused in the package’s import graph.

---

## 1. Why this extraction exists

### 1.1 Provenance (both projects)

The unified design was produced by a cross-repo audit:

> Produced 2026-07-06 by a cross-repo audit of the Postgres task queues in `diverse-data-api` (`scrape_jobs` queue domain) and `qdarteAPI` (`qdarte_ops.worker_jobs` platform). … **Replaces:** the `scrape_jobs` queue in diverse-data-api AND the `qdarte_ops.worker_jobs` platform in qdarteAPI.
>
> — *Task Queue — Unified Design Spec*, header

The audit’s operating summary (vault / gap analysis lineage):

> **qdarte has the better engine kernel; Diverse has the better operational shell.**

`taskq` is the reconciliation of those strengths — not a greenfield queue invented for packaging aesthetics.

### 1.2 The packaging intent is already normative

The unified spec already names the deliverable:

> One package (`taskq`, published like outlabs-auth), consumed by qdarteAPI, qdarte-workers, diverse-data-api, diverse-data-workers. It contains the SQL installer/migrations, the asyncio client, the worker runtime, the **only** pydantic contract models (no hand-mirroring — DCP 7.14 dead), the FastAPI facade router, and the CLI.
>
> — *Unified Design Spec* §14

This brief turns that paragraph into packaging, dependency, auth, and migration rules precise enough to implement without re-litigating the protocol.

### 1.3 What is broken today without a shared package

| Symptom | Diverse evidence | QDarte evidence |
|---|---|---|
| Hand-mirrored transport models | `diverse-data-api/.../queue/contracts.py` vs `diverse-data-workers/.../transport/queue.py` | `qdarte-runtime/.../worker_api/models.py` shared more carefully, but still a second contract surface beside SQLModel tables |
| Queue logic owned by app services | `domains/queue/taskq_service.py` (SQL facade) + Alembic installer in-repo | `WorkerJobService` (~6.5k lines) owns claim/settle/reclaim in Python |
| Auth fused at route import time | `taskq_api.py` imports `require_job_read` / `require_job_write` from `iam.auth` | Worker routes gated by `require_worker_route_access` → `WORKER_RUN` / `JOB_CONTROL` |
| Dual-write / strangler residue | taskq facade still bridges into legacy `scrape_jobs` | Pre-taskq `qdarte_ops.worker_jobs` still production |

DCP 7.14 (hand-mirrored models) is the packaging bug class. Fencing/budget bugs are the protocol bug class — already solved in the SQL contract. The library must kill the first without reopening the second.

---

## 2. Current state of each consumer

### 2.1 Diverse Data Platform

| Layer | Location | Role today |
|---|---|---|
| Protocol / SQL | `alembic/versions/20260709_e1a2b3c4d5f6_add_taskq_schema.py` (+ follow-ons) | Installs schema `taskq`, functions, roles |
| Async SQL client | `src/diverse_data_api/domains/queue/taskq_service.py` | Calls `taskq.*` via SQLAlchemy async |
| HTTP facade | `src/diverse_data_api/domains/queue/taskq_api.py` | Mounted at `/api/v1/taskq` |
| Contracts | `src/diverse_data_api/domains/queue/contracts.py` | Pydantic request/response models |
| Auth | `src/diverse_data_api/iam/auth.py` | `outlabs-auth` SimpleRBAC; `job:read` / `job:write` |
| HTTP workers | `diverse-data-workers` | No DB DSN; `DiverseQueueClient` with `X-API-Key` or Bearer |
| Cutover gates | `DIVERSE_TASKQ_*` settings | Env match + queue/job-type allowlists |

Diverse facade auth is hard-wired today:

    from diverse_data_api.iam.auth import require_job_read, require_job_write
    JobReadAuth = Annotated[dict[str, Any], Depends(require_job_read)]
    JobWriteAuth = Annotated[dict[str, Any], Depends(require_job_write)]

Worker HTTP credentials (no outlabs-auth dependency in the workers package):

    # diverse-data-workers/.../contracts/clients/queue.py
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if api_key:
        headers["X-API-Key"] = api_key

Topology quote (normative for Diverse):

> **HTTP-facade deployments (the no-DB-credential Diverse fleet):** HTTP workers *cannot* tick — they have no DB connection… **The API process hosting the facade (diverse-data-api) runs the housekeeper coroutine.**
>
> — *Unified Design Spec* §11.4

### 2.2 QDarte Platform

| Layer | Location | Role today |
|---|---|---|
| Legacy engine | `qdarteAPI/app/domains/workers/services/jobs.py` | Python state machine over `qdarte_ops.worker_jobs` |
| HTTP worker API | `qdarteAPI/app/domains/workers/api/routes.py` | `/worker/jobs/*`, `/ops/jobs/*` |
| Shared contracts/client | `qdarte-runtime/.../worker_api/` | HTTP client + pydantic models |
| Workers | `qdarte-workers` | Subclass of shared client; Bearer service token |
| Auth | `qdarteAPI/app/auth.py` | `outlabs-auth` SimpleRBAC; `qdarte:worker-run`, job/process control |

QDarte auth wiring:

    auth = SimpleRBAC(
        engine=engine,
        auto_migrate=False,
        database_schema=settings.OUTLABS_AUTH_SCHEMA,
        secret_key=settings.OUTLABS_AUTH_SECRET_KEY,
        ...
    )

Worker route dispatch:

    # qdarteAPI/app/auth.py — require_worker_route_access
    if path.startswith("/ops/jobs"):
        return await require_job_control(...)
    if path.startswith("/worker/"):
        return await require_worker_run(...)

Worker config is explicit about OutlabsAuth tokens:

> Worker runtime settings backed by a managed OutlabsAuth service token.

Despite the unified-spec wording “DB-connected workers (qdarte)” for housekeeper topology, **today’s qdarte-workers are HTTP-only** — same shape as Diverse. The library must support:

1. **HTTP-facade topology** (both fleets today): API holds DB + ticker; workers are HTTP clients.
2. **DB-direct topology** (allowed by the protocol, useful for CLI/ops and future in-process workers): process holds capability-role credentials (runner+observer, ADR-011) and calls SQL functions directly.

Do not bake “QDarte = DB-direct” into the package API; bake “transport is chosen by the host.”

### 2.3 outlabsAPI — third consumer (planned, smallest host)

outlabsAPI (personal automation API, Coolify + Neon) is today the **anti-pattern the stack decision retired**: tool runs go `POST /tools/{tool}/runs/queued` → FastAPI `BackgroundTasks` → RabbitMQ (aio-pika/pika) → in-process consumers, with **no durable run row, no status surface, no retry budget**; email/newsletter/analytics/WhatsApp lanes ride the same broker (plus the separate `outlabs-workers` processes). taskq replaces all of it with the borrowed-feature-14 embedded topology: enqueue in-route (durable, typed, idempotent), embedded worker + housekeeper in the same container, `GET /taskq/jobs/{id}` as the run-status API, RabbitMQ retired lane by lane. This makes outlabsAPI the proving ground for the **minimal-footprint path** (one process, one Postgres, `static_api_key_auth` or outlabs adapter) exactly as Diverse/QDarte prove the fleet path — and it is the extraction's guarantee that the package stays small enough for a host with no fleet at all. Sequencing: after Phase C (worker runtime), before/parallel to Diverse production lanes; zero blast-radius coupling to the other two hosts. Caveats to carry: Neon pooler LISTEN + autosuspend-vs-tick behavior is Unified Spec §20.3 — outlabsAPI is where it gets answered; its `outlabs-auth` pin (0.1.0a20) trails the adapter target (≥0.1.0a24), so the auth extra waits for its normal auth upgrade cycle (`outlabs-auth-rollout` skill).

### 2.4 Defects the shared package must not reintroduce

From the unified problem statement (both systems):

> - **Dedup by SELECT-then-INSERT** … duplicate active jobs under concurrent producer load
> - **Lost settle responses are indistinguishable from theft**
> - **No ownership fencing** … two live executions
> - **Lease expiry with zero backoff** (qdarte) / compose drains consuming retry budget (Diverse)
> - **Hand-mirrored transport models** (DCP 7.14)
>
> — *Unified Design Spec* §0 “Current shortcomings”

And from qdarte’s claim path specifically (pool convoy — to be replaced by enqueue-time `concurrency_key` + try-lock admission):

    # qdarteAPI WorkerJobService.claim_next_job (legacy)
    pool_stmt = (
        select(WorkerConcurrencyPool)
        .where(WorkerConcurrencyPool.name == payload.concurrency_pool)
        .with_for_update()
    )

The package’s claim path must call `taskq.claim_jobs(...)` — never reimplement admission in Python.

---

## 3. Goals and non-goals

### 3.1 Goals

1. **One publishable package** (`taskq`) that both platforms depend on for schema install, typed contracts, asyncio SQL client, worker runtime, optional FastAPI facade, and CLI.
2. **SQL remains the contract.** Python is a client. psql remains a first-class client.
3. **Works excellently with outlabs-auth** when the host already uses SimpleRBAC (Diverse + QDarte today).
4. **Does not require outlabs-auth** to install, import core modules, run workers against SQL, run the CLI, or mount the HTTP facade with a custom auth dependency.
5. **Kill DCP 7.14** — workers import models from `taskq`, never re-declare them.
6. **Preserve strangler cutover** — package must coexist with legacy `scrape_jobs` / `worker_jobs` until lanes drain; no big-bang requirement.
7. **Runnable by a two-person team** — private package publishing is enough; no multi-tenant SaaS ambitions.

### 3.2 Non-goals

- Replacing outlabs-auth, or becoming an IAM product.
- Multi-tenant fair scheduling / multi-tenant-orchestrator-class scale.
- Shipping Diverse court domains or QDarte launch-pipeline orchestration inside `taskq`.
- Requiring workers to hold database credentials.
- Mandating per-worker API keys (shared fleet key remains acceptable single-tenant — already documented).

---

## 4. Package architecture

### 4.1 Recommended distribution layout

    taskq/
      pyproject.toml
      README.md
      src/taskq/
        __init__.py              # TaskQ, Result, Retry, public surface
        types.py                 # enums, PRIORITY bands, settle result literals
        models.py                # ONLY pydantic contracts (enqueue/claim/settle/…)
        sql/
          installer.py           # apply/verify schema; capability detection
          migrations/            # versioned SQL (or alembic scripts owned by taskq)
        client.py                # asyncio SQL client over taskq.* functions
        worker.py                # claim loop, heartbeat, settle retries, housekeeper
        http/
          router.py              # FastAPI APIRouter factory (auth-injected)
          deps.py                # AuthContext protocol + NullAuth / dependency adapters
          outlabs.py             # OPTIONAL adapter — only imported when extra installed
        cli.py                   # taskq tick|janitor|stats|redrive|…
      tests/
        test_sql_contract.py
        test_client_settle_races.py
        test_worker_loop.py
        test_http_router_auth_injection.py
        test_outlabs_adapter.py  # skipped if outlabs-auth not installed

### 4.2 Dependency tiers (hard rule)

| Extra / install | Dependencies | What you get |
|---|---|---|
| `taskq` (core) | `pydantic>=2`, `sqlalchemy[asyncio]`, `asyncpg`, `croniter` | installer, models, SQL client, worker runtime, CLI |
| `taskq[http]` | core + `fastapi`, `httpx` | facade router + HTTP worker client |
| `taskq[outlabs]` | `taskq[http]` + `outlabs-auth` | helper to bind SimpleRBAC permission deps |
| `taskq[dev]` | pytest, etc. | package tests |

**Import rule:** `import taskq` and `import taskq.client` must succeed in an environment that has never installed `outlabs-auth` or even FastAPI.

    # FORBIDDEN in taskq/client.py, taskq/models.py, taskq/sql/*, taskq/worker.py
    import outlabs_auth  # noqa: this must never appear

    # ALLOWED only in taskq/http/outlabs.py (and only under taskq[outlabs])
    from outlabs_auth...

### 4.3 What moves out of each repo

| Artifact today | Moves into `taskq` | Stays in host app |
|---|---|---|
| Alembic taskq schema migration (Diverse) | SQL installer + schema version | Host alembic may call `taskq.sql.install()` as a thin revision, or depend on package migrations |
| `taskq_service.py` | `taskq.client.TaskQ` | Domain bridges (e.g. `persist_taskq_results` → legacy `scrape_jobs`) |
| `taskq_api.py` routes | `taskq.http.create_router(...)` | Mount path, cutover allowlists, legacy dual-write hooks |
| Queue pydantic models (both sides) | `taskq.models` | Domain-specific job payloads / result DTOs |
| Worker claim/heartbeat/settle loop | `taskq.worker` | Job handlers, proxy pools, domain side effects |
| Diverse `DiverseQueueClient` taskq methods | `taskq.http.client.TaskqHttpClient` | Env interlocks that are product-specific (`DIVERSE_*`) may wrap the shared client |
| QDarte `WorkerJobService` lifecycle | Deleted over time (strangler) | Fleet control, browser proxies, LLM budgets, launch pipeline |

### 4.4 Public Python surface (stable)

    from taskq import TaskQ, JobContext, RetryPolicy, Retry, NonRetryable, Snooze, Enqueue, Result, PRIORITY

    tq = TaskQ(dsn=...)  # or bind_existing_session=; schema is FIXED at `taskq` (ADR-002)

    job = await tq.enqueue(..., session=session, idempotency_key=...)
    claimed = await tq.claim(queue="courts", worker_id=..., limit=1)
    await tq.complete(job_id, attempt_id, worker_id, result=...)
    await tq.fail(...)
    await tq.release(...)
    await tq.heartbeat(...)
    await tq.tick()

HTTP facade:

    from taskq.http import create_router
    from taskq.http.outlabs import outlabs_permission_auth  # only if using outlabs

    app.include_router(
        create_router(
            client_factory=...,
            auth=outlabs_permission_auth(
                read=("job:read",),          # Diverse
                write=("job:write",),
                # or QDarte: read/write=("qdarte:worker-run",) + operator split
            ),
        ),
        prefix="/api/v1/taskq",
    )

Without outlabs-auth:

    from taskq.http import create_router, static_api_key_auth, no_auth_for_tests

    app.include_router(
        create_router(auth=static_api_key_auth(env_var="TASKQ_FACADE_KEY")),
        prefix="/taskq",
    )

---

## 5. Auth architecture (the load-bearing section)

> **Extended 2026-07-18:** queue-scoped authorization (per-queue permissions, the `QueueAuthorizer` protocol that supersedes the read/write/operator `TaskqAuth` shape, the `taskq_{queue}:{action}` naming grammar, and the opt-in provisioning helper) is specified in [`Task Queue Authorization & Queue Permissions.md`](./Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md). This section remains authoritative for the trust layering and the optionality rules; where the two disagree on the dependency-protocol shape, the Authorization doc wins.

### 5.1 Three layers of trust (must stay separate)

```
┌─────────────────────────────────────────────────────────────────┐
│ L3 Host identity (OPTIONAL)                                     │
│   outlabs-auth SimpleRBAC / static API key / custom Depends     │
│   Answers: who is calling the HTTP facade?                      │
└────────────────────────────┬────────────────────────────────────┘
                             │ injects AuthContext into routes
┌────────────────────────────▼────────────────────────────────────┐
│ L2 taskq HTTP facade (OPTIONAL)                                 │
│   FastAPI router — transport only                               │
│   Maps AuthContext → actor string for events / operator actions │
│   Never implements claim/fencing/budget                         │
└────────────────────────────┬────────────────────────────────────┘
                             │ calls
┌────────────────────────────▼────────────────────────────────────┐
│ L1 taskq SQL contract (REQUIRED)                                │
│   PL/pgSQL functions + taskq_worker / taskq_admin roles         │
│   Answers: is this state transition legal?                      │
│   Attempt ids are capability tokens; CAS + partial uniques      │
└─────────────────────────────────────────────────────────────────┘
```

Unified-spec grounding:

> dedicated capability roles have EXECUTE on the functions and **no direct DML on the tables**, so the fencing and budget invariants cannot be bypassed by a raw UPDATE.
>
> — *Unified Design Spec* §0

> Auth: per-worker API keys supported; a single shared fleet key is acceptable for the current single-tenant posture (documented — `worker_id` attribution is then advisory).
>
> — *Unified Design Spec* §14

> **Coarse trust model within the role:** any holder of `taskq_worker` can settle any job given `(id, attempt_id)`. Attempt ids are capability tokens (never in read models)…
>
> — *Unified Design Spec* §16 (kept decisions)

**Implication:** outlabs-auth protects the *door to the facade*. It does not, and must not, become part of the settle CAS path.

### 5.2 AuthContext protocol (core of optional IAM)

Defined in `taskq.http.deps` with **zero** outlabs imports:

    from typing import Protocol, Any, runtime_checkable

    @runtime_checkable
    class AuthContext(Protocol):
        """Principal attached to a facade request after host auth succeeds."""

        @property
        def actor(self) -> str:
            """Stable string for taskq events / operator fields (email, key name, 'cli')."""
            ...

        @property
        def principal(self) -> dict[str, Any]:
            """Opaque host principal (outlabs claims dict, API-key row, etc.)."""
            ...

    class TaskqAuth(Protocol):
        """Host-provided FastAPI dependency pair."""

        def require_read(self):
            """Return a FastAPI dependency yielding AuthContext."""
            ...

        def require_write(self):
            """Return a FastAPI dependency yielding AuthContext."""
            ...

        def require_operator(self):
            """Optional; defaults to require_write if not overridden."""
            ...

`create_router(auth: TaskqAuth, ...)` is the only supported way to mount the facade. There is **no** default that imports outlabs-auth.

### 5.3 First-class outlabs-auth adapter (`taskq[outlabs]`)

Both hosts already use the same pattern — a thin factory over `auth.deps.require_permission`:

Diverse (`iam/auth.py`):

    def _permission_dependency(*permissions: str, require_all: bool = False):
        async def dependency(request: Request, session: AsyncSession = Depends(get_async_session)):
            checker = get_auth().deps.require_permission(*permissions, require_all=require_all)
            return await checker(request=request, session=session)
        return dependency

    require_job_read = _permission_dependency(DiversePermission.JOB_READ)
    require_job_write = _permission_dependency(DiversePermission.JOB_WRITE)

QDarte (`app/auth.py`):

    require_worker_run = _permission_dependency(QdartePermission.WORKER_RUN)
    require_job_control = _permission_dependency(QdartePermission.JOB_CONTROL)

The adapter standardizes that pattern without owning permission catalogs:

    # taskq/http/outlabs.py
    def outlabs_permission_auth(
        *,
        auth: SimpleRBAC,                    # host's already-built instance
        session_dependency,                  # host's get_async_session / get_db
        read: Sequence[str],
        write: Sequence[str],
        operator: Sequence[str] | None = None,
        actor_from_principal: Callable[[dict], str] | None = None,
    ) -> TaskqAuth:
        ...

**Recommended host mappings**

| Host | Read | Write (worker) | Operator |
|---|---|---|---|
| Diverse | `job:read` | `job:write` | `job:write` (or future `job:admin`) |
| QDarte | `qdarte:worker-run` | `qdarte:worker-run` | `qdarte:job-control` |

Permission *names* stay in the host catalogs (`DiversePermission`, `QdartePermission`) during the strangler. The package never seeds IAM rows **implicitly** — no import-time, mount-time, or migration-time seeding, ever. It DOES ship an explicit, host-invoked provisioning helper (`provision_taskq_auth` / `taskq auth sync-permissions`) that seeds the canonical `taskq_{queue}:{action}` catalog + optional standard roles when the host calls it from its own bootstrap — the same place `seed_qdarte_auth_records` / `bootstrap init-local` already run. See the Authorization doc §4; role names stay prefixable so hosts keep naming authority.

### 5.4 Non-outlabs auth adapters (must ship in core `[http]`)

So the facade is usable without outlabs-auth:

| Adapter | Use case |
|---|---|
| `static_api_key_auth(header="X-API-Key", env_var=...)` | Small deployments / labs |
| `bearer_token_auth(env_var=...)` | Single shared fleet token |
| `no_auth_for_tests()` | Unit tests / local scratch (explicitly named dangerous) |
| `callable_auth(read=..., write=...)` | Escape hatch: pass any FastAPI dependencies |

These adapters only establish an `AuthContext`. They do not weaken L1 SQL fencing.

### 5.5 What outlabs-auth integration should feel like (DX)

**Diverse mount (target):**

    from taskq.http import create_router
    from taskq.http.outlabs import outlabs_permission_auth
    from diverse_data_api.iam.auth import auth, get_async_session
    from diverse_data_api.iam.seed import DiversePermission

    taskq_router = create_router(
        get_client=lambda session: TaskQ.bind(session),
        auth=outlabs_permission_auth(
            auth=auth,
            session_dependency=get_async_session,
            read=(DiversePermission.JOB_READ,),
            write=(DiversePermission.JOB_WRITE,),
        ),
        # host policy hooks — not auth:
        allow_queue=settings.taskq_allows,
        require_cutover_active=settings.taskq_cutover_active,
    )
    app.include_router(taskq_router, prefix="/api/v1/taskq")

**QDarte mount (target):**

    taskq_router = create_router(
        get_client=...,
        auth=outlabs_permission_auth(
            auth=auth,
            session_dependency=get_async_session,
            read=(QdartePermission.WORKER_RUN,),
            write=(QdartePermission.WORKER_RUN,),
            operator=(QdartePermission.JOB_CONTROL,),
        ),
    )
    app.include_router(taskq_router, prefix="/worker/taskq")  # or strangler path choice

**Lab / no-IAM mount:**

    app.include_router(
        create_router(auth=static_api_key_auth(env_var="TASKQ_API_KEY")),
        prefix="/taskq",
    )

### 5.6 Explicit non-requirements (so nobody “helpfully” couples them)

1. `taskq` does **not** call `SimpleRBAC(...)`, `auth.initialize()`, or `outlabs-auth migrate`.
2. `taskq` does **not** require Redis (outlabs-auth may use Redis; taskq must not).
3. `taskq` does **not** require the `outlabs_auth` schema to exist in the database where `taskq` schema lives (they often co-reside today; that is coincidence, not a contract).
4. DB-direct workers and CLI use role credentials / DSN only — no HTTP auth object in that path.
5. Facade tests in the package use `no_auth_for_tests` or dependency overrides — they must not need a running outlabs-auth seed.

### 5.7 Credential shapes the HTTP client must accept

Mirror both fleets without preferring one header:

| Mode | Header | Used by |
|---|---|---|
| API key | `X-API-Key: …` | Diverse workers / system-integration keys |
| Bearer | `Authorization: Bearer …` | QDarte workers (service token); Diverse optional Bearer |

    TaskqHttpClient(base_url=..., api_key=...)       # XOR
    TaskqHttpClient(base_url=..., bearer_token=...)  # XOR

Exactly one credential — same invariant Diverse’s client already enforces.

---

## 6. Protocol ownership (unchanged, restated for packaging)

### 6.1 SQL-first

> One canonical contract, SQL-first. The protocol is a set of PL/pgSQL functions in schema `taskq`. The Python library, the HTTP facade, and psql are equal clients; no hand-mirrored transport models (kills DCP 7.14).
>
> — *Unified Design Spec* §1

Packaging consequence: schema version is a package version concern. Host apps must not fork function bodies.

### 6.2 Installer vs host migrations

**Pattern (ADR-004 — ordered migrations are canonical):**

1. `taskq` owns ordered, immutable migrations + the `taskq.schema_migrations` ledger (id, package version, checksum, applied_at); `taskq migrate` applies under an advisory lock, `taskq verify` compares objects/signatures/ownership/privileges/checksums read-only. `schema.sql` is a **generated snapshot** for review, diffing, and clean test fixtures — never an upgrade mechanism.
2. Host Alembic (or QDarte migrations) contains a thin revision calling the **supported synchronous adapter** (Alembic runs sync; no ad-hoc async bridging):

       def upgrade():
           from taskq.sql import migrate_sync
           migrate_sync(op.get_bind())

3. A package test asserts live schema == migration chain output (harness T8; already mandated by the spec against qdarte’s historical model/migration drift). Application startup verifies compatibility (`contract_version` + feature-12 matrix) and never silently migrates production.

### 6.3 Roles

The role model is ADR-010’s capability set (spec §4): `taskq_owner` (NOLOGIN, owns everything) + `taskq_producer` / `taskq_runner` / `taskq_observer` / `taskq_operator`, every function SECURITY DEFINER with pinned `search_path` and PUBLIC execute revoked at creation. The migrations own role creation and grants.

Host app DB users are **memberships**: a facade user typically holds producer+runner+observer; ops tooling adds operator. Operator break-glass uses a personal role granted `taskq_operator` / psql.

### 6.4 Housekeeper topology (package must support both)

| Topology | Who calls `taskq.tick()` | Who claims |
|---|---|---|
| HTTP facade (Diverse + QDarte today) | API process housekeeper coroutine | Workers via HTTP → facade → SQL |
| DB-direct workers (optional) | Worker housekeeper coroutine (advisory-lock deduped) | Workers via SQL client |
| Quiet / serverless | `taskq tick` CLI / cron | whichever consumer exists |

The package exposes `Housekeeper` runnable from API lifespan **or** worker process; hosts choose.

---

## 7. Worker runtime extraction

### 7.1 Normative guarantees (must move with the package)

From §14 — these are the bugs both fleets hit in application code:

1. Heartbeat with retry/backoff — never die on first transport error (Diverse DCP 7.2).
2. Settle retries; `already_settled` = success; `lost` = hard error path.
3. Graceful shutdown → `release_job(p_cause='worker_shutdown')`.
4. Unknown `job_type` → `release_job(p_cause='no_handler')`, no budget burn.
5. Housekeeper + optional LISTEN, polling always correct.

### 7.2 Handler registration stays host-owned

    # in diverse-data-workers or qdarte-workers
    from taskq import TaskQ

    tq = TaskQ.from_http(client)  # or from_dsn for DB-direct

    @tq.task(queue="courts", job_type="missouri_casenet", ...)
    async def handle_missouri(ctx, payload): ...

Domain side effects (court ingest, render publish, proxy checkout) remain in the host. `complete_job` stays side-effect-free per spec.

---

## 8. Compatibility and strangler plan

### 8.1 Cutover order (unchanged)

> Migration is strangler-style, **qdarte first** (personal blast radius), **Diverse second** (protected income realm), per-lane, no big bang.
>
> — *Unified Design Spec* §0

Library extraction should follow the same blast-radius order:

1. Publish `taskq` core + installer from the Diverse SQL that already exists (or a cleaned export of it).
2. Point **qdarteAPI** staging at the package for one low-risk lane; keep legacy `WorkerJobService` for others.
3. Point **diverse-data-api** staging facade at `taskq.http.create_router` with outlabs adapter + existing `job:read`/`job:write`.
4. Switch workers to import `taskq.models` / shared HTTP client; delete hand mirrors.
5. Only then expand production lanes.

### 8.2 Versioning policy

- SemVer. **SQL contract changes that alter function signatures or settle semantics → minor at least; breaking semantics → major.**
- Hosts pin `taskq>=X,<Y` like they pin `outlabs-auth>=0.1.0a24,<0.2`.
- Facade route shape changes are major if workers are in the wild.

**Rolling upgrades of taskq itself (the gap the spec leaves open):** the Unified Spec designs migrating *onto* taskq, not evolving it with workers in flight. The extraction owns that story, built from borrowed-features 12 + 13:

1. `taskq.meta.contract_version` = the SQL contract's version; each package release declares its supported contract range and **asserts it at startup** (worker `run()` and facade mount fail fast on skew; workers also LISTEN `taskq_migrate` and soft-stop on a breaking bump — feature 12's matrix).
2. Contract changes ship as pre/post migration pairs with versioned function names during rollout (`claim_jobs_v2` beside `claim_jobs`; old dropped in the post migration after fleets upgrade — feature 13 §2.2/§2.3).
3. Sequence for a breaking contract change: publish package supporting both contracts → upgrade fleets → apply pre migration → flip → post migration → raise the package floor. Never require a simultaneous fleet+schema flip.
4. The harness (Test & Benchmark Harness doc) gates this with an upgrade test: install contract N, start a worker, apply N+1 pre-migration mid-flight, assert the worker finishes or soft-stops cleanly — never corrupts.

### 8.3 What must not block extraction

- Legacy dual-write bridges (`persist_taskq_results`) may remain host-local shims.
- Product cutover flags (`DIVERSE_TASKQ_ENABLED`, allowlists) stay host-local.
- Fleet / machine control planes stay host-local (“pool” word remains banned in taskq).

---

## 9. Testing requirements for the package

> **Expanded 2026-07-18:** the full harness — suite layout, deterministic race technique, property/chaos suites, CI matrix, and the benchmark suite with regression gates — is now its own design: [`Task Queue Test & Benchmark Harness.md`](./Task%20Queue%20Test%20%26%20Benchmark%20Harness.md). The gates below remain the minimum bar; the harness doc is how they get built.

### 9.1 Mandatory gates (from the unified spec spirit)

1. **Schema verify** — installer output == live schema.
2. **Settle race suite** — duplicate complete/fail/release → typed `already_settled` / `lost`, never discarded success.
3. **Claim fencing** — two workers cannot both hold the same attempt.
4. **Idempotent enqueue** — concurrent same key → one active row.
5. **Auth injection suite** — router rejects when auth dependency fails; works with static key adapter; works with mocked outlabs adapter; **core tests run without outlabs-auth installed.**
6. **Import lints** — CI matrix job: install `taskq` only (no extras) and `python -c "import taskq"`.

### 9.2 Host regression tests after adoption

- Diverse: existing `tests/test_taskq_routes.py` becomes a thin mount test over `create_router` + outlabs adapter (or dependency overrides).
- QDarte: worker claim/complete path against facade with `WORKER_RUN` token.
- Workers: delete duplicated pydantic models; import from `taskq`.

---

## 10. Open decisions (small, explicit)

| # | Question | Status / recommendation |
|---|---|---|
| 1 | Repo location? | **Resolved 2026-07-18:** `~/Documents/projects/outlabs-taskq`, canonical docs home |
| 2 | Package name on index? | **Resolved:** `outlabs-taskq` (pyproject), import name `taskq` |
| 3 | Alembic ownership? | **Resolved (ADR-004):** package-owned ordered migrations; hosts call the sync adapter from one revision |
| 4 | Default facade auth? | **None** — host must pass `auth=`; fail fast if omitted |
| 5 | Should `[outlabs]` be a required extra for Diverse/QDarte hosts? | Yes for those hosts; never for core |
| 6 | Keep `/api/v1/taskq` vs move to `/taskq`? | Preserve Diverse paths during strangler; QDarte may choose `/worker/taskq` |
| 7 | Sync vs async worker client? | **Upgraded from "maybe" to requirement:** BOTH fleets' workers are sync httpx today (playwright/camoufox handlers) — a sync `TaskqHttpClient` variant + a sync worker-loop adapter (asyncio core, thread-offloaded handlers) must ship in Phase C, or adoption stalls on an async rewrite of every worker |

---

## 11. Implementation phases

### Phase A — Package skeleton (no host cutover)

- Create repo with core client + models + installer extracted from Diverse’s taskq migration.
- CI: core import without FastAPI/outlabs-auth.
- Port SQL contract tests.

### Phase B — HTTP facade + auth injection

- `create_router(auth: TaskqAuth)`.
- Ship `static_api_key_auth` + `no_auth_for_tests`.
- Ship `taskq.http.outlabs.outlabs_permission_auth`.
- Matrix test with and without `[outlabs]`.

### Phase C — Worker runtime

- Move heartbeat/settle/shutdown guarantees into `taskq.worker`.
- HTTP client supporting `X-API-Key` and Bearer.

### Phase D — Host adoption

1. qdarteAPI staging lane on package facade + outlabs adapter (`WORKER_RUN` / `JOB_CONTROL`).
2. diverse-data-api replace in-tree `taskq_api.py` body with mounted package router; keep cutover gates as host hooks.
3. diverse-data-workers + qdarte-workers drop mirrored models; depend on `taskq[http]`.
4. Delete dead legacy paths per existing runbooks.

### Phase E — Harden

- Schema verify in production preflight (Diverse already has `scripts/taskq_production_preflight.py` — rehome against package API).
- Documentation: this brief + slim README; protocol details remain in the Unified Design Spec.

---

## 12. Acceptance criteria

Extraction is “done” when all of the following are true:

1. **`pip install taskq`** gives a working SQL client + installer with **no** FastAPI and **no** outlabs-auth.
2. **`pip install taskq[outlabs]`** lets Diverse and QDarte mount the facade with one adapter call each, using their existing permission names.
3. A third host can mount the facade with `static_api_key_auth` and never hear about outlabs-auth.
4. Workers in both fleets import settle/enqueue models from `taskq` only — zero hand mirrors.
5. Claim/settle/fencing tests pass against the package installer on PG16+ (PG18 target).
6. Diverse cutover allowlists and QDarte fleet/proxy domains remain outside the package.
7. No application capability role can `UPDATE taskq.jobs` directly (ADR-010/011).

---

## 13. Appendix — quote bank (keep nearby while implementing)

**Package intent**

> One package (`taskq`, published like outlabs-auth), consumed by qdarteAPI, qdarte-workers, diverse-data-api, diverse-data-workers.
>
> — §14

**SQL contract**

> Because raw DML is denied, every documented flow has a function. … There is no flow in this document that requires `taskq_worker` to touch a table directly — any such flow is a design bug by definition.
>
> — §4

**Facade auth posture**

> Auth: per-worker API keys supported; a single shared fleet key is acceptable for the current single-tenant posture (documented — `worker_id` attribution is then advisory).
>
> — §14

**Housekeeper split**

> HTTP workers *cannot* tick — they have no DB connection… The API process hosting the facade … runs the housekeeper coroutine.
>
> — §11.4

**Settle side effects**

> `complete_job` performs **no business side effects** — no publishes, no proxy bookkeeping, no domain writes.
>
> — §5.x (complete_job hard rule)

**Diverse route auth today (to be injected, not imported by the package)**

    from diverse_data_api.iam.auth import require_job_read, require_job_write

**QDarte worker auth today (to be injected, not imported by the package)**

    if path.startswith("/worker/"):
        return await require_worker_run(...)

---

## 14. Summary

`taskq` should be extracted as a **protocol package with an optional HTTP door**, not as an “outlabs queue product.”

- **L1 SQL** is mandatory and IAM-agnostic.
- **L2 HTTP facade** is optional and auth-pluggable.
- **L3 outlabs-auth** is a blessed adapter for Diverse and QDarte — ergonomic, documented, tested — and **never** an import-time requirement for the engine.

That is how you get the benefit of one shared queue library without turning every future consumer into an outlabs-auth customer, while still making the two systems you already run feel native.
