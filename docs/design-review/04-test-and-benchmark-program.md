# Test and benchmark program

The existing harness plan is one of TaskQ's strongest ideas. This proposal turns it into a release program with explicit correctness gates, reproducible performance evidence, and fast feedback for ordinary development.

## Quality contract

Performance never excuses a correctness failure. The hard invariants are:

- zero jobs accepted without a durable row after commit;
- zero double successful settlements for one attempt;
- zero settlements accepted from a stale fence;
- zero observed concurrency-cap overshoot;
- zero failure-budget charges for release/snooze;
- zero authorization grants based only on caller-supplied job metadata;
- zero privileged function execution by ungranted roles;
- zero silent loss of accepted follow-ups;
- every migration either completes once or leaves the prior schema usable;
- graceful shutdown stops claims before worker ownership is abandoned.

At-least-once execution does **not** promise that handler code is invoked only once. A worker may complete an external side effect and die before settlement. Tests should assert unique current ownership and fenced state transitions, not make the false claim that execution can never repeat. Handler documentation and examples must teach idempotent effects or transactional outbox patterns.

## Test layers

### L0 — pure model and API tests

Fast, database-free tests cover:

- Pydantic payload/upcast validation;
- task registration, aliases, and duplicate wire-name rejection;
- queue-profile resolution and `config explain` provenance;
- retry/backoff calculations at boundary values;
- typed result serialization and forward-compatible decoding;
- permission catalog generation using OutLabsAuth's real validator when the extra is installed;
- HTTP status/outcome mapping;
- metrics label cardinality rules;
- sync/async handler dispatch and cancellation behavior.

Use property-based generation for retry parameters, payload versions, queue names, and result round trips.

### L1 — SQL function contract tests

Each SQL function is tested against a real PostgreSQL instance as:

- the owner during installation only;
- each capability role for allowed calls;
- an untrusted application role for denied calls and object-shadow attempts.

Tests assert row state, attempt/event history, result code, budget counters, and replay behavior. They must call public functions rather than mutate tables, except for owner-only fixture setup in an isolated database.

### L2 — protocol conformance

Run one scenario suite against:

- direct SQL transport;
- FastAPI HTTP transport with a database-backed facade;
- a small deterministic model transport for client-only tests.

The suite compares domain outcomes, not incidental timestamps or transport-specific exception text. It covers enqueue, claim, every settlement, replay, stale fence, visibility, pause, retry/redrive, and authorization.

### L3 — concurrency and fault injection

Use independent processes/connections, not only asyncio tasks sharing one pool. Scenarios include:

- 2–100 workers claiming one queue;
- many producers racing the same idempotency key;
- workers racing heartbeat, completion, cancellation, and lease recovery;
- per-key concurrency under skewed and uniform key distributions;
- paused/draining queues during active claims;
- process kill before handler, during handler, after side effect, and during settlement;
- TCP connection drop, statement timeout, client cancellation, database restart/failover, and listener disconnect;
- long application transactions holding enqueued jobs invisible before commit;
- rollback after enqueue;
- notification loss with polling recovery;
- connection-pool starvation and deployment surge;
- PgBouncer-compatible modes if a supported deployment uses PgBouncer.

Fault tests must state which guarantees survive and which outcomes are intentionally at-least-once.

### L4 — migration and compatibility

For every supported release pair:

- clean install at version N;
- double invocation/lock contention;
- upgrade N to N+1 with queued, scheduled, running, failed, and archived jobs present;
- old client against new schema during the documented compatibility window;
- new client against old schema fails fast with a stable compatibility error;
- interrupted migration resumes or reports a deterministic operator action;
- `verify` detects changed signatures, ownership, grants, checksums, and missing indexes;
- package uninstall leaves application-owned data untouched and follows an explicit destructive procedure if schema removal is requested.

There is no automatic production downgrade unless a migration specifically implements one. Recovery is restore/forward-fix and must be rehearsed.

### L5 — FastAPI and authorization

