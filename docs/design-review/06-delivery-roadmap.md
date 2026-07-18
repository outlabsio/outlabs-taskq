# Delivery roadmap

This roadmap builds confidence in layers. It begins with irreversible contracts—state transitions, privileges, migrations, and typed outcomes—then adds runtime ergonomics and integrations, then migrates existing systems, and only then expands the workflow feature set.

The milestones are capability gates, not date estimates.

## Stage 0 — decide the contract

### Deliverables

- ADRs accepting/changing/rejecting D-01 through D-12 in the critical findings;
- `0.1` public capability list and explicit non-goals;
- state-transition table for every job status and command;
- result-code catalog for enqueue, claim, heartbeat, settlement, and controls;
- canonical HTTP protocol and SQL-to-HTTP outcome mapping;
- support matrix for Python, PostgreSQL, FastAPI, SQLAlchemy, and OutLabsAuth;
- existing-system inventory: database versions, connection budgets, sync/async call sites, queues, peak rates, retry/retention needs, and credential topology;
- performance-runner profile and first workload manifests.

### ADRs that should be written first

```text
ADR-001  Product boundary: durable task queue, not message bus
ADR-002  Fixed taskq schema and SQL-function ownership
ADR-003  Attempt fencing and typed replay outcomes
ADR-004  Canonical ordered migrations and compatibility window
ADR-005  SQL and HTTP transport parity
ADR-006  Valid OutLabsAuth permission grammar and authoritative lookup
ADR-007  Atomic follow-ups and fenced handler cancellation
ADR-008  FastAPI lifespan/process model
ADR-009  First-release scope and deferred policies
ADR-010  Supported database roles and SECURITY DEFINER posture
```

### Exit gate

No public SQL migration is written until the state table, privilege model, and result vocabulary have review sign-off. Changing those after host migration is far more expensive than changing a Python convenience method.

## Stage 1 — secure SQL kernel and installer

### Build

- package migration ledger and advisory migration lock;
- fixed `taskq` schema and `NOLOGIN` owner;
- queue, job, attempt, event, worker-presence, and schema-migration objects;
- partial indexes supporting claim/idempotency/current-attempt paths;
- enqueue, claim, heartbeat, complete, fail, release, snooze, and fenced running-cancel functions;
- pause/resume, retry/redrive, operator cancel, and bounded janitor functions;
- safe observation/authorization projection functions;
- database capability roles and grants;
- `taskq migrate`, `taskq verify`, and lock-recovery tooling;
- generated schema snapshot and object/privilege manifest.

### Build alongside it

- SQL contract suite on real PostgreSQL;
- stateful reference model;
- privilege/shadow-object attack tests;
- migration clean-install, double-run, lock, and interruption tests;
- B1–B4 benchmarks and representative query plans.

### Exit gate

- all P0 correctness/security cases pass on the minimum and current PostgreSQL versions;
- the model and SQL agree on every generated sequential transition;
- untrusted roles have no table DML and cannot execute ungranted functions;
- stale attempts cannot heartbeat or settle;
- concurrent claims never produce two current owners;
- installer verify detects deliberately corrupted ownership, signature, grant, and checksum;
- query plans stay index-backed at the initial 1m-row dataset.

## Stage 2 — delightful Python runtime

### Build

- `TaskQ`, `Task[InputT, OutputT]`, task registry, stable names, aliases, payload versioning;
- queue profiles with safe defaults and `config explain`;
- `EnqueueResult` and handler result unions;
- direct async SQL transport;
- SQLAlchemy `AsyncSession` transaction adapter;
- worker supervisor, bounded concurrency, heartbeat, graceful drain, and sync-handler executor;
- notification listener plus polling fallback;
- structured logging, core metrics, OpenTelemetry spans, and safe error envelopes;
- `taskq worker` and read/control CLI commands;
- `taskq.testing` fixtures and inline model transport.

### API usability test

A new FastAPI service should be able to:

1. install the package and migrate an ephemeral database;
2. define a Pydantic payload and async task in fewer than 15 lines;
3. enqueue inside an application transaction;
4. run a worker with one CLI command;
5. understand a retry, stale fence, and idempotent replay from logs/CLI without opening tables.

This should be tested as a small executable example, not judged only from API review.

### Exit gate

