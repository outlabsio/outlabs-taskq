# ADR-005 — One versioned protocol; SQL/HTTP transport parity

**Status:** Accepted 2026-07-18
**Resolves:** D-11

## Context

Route sketches diverge across the family: spec §14 (`POST /taskq/{queue}/claim`, `/taskq/jobs/{id}/…`), the Diverse cutover runbook (`/api/v1/taskq/jobs/claim`), the authorization doc's table, and design-review 01 (`/taskq/v1/queues/{queue}/claims`). Each was illustrative; none was frozen. Two transports without one contract is how hand-mirroring (DCP 7.14) comes back.

## Decision

1. Before kernel implementation, publish **one versioned, transport-neutral protocol document**: canonical command names (`enqueue, claim, heartbeat, complete, fail, release, snooze, cancel_running, get_job, list_jobs, control_queue`), request/response Pydantic models, the exact SQL-outcome → HTTP status/body mapping, stable machine-readable error codes (no exception-text matching), an idempotency/retry table per command, authoritative-vs-assertion fields (ADR-006), and additive-compatibility rules.
2. Both clients implement a small **`TaskqTransport`** protocol; a single conformance suite (harness T6/L2) runs the same behavioral cases against direct SQL and the FastAPI facade and compares domain outcomes.
3. HTTP surface direction: **command-oriented and versioned from day one** (`/taskq/v1/...`), queue in the path for queue-addressed commands (claims, enqueue-by-queue, queue controls) — this is what queue-scoped authorization keys on. Diverse's legacy `/api/v1/taskq/*` paths survive the strangler as a host-mounted compatibility prefix, not as the protocol.
4. Until the protocol doc exists, every route listing in the family is **illustrative** — the specs now say so where they sketch routes.

## Consequences

- Stage-0 deliverable (roadmap): the protocol doc blocks the first public migration.
- Facade route shape changes after workers ship are major versions (brief §8.2 stands).
- Sync (QDarte) and async HTTP clients are both first-class (brief open decision 7).
