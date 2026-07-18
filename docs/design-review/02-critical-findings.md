# Critical findings and proposed decisions

This document is the decision log for the review. “Critical” means the issue can affect correctness, security, operability, or a public contract. The evidence comes from the current TaskQ documents, the checked-out OutLabsAuth `0.1.0a24` implementation, the Diverse TaskQ scaffold, and official PostgreSQL/FastAPI documentation.

## Decision table

| ID | Priority | Finding | Proposed decision |
|---|---:|---|---|
| D-01 | P0 | Documented per-queue permission names are invalid in current OutLabsAuth | Use `taskq_{queue}:{action}` and validate the generated catalog with OutLabsAuth itself |
| D-02 | P0 | `SECURITY DEFINER` functions need an explicit hardening contract | Fixed schema, qualified objects, safe `search_path`, no public execute, capability-specific roles |
| D-03 | P0 | `REINDEX ... CONCURRENTLY` cannot run inside the proposed janitor function | Split transactional janitor work from out-of-transaction maintenance |
| D-04 | P0 | Follow-up savepoints and truncation can report success after losing intended work | Validate first and make parent completion plus follow-up enqueue atomic |
| D-05 | P0 | Handler `Cancel` lacks a fenced worker settlement operation | Add a fenced `cancel_running_job` contract; reserve operator cancel for operators |
| D-06 | P0 | The Diverse scaffold trusts caller-supplied queue/job type during settlement authorization | Resolve authorization metadata from the database row before mutation |
| D-07 | P0 | Full-schema install and migrations are both described as canonical | Ordered migrations are canonical; generate a schema snapshot and manifest from them |
| D-08 | P1 | The first release includes too many advanced features | Ship a small complete kernel, then workflows/schedules/archive refinements in capability releases |
| D-09 | P1 | Fixed-schema SQL is presented as configurable schema installation | Support only the `taskq` schema until alternative schemas are genuinely parameterized and tested |
| D-10 | P1 | FastAPI lifespan embedding can multiply workers and replace host lifecycle behavior | Provide composition helpers and explicit per-process connection/concurrency safeguards |
| D-11 | P1 | HTTP routes and settlement outcomes differ across documents | Freeze one versioned transport-neutral protocol and contract-test SQL and HTTP against it |
| D-12 | P1 | Several “advanced” policies hide surprising behavior | Defer replace uniqueness, redirect DLQs, and exact depth limits until their invariants are complete |

## D-01 — OutLabsAuth permission naming

The current authorization design uses names such as `taskq.email:run`. OutLabsAuth `0.1.0a24` accepts exactly one colon and permits only lowercase letters, digits, `_`, and `-` in each resource/action component. The dot makes the documented resource invalid.

Adopt this grammar:

```text
global:     taskq:{action}
per queue:  taskq_{queue}:{action}
wildcard:   taskq_{queue}:*
```

Examples:

```text
taskq:admin
taskq_email:enqueue
taskq_email:run
taskq_email:read
taskq_email:control
taskq_email:*
```

Queue names should already use the queue-name grammar `[a-z0-9_]+`, so permission resources can be generated without lossy escaping. A catalog test must pass every generated permission through OutLabsAuth's real permission-name validator. Do not maintain a look-alike regex in TaskQ.

The standard action set remains `enqueue`, `run`, `read`, `control`, and `admin`. Global permissions are optional shortcuts; production worker roles should normally receive only the queues they run.

## D-02 — `SECURITY DEFINER` hardening

PostgreSQL explicitly warns that `SECURITY DEFINER` functions must have a safe `search_path`, with attacker-writable schemas excluded and `pg_temp` last. Functions are also executable by `PUBLIC` by default unless privileges are revoked. See PostgreSQL's [CREATE FUNCTION safety guidance](https://www.postgresql.org/docs/current/sql-createfunction.html).

Every privileged TaskQ function should satisfy all of the following:

- be owned by a `NOLOGIN` role such as `taskq_owner`;
- be created with a fixed safe path, for example `SET search_path = pg_catalog, taskq, pg_temp`;
- fully qualify TaskQ tables, types, sequences, and called functions anyway;
- revoke default execution from `PUBLIC` in the same migration;
- grant execution to the smallest database capability role;
- never interpolate a schema, table, or function identifier from caller input;
- include privilege regression tests run as untrusted application roles.

Recommended database roles:

