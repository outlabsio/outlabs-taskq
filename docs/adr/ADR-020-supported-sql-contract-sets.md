# ADR-020 — Supported SQL-contract sets for additive migrations

**Status:** Accepted 2026-07-20
**Resolves:** S5-CQ-01

## Context

The SQL contract revision is recorded by `taskq.get_contract_meta()` and the
runtime has correctly treated an unsupported value as a fail-closed startup
error. Before this decision, that check was an exact `0.1.2` comparison.
Migration `0004_read_models.sql` is additive but changes the recorded revision
to `0.1.3`; applying it under an otherwise supported `0.1.2` runtime would
therefore strand that runtime on its own database.

This is the normal expand → migrate → contract shape. It must be represented
as an explicit compatibility policy, not as a loose prefix/range comparison or
an undocumented one-off exception. The existing meta and version-error wire
shapes are already sufficient: the database reports its exact revision and the
runtime decides whether that revision is supported.

## Decision

1. A runtime release declares a **closed supported SQL-contract set**. Startup
   succeeds only when the database-reported `contract_version` is a member of
   that exact set. Prefix, ordering, and semver-range comparisons are forbidden.
   A value outside the set continues to raise the existing typed version error
   with the existing wire surface.
2. The bridge runtime for this change declares `{0.1.2, 0.1.3}`. The preceding
   runtime's historical set remains `{0.1.2}`; its rejection of `0.1.3` is
   retained as a regression proof. This decision changes neither protocol
   wire-major nor `/meta`/version-error payloads.
3. The bridge exposes **no read-model surface**. Its capability set remains
   empty; all three `read_model_list_*` views and profile read are absent from
   generated commands, facade routes, and clients. It does not call a 0004
   function under either supported metadata value. H-08/H-11 remain gated by
   their later generated-surface, parity, and B9 decisions.
4. Applying 0004 raises that database's runtime rollback floor to the bridge
   release by design. A pre-bridge runtime cannot boot against its `0.1.3`
   metadata. Applying 0004 to production is a separate, later gated deployment
   decision: it is not authorized by S5-RM-01 and may occur only after the
   bridge is both the deployed and rollback baseline. That future decision must
   re-evaluate the Stage-4 `3f50b7d` rollback-tag obligations, because that tag
   cannot boot after production receives 0004.
5. The required release sequence is bridge release → bridge deployment → 0004
   migration → bridge rollback floor. S5-RM-01 authorizes only the library
   bridge, migration implementation, and PG16/PG18 proofs; it authorizes no
   production database migration.

## Consequences

- The Manifest records the general supported-set rule for future additive SQL
  revisions. Every future contract bump must inventory exact pin sites,
  explicitly declare its set, and prove both accepted and rejected metadata
  vectors.
- The bridge proof covers fresh 0004 install and full `0001 → 0004` upgrade on
  PostgreSQL 16 and 18, plus a simulated pre-bridge `{0.1.2}` pin rejecting
  `0.1.3` with the typed error.
- CLI and verification tooling do not decide runtime compatibility; they keep
  reporting and verifying the database's exact SQL revision. Their documented
  inventory status is therefore "no startup pin," not an implicit widening.
