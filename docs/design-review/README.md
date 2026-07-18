# OutLabs TaskQ design review

**Review date:** 2026-07-18

**Status:** Additive proposal; the existing design documents remain unchanged and remain the current source of truth until these decisions are accepted.

This review keeps the strongest part of the current plan—a PostgreSQL-owned correctness kernel—and reshapes the surrounding product so the default path is tiny, the advanced path is explicit, and integrations do not weaken the queue's safety properties.

## Verdict

The project should be built. The existing design already has unusually good foundations:

- database-clock leases, attempt fencing, and `FOR UPDATE SKIP LOCKED` claims;
- explicit retry, release, snooze, poison, and redrive semantics;
- transactional enqueue and settle-time chaining;
- a Python client that returns typed outcomes instead of hiding conflicts;
- HTTP isolation for systems that should not hold database credentials;
- a serious correctness and benchmark harness from the start.

The design is currently broader than a first release needs to be, and seven issues should be resolved before SQL becomes a public contract. None requires abandoning the architecture. They require tightening it.

## Recommended product shape

TaskQ should describe itself as a **Postgres-native durable task queue for Python services**, not as a general-purpose message bus.

The experience should have three layers:

1. **TaskQ Core** — the SQL state machine, Python task API, worker, installer, CLI, and operational views.
2. **TaskQ Integrations** — FastAPI router/runtime, OutLabsAuth authorization, SQLAlchemy transaction participation, metrics, and tracing.
3. **TaskQ Advanced** — workflows, schedules, uniqueness policies, archive partitions, and other features that can mature without bloating the first successful path.

The default user should need to understand only a task, a queue, and `.enqueue()`. Operators and platform teams should still be able to control leases, concurrency, retry policy, credentials, retention, and transport.

## Decisions at a glance

| Area | Recommendation |
|---|---|
| Product boundary | Durable at-least-once task execution; deliberately not pub/sub or an event log |
| Correctness owner | PostgreSQL functions in the fixed `taskq` schema |
| First release | Kernel, worker, typed enqueue/settle, HTTP facade, OutLabsAuth adapter, ops views, test harness |
| Python API | Typed task objects with Pydantic input/output models and transaction-aware enqueue |
| Transports | Direct SQL and HTTP with one semantic protocol and the same typed outcomes |
| FastAPI | Composable runtime/lifespan and router; never silently replace a host lifespan |
| Queue authorization | Facade-level queue permissions plus database function-capability roles |
| Worker credentials | OutLabsAuth service tokens by default; enterprise system-integration keys only where enabled |
| Follow-ups | Atomic and lossless; invalid follow-ups fail settlement instead of being dropped |
| Maintenance | Transactional janitor work in SQL; concurrent reindex/repack in an external maintenance command |
| Installation | Ordered migrations are canonical; a generated schema snapshot is review material, not an upgrade mechanism |
| Quality | Correctness gates on every PR, performance smoke tests on PRs, full contention/soak/fault suites nightly and before releases |

## Reading order

1. [01 — Product and architecture](./01-product-and-architecture.md) defines the product, API, layers, contracts, and release scope.
2. [02 — Critical findings](./02-critical-findings.md) records the issues that should be decided before implementation.
3. [03 — FastAPI and OutLabsAuth](./03-fastapi-and-outlabsauth.md) gives a concrete, compatible integration design.
4. [04 — Test and benchmark program](./04-test-and-benchmark-program.md) turns correctness and performance into release gates.
5. [05 — Peer research addendum](./05-peer-research-addendum.md) adds current peers and identifies what to borrow or reject.
6. [06 — Delivery roadmap](./06-delivery-roadmap.md) sequences the work and existing-system pilots.

## Relationship to the existing documents

These review documents do not replace the existing specifications. They propose amendments and a build order. In particular:

- the SQL-first invariants in the unified design should remain normative;
- the borrowed-feature documents remain a useful idea backlog, but should not all be first-release commitments;
- the authorization document needs a naming correction for OutLabsAuth `0.1.0a24`;
- the test harness should be expanded with security, upgrade, failure-injection, and connection-budget suites;
- the cutover plan should be retained, with an earlier low-risk dogfood stage.

Once the decisions in [02 — Critical findings](./02-critical-findings.md) are accepted, they should be expressed as short ADRs and folded into the normative specifications in one deliberate pass.
