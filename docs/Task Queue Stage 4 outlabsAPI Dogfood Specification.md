# taskq — Stage 4 outlabsAPI Dogfood Specification

> **Status:** Frozen by S4-00 — 2026-07-19; production-reality amendment approved by S4-CQ-01
> **Tier:** 3 implementation design; subordinate to Protocol v1 document revision 1.0.4,
> Function Manifest 0.1.2, and ADR-001..017
> **Host baseline:** deployed `outlabsAPI` production line at `d1b00fe`; S4-00's `a0019cd`
> inventory is retained as the stale-default-branch provenance that S4-CQ-01 corrected
> **Library baseline:** `outlabs-taskq` at `8a13262` (accepted Stage-3 source at `b6e29ca`)
> **Scope:** plan, integration seams, evidence, rollback, and task decomposition only. S4-00 changes no
> host source, SQL contract, migration, deployment, or production state.

## 1. Outcome and non-negotiable boundary

Stage 4 proves the smallest supported production topology: one FastAPI process, one PostgreSQL
database, one taskq queue, the housekeeper, and one opt-in embedded worker. The first lane is the
existing queued tools flow. It is chosen because the registered tools are asynchronous read-only
network operations and the host already exposes an authenticated queued endpoint, while notification
lanes produce external side effects and therefore wait until the dogfood gate is accepted.

The first-host slice must:

1. replace the selected tool's fire-and-forget publish with a durable typed enqueue;
2. return a job id and canonical result-read URL from HTTP 202;
3. execute through the unchanged taskq worker, fencing, heartbeat, retry, and settlement contracts;
4. use the real OutLabs authorization adapter against exact `outlabs-auth==0.1.0a24`;
5. survive two normal deploy cycles, one controlled process failure, and one rollback rehearsal;
6. retain the legacy path as a mutually exclusive fallback until acceptance; and
7. require zero manual table edits in every success, failure, and rollback path.

No Stage-4 code may add or change a taskq SQL function, migration, outcome, permission grammar,
HTTP route, or wire field. A discovered contract defect triggers the normal STOP-and-record rule.

## 2. Verified host reality

S4-00 inspected clean default branch `main` at `a0019cd`, but production had already diverged onto
Coolify's `staging-prep` line in mid-June. Round 6 therefore audited a stale branch accurately rather
than the deployed host. S4-CQ-01 corrects the living design without rewriting that historical review:

- Production now runs reconciled commit `d1b00fe` from `staging-prep`. The deployed line collects 58
  tests: 53 pass and five opt-in infrastructure tests skip; Ruff is clean and MyPy covers 61 files.
  The accepted taskq integration and its 18 tests are byte-identical to the accepted default-branch
  slice. After Stage 4, `main` and the production lineage must become one authoritative line, and
  future host gates inspect the deployed line.
- The host locks exact `outlabs-auth==0.1.0a24` and immutable `outlabs-taskq==0.1.0a1`; the earlier
  a20/default-branch overlay evidence remains provenance, not the current production dependency fact.
- Production starts one Uvicorn process from the root Dockerfile. The host lifespan starts
  authentication and checks PostgreSQL and Redis; no message broker or WhatsApp domain remains.
- The legacy queued-tools path is the interim Postgres-backed `outbound_tasks` queue created by host
  migration `20260616_0005`. `POST /tools/{tool_name}/runs/queued` schedules an insert through
  `enqueue_tool_task`; a separate polling worker invokes the shared registry and discards successful
  result data. Taskq strangles this path for allowlisted tools only.
- The registered `umami` and `aerolineas` tools are asynchronous, read-only HTTP operations. Neither
  is CPU/browser/render work. The synchronous `/tools/{tool_name}/runs` route remains unchanged.
- The live application DSN targets Coolify's internal PostgreSQL service, not Neon. Before taskq
  enablement, the complete role/migration/IAM/profile drill must run in a disposable database on
  this same cluster and record server, connection, TLS, role-authority, backup, and durability facts.
- S4-01's deleted Neon-branch evidence is superseded only for Neon-specific production claims:
  provider/pooler class, TLS proxy behavior, and ceiling 901 are non-applicable. Its immutable
  migration, role, IAM, and queue-profile mechanics remain the rehearsal to reproduce in place.

## 3. Adopted decisions

### 3.1 R2-17 authentication prerequisite