- core imports with all integration extras absent;
- direct transport passes the full protocol suite;
- cancellation and shutdown never leave new claims after drain begins;
- synchronous handlers do not block the event loop;
- notification loss still makes progress through polling;
- all typed outcomes serialize/deserialize across the supported compatibility window;
- B1–B8 and a 6-hour mixed workload meet the initial envelope.

## Stage 3 — FastAPI and OutLabsAuth

### Build

- `taskq.fastapi` router, runtime, dependency, health/readiness, sync/async HTTP clients;
- versioned OpenAPI models and stable error codes;
- SQL/HTTP shared conformance tests;
- `taskq.outlabs` permission catalog and name validator integration;
- queue authorizer, authoritative job projection, actor attribution;
- idempotent role-report/reconciliation helper;
- service-token provisioning guidance and Enterprise system-key policy checks;
- per-process embedded-worker/connection-budget validation.

### Exit gate

- HTTP transport returns domain-equivalent outcomes to SQL transport;
- no route authorizes a job mutation using caller-supplied queue/task metadata;
- an `email` token cannot observe, claim, or settle an `exports` job;
- FastAPI startup failure cleans up earlier resources and shutdown drains once;
- one-, two-, and four-process tests match the documented resource multiplication;
- API schemas never expose attempt fences on observer routes;
- auth overhead and HTTP overhead are measured separately in B9.

## Stage 4 — low-risk dogfood

Use outlabsAPI as the first real developer-experience proving ground, before broad production migration.

### Goals

- one or two low-consequence tasks;
- direct transactional enqueue where it improves correctness;
- embedded runtime only if the deployment process count and connection budget are explicit;
- dedicated worker comparison if embedded operation is less clear;
- real dashboards/logs, retry diagnosis, graceful deploy, and rollback rehearsal;
- collect every piece of custom glue needed by the host.

Any reusable glue becomes a package integration only after it is shown to be general. Host-specific compatibility remains in outlabsAPI.

### Exit gate

- at least two normal deploy cycles and one forced worker/database failure are recovered without manual table edits;
- task authors report that task declaration, enqueue, inspection, and retry are understandable;
- the observed connection/backlog/latency profile fits the envelope;
- rollback to the prior execution path is rehearsed.

## Stage 5 — QDarte pilot

QDarte is the first remote/synchronous-client proving ground.

### Topology

- QDarte calls the TaskQ HTTP facade with `httpx.Client`;
- it holds a narrow producer or worker service token, not database credentials;
- existing task endpoints can be preserved behind a QDarte-owned compatibility adapter;
- TaskQ returns the same typed protocol outcomes used by async clients.

### Rollout

1. inventory current task operations and error expectations;
2. map each to the canonical TaskQ command/result;
3. shadow safe reads and compare state;
4. canary a low-risk queue/lane;
5. exercise token expiry, HTTP timeout, retry, and TaskQ unavailability;
6. expand one queue at a time with a rollback switch;
7. remove the compatibility adapter only after callers use the TaskQ client directly.

### Exit gate

- synchronous transport passes the conformance suite;
- client retry never creates an unintended duplicate job or terminal transition;
- QDarte has no TaskQ database credential;
- authorization is queue-scoped and auditable;
- the previous route remains a tested rollback until the observation window closes.

## Stage 6 — Diverse staged cutover

Diverse is the highest-complexity migration and should follow proven SQL, HTTP, auth, and operations.

### Required corrections before migration

- replace caller-authoritative queue/task settlement fields with database lookup;
- align the scaffold's functions and routes to the versioned TaskQ protocol;
- apply the hardened owner/search-path/grant model;
- use package migrations rather than a divergent embedded schema history;
- map existing lane allowlists after queue authorization;
- measure current and projected connection pools across API/worker replicas.

### Rollout

Keep the existing cutover runbook's staged/canary approach, adding:

- a protocol-compatibility adapter at the boundary;
- audit comparison of authoritative queue/task metadata;
- schema/version verify as a deployment preflight;
- worker credentials scoped by queue/lane;
- a dedicated fault drill before each higher-risk lane;
- release-envelope comparison against actual Diverse peak/backlog measurements.

### Exit gate

- all selected lanes operate through package-owned contracts;
- no host table DML or host-private TaskQ function remains;
- backlog age, retry/lease recovery, connection use, and database load stay in envelope;
- rollback is demonstrated at the route and worker level;
- old TaskQ schema/function code is removed only after the observation window and backup verification.