Cover:

- lifespan composition, partial startup failure, shutdown drain, and dependency override;
- one and multiple ASGI processes;
- async and sync handlers without event-loop blocking;
- exact/specific/global/wildcard permissions;
- SimpleRBAC service tokens and EnterpriseRBAC system-integration policy;
- job-ID authorization from database metadata;
- bulk authorization preflight;
- safe OpenAPI and error bodies;
- authentication cache/snapshot behavior without authorization leakage;
- credential rotation/expiry during worker operation.

### L6 — soak and recovery drills

Nightly or release-candidate runs should include:

- 6-hour contention soak on the minimum supported PostgreSQL;
- 24-hour mixed workload on the primary performance runner;
- continuous enqueue/claim/settle while janitor/archive work runs;
- external concurrent maintenance on a populated database;
- database restart and worker fleet restart;
- backlog growth followed by drain;
- poison jobs and repeated lease expiry;
- metrics/traces/log collection under sustained load.

Track memory, connections, table/index size, dead tuples, autovacuum activity, WAL, latency, and throughput over time. A stable throughput number with unbounded bloat is a failed soak.

## PostgreSQL and Python matrix

Proposed initial database support is PostgreSQL 16, 17, and 18. PostgreSQL 19 can run an informational pre-release lane while it is beta; no `0.x` invariant should depend on it. If existing-system inventory requires PostgreSQL 15, add it as a tested supported version rather than assuming compatibility.

The Python matrix begins at the declared package minimum (currently 3.12) and includes each supported minor through the current stable release. A release should not claim a Python/PostgreSQL pair that never runs the SQL contract suite.

CI tiers:

| Tier | Trigger | Matrix/workload |
|---|---|---|
| PR fast | Every PR | L0; L1 on primary PG/Python; protocol/security smoke; microbench smoke |
| PR full label | Kernel/migration changes | L0–L5 on min/current PG and min/current Python |
| Main nightly | Scheduled | Full version matrix, concurrency faults, explain plans, 6-hour soak |
| Release candidate | Manual gate | Full matrix, upgrade paths, 24-hour soak, dedicated-runner benchmarks, recovery drill |

## Deterministic fixtures

Provide a public `taskq.testing` package:

```python
@pytest.fixture
async def taskq_harness(postgres_url) -> TaskqHarness:
    ...
```

Useful operations:

```text
harness.enqueue(...)
harness.run_one(task=...)
harness.drain(queue=..., limit=...)
harness.wait_for(status=..., timeout=...)
harness.expire_lease(job_id)
harness.disconnect_worker(worker_id)
harness.assert_no_stale_running()
harness.snapshot_job(job_id)
```

Time travel and direct state setup must use test-only owner functions installed only in the ephemeral test schema/database. Production APIs should continue to use the database clock and should not expose “set now” hooks.

An inline transport is useful for unit tests, but it must implement the same typed protocol and make its limitations obvious. It cannot be used as evidence that database locking, transactions, or notification behavior work.

## Stateful reference model

Build a small executable model of the job state machine. Generate command sequences such as:

```text
enqueue -> claim -> heartbeat -> release -> claim -> fail -> claim -> complete
enqueue -> claim -> lease_expire -> recover -> stale_complete
enqueue -> pause -> claim -> resume -> claim
```

After every SQL command, compare the externally visible SQL outcome and projected state to the model. Concurrency schedules remain separate real-database tests, but model-based sequential testing catches transition holes and replay inconsistencies cheaply.

## Benchmark workloads

Every benchmark declares dataset size, payload bytes, queue/key distribution, producer/worker count, connection limits, lease/retry settings, database version/config, hardware/container limits, warmup, duration, and repetitions.

### Core workloads

