# ADR-015 — Defer the queue-profile read route

**Status:** Accepted 2026-07-18
**Resolves:** S3-CQ-02; amends Transport Protocol v1 as document revision 1.0.2

## Context

The adopted Protocol-v1 base lists `GET /taskq/v1/queues/{queue}` as an active 0.1 command backed
by a “safe queue projection.” SQL contract 0.1.2 has no such function or view. Its observer surface
contains `get_queue_stats` and the safe operational views, but observers cannot read the base queue
configuration table. The operator-only `ensure_queue` command mutates configuration and cannot
honestly back a GET.

This is a contradiction between the two Tier-0 contracts, not an implementation choice. The
documentation constitution states that the 0.1 Function Manifest wins for 0.1 SQL specifics. The
route is therefore a Protocol drafting error, not a missing Manifest function. Adding a one-off
projection now would also pre-empt H-11 and the pending Growth §4 / R2-16 read-model design, which
requires exact projections, redaction, authorization, query bounds, and plan evidence before a
public read model freezes.

## Decision

1. Transport Protocol v1 document revision **1.0.2** moves
   `GET /taskq/v1/queues/{queue}` from the active route table into the explicit deferred-routes
   section. The route identifier remains reserved; this is a visible amendment, never a silent
   deletion from the locked contract.
2. In 0.1 the reserved route has no queue-detail model or successful outcome. A request receives
   the registered `TQ501` capability-inactive response. It never falls back to base-table access,
   the operator credential, `ensure_queue`, or `get_queue_stats`.
3. H-13's active generated command table, OpenAPI operation set, sync and async clients, and
   SQL/HTTP conformance vectors exclude the deferred route. A negative capability vector pins its
   `TQ501` posture so no official consumer can bind to an accidental queue-detail shape.
4. H-11 owns reactivation. It must arrive through the Growth §4 / R2-16 read-model design with an
   exact observer-granted projection, field/redaction contract, authoritative queue-scoped
   authorization, bounded query plan, optimistic-concurrency semantics where applicable, and its
   own contract amendment before implementation.
5. The honest 0.1 posture is intentionally asymmetric: observers read operational state through
   `get_queue_stats`; administrators declare configuration through idempotent
   `PUT /taskq/v1/queues/{queue}` / `ensure_queue`, whose response contains the canonical profile.
   Profile fields are admin-adjacent bootstrap configuration, so this is sufficient for 0.1.
6. This is additive contract governance. The wire-major header remains `1`, the canonical Protocol
   document advances to revision `1.0.2`, and SQL contract 0.1.2, the Function Manifest, function
   count, grants, and immutable migration chain remain unchanged. There is no migration 0004.

## Consequences

- Stage 3 can implement only SQL-backed active routes without privilege broadening or read-time
  mutation.
- Round 5 can audit the deferral and negative capability behavior as a contract decision rather
  than mistaking an absent client method for an omission.
- A future queue-detail endpoint cannot ship until its read model is designed with the same
  projection, authorization, redaction, and plan discipline as other public reads.