The host upgrades to **exact `outlabs-auth==0.1.0a24`** before importing taskq's OutLabs adapter.
The static adapter is rejected for this host: it would create a second credential policy beside an
already-mounted EnterpriseRBAC installation and would fail to dogfood the integration Stage 3 built.

S4-01 completed, in this order:

1. build/publish the next immutable `outlabs-taskq` alpha from the accepted source and record its
   version plus wheel SHA-256;
2. replace the host's a20 pin with exact a24 and add the exact taskq `[outlabs]` artifact;
3. regenerate `uv.lock` with one a24 installation—never overlay two conflicting pins;
4. prove the host suite and OutLabs import/API surface from the locked environment;
5. run the OutLabs migration/current checks on a disposable database copy and then through the host's
   documented pre-deploy procedure; and
6. keep `OUTLABS_AUTH_AUTO_MIGRATE=false` in deployed processes.

The accepted artifact is `outlabs-taskq==0.1.0a1`, published from `a6967e6`; its wheel SHA-256 is
`01ac3129866a8db34281688d65a95e9f30437b52739cec75c287c69e4d11a6ab`. No floating Git dependency,
local path, unversioned wheel, dependency override, or compatibility range is accepted for the
dogfood gate.

### 3.2 One queue, one task, staged allowlist

The Stage-4 queue is exactly `tools`. The task registry exposes one canonical host task, plus the
§6 probe task only while its dedicated flag is enabled:

```text
job type: outlabs.tools.run
input:    {tool_name: bounded string, params: bounded JSON object}
output:   the existing strict ToolResult projection
```

The task name is stable and tool names stay data; adding a registered tool does not create a new wire
type. The route validates that the tool exists, validates its parameters before enqueue, and checks a
deployment allowlist. The handler repeats existence/parameter validation defensively.

Canary order is fixed:

1. deploy cycle 1: `umami` only;
2. deploy cycle 2: add `aerolineas` after cycle-1 evidence is green; and
3. external-effect notification/contact/newsletter/analytics lanes remain on the existing
   `outbound_tasks` path throughout Stage 4; the removed WhatsApp lane does not exist.

This is one low-consequence lane, which satisfies the Build Plan. Expanding to notifications is a new
board task after acceptance, not a quiet addition to the dogfood slice.

### 3.3 Producer switch and no dual execution

The host adds frozen settings:

```text
TASKQ_ENABLED=false
TASKQ_TOOLS_MODE=legacy          # legacy | taskq
TASKQ_TOOLS_ALLOWLIST=           # comma-separated canonical registered names
TASKQ_DOGFOOD_PROBE_ENABLED=false
```

When taskq is disabled or a tool is outside the allowlist, the existing Postgres-backed
`outbound_tasks` path is used. When
`TASKQ_TOOLS_MODE=taskq` and the tool is allowlisted, only taskq receives the job. Dual publishing,
shadow execution, best-effort fallback after an ambiguous enqueue response, and catch-and-publish to
the legacy system are forbidden because each can execute a tool twice.

A typed taskq error is returned to the caller. The operator may change the feature flag and retry with
an idempotency key; application code never guesses whether a failed enqueue committed.

That idempotency key has no meaning on the `outbound_tasks` path: changing modes after an ambiguous
taskq enqueue and then retrying can place one row in each Postgres queue and execute the read-only
tool twice. This is the unchanged R6-06 cross-path replay residual. Stage 4 accepts it only as an
operator-owned recovery risk for the explicitly read-only lanes; application code never publishes
to both tables, and §9 forbids any side-effecting lane without downstream idempotency proof.

### 3.4 HTTP 202 and result readback (R2-18)

The queued route retains its existing path and authentication dependency, but its success response is
frozen as HTTP 202:

```json
{
  "job_id": "uuid",
  "disposition": "created | existed",
  "status_url": "/taskq/v1/jobs/{job_id}?include_result=true&include_error=true"
}
```

The route accepts optional `Idempotency-Key` using a bounded safe grammar. When absent, the server
mints a UUID for that request and echoes it in the response header; this preserves current unkeyed
behavior but cannot make a lost response replay-safe. Clients requiring replay safety must provide a
stable key. Concurrent or repeated keyed requests converge to `existed` and the same job id only
while the original job is active (`blocked`, `queued`, or `running`). After settlement, the active
deduplication window has ended: replaying the same key creates a new job and a new execution.

