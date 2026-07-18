# Peer research addendum — July 2026

The existing peer research contains useful patterns from mature PostgreSQL queues. This addendum focuses on projects and current features that materially change TaskQ's design or positioning. It uses upstream repositories/documentation and was checked on 2026-07-18.

The goal is not feature parity. It is to borrow proven ideas while preserving TaskQ's differentiator: a secure SQL state machine, excellent typed Python ergonomics, transaction-native enqueue, and SQL/HTTP parity.

## Most important missing peer: the newer Python/Postgres queue library

the newer Python/Postgres queue library is the closest current Python/PostgreSQL peer. Its upstream `v1.2.0` release was published on 2026-07-15. The project demonstrates that a modern Python API can combine PostgreSQL transactional enqueue, `SKIP LOCKED`, LISTEN/NOTIFY plus polling, scheduling, per-entrypoint concurrency, asyncpg/psycopg support, dashboards/telemetry, testing utilities, and a completion-watcher pattern.

TaskQ should borrow:

- an ergonomic decorator/registry and a small worker command;
- notification as latency optimization with polling as correctness fallback;
- handler resource injection;
- a completion handle/watcher for callers that deliberately wait for results;
- an in-process test adapter whose limitations are explicit;
- read-only operational tooling after the core CLI/diagnostic contract is stable;
- upstream's bias toward observable worker behavior.

TaskQ should differentiate with:

- database-enforced attempt fencing and replay outcomes as a first-class contract;
- queue-scoped OutLabsAuth integration;
- an authenticated HTTP transport identical in semantics to direct SQL;
- application-transaction participation through SQLAlchemy;
- capability roles and a hardened `SECURITY DEFINER` surface;
- a documented failure budget distinct from claim attempts;
- a release-blocking fault/security/upgrade harness.

the newer Python/Postgres queue library should be added to future feature and benchmark reviews. Ignoring the closest Python peer would weaken API and positioning decisions.

## Peer map

| Project/category | What it proves | Borrow | Do not copy blindly |
|---|---|---|---|
| the newer Python/Postgres queue library | Python-native Postgres queue can be ergonomic and observable | Registry, resources, completion watcher, notification/poll strategy, test adapter ideas | Its exact schema/API; TaskQ needs its own fence/auth/transport contracts |
| the mature Python/Postgres task library | Mature Python/Postgres worker lifecycle and task declaration | Operational vocabulary, retry/scheduling UX, app integration lessons | Compatibility surface that compromises TaskQ's typed protocol |
| the Node/Postgres SQL-first worker | High-throughput SQL jobs, LISTEN/NOTIFY, queues, cron, backfill | Scheduler backfill policy and performance methodology | Node-specific API and marketing throughput without equivalent workload proof |
| the Go/Postgres job queue | Strong typed jobs and transaction-aware Postgres enqueue in Go | Typed job identity, uniqueness clarity, insertion APIs | Go-specific generic/codegen assumptions |
| the Node/Postgres queue library | Broad operational policy and mature queue controls | Retry/dead-letter/retention terminology | A kitchen-sink first release |
| the Postgres message-queue extension | Simple durable message semantics can live in a Postgres extension/library | Visibility/delete/archive clarity and a crisp message-queue boundary | Treating task execution and raw message consumption as the same product |
| a pure-SQL event-stream project | Postgres can also support a distinct append/read event-stream abstraction | Clear separation between task queue and event/fan-out stream | Adding pub/sub/event log semantics to TaskQ Core |
| an async Python task framework | Async Python task ecosystems value DI, pipelines, schedules, and lifecycle hooks | Typed task handles, dependency/resource ergonomics, testing UX | Broker portability and deep middleware stacks that erase Postgres advantages |
| a Postgres-backed durable-workflow platform | Postgres-backed durable workflows can integrate directly with FastAPI and global controls | Later workflow ergonomics and explicit concurrency/rate policy | Turning TaskQ into an application runtime/durable-execution framework |
| [FastAPI BackgroundTasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) | In-process background work has a useful but limited niche | Clear documentation of when TaskQ is necessary | Pretending an in-process task is durable after process loss |

## Patterns to add to the backlog

### P-18 — Typed task handles and payload evolution

Borrow the best typed-job experience from the Go/Postgres job queue/an async Python task framework while keeping Pydantic-native Python:

- `Task[InputT, OutputT]` returned by the decorator;
- one stable wire name independent of Python import path;
- explicit schema version and upcasters;
- `.enqueue()` and `.enqueue_many()` returning typed results;
- result/completion handle available without exposing worker fences.

This improves the simple path and should begin in `0.1`; full stored outputs and watchers can mature in `0.2`.

### P-19 — Completion handles

A producer sometimes needs to enqueue and later observe terminal state. Provide:

```python
handle = result.handle()
terminal = await handle.wait(timeout=30)
```

