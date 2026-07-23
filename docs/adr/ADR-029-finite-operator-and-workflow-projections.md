# ADR-029 — Finite operator and workflow projections

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-02D

## Context

QDarte's closed replacement inventory needs bounded visibility into running and
finished queue work plus one workflow detail page. It does not need the retired
attempt/event stream, a generic reporting API, or an all-queue browser.

ADR-019 already contracts finite `running` and `finished` queue views but keeps
them inactive until each has its own PostgreSQL 16/18 B9 proof. ADR-026 creates
workflow identity and members but deliberately exposes no observer read.
Computing exact workflow counts by scanning every member would be unbounded,
while copying QDarte's former status/event model into taskq would preserve the
legacy system under a new name.

## Decision

1. Protocol document revision **1.0.13**, SQL contract **0.2.3**, immutable
   migration **`0011_finite_projections.sql`**, and metadata-only activation
   migration **`0012_activate_finite_projections.sql`** define FR-02D. Wire
   major remains `1`.
2. Existing queue-scoped `running` and `finished` pages retain ADR-019's exact
   job projection, cursor and authorization. Migration 0011 adds only the
   queue-and-order indexes needed for independent B9 evaluation. Each view
   remains `TQ501` unless migration 0012 activates its existing capability
   after its own PostgreSQL 16/18 proof.
3. The sole new read command is an exact workflow page. It returns a redacted
   workflow profile, six exact state counts, and a UUID-keyset page of redacted
   members. It exposes no params, creator, payload, headers, result, progress,
   error, attempt, event, fence, token, worker identity, or provider evidence.
4. Exact workflow counts are maintained in an owner-private one-row-per-
   workflow relation by an owner-private job-state trigger. The counter row is
   separate from the workflow row so job mutation does not invert the existing
   workflow-to-job lock order. Migration 0011 backfills counts before enabling
   the trigger. Counts may never be inferred by an unbounded request-time scan.
5. Workflow member order is job UUID ascending. The opaque cursor binds the
   workflow id and last member id, is decoded only after authentication and
   authorization, and cannot widen scope. A page contains at most 100 members.
6. HTTP authenticates, obtains the existing safe workflow authorization
   projection, authorizes `read` on **every** authoritative declared queue, and
   only then validates the cursor or calls SQL. Denial and absence retain the
   facade's hiding posture.
7. Direct SQL returns exactly the HTTP projection and receives no raw-table or
   wider observer access. The new public identity is
   `taskq.get_workflow_page(uuid,integer,uuid)` under `taskq_observer`.
8. The exact-job timeline proposed in the Tier-3 program is **not activated**.
   QDarte's final replacement inventory deletes attempt/event parsing and needs
   only canonical job detail plus the projections above. Timeline and arbitrary
   reporting remain new docs-first growth decisions, not FR-02D leftovers.
9. ADR-020's supported set grows to include `0.2.3` before migration 0011.
   The bridge exposes no new projection without exact capability metadata.
   Applying 0011 raises the rollback floor to that bridge.
10. Migration 0011 leaves all new and existing FR-02D capabilities inactive.
    After B9 and parity evidence, migration 0012 replaces capability metadata by
    exact equality with only the proven winners. Deactivation likewise requires
    a later immutable metadata migration, never manual DML.

## Consequences

- QDarte can replace its legacy dashboard and workflow-status reads without a
  compatibility model, wrapper, event parser, or queue-history migration.
- The public surface stays finite: two queue views and one exact workflow page.
- Acceptance requires fresh/full 0001→0012 chains on PostgreSQL 16/18,
  catalog/grant/metadata equality, exact counter-transition races, cursor and
  redaction parity, authorization-before-decode, million-row plans, artifacts,
  and proof that inactive or rejected projections remain typed `TQ501`.