The canonical mounted taskq job-detail route is the status/result surface—no host copy of the taskq
state model is introduced. A caller needs `taskq_tools:read` or global `taskq:read`. The existing
`tools:run` dependency continues to protect the producer route; callers are **not** granted direct
`taskq_tools:enqueue`, because that would bypass the host's registered-tool allowlist and payload
validation.

Terminal mapping is the taskq projection:

- `succeeded`: `result` contains the validated `ToolResult`;
- `failed`: the bounded taskq error projection is available only when requested and authorized;
- active states: result remains absent; and
- denial and absence retain the Stage-3 hiding posture.

### 3.5 Handler settlement policy

The handler is async and never declares a sync executor. It maps outcomes deliberately:

- successful `ToolResult` → `Complete(result=...)`;
- a returned `ToolResult(success=false)` → `NonRetryable(error=...)`;
- missing tool or invalid parameters → `NonRetryable(error=...)`;
- a retryable transport fault may raise or return `Retry(error=...)`; and
- cancellation/shutdown → the unchanged worker cancellation/release contract.

Every durable error is a bounded sanitized classification plus the canonical tool name; the handler
never stores a raw upstream response, authentication body, credential, exception string, or caller
input. The S4-02 host change removes the existing raw authentication-body interpolation from the
`umami` tool and pins the stored projection with a forced-failure vector. The queued route rejects
`token` and any other credential-bearing parameter before enqueue; no caller bearer token may enter
the durable payload. The existing module-global aerolineas token cache is a pre-existing host defect
owned by a later follow-up, not permission to persist or share a caller token in Stage 4.

Queue retries are not used to pretend arbitrary external effects are exactly once. The chosen tools
are read-only; any future side-effecting handler must supply its own downstream idempotency proof
before entering this queue.

## 4. Database, queue, and IAM provisioning

Provisioning is explicit and pre-deploy; application startup performs no migration or IAM mutation.

### 4.1 Managed-database capability drills

S4-01 used a disposable Neon branch to prove the mechanics below. S4-CQ-01 supersedes its
provider-specific facts and requires the same proof in a disposable database on the actual Coolify
PostgreSQL cluster before enablement, with the exact intended credentials:

1. OutLabs a20→a24 migration/current/seed behavior;
2. `taskq migrate` fresh installation and `taskq verify`;
3. cluster-role creation through an owner/admin credential;
4. membership of the runtime login in producer, runner, observer, and housekeeper capabilities;
5. an operator-only pre-deploy credential for `ensure_queue`, never present in the app pool;
6. taskq IAM provisioning in `report` mode, then `apply`, then idempotent report;
7. actual server version, measured `max_connections`, connection class, and TLS behavior;
8. the internal service's backup, restore, and durable-volume posture stated without invention; and
9. teardown by dropping only the disposable database after proving the production database stayed
   untouched.

Because roles are cluster-wide, the drill records pre-existing safe taskq roles and removes only
objects proven to have been created by the drill; it never drops a shared role by assumption. The
immutable migrations and `verify()` run twice, IAM runs report → apply → idempotent report, and the
`tools` queue profile returns `created` → `unchanged`.

The 2026-07-19 actual-cluster drill completed those mechanics on a disposable database and then
dropped only that database. It measured PostgreSQL 16.14, `max_connections=100`, direct internal
port 5432, and TLS disabled. Taskq migrations 0001–0003 plus both verifier passes converged; OutLabs
Auth reached `20260715_0020` twice; IAM converged from fourteen creates to fourteen existing records
with no changes or conflicts; and the complete `tools` profile returned `created` then `unchanged`.
The production sentinels remained application migration `20260616_0005`, one legacy
`outbound_tasks` row, and no `taskq` schema before and after teardown. The six taskq contract roles
remain as cluster-wide `NOLOGIN` roles.

The same drill found that the deployed application DSN authenticates as PostgreSQL superuser. That
credential bypasses the role boundary and therefore cannot be the Stage-4 runtime pool. S4-CQ-02
approved a dedicated non-superuser runtime login plus an owner/operator-only provisioning path.
Before rotation, a disposable same-cluster database must run the real API startup, a successful
authenticated request, a legacy `outbound_tasks` enqueue/claim/settle path, and the separate worker
surface under that login. It must also prove no operator role switch or `ensure_queue`, no role or
database creation, and no RLS bypass.

