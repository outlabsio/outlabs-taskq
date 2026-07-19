# ADR-017 — Final manifest-backed wire corrections

**Status:** Accepted 2026-07-18
**Resolves:** R5-CQ-A, R5-CQ-B, and R5-09; amends Transport Protocol v1 as document revision 1.0.4

## Context

The round-5 contract audit found two adopted-base promises that SQL contract 0.1.2 cannot produce.
First, the Protocol described a general job-list operation as “operator-minimal” even though the
Function Manifest and migration catalog contain no `list_jobs` function or safe list projection.
Second, the single-enqueue response promised `created_at`, while `taskq.enqueue` returns only
`(job_id, created)`. A facade timestamp, client clock, or follow-up observer lookup would fabricate
or mix authority rather than expose the SQL command's result.

The same audit found that ADR-016 settled invalid request-id handling only below Tier 0. Whether an
invalid supplied correlation value is echoed, when it is rejected, and which identifier appears in
the rejection are wire behavior and must be explicit in the Protocol itself.

The Function Manifest is senior for 0.1 SQL specifics. These are Protocol drafting errors, not
reasons to add a function, widen a grant, or invent a response field.

## Decision

1. **General job list is deferred out.** `GET /taskq/v1/jobs` has no active 0.1 success contract.
   H-08 owns its reactivation through Growth §4 / R2-16: an exact observer-granted projection,
   redaction rules, authorization, keyset cursor, supporting indexes, and bounded EXPLAIN evidence
   must be frozen before implementation. The reserved path still returns a typed `TQ501`
   capability-inactive envelope, but is excluded from generated success models, OpenAPI operation
   discovery, official client methods, and SQL/HTTP success-parity vectors. This is the same
   deferred-out posture as ADR-015 queue detail.
2. **The historical “operator-minimal” wording is not a latent surface.** Protocol amendment 3 is
   retained only as amendment history and explicitly superseded by this decision. H-08 and the
   Function Manifest state unambiguously that no `list_jobs` function or list route exists in 0.1.
3. **Single-enqueue response fields match SQL exactly.** The response envelope's outcome is exactly
   `created | existed`; `data` contains exactly the authoritative `job_id`. The queue is implied by
   the canonical path. `created_at` is removed, and no payload, schedule, queue, job-type, or other
   request echo appears in the result. In particular, an `existed` response never represents the
   current request's values as durable row truth. A caller needing timestamps or stored state uses
   the separately authorized job-detail command.
4. **Invalid request IDs never become correlation values.** An invalid supplied
   `Taskq-Request-Id` is treated as absent for correlation selection: the server mints a lowercase
   UUID, uses it in the response envelope and header, and returns `TQ422` only after authentication.
   The invalid bytes are never echoed in a response or copied to diagnostics. Bad credentials plus
   an invalid correlation header therefore return the authentication error with a minted request
   ID, not an unauthenticated validation oracle.
5. Protocol document revision advances to **1.0.4**; the wire-major header remains `1`. SQL
   contract 0.1.2, the Function Manifest's function identities and grants, and migrations
   0001–0003 remain unchanged.

## Consequences

- H-13 can derive exact response fields and capability disposition without guessing or performing
  a hidden database read.
- Reserved deferred paths remain protocol-shaped negative capabilities without advertising a
  success surface.
- Correlation rejection is bounded, authenticated, and non-reflective.
- This decision changes documentation only: no SQL, migration, grant, or application source is
  added or modified.
