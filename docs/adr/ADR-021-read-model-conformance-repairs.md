# ADR-021 — Read-model conformance repairs and release compatibility

**Status:** Accepted 2026-07-20
**Resolves:** S5-CQ-02, S5-CQ-03, S5-CQ-04

## Context

ADR-019 froze the H-08/H-11 read-model identities, and ADR-020 established
closed SQL-contract compatibility sets for additive migrations. Generated
clients already ship the established `PUT /taskq/v1/queues/{queue}` response
envelope as `{ "profile": { ... } }`, while Protocol 1.0.5 accidentally
described that same command's success data as a flat profile. The profile
projection, ETag, and route identity are otherwise identical; changing the
decoder or adding an alternate identity would create an avoidable compatibility
split.

Separately, immutable migration `0004_read_models.sql` implements
`taskq.list_jobs` without first establishing that its queue exists. An active
view would therefore answer an authorized unknown queue with an empty page,
contradicting the already-frozen Protocol `TQ001` outcome and direct-SQL/HTTP
parity. Editing an applied migration would destroy the upgrade evidence that
the migration discipline relies on.

Finally, Protocol document revision 1.0.6 is already occupied by ADR-020's
compatibility-only amendment. The read-model envelope correction must take the
next sequential revision rather than overwrite that accepted decision.

## Decision

1. Protocol v1 document revision **1.0.7**, amendment 14, corrects the
   `PUT /taskq/v1/queues/{queue}` success shape. Its canonical data is
   `{ "profile": { ... } }`, where `profile` is the exact 13-field H-11
   projection and includes `profile_version`; successful responses retain the
   existing profile ETag. `GET /taskq/v1/queues/{queue}` remains flat. This is
   a documented drafting-error correction to the existing generated command
   identity, not a second route or a permissive dual-shape decoder. The
   generated-model and conformance oracle pin the envelope.
2. The H-11 `If-Match` grammar and absent, malformed, exact, and stale behavior
   remain unchanged. In particular, stale `TQ409 profile_version_conflict`
   carries only `{ "current_version": N }`; it contains no request echo.
3. SQL contract **0.1.4** reserves immutable migration
   `0005_read_model_conformance.sql`. It replaces only the body of the
   existing `taskq.list_jobs(text,text,integer,jsonb)` identity. After input
   validation it establishes queue existence before checking a view capability:
   an authorized unknown queue raises the established `TQ001` marker for both
   active and inactive views; an existing inactive view remains typed `TQ501`;
   an existing empty active view returns the existing bounded empty page.
   No new function, grant, composite, index, or wire outcome is introduced.
4. Per ADR-020, the bridge runtime's supported SQL-contract set grows to
   `{0.1.2, 0.1.3, 0.1.4}`. The historical exact `{0.1.2}` rejection is
   retained and extended as a negative proof for 0.1.4. A
   `read_model_*` capability may be activated only when metadata is 0.1.4 or
   later. Applying 0005 raises that database's rollback floor to this bridge;
   applying it to production remains a separately authorized deployment after
   the bridge is its deployed and rollback baseline.
5. Migration 0005 requires fresh-install and complete `0001 → 0005` upgrade
   proofs, `verify()` and catalog-parity evidence, and the three-case vector on
   PostgreSQL 16 and 18. This ADR authorizes library implementation and those
   proofs only; it authorizes neither production migration nor host adoption.

## Consequences

- Protocol revision 1.0.7 remains wire-major 1. The SQL repair does not change
  its wire identities or version-error surface.
- The generated facade and official clients may implement the corrected H-11
  envelope only after the docs-first amendment and SQL 0.1.4 proof chain land.
- `ready` activation remains separately controlled by its B9 evidence and the
  later generated-surface/parity decision; `running` and `finished` remain
  inactive unless their own evidence later authorizes them.
