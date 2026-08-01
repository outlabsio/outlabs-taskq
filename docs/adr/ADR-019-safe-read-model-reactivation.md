# ADR-019 — Safe read-model reactivation

**Status:** Accepted 2026-07-20
**Resolves:** H-08/H-11; R2-16; R5-29; the deferred-route decisions in ADR-015 and ADR-017

## Context

ADR-015 deferred queue-profile read because the 0.1.2 manifest had no
observer-safe backing. ADR-017 likewise deferred the general job list because
it had no fixed projection, cursor, index, or bounded-plan contract. The
existing 0.1 surfaces remain `get_job` and snapshot queue stats; neither is a
safe substitute for browser-style job visibility or a non-mutating profile
read.

The [Read Model Specification](../Task%20Queue%20Read%20Model%20Specification.md)
now fixes the minimum safe shapes. This ADR accepts that package and makes the
docs-first ordering binding. It does not authorize a UI, an all-queue browser,
an unbounded reporting API, a base-table observer grant, SSE, or host adoption.

## Decision

1. **Exact identities.** This decision creates Protocol v1 document revision
   **1.0.5**, Function Manifest and SQL contract **0.1.3**, and immutable
   migration **`0004_read_models.sql`**. Wire major remains `1`. Migration
   0004 is not written until the 0.1.3 Manifest entry below is canonical.
2. **H-08 activation shape.** `GET /taskq/v1/jobs` becomes a generated,
   queue-scoped `read` command only with required `queue` and finite
   `view=ready|running|finished`. It returns the exact 13-field observer
   projection, bounded keyset page, and no sensitive optional fields. There is
   no all-queue form, arbitrary status/job-type/time/text filter, payload
   inclusion, attempt/event timeline, or direct view fallback.
3. **Per-view capability dispositions.** `ready`, `running`, and `finished`
   are independently capability-gated. The Protocol records each capability
   and its exact `TQ501` negative response. A migration/release may activate
   only views that have their own PG16 and PG18 B9 proof; an unproven view stays
   a visible `TQ501`, never an implicit absence or an unbounded query.
4. **H-11 profile shape.** `GET /taskq/v1/queues/{queue}` becomes an
   observer-backed `read(queue)` projection of the fixed canonical profile plus
   `profile_version` and current non-promissory `paused`. It exposes no pause
   reason, worker, IAM, host, or raw-row data. `profile_version` increments
   only for canonical-profile changes, not pause/resume.
5. **Conditional update.** Existing
   `ensure_queue(name, profile, actor)` remains its three-argument bootstrap
   identity. A separately named
   `update_queue_profile(name, profile, actor, expected_version)` performs the
   row-locked compare-and-set. This avoids a PostgreSQL default-argument
   overload ambiguity and preserves existing direct-SQL callers.
6. **ETag and conflict matrix.** Profile ETags are exactly
   `"taskq-profile-<positive decimal version>"`; weak tags and `*` are invalid.
   An absent `If-Match` preserves current idempotent bootstrap behavior. A
   malformed header is `TQ422`. An exact current ETag conditionally updates.
   A stale ETag returns existing `TQ409`, `retryable=false`, typed reason
   `profile_version_conflict`, and error details containing **only**
   `current_version`; it returns no request echo, profile, or other row data.
7. **SQL/HTTP parity.** Direct SQL receives the same bounded page composite
   and profile projection as HTTP. A direct SQL client must not get a wider
   projection merely because it bypasses HTTP. The Manifest makes that sentence
   contractual; observer base-table grants remain forbidden.
8. **Docs-first ordering.** ADR-019, Protocol 1.0.5, Manifest 0.1.3, the
   accepted status of the Read Model Specification, and board disposition land
   together in a docs-only commit. Only then may migration 0004, generated
   models, transports, facade, clients, tests, and B9 evidence begin.

## Consequences

- R5-29 is closed by this package: Growth's former broad proposal now has an
  exact, contract-owned H-08/H-11 activation design. Future views remain new
  decisions, not query parameters.
- Fresh-install and 0001→0002→0003→0004 upgrade tests must run on PG16 and
  PG18. `verify()` and catalog parity must assert the new types, functions,
  grants, column, indexes, capability rows, and per-view negative vectors.
- Before either route is adopted by a host, the implementation must satisfy the
  independent SQL/HTTP/raw-row oracle, authorization/redaction, pagination,
  conflict, resource, and B9 plan gates in the accepted specification, then
  receive targeted independent review.
- Stage-4 retirement observation, its L1 ledger, the hard-kill gate, and all
  host producer/consumer behavior remain unchanged.
