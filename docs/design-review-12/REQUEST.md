# Round 12 — QDarte contact-verify consolidation review request

## Authority and scope

Review the proposed Tier-3 [Stage 5 QDarte Contact Verify Consolidation
Specification](../Task%20Queue%20Stage%205%20QDarte%20Contact%20Verify%20Consolidation%20Specification.md)
against the locked Transport Protocol v1, Function Manifest 0.1.4, ADR-006,
ADR-007, ADR-010, ADR-011, ADR-020, `TASKS.md`, the Stage 5 QDarte Pilot
Specification, and the current QDarte API/worker/runtime sources.

This is a design review. Do not edit any repository. Write the response only as
new files in this directory. The response is historical once recorded.

The review may authorize only a later implementation specification for one
QDarte side-effecting contact-verification lane. It may not authorize source,
SQL, migration, credential, worker, route, deployment, production,
retirement, cloud, or existing-queue-state change.

## Independently derive before trusting the proposal

Start from source, not this document. Reconstruct:

1. the direct QDarte `taskq` migration/catalog, current grants, direct API
   client, producer routes, worker routes, and current local queue state;
2. the generic `qdarte_ops` ledger and its relationship (or lack of one) to
   contact verification;
3. the actual worker handler's external effects and all result-write paths;
4. the idempotency identity at the domain write, including whether it changes
   on attempt/reclaim; and
5. the fixed package schema/migration/role assumptions that make a shared
   database safe or unsafe.

Call out every proposal claim that source cannot establish. Do not accept a
test name, a status paragraph, or a previous pure-pilot result as proof of a
side-effecting property.

## Required attack program

### A. Catalog and topology

- Compare direct `taskq` tables, function identities, grants, and metadata
  with the package catalog. Determine whether a package migration can coexist
  in the same database without overwriting or colliding with incumbent objects.
- Verify the direct queue's current data/queue posture read-only. Distinguish
  an empty local queue from evidence that a production migration is safe.
- Recompute package runtime/worker connection arithmetic for a future separate
  database; reject an assumption that the pilot database or API superuser
  identity can be reused.

### B. Exclusivity, compatibility, and rollback

- Trace every contact-verify enqueue route and every worker entrypoint. Prove
  or disprove that the proposed `legacy` / `draining` / `package` modes can be
  exclusive and startup-validated.
- Challenge the no-dual-publish, no-active-import, no-cross-backend-fallback
  rules with lost request/response and partial-drain scenarios.
- Determine whether the advertised public response can remain compatible
  without exposing package SQL models, fences, or internal errors.
- Attack each rollback phase. In particular, reject any post-publish rollback
  that could duplicate external verification by recreating a direct job.

### C. Side effects and idempotency

- Trace network verification, domain writes, contact-method updates, and usage
  counter mutations. Identify the durable effect identity actually used.
- Run or reason through: result-write commit followed by lost response; worker
  death before/after result write; lease expiry/reclaim; duplicate worker;
  upstream retry; and a second process attempting the same planned entity.
- Decide whether the proposed stable package-job-plus-entity effect key is
  necessary and sufficient. Demand an exact oracle for each durable side effect.
- Verify that a pure handler hard-kill proves none of these side-effecting
  claims by itself.

### D. Security and operational ownership

- Verify role isolation: no superuser facade, no package worker database
  password, no operator capability in runtime pools, no broad legacy worker
  allowlist, no direct base-table read bypass.
- Review secrets/logging, result authentication, bounded diagnostics, and the
  provenance of every credential used by proposed preflight/cutover actions.
- Evaluate backup/restore, rollback-floor, immutable release, and direct-ledger
  archival requirements as concrete gates rather than promises.

## Acceptance rubric

Return one of:

- **READY:** the decision is precise enough to open a separate implementation
  specification, with any bounded follow-ups named and owned;
- **BLOCKED:** list minimal docs-first or source-backed preconditions;
- **CONTRACT QUESTION:** only if Tier-0 contracts themselves conflict or lack a
  required package behavior. Stop rather than inventing a wire or SQL change.

For each finding, include severity, evidence (path and line or executed
counterexample), violated authority, smallest remedy, required regression or
oracle, and the exact owning slice. Do not treat a local empty queue as
production evidence, and do not broaden scope to retirement or a production
rollout.
