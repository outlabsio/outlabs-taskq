# taskq — Architecture Decision Records

This public distribution contains the accepted ADRs listed below. Numeric gaps mean that a record is
not part of the public repository; they are not broken links or evidence that a decision was
superseded. Supersession happens by writing a new ADR, never by silently editing an accepted one.

| ADR | Title | Resolves |
|---|---|---|
| [001](./ADR-001-product-boundary.md) | Product boundary: durable task queue, not a message bus | review 01/05 |
| [002](./ADR-002-fixed-schema-sql-ownership.md) | Fixed `taskq` schema; SQL functions own correctness | D-09 |
| [003](./ADR-003-fencing-typed-outcomes.md) | Attempt fencing and typed replay outcomes | reaffirmation |
| [004](./ADR-004-migrations-canonical.md) | Ordered migrations are canonical; snapshot is generated | D-07 |
| [007](./ADR-007-atomic-followups-fenced-cancel.md) | Lossless atomic follow-ups; fenced handler cancel | D-04, D-05 |
| [012](./ADR-012-null-boundaries-byte-safe-diagnostics.md) | Explicit-null boundaries and byte-safe stored diagnostics | R3 CQ-01, CQ-02 |
| [013](./ADR-013-effective-lease-in-claim-projection.md) | Effective lease duration in the claim projection | S2-CQ-01 |
| [014](./ADR-014-http-worker-presence.md) | Canonical HTTP worker-presence command | S3-CQ-01 |
| [015](./ADR-015-defer-queue-profile-read.md) | Defer queue-profile read to the designed read-model slice | S3-CQ-02 |
| [016](./ADR-016-final-http-wire-normalization.md) | Final request-id, queue-ensure, and worker-list wire normalization | S3-CQ-03 |
| [017](./ADR-017-final-manifest-backed-wire-corrections.md) | Final manifest-backed list, enqueue, and request-id wire corrections | R5-CQ-A, R5-CQ-B, R5-09 |
| [019](./ADR-019-safe-read-model-reactivation.md) | Safe read-model reactivation: bounded job pages and versioned queue profiles | H-08, H-11, R2-16, R5-29 |
| [020](./ADR-020-supported-sql-contract-sets.md) | Supported SQL-contract sets for additive migrations | S5-CQ-01 |
| [021](./ADR-021-read-model-conformance-repairs.md) | Read-model conformance repairs and release compatibility | S5-CQ-02, S5-CQ-03, S5-CQ-04 |
| [025](./ADR-025-followup-helper-return-shape.md) | Follow-up helper return shape uses existing enqueue projection | S5-QD-FR-CQ-01 |
| [028](./ADR-028-maintenance-schedule-http-boundary.md) | Package maintenance schedules are not HTTP resources | S5-QD-FR-CQ-05 |
| [030](./ADR-030-workflow-counter-lock-order.md) | Workflow counters preserve cancellation lock order | S5-QD-FR-CQ-06 |
| [036](./ADR-036-trusted-effect-fence.md) | Trusted host-effect fence | SQL contract 0.2.6 |

Format: Status / Resolves / Context / Decision / Consequences. Supersession happens by writing a new ADR, never by editing an accepted one.