The old owner DSN remains available outside the running application for an immediate env-flip and
restart rollback. Deployment steps name their credential explicitly: host Alembic and OutLabs Auth
migrations use the owner; queue ensure and IAM use an operator-only login; API and worker containers
receive only the restricted runtime DSN. S4-CQ-03 approved direct taskq migration and verification
by the owner/admin credential without `SET ROLE`; migration SQL assigns objects to the deliberately
unprivileged `taskq_owner`, and `verify()` proves ownership. The mandatory order remains grant proof,
rotation, healthy disabled posture, production taskq provisioning, then
`TASKQ_ENABLED=true` in legacy mode. The final audit records this broader host-security improvement
and keeps a restore/PITR rehearsal on the host backlog.

Coolify mounts the named PostgreSQL data volume at `/var/lib/postgresql/data`. Scheduled backups run
daily at 03:00 to S3; the 2026-07-19 run completed successfully. This records the observed durability
posture, not a claim of tested restore or point-in-time recovery.

If the runtime credential cannot create roles, that is expected least privilege: the migration uses
the managed owner/admin credential. If the managed service cannot support the contract even with its
owner credential, Stage 4 stops; source must not emulate or weaken migrations.

### 4.2 Queue profile

The operator pre-deploy step ensures queue `tools` with this complete 0.1 profile:

```json
{
  "default_priority": 100,
  "default_lease_seconds": 60,
  "default_max_attempts": 3,
  "default_backoff_mode": "exponential",
  "default_backoff_base": 5,
  "default_backoff_cap": 60,
  "retention_hours": 168,
  "failed_retention_hours": 720,
  "max_depth": 1000,
  "notify_enabled": false
}
```

The first managed-database profile is poll-only. Correctness never depends on LISTEN, and this avoids
claiming session-listener support through an unclassified pooler. Enabling notifications later needs
a dedicated DSN/session proof and its own measured change.

### 4.3 IAM posture

`provision_taskq_auth(..., queues=("tools",))` runs report-first against exact a24. Reconciliation is
off unless a reviewed drift report names every changed row. The catalog creates
`taskq_tools:{enqueue,run,read,control,admin}` plus global permissions and standard roles, but role
creation does not silently grant them to users or keys.

For dogfood:

- the existing trusted tool caller receives only `taskq_tools:read` in addition to its existing
  `tools:run`/`tools:read` posture;
- the embedded worker is DB-direct trusted code and receives no HTTP credential;
- the ordinary app pool never receives operator capability;
- operator routes are omitted by constructing the facade without an operator transport/authorizer;
- no wildcard API-key scopes are minted; and
- the Enterprise personal-key policy and actor attribution remain those of exact a24.

## 5. Runtime and FastAPI composition

The host constructs `TaskqRuntime.from_dsn` only when `TASKQ_ENABLED=true`, using the shared registry
and a frozen production posture equivalent to:

```text
housekeeper_enabled=true
long_poll_listener_enabled=false
embedded_worker.queues=("tools",)
embedded_worker.acknowledge_process_multiplication=true
embedded_worker.concurrency=1
embedded_worker.batch=1
embedded_worker.listen=false
embedded_worker.poll_interval=1.0
request_pool_max=4
housekeeper_pool_max=1
embedded_worker_pool_max=2
expected_asgi_processes=1
soft_stop_timeout=20s
asgi_graceful_timeout=30s
expected_environment="production"
allow_production=true
```

The deployment platform's application Stop Grace Period is configured to **35 seconds**, at least
the 30-second ASGI graceful timeout and above the 20-second soft stop. S4-AUDIT records the live
platform value and a normal-deploy transcript proving the process drains inside it.

Production must supply a measured database connection ceiling and a reserve covering the host's own
SQL/OutLabs pools plus platform headroom. Runtime construction refuses an over-budget estimate. The
single-process invariant is backed by the Docker command; adding Uvicorn workers requires a budget
recalculation and a new deployment review.

The existing host lifespan is composed explicitly with taskq. Host startup completes first; taskq
then starts. On shutdown taskq stops/drains before the host closes authentication resources. The
lifespan-free taskq sub-application mounts at `/taskq`; the host never copies generated routes or
models. Existing security middleware remains outermost.

The host CORS contract adds `Idempotency-Key`, `Taskq-Protocol-Version`, and `Taskq-Request-Id` to
allowed headers and exposes the two taskq response headers. The mounted facade uses
`OutlabsQueueAuthorizer(auth=existing_auth, session_dependency=existing_auth.get_session)` and has no
operator transport.