The direct implementation can use notification as a nudge plus bounded polling. The HTTP implementation can use long polling first; SSE is optional later. Waiting must not hold a database transaction or expose attempt IDs. High-cardinality watchers need connection and fan-out limits.

### P-20 — Runtime resource injection

Allow handlers to receive host-owned resources such as an HTTP client, mailer, or SQLAlchemy session factory. Resources are registered with the worker runtime and resolved locally; they are never job payloads. This borrows Python ergonomics without adopting a middleware framework.

Resource setup/teardown should compose with the host lifespan and be visible in worker health.

### P-21 — Read-only diagnostics contract

the newer Python/Postgres queue library's operational/dashboard direction suggests a useful sequence:

1. stable safe SQL projections;
2. `taskq inspect --json` and authenticated diagnostic HTTP models;
3. human dashboard;
4. optional read-only MCP adapter for operational investigation.

Do not let a dashboard or MCP server become a privileged table client. Mutations remain explicit operator commands with authorization and audit events.

### P-22 — Schedule backfill policies

the Node/Postgres SQL-first worker's cron experience reinforces that “run every interval” is incomplete without missed-run behavior. Each schedule should choose:

```text
skip          # run only future occurrences
latest        # enqueue the most recent missed occurrence
all_bounded   # enqueue up to N missed occurrences
```

The scheduled occurrence timestamp becomes the idempotency identity. Backfill is `0.2`, not a reason to delay the core worker.

### P-23 — Optional rate/resource admission

a Postgres-backed durable-workflow platform and other systems expose rate as distinct from concurrency. TaskQ should retain this distinction:

- concurrency caps the number currently running;
- rate limits starts within a time window;
- backpressure limits admission/backlog.

Only concurrency belongs in `0.1`. A later database token-bucket or start-window design must be benchmarked under contention and must not become a global hot row. External API providers may still be better protected by application-specific limiters.

### P-24 — Driver and transaction adapters

the newer Python/Postgres queue library supports multiple PostgreSQL drivers, but TaskQ should not duplicate the kernel per driver. Define one parameterized SQL command layer and initially support:

- async direct runtime transport on the chosen production driver;
- SQLAlchemy `AsyncSession` transaction participation;
- synchronous HTTP client for QDarte and similar systems;
- asynchronous HTTP client for FastAPI services.

Add psycopg/direct sync database support only when a host needs it and the protocol suite can cover it. Transport breadth is useful; untested adapter breadth is not.

## Patterns to keep out of Core

### General message-broker abstraction

Making Redis/RabbitMQ/SQS interchangeable would discard the transaction and SQL-function model that makes TaskQ valuable. Existing systems integrate through HTTP when they cannot share database credentials; they do not need a fake storage-neutral backend.

### Pub/sub and event streams

Task execution has ownership, retry budget, heartbeat, and settlement. Event streams have consumers, offsets, retention, and fan-out. Combining them creates ambiguous deletion and delivery promises. Use a Postgres outbox plus an event tool when an application needs both.

### Exactly-once side effects

Attempt fencing guarantees exactly one accepted state transition, not exactly one external email/payment/API call. TaskQ should offer idempotency keys, transactional enqueue, examples, and outbox patterns—but never market impossible general exactly-once execution.

### Unbounded middleware stacks

Lifecycle hooks, resources, metrics, and tracing are valuable. A middleware order that can rewrite payloads, retries, settlement, and exceptions at arbitrary stages makes correctness impossible to reason about. Prefer narrow protocols with documented ordering.

### Load-bearing pre-release PostgreSQL features

PostgreSQL 19 adds attractive capabilities such as `INSERT ... ON CONFLICT DO SELECT` and concurrent repack, but [PostgreSQL 19 is still in beta](https://www.postgresql.org/about/news/postgresql-19-beta-1-released-3313/) during this review. Keep optimizations behind server-version detection and preserve a supported 16–18 implementation.

## Positioning after the review

A concise public comparison should be honest:

> Choose TaskQ when PostgreSQL already anchors your Python services and you want typed transactional enqueue, database-enforced leases/fences, a first-class FastAPI surface, and queue-scoped OutLabsAuth permissions. Choose a traditional broker when you need broker-scale fan-out, cross-language routing, or independent queue infrastructure; choose an event stream when replay and many consumers are the primary abstraction.

This positioning is narrower than “best queue for everything,” but it makes the product memorable and credible.

## Research cadence

Before each minor release:

- check current releases of the newer Python/Postgres queue library, the mature Python/Postgres task library, the Go/Postgres job queue, the Node/Postgres queue library, the Node/Postgres SQL-first worker, the Postgres message-queue extension, an async Python task framework, and a Postgres-backed durable-workflow platform;
- record only changes that affect a TaskQ decision or benchmark;
- pin versions in any comparison result;
- prefer upstream documentation/repositories;
- distinguish borrowed concept, independently designed contract, and compatibility requirement;
- remove peer-inspired backlog items that no longer solve a real OutLabs use case.
