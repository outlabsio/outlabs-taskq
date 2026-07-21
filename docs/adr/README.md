# taskq — Architecture Decision Records

> Accepted 2026-07-18, resolving the design-review decision log (`../design-review/02-critical-findings.md`, D-01..D-12) with three carve-outs adjudicated in review: the 0.1 janitor trigger (ADR-009/010), concurrency caps kept in 0.1 (ADR-009), and schedule backfill recognized as already-specified (ADR-009).
>
> ADRs are the decision authority. The normative specs (Unified Design Spec, extraction brief, authorization doc, harness doc, borrowed features) carry the detail and were amended in the same pass — where an older passage survives that contradicts an ADR, the ADR wins and the passage is a doc bug.

| ADR | Title | Resolves |
|---|---|---|
| [001](./ADR-001-product-boundary.md) | Product boundary: durable task queue, not a message bus | review 01/05 |
| [002](./ADR-002-fixed-schema-sql-ownership.md) | Fixed `taskq` schema; SQL functions own correctness | D-09 |
| [003](./ADR-003-fencing-typed-outcomes.md) | Attempt fencing and typed replay outcomes | reaffirmation |
| [004](./ADR-004-migrations-canonical.md) | Ordered migrations are canonical; snapshot is generated | D-07 |
| [005](./ADR-005-transport-parity.md) | One versioned protocol; SQL/HTTP transport parity | D-11 |
| [006](./ADR-006-permission-grammar-authoritative-lookup.md) | outlabs-auth permission grammar + authoritative authorization lookup | D-01, D-06 |
| [007](./ADR-007-atomic-followups-fenced-cancel.md) | Lossless atomic follow-ups; fenced handler cancel | D-04, D-05 |
| [008](./ADR-008-fastapi-lifespan-process-model.md) | FastAPI lifespan composition and process model | D-10 |
| [009](./ADR-009-first-release-scope.md) | First-release scope and deferred policies | D-08, D-12 |
| [010](./ADR-010-db-roles-security-definer-maintenance.md) | Database roles, SECURITY DEFINER hardening, maintenance split | D-02, D-03 |
| [011](./ADR-011-housekeeper-role-credentials.md) | Housekeeper role, deployment credentials, version-aware maintenance (amends 010) | R2-04, R2-05 |
| [012](./ADR-012-null-boundaries-byte-safe-diagnostics.md) | Explicit-null boundaries and byte-safe stored diagnostics | R3 CQ-01, CQ-02 |
| [013](./ADR-013-effective-lease-in-claim-projection.md) | Effective lease duration in the claim projection | S2-CQ-01 |
| [014](./ADR-014-http-worker-presence.md) | Canonical HTTP worker-presence command | S3-CQ-01 |
| [015](./ADR-015-defer-queue-profile-read.md) | Defer queue-profile read to the designed read-model slice | S3-CQ-02 |
| [016](./ADR-016-final-http-wire-normalization.md) | Final request-id, queue-ensure, and worker-list wire normalization | S3-CQ-03 |
| [017](./ADR-017-final-manifest-backed-wire-corrections.md) | Final manifest-backed list, enqueue, and request-id wire corrections | R5-CQ-A, R5-CQ-B, R5-09 |
| [018](./ADR-018-operator-ui-tech-stack.md) | Operator UI tech stack: React + Vite + TanStack + Base UI | frontend stack lock-in 2026-07-20 |
| [019](./ADR-019-safe-read-model-reactivation.md) | Safe read-model reactivation: bounded job pages and versioned queue profiles | H-08, H-11, R2-16, R5-29 |
| [020](./ADR-020-supported-sql-contract-sets.md) | Supported SQL-contract sets for additive migrations | S5-CQ-01 |
| [021](./ADR-021-read-model-conformance-repairs.md) | Read-model conformance repairs and release compatibility | S5-CQ-02, S5-CQ-03, S5-CQ-04 |
| [022](./ADR-022-trusted-worker-side-effect-reporter.md) | Trusted worker side-effect reporter | S5-QD-CV-CQ-01 |

Format: Status / Resolves / Context / Decision / Consequences. Supersession happens by writing a new ADR, never by editing an accepted one.
