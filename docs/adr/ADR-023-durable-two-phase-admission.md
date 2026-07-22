# ADR-023 — Durable two-phase admission

**Status:** Accepted 2026-07-22
**Resolves:** S5-QD-C6-CQ-03

## Context

The existing `taskq.enqueue` idempotency authority intentionally covers only
active jobs. After a job settles, the same `(queue, idempotency_key)` may create
new work. That is the right behavior for ordinary task enqueue, but it cannot
implement an integration boundary whose durable response must survive planning
changes, process loss, settlement, and retry.

QDarte exposed the distinction. Its direct contact-verification path checks a
canonical key before it computes a volatile candidate plan. The first package
exercise admitted a job successfully, but an identical replay recomputed the
plan before it could reach `taskq.enqueue`; changed planning inputs caused a
host validation error instead of the authoritative `existed` result. A
read-before-enqueue lookup cannot repair this because the active job may settle
between the lookup and insert.

This is not QDarte-specific. Any integration that performs bounded planning,
quota checks, pricing, fan-out selection, or other volatile work before
enqueue needs a durable admission receipt distinct from active-job deduplication.

## Decision

1. Protocol v1 document revision **1.0.8**, amendment 15, and SQL contract
   **0.1.5** add a queue-native two-phase admission capability. Immutable
   migration `0007_admission_reservations.sql` is its sole SQL implementation
   vehicle. The wire major remains `1`.
2. Admission identity is `(queue, idempotency_key)`. Before planning, a caller
   supplies a required SHA-256 intent fingerprint and a fresh opaque UUID
   handle. The fingerprint binds the key to stable business intent without
   storing the request. The handle identifies one planning owner and is not a
   worker fence, credential, or job id.
3. `reserve_admission` atomically returns one of three outcomes:
   `reserved` for the current handle, `pending` for a competing handle with the
   same intent, or `admitted` with the durable job id and immutable receipt.
   An admitted replay therefore never recomputes a plan. A different intent
   under a retained key is a typed `TQ409 idempotency_mismatch`.
4. `finish_admission` locks the reservation, validates the same current,
   unexpired handle, creates exactly one job through the existing enqueue
   semantics, attaches that job to the admission row, and commits the bounded
   receipt plus a database-computed SHA-256 of the canonical job+receipt JSON
   in the same database transaction. A canonical-content-identical response-loss
   replay returns `existed` with the same job id and receipt; changed finish
   content is typed `TQ409 finish_mismatch`. Backpressure or any rollback leaves
   the reservation pending and creates no job.
5. Admission identity is separate from `jobs_idem_uq`. A reserved finish does
   not race an ordinary active-key enqueue and does not change ordinary
   enqueue's active-only reuse semantics. Jobs created by this path carry an
   internal `admission_id`; the public reservation key remains in the admission
   ledger rather than being overloaded into `jobs.idempotency_key`.
6. `cancel_admission` cancels only the current unadmitted reservation. It never
   cancels or releases an admitted job. Expired/cancelled reservations may be
   reacquired; admitted receipts remain authoritative for their database-clock
   retention window and at least while their job row exists. Cleanup is
   bounded and housekeeper-owned.
7. All three commands require the existing queue-scoped `enqueue` action and
   SQL `taskq_producer` capability. Authentication and queue authorization
   precede body/fingerprint/handle processing. No new IAM action or operator
   credential is introduced.
8. The exact models, bounds, outcomes, errors, retry rules, retention behavior,
   and race matrix live in the Protocol, Function Manifest, and Durable
   Admission Reservation Specification. Unknown command fields are rejected;
   receipt data is a bounded non-sensitive JSON object and is durable state,
   never a request echo inferred by the facade.
9. Per ADR-020, a bridge runtime first grows its closed supported SQL-contract
   set to `{0.1.2, 0.1.3, 0.1.4, 0.1.5}` while exposing no admission commands.
   Only after that bridge is the deployment and rollback floor may 0007 be
   applied. A feature runtime may mount the admission commands only after
   startup proves SQL 0.1.5 and capability `admission_reservations`; older
   databases receive no accidental undefined-function path.

## Consequences

- The feature is a general producer primitive. QDarte becomes its first
  integration, not its owner, and its temporary compatibility adapter remains
  eligible for deletion when the direct queue retires.
- The official SQL and HTTP clients gain low-level reserve/finish/cancel calls
  plus a bounded orchestration helper. Automatic retries reuse the same handle;
  they never mint one inside a transport retry loop.
- Migration 0007 adds the private admission ledger, internal job link, three
  composites, three producer functions, bounded cleanup in the existing
  housekeeper path, and exact capability metadata. It changes no existing
  function identity or ordinary enqueue outcome.
- Fresh install and the complete 0001→0007 chain must pass on PostgreSQL 16 and
  18. Required evidence includes concurrent reserve/finish races, response loss,
  expiry/takeover, cancellation, intent mismatch, backpressure rollback,
  receipt retention, authorization, SQL/HTTP parity, catalog/grant parity, and
  resource cleanup.
- Applying 0007 raises the database rollback floor to the bridge release. This
  ADR authorizes library implementation and isolated local proofs only; it does
  not authorize a production migration or QDarte production cutover.