`GET /health` reports not-ready/503 when taskq is enabled but its runtime is not ready. Backlog alone
does not fail health. The embedded runtime's five-second housekeeper means the managed database may
remain awake; Stage-4 evidence records that cost honestly rather than claiming autosuspend.

## 6. Controlled failure probe

Stage 4 includes one registered probe task only when `TASKQ_DOGFOOD_PROBE_ENABLED=true`. It is absent
from public tool discovery and cannot be selected through the tools route. An operator invokes it
through a local/pre-deploy command using the trusted producer transport.

The probe is side-effect-free and has two modes:

1. `fail_once`: returns one retryable failure, then succeeds on the next fenced attempt; and
2. `hold`: signals that the handler started and waits, allowing the deployment process to be
   terminated deliberately.

The required forced-failure drill uses `hold`: terminate the old API process, start the replacement,
allow the database lease to expire and the housekeeper to reap it, then prove the **same job id** is
claimed under a new attempt and reaches `succeeded`. Evidence includes attempts/events, budget use,
worker presence, and zero manual DML. The probe flag is disabled after the drill.

## 7. Rollout and rollback

### 7.1 Deployment cycle 1

1. Finish S4-01 preview migration/IAM/artifact proof.
2. Deploy S4-02 integration disabled, adjudicate S4-CQ-01, and complete the same-cluster disposable
   database preflight.
3. Enable taskq with tools mode still `legacy` and allowlist empty.
4. Verify runtime readiness, pools, housekeeper, queue profile, IAM, and canonical meta/job reads.
5. Set tools mode `taskq` with allowlist `umami`.
6. Run keyed created/existed, success-result, terminal-failure, auth-denial, and concurrency probes.

### 7.2 Deployment cycle 2

1. Deploy a normal application revision while cycle-1 taskq remains enabled.
2. Prove old/new overlap yields no duplicate execution and old process drains within 20 seconds.
3. Add `aerolineas` to the allowlist only after the first cycle is green.
4. Repeat HTTP 202→canonical GET result and keyed replay evidence.
5. Execute the controlled process-failure drill; S4-AUDIT owns this step and its evidence even though
   it is scheduled during deployment cycle 2.

### 7.3 Rollback rehearsal

Rollback is a producer switch, not a schema downgrade:

1. set `TASKQ_TOOLS_MODE=legacy` so new allowlisted requests use only the legacy path;
2. keep the embedded worker enabled until queue `tools` reports
   `ready + scheduled + blocked + running = 0`;
3. soft-stop and disable taskq runtime after the drain;
4. leave taskq migrations, roles, IAM catalog, and durable job history intact;
5. prove the legacy queued endpoint still functions; and
6. re-enable taskq and prove the same configuration converges without manual repair.

Emergency rollback with an unsafe in-flight job follows the worker contract: never release a live
sync thread (none are planned); async work is settled or released budget-free. Queued work is never
copied into the legacy system. A one-off taskq worker may drain it using the same registry if the API
runtime cannot be restored.

The legacy tools producer/consumer is retired only **after** S4-AUDIT is independently accepted. No
other legacy queue is removed in Stage 4.

## 8. Permanent acceptance evidence

### S4-01 — dependency, auth, and managed-database preflight

- locked exact a24 plus immutable taskq alpha resolve without overrides;
- the deployed-line 58-test collection (53 pass, five explicit infrastructure skips), Ruff, type
  check, Docker build, and import boundaries pass;
- the two application-path tests pass under both FastAPI 0.135.1 and the locked 0.139.2 resolution;
- the original Neon preview evidence remains as superseded rehearsal, while the actual-cluster
  disposable database passes taskq fresh migrate/verify/provision and exact-a24 IAM/profile flows;
- role membership, connection class, server version, measured connection ceiling, SSL, and
  backup/durability posture are recorded without credentials; and
- production pre-deploy commands are idempotent and runtime auto-migration remains off; and
- the live platform Stop Grace Period is recorded at 35 seconds, with practical drain proof deferred
  to the S4-AUDIT normal-deploy transcript.

### S4-02 — disabled-by-default host integration

- strict settings reject unknown mode, empty required ceiling, multi-process mismatch, bad allowlist,
  inverted grace, missing `expected_environment="production"`, or production enablement without
  `allow_production=true`;
- unit tests prove mutually exclusive producer selection, one mode/allowlist snapshot per request,
  and no fallback after ambiguous enqueue using a fault-injection seam plus legacy-publisher spy;
