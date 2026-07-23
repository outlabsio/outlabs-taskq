# ADR-024 — Native lossless follow-up activation

**Status:** Accepted 2026-07-22
**Resolves:** S5-QD-FR-02A; activates ADR-007's deferred 0.2 design

## Context

ADR-007 froze lossless follow-ups but SQL 0.1 intentionally rejects every
non-empty list with `TQ501`. FR-01 now proves that multiple QDarte task families
require successful settlement to create later work. Keeping that behavior in a
host service would preserve the legacy orchestration engine as a wrapper.

The activation must not turn a runner into a generic producer, lose children
after a successful parent, let a response-loss replay create duplicates, or
authorize cross-queue work through a credential that cannot run the target.

## Decision

1. Protocol document revision **1.0.9**, SQL contract **0.2.0**, and immutable
   migration **`0008_followups.sql`** activate capability `followups`. Wire major
   remains `1`; the existing complete route and SQL signature do not change.
2. The accepted ADR-007 transaction is implemented exactly: validate all
   children before mutation; settle parent/attempt and insert every child in one
   transaction; no savepoint, truncation or fail-open item; replay returns
   `already_settled` before child revalidation.
3. The child model is closed and typed as specified by the 0.2 Native
   Orchestration Specification §4. `step` is required and unique. The private
   inserter derives `chain:<parent_job_id>:<step>`; the caller cannot provide an
   idempotency key. Created and existing derived children are both success.
4. `taskq._enqueue_followup(uuid,text,jsonb,integer)` is owner-only. No
   application role receives EXECUTE. It applies ordinary enqueue validation
   except producer depth admission; follow-ups are continuations of accepted
   work and cannot make a finished parent re-execute due to child backpressure.
5. A worker registry declares finite child `(queue, job_type)` targets. Runtime
   startup rejects an incomplete declaration and the handler receives no
   generic enqueue client. HTTP completion authenticates and authorizes the
   parent queue before body decode, then authorizes `run` for every distinct
   resolved child queue before SQL. Any denial performs no settlement or child
   insert. Direct SQL retains the documented trusted runner-role boundary.
6. Deterministic invalid children raise `TQ422`; inactive non-empty follow-ups
   remain `TQ501` on pre-0008 databases. The worker's accepted anti-wedge path
   terminal-fails the parent as `invalid_followup` and soft-stops on version
   skew. No new TQ code or response shape is introduced.
7. ADR-020's supported set becomes
   `{0.1.2, 0.1.3, 0.1.4, 0.1.5, 0.2.0}` in the bridge. The bridge exposes
   typed follow-ups only when metadata contains `followups`; it never calls the
   private helper. Applying 0008 raises the database rollback floor to this
   bridge. Production application remains a separate host decision.
8. Migration 0008 preserves existing active capabilities and replaces metadata
   by exact equality with
   `admission_reservations`, `followups`, and `read_model_list_ready`.
   Deactivation requires a future immutable metadata migration.

## Consequences

- Completion can now be the only queue-native chain boundary; QDarte may delete
  its completion-time enqueue service as handlers migrate.
- A malicious or misconfigured HTTP runner cannot create work in a queue it is
  not authorized to run, while no producer grant enters the worker process.
- Worker releases, failures, snoozes and cancellations do not accept follow-ups.
  Terminal-failure domain reactions remain scheduled reconcilers because a
  reaper death executes no handler.
- Fresh/full 0001→0008 chains, dual-major catalog parity, SQL/HTTP/fake graph
  parity, response-loss and concurrency races, rollback-on-Nth-child, privilege
  equality, packaging and plan evidence gate implementation acceptance.