| ID | Workload | Primary measurement |
|---|---|---|
| B1 | Single-row enqueue | p50/p95/p99 latency; transactions/s; WAL/job |
| B2 | Contended idempotent enqueue | winner correctness; p99; lock wait |
| B3 | Claim + no-op complete | jobs/s; end-to-end age; SQL time/job |
| B4 | Claim + heartbeat + complete | heartbeat overhead; pool use |
| B5 | Backlog drain at 10k/100k/1m rows | throughput curve; query-plan stability |
| B6 | Skewed concurrency keys | cap correctness; fairness; lock contention |
| B7 | Mixed priorities/schedules | scheduling delay; starvation |
| B8 | Retry/lease-recovery storm | recovery rate; event/WAL amplification |
| B9 | HTTP facade versus direct SQL | transport overhead; auth cost; connections |
| B10 | Janitor/archive mixed with workers | foreground regression; bloat trend |
| B11 | NOTIFY enabled/disabled/disconnected | empty-queue latency; poll load; recovery |
| B12 | Sync handlers under FastAPI | event-loop delay; thread saturation |
| B13 | Migration/verify on populated schema | lock duration; service disruption |
| B14 | Graceful fleet shutdown | drain duration; released/expired claims |

### Measurements

Capture at minimum:

- jobs accepted, claimed, settled, replayed, and rejected;
- throughput and enqueue-to-start/end-to-end latency distributions;
- database CPU, I/O, connections, transaction rate, lock waits, buffer hits/reads;
- WAL bytes per accepted/settled job;
- table/index bytes, live/dead tuples, vacuum/analyze activity;
- client CPU/memory and event-loop delay;
- notification count, coalescing, listener reconnects, fallback polls;
- errors grouped by stable domain code.

Use `EXPLAIN (ANALYZE, BUFFERS, WAL)` on representative queries. Assert structural properties—expected index family, no unbounded full scan, bounded rows—not exact planner cost strings that vary by patch release.

## Performance gates

Do not invent impressive marketing numbers before measuring the actual hosts. Establish a pinned dedicated runner and commit a `performance-envelope.toml` after the first calibration.

Release gates should combine:

1. **Correctness:** every hard invariant remains zero-tolerance.
2. **Capacity floor:** the no-op claim/settle path sustains at least the greater of the existing 80 jobs/s planning floor or twice the highest measured production peak on the pinned profile.
3. **Regression:** median throughput does not fall more than the agreed percentage and p99 does not grow more than the agreed percentage versus the last accepted baseline without an approved explanation.
4. **Stability:** connection, memory, dead-tuple, and index-growth trends remain bounded during the soak.
5. **Fairness:** low-volume queues/keys make progress under the specified skew workload.

The exact regression percentages belong in the calibrated envelope, not prose. PR environments are noisy, so PR microbenchmarks should flag large regressions; only dedicated-runner results block a release on small changes.

## Result artifacts

Each run emits machine-readable JSON plus a short Markdown report containing:

```text
git revision
TaskQ/schema versions and checksums
Python/PostgreSQL versions
database and host fingerprint
workload manifest and seed
warmup/duration/repetitions
raw sample artifact URI
summary percentiles/rates/resources
correctness counters
EXPLAIN artifacts
comparison baseline and decision
```

Never commit a laptop result as a universal baseline. Laptop results are development signals; dedicated-runner results are release evidence.

## Peer comparisons

An optional, version-pinned comparison against the newer Python/Postgres queue library and the mature Python/Postgres task library can inform positioning and expose surprising overhead. It must use equivalent semantics and clearly state when a peer omits TaskQ behavior such as HTTP authorization or transactional application enqueue.

Peer throughput is not a regression gate. TaskQ's stable historical baseline is the gate.

## Harness repository layout

```text
tests/
  unit/
  model/
  sql_contract/
  protocol/
  security/
  fastapi/
  migrations/
  concurrency/
  faults/
benchmarks/
  workloads/
  datasets/
  explain/
  envelopes/
  reports/
scripts/
  benchmark
  soak
  collect-postgres-stats
```

The first kernel migration is not complete until L1, security-role tests, the reference-model skeleton, and B1–B3 exist. This keeps the harness from becoming post-release cleanup.