## Stage 7 — composition release (`0.2`)

Build only after single-task operation is stable in real systems:

- atomic follow-ups with strict validation and bounded fan-out;
- dependency/workflow state and inspection;
- schedules with `skip`, `latest`, and bounded-all backfill;
- completion handles/long polling;
- payload upcasters and registered resource injection if not already mature;
- additional uniqueness modes one at a time, each with a transition/security/benchmark spec.

Workflows should reuse the same job/attempt kernel. Do not introduce a second execution state machine.

### Exit gate

- every workflow edge is either durably accepted or visibly fails the parent settlement;
- no fan-out truncation;
- schedule occurrence identity makes tick replay safe;
- graph inspection is bounded and safe for operators;
- redrive semantics across dependencies are explicit and tested;
- upgrade from `0.1` with live jobs is rehearsed.

## Stage 8 — scale and operations (`0.3`)

- partitioned archive/retention based on measured table growth;
- external concurrent maintenance with dry run and singleton locking;
- completion/dashboard experience;
- optional rate/resource admission based on a demonstrated workload;
- optional read-only MCP adapter over safe diagnostic contracts;
- PostgreSQL 19 optimizations after stable release and matrix qualification.

### Exit gate

- archive/maintenance does not break foreground performance envelope;
- the 24-hour soak has bounded bloat, WAL, memory, and connection trends;
- every new privileged surface passes the role/authorization suite;
- operational tooling never requires direct table edits for normal recovery.

## Feature triage from the current plan

| Feature | `0.1` | Later | Reason |
|---|:---:|:---:|---|
| Typed enqueue results | Yes |  | Core clarity |
| Reject/existed idempotency | Yes |  | Core production safety |
| Replace/preserve/by-args uniqueness |  | `0.2+` | Needs complete mutation/dependency semantics |
| Handler Complete/Retry/Snooze/Cancel | Yes |  | Core worker experience; Cancel must be fenced |
| Queue profiles | Yes |  | Simple defaults plus operator control |
| NOTIFY + poll | Yes |  | Low latency without correctness dependency |
| Same-queue failed state + redrive | Yes |  | Operational minimum |
| Redirect dead-letter queues |  | `0.3` if needed | Routing/auth/retention complexity |
| Blueprints/namespaces |  | Reassess | Stable task objects and profiles may cover the need |
| Retry value surface | Yes |  | Predictable handler control |
| Test helpers/inline transport | Yes |  | Development velocity |
| Soft stop/drain | Yes |  | Deployment correctness |
| Migration break channel | Yes |  | Operational recovery |
| SQL packaging conventions | Yes |  | Core distribution contract |
| Embedded FastAPI worker | Yes, opt-in |  | Good dogfood/small-service path with clear process caveat |
| Workflows/dependencies/follow-ups |  | `0.2` | Important, but not needed to prove core |
| Cron schedules |  | `0.2` | Needs backfill policy |
| Partitioned archive |  | `0.3` | Ship when measured growth justifies it |
| Exact max queue depth |  | Reassess | Expensive/racy without serialized counters |

## Release definition of done

A capability is done only when all applicable items are true:

- normative state/authorization/error contract exists;
- SQL migration and verify manifest exist;
- privilege and migration tests pass;
- direct and HTTP transports conform where exposed;
- sync and async clients behave consistently where supported;
- metrics, trace attributes, logs, and operator inspection explain it;
- failure/replay/shutdown behavior is tested;
- benchmark workload and accepted envelope impact exist;
- upgrade compatibility and rollback/forward-recovery are documented;
- FastAPI/OpenAPI and OutLabsAuth integrations remain optional from Core;
- an example demonstrates the simple path without hiding important behavior;
- existing-system rollout and removal plan is known if the capability replaces host code.

## Immediate next work

1. Review and decide D-01 through D-12.
2. Inventory QDarte, Diverse, and outlabsAPI runtime/database/auth constraints in one matrix.
3. Freeze the state-transition and typed-outcome tables.
4. Write ADR-002/003/004/006/010 before the first migration.
5. Build the ephemeral PostgreSQL harness and security-role fixture.
6. Implement the migration ledger, roles, and the smallest enqueue/claim/fenced-complete vertical slice.
7. Measure that slice before adding workflows, schedules, or archive complexity.

That sequence gets TaskQ to real usage quickly while protecting the contracts that make it trustworthy.