- request/response models prove bounded params, stable job type, exact 202 body, keyed replay, and
  fence/credential-safe representations;
- the registry contains only `outlabs.tools.run` unless the controlled probe flag is enabled, when
  exactly the private §6 probe is additionally registered;
- active-window keyed requests converge while post-settlement replay creates a new job;
- settlement vectors prove successful, terminal, retryable-transport, and cancellation outcomes:
  every terminal row uses one attempt and the retryable transport row schedules a retry;
- forced tool failures store only sanitized classification/tool name, never raw credential,
  response-body, exception, or request text; queued credential-bearing parameters are rejected or
  proven absent from the stored payload;
- health vectors prove disabled → 200, enabled-but-not-ready → 503, and backlog alone → 200;
- lifespan success/failure/cancellation tests return every pool/task/listener/session to baseline;
- canonical CORS/OpenAPI/job-result projections are exact; and
- the diff contains no taskq SQL/migration/contract change and no unrelated host-lane migration.

### S4-03 — live canary

- live route→enqueue→embedded claim→handler→settle→authorized GET result against real PostgreSQL;
- unauthorized/missing/wrong-queue reads preserve hiding and leave the job untouched;
- keyed concurrent producer requests converge to one job and one tool invocation, proved by taskq
  attempt/event-ledger arithmetic plus an independent target-service access log or local counting
  endpoint;
- worker response-loss replay invokes the tool once and converges, proved by the same independent
  ledger-plus-external-counter pair;
- queue depth refusal is typed and never falls back, proved against an operator-created scratch queue
  with a deliberately tiny `max_depth` (or by the accepted harness-level equivalent for this row);
- health and synchronous-tool endpoints each answer within five seconds while an embedded job runs;
- shutdown releases/drains inside platform grace; and
- poll-only recovery works after database disconnect without correctness sleeps.

### S4-AUDIT — production completion

The audit packet contains, for both normal deploy cycles and the forced failure:

- immutable host/library commits and artifact hashes;
- redacted configuration manifest, queue profile, IAM report, server/pooler facts, and connection
  arithmetic, including the live 35-second platform Stop Grace Period;
- job ids, typed outcomes, attempt/event conservation, worker ids, and timestamps;
- before/after resource ledger and health/readiness observations, including a normal-deploy drain
  transcript completed inside the configured platform grace;
- canonical 202→GET result examples with secrets, payloads, and fences absent;
- rollback and re-enable transcript with zero manual DML;
- confirmation that only the selected tools changed producer; and
- an honest latency/cost note with no baseline or win invented from two deployments.

Exit requires two normal deploy cycles, the controlled process failure recovered to success, the
rollback rehearsal, all local/CI gates green, and independent acceptance. Until then, the legacy
tools path remains deployable and no legacy component is retired.

## 9. Board sequence and stop conditions

1. **S4-00-R6** — external review of this frozen plan; implementation stays closed.
2. **S4-01** — immutable taskq artifact, exact a24 host upgrade, and managed-database preflight.
3. **S4-02** — disabled-by-default host integration and local/live acceptance harness.
4. **S4-03** — allowlisted canary plus two normal deployment cycles.
5. **S4-AUDIT** — controlled failure, rollback/re-enable, evidence packet, and independent acceptance.

Stop and record before proceeding if:

- exact a24 requires an unsupported host API or schema workaround;
- the managed database cannot install/verify the unchanged taskq contract with an owner credential;
- an immutable taskq artifact cannot be locked and reproduced;
- the platform cannot guarantee the declared one-process/budget posture;
- the canonical job read cannot enforce queue authorization;
- rollback would require copying jobs, dropping schema, or manual DML; or
- a selected tool is discovered to perform an external mutation without downstream idempotency.

## 10. Explicit non-goals

- Changing taskq SQL, wire contracts, retry budgets, or authorization grammar
- Migrating notification, contact, newsletter, or analytics-event lanes
- Removing the legacy `outbound_tasks` producer/worker before Stage-4 acceptance
- Side-effecting, CPU-heavy, browser, rendering, or batch handlers in the embedded worker
- Multi-process API execution, autoscaling, or a dedicated taskq worker fleet
- Proving managed-database LISTEN support or database autosuspend compatibility
- General host job lists, queue-detail reads, dashboards, timelines, or future taskq capabilities
- Treating a two-deploy laptop/platform sample as a performance baseline