| Role | Capability |
|---|---|
| `taskq_owner` | Owns schema objects; `NOLOGIN`; never used by an application |
| `taskq_producer` | Enqueue and inspect its returned result; no claim/settle operations |
| `taskq_runner` | Claim, heartbeat, and fenced settlement operations |
| `taskq_observer` | Safe operational functions/views; no mutation |
| `taskq_operator` | Pause, resume, cancel, retry, redrive, and maintenance operations |

Per-queue authorization still belongs in the authenticated HTTP facade. PostgreSQL roles add function-level least privilege; they are not a replacement for queue-level policy.

## D-03 — Concurrent maintenance cannot be a janitor function

The unified design schedules `REINDEX INDEX CONCURRENTLY` from `taskq.janitor()`. PostgreSQL does not allow concurrent reindexing inside a transaction block, while a function always runs inside its caller's transaction. PostgreSQL's [REINDEX documentation](https://www.postgresql.org/docs/18/sql-reindex.html) requires each concurrent reindex to be its own transaction.

Split maintenance into two products:

1. `taskq.janitor(...)` performs bounded, transactional work: abandoned-attempt repair, terminalization, archive movement, event pruning, and limited deletes.
2. `taskq maintenance` is an external CLI/daemon command. It takes a database advisory lock, examines bloat/age thresholds, and executes `REINDEX ... CONCURRENTLY` outside a transaction. PostgreSQL 19's new concurrent repack can be selected only after PostgreSQL 19 is stable and tested; PostgreSQL 19 is still beta as of this review.

The maintenance command must log its plan, require explicit opt-in for expensive operations, expose a dry run, and be safe when two schedulers invoke it.

## D-04 — Follow-ups must not be lost

The current follow-up proposal catches an invalid child enqueue inside a savepoint, succeeds the parent, and records an event. It also allows excess follow-ups to be truncated. Both behaviors violate the “no lost accepted work” promise: a successful parent can mean that a requested child silently disappeared.

First-release settlement should use strict atomic behavior:

1. Validate the follow-up count and all task references before any parent state change.
2. Reject more than the configured maximum; never truncate. Large fan-out should enqueue a planner task.
3. Complete the parent and enqueue every follow-up in one database transaction.
4. Treat `created` and the intended idempotent `existed` outcome as success.
5. Roll back the settlement if any child is invalid or cannot be accepted.
6. For a deterministic handler bug, terminally fail the parent with an “invalid follow-up” error instead of declaring the workflow successful.

Derived child idempotency keys should make a transport retry safe. If future use cases genuinely require fail-open settlement, add a durable `followup_intents` outbox with a reconciler; an event that merely reports dropped work is not enough.

## D-05 — Handler cancellation needs fencing

An operator can cancel a queued or running job because an operator is intentionally overriding the worker. A handler returning `Cancel(...)` is different: it is a worker settlement and must prove ownership of the current attempt.

Add a function with the same replay and stale-owner semantics as completion and failure:

```text
cancel_running_job(job_id, attempt_id, worker_id, reason) -> SettleResult
```

The function accepts only the matching running attempt, returns `already_settled` for an identical replay, and returns `lost` for a stale attempt. The public Python `Cancel` result maps to this function. `cancel_job` remains an operator-only command and is never called by a worker result mapper.

## D-06 — Authorize from authoritative job metadata

The current Diverse scaffold includes queue and job type in settlement payloads and uses them for lane authorization before settling by job ID. A caller can lie about those fields. Any route whose path/payload identifies a job by ID must obtain the queue and task type from TaskQ itself.

Required route order:

1. authenticate the principal;
2. look up the job's authoritative authorization projection by ID;
3. authorize the principal for that queue/action and, if retained, worker lane;
4. invoke the fenced mutation, which repeats ownership validation atomically;
5. return the typed settlement outcome.

Caller-supplied queue/task type may be treated as an assertion and rejected if it does not match; it must never be the authorization source. Bulk operations must preflight authorization for every affected queue before mutating any item.

## D-07 — One installation source of truth

A full `schema.sql` containing types and objects is not naturally safe to rerun, while ordered migrations express upgrades. Describing both as canonical creates drift and makes host integration unclear.

Use this packaging contract:

- ordered, immutable package migrations are the source of truth;
- the installer records package version, migration checksum, and applied timestamp in `taskq.schema_migrations`;
- `taskq migrate` applies missing migrations under an advisory lock;
- `taskq verify` compares required objects, signatures, ownership, privileges, and checksums without changing state;
- a generated `schema.sql` snapshot exists for review, clean installs in test fixtures, and diffing—not for upgrading a live database;
- host Alembic migrations invoke a supported synchronous adapter or execute a version-pinned migration bundle; they do not improvise an async call from a sync migration context;
- application startup verifies compatibility but does not silently migrate production.

## D-08 — Reduce the first-release surface

The current plan combines a queue kernel, workflow engine, scheduler, archival subsystem, multiple uniqueness policies, and embedded runtime. This is a good destination but a risky first public contract.

The `0.1` release should include:

- install/verify/migrate/break-lock tooling;
- queue definitions and profiles;
- enqueue, claim, heartbeat, complete, fail, release, snooze, fenced cancel;
- idempotency with `reject` behavior;
- retry budgets, lease recovery, poison protection, pause/resume, retry/redrive;
- direct SQL and HTTP transports with identical results;
- worker runtime, FastAPI adapter, OutLabsAuth adapter;
- safe operational views, core metrics/traces, and the full correctness harness.

Schedules, dependencies/workflows, settle follow-ups, replace/by-args uniqueness, redirect DLQs, and partitioned archival can land in subsequent capability releases without changing the kernel's basic job/attempt protocol.

## D-09 — Fixed schema until proven otherwise

The current SQL and security model consistently reference `taskq`, while parts of the Python API imply an arbitrary `schema=` option. Dynamic schema installation expands every migration, privilege, query, and verification test.

Use the fixed `taskq` schema for `0.x`. Remove arbitrary schema configuration from the public promise. Reconsider multiple installations per database only when a real customer need justifies full parameterization and a dedicated security matrix.

## D-10 — FastAPI lifecycle and process multiplication

FastAPI recommends the lifespan context for startup/shutdown, and setting a lifespan means legacy event handlers no longer run. Mounted sub-app lifespans also do not automatically manage the main app. See FastAPI's [lifespan documentation](https://fastapi.tiangolo.com/advanced/events/).

TaskQ should expose a composable `TaskqRuntime` async context manager and a helper that explicitly wraps an existing lifespan. The helper must not replace a host lifecycle without making that behavior visible.

An embedded worker starts once per ASGI process. With `uvicorn --workers 4`, configured worker concurrency and connection pools multiply by four. Startup should emit the effective process-local and deployment-wide assumptions; configuration should allow embedded execution to be disabled independently of the router. The recommended production topology remains a dedicated worker process unless the embedded trade-off is intentional.

## D-11 — Freeze one HTTP/SQL protocol

Route shapes and response behavior differ among the current documents. Before implementation, publish a versioned protocol containing:

- canonical command names and request/response Pydantic models;
- the exact mapping from SQL outcomes to HTTP status/body;
- stable error codes, not exception-text matching;
- an idempotency/retry table for every command;
- authoritative fields versus caller assertions;
- compatibility rules for adding fields and outcomes.

Both the direct SQL client and HTTP client should implement a small `TaskqTransport` protocol. A shared contract suite runs the same behavioral cases against both transports. This gives existing systems a choice of credentials and topology without creating two products.

## D-12 — Defer surprising policies

Three advanced policies need more design before becoming public contracts:

- **Replace uniqueness:** mutating the payload of an existing job can conflict with dependency graphs, audit history, and a consumer that already observed the job. Keep `reject` in `0.1`; add replace/preserve after explicit state-transition rules exist.
- **Redirect DLQs:** keeping exhausted jobs failed in their original queue with lineage and explicit redrive is easier to operate. Queue redirection can follow after routing, authorization, and retention semantics are settled.
- **Exact max depth:** a count/offset check is both expensive and subject to off-by-one/concurrency errors without a serialized counter. Treat depth as an observed metric and producer backpressure signal in `0.1`; add a hard admission counter only if a real workload needs it.

## Acceptance checklist before kernel implementation

- [ ] Record D-01 through D-12 as accepted, changed, or rejected ADRs.
- [ ] Run every generated permission name through OutLabsAuth `0.1.0a24` tests.
- [ ] Prove a non-owner cannot execute or shadow privileged functions.
- [ ] Remove concurrent index maintenance from transaction-bound functions.
- [ ] Specify lossless follow-up and fenced handler-cancel outcomes.
- [ ] Specify authoritative authorization lookup for every job-ID route.
- [ ] Choose migrations as the sole install/upgrade history.
- [ ] Publish the `0.1` capability boundary and versioned transport contract.
