# ADR-027 — Native recurring schedules and finite maintenance takeover

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-02C

## Context

QDarte's replacement inventory requires recurring interval work, while taskq
0.1–0.2.1 already owns ordinary one-time delay and retry through each job's
database `scheduled_at`. Reusing a recurring template job, computing due truth
from an application clock, or retaining a host scheduler would create a second
queue implementation instead of completing the native product.

PostgreSQL supplies the authoritative clock and serialization, but core
PostgreSQL intentionally has no cron parser. The schedule contract therefore
needs an honest boundary: SQL decides whether a definition is due and owns its
lease, while one package-owned deterministic calendar evaluator expands the
database-projected instant. SQL validates the finite expansion and atomically
enqueues the resulting keyed occurrences.

ADR-009 also requires the 0.1 hardwired daily janitor trigger to be replaced by
a seeded schedule when schedules activate. Sending janitor work to an ordinary
runner would collapse the runner/housekeeper privilege split. Allowing a
schedule to name an arbitrary function would be a privileged execution escape
hatch.

## Decision

1. Protocol document revision **1.0.11**, SQL contract **0.2.2**, and immutable
   migration **`0010_schedules.sql`** activate capability `schedules`. Wire
   major remains `1`.
2. A schedule is a durable operator-owned definition identified by a canonical
   bounded name. Its target is either:
   - `job`, containing one queue, job type, static payload/headers and ordinary
     enqueue profile; or
   - the one reserved seeded target `maintenance:janitor`.
   No caller can create or retarget a maintenance definition, and no target can
   contain a SQL/function name or dynamic payload factory.
3. A recurrence is exactly one of:
   - `interval`, an elapsed duration in `60..31_536_000` seconds anchored to the
     prior due instant; or
   - `cron`, a five-field package grammar evaluated in an IANA timezone.
     Nonexistent spring-forward wall times do not occur; ambiguous fall-back
     wall times occur once at the earlier instant (`fold=0`). Calendar
     evaluation is deterministic and receives no wall-clock input.
4. Creation is compile-first. SQL stamps `next_fire_at = now()` and
   `initialized = false`. The first housekeeper claim returns that database
   instant and the database `as_of`; the evaluator must return no occurrence
   and the first recurrence instant strictly after `as_of`. This initializes
   the definition without accidentally executing business or maintenance work.
5. Subsequent catch-up is exact:
   - `skip` enqueues nothing and advances to the first recurrence after
     database `as_of`;
   - `fire_once` enqueues only the latest due occurrence and advances to the
     first recurrence after `as_of`; and
   - `fire_all` enqueues the oldest due occurrences in order, at most
     `max_catchup` per successful fire. If more remain, `next_fire_at` remains
     due so later bounded claims continue the backlog.
   `max_catchup` is `1..100`. Every occurrence key is derived solely from
   schedule id and its UTC due instant.
6. Housekeepers claim at most 100 due definitions with `FOR UPDATE SKIP
   LOCKED`, a `5..300` second database lease and an opaque non-nil token. The
   claim projects database `as_of`, authoritative definition version,
   recurrence, target and current due instant. Client time never decides due
   truth.
7. `fire_schedule` verifies the live token and version, policy-shaped ordered
   occurrence list, bounds and strict next-fire advancement. It atomically
   inserts ordinary jobs and advances the schedule, or directly runs the
   reserved bounded janitor pass. It records enough canonical replay identity
   for response-loss replay of the last successful token to return the same
   result without another job or janitor pass.
8. `schedule_error` verifies the same token, stores only byte-bounded
   diagnostics, clears the claim and sets a database-time retry delay without
   advancing recurrence truth. It is also response-loss safe. Token mismatch,
   expired lease or definition change is a typed fenced outcome, never an
   implicit success.
9. Definition writes are operator-only. `put_schedule` uses create/exact-replay
   when no version is supplied and compare-and-set update when an exact positive
   version is supplied. A stale version is the existing non-retryable `TQ409`
   posture with current version only. Pause preserves recurrence position;
   resume resets to compile-first database time so paused wall time is never
   silently backfilled. Retirement is permanent and preserves the row and
   occurrence identity; there is no physical delete in 0.2.2.
10. HTTP authenticates before lookup. Existing-definition operations project
    only schedule name and authoritative queue, authorize `control` on that
    queue, then decode. Creation strictly decodes before it can know the queue,
    authorizes that queue, and then writes. A queue-changing update authorizes
    both old and new queues. The seeded janitor is SQL/CLI maintenance only and
    has no HTTP mutation route.
11. Public operator routes are finite schedule PUT, GET and retire commands.
    Housekeeper claim/fire/error commands are direct SQL only through the
    housekeeper pool. Ordinary producer/runner clients receive no schedule
    mutation or firing authority. Schedule profile output is exact and bounded;
    it excludes tokens, diagnostics, actors, internal timestamps and janitor
    internals.
12. Migration 0010 seeds the immutable daily UTC janitor definition, activates
    exact capability `schedules`, and disables the hardwired
    `claim_janitor_due` branch in the same transaction. Exactly one trigger can
    win. A later immutable metadata migration is required to deactivate
    schedules.
13. ADR-020's bridge set becomes
    `{0.1.2, 0.1.3, 0.1.4, 0.1.5, 0.2.0, 0.2.1, 0.2.2}` before migration 0010.
    Schedule routes and runtime loops remain absent without exact 0.2.2 plus
    capability `schedules`. Applying 0010 raises the database rollback floor to
    that bridge; production application remains a separate host decision.

## Consequences

- Retry delay remains one job-state primitive; recurring schedules do not
  duplicate worker retry policy.
- No local application clock, host cron process, dynamic payload callback or
  arbitrary maintenance function participates in durable due truth.
- Schedule firing is horizontally safe, bounded and replayable across SQL and
  HTTP producer paths.
- QDarte can delete its recurring-template tables and scheduler services rather
  than wrapping them.
- Acceptance requires fresh/full 0001→0010 chains on PostgreSQL 16/18,
  catalog/grant/metadata equality, every state/outcome, DST and catch-up
  vectors, definition/claim/fire races, response loss, skewed-clock proof,
  bounded plans, one-only janitor takeover, SQL/HTTP/fake parity, resource
  cleanup and installed-artifact evidence.
