# ADR-032 — Queue-independent search and proxy control

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-CQ-20
**Amends:** ADR-022 and ADR-031

## Context

QDarte's `region_rescue_scope` handler performs three externally metered or
leased operations through the retiring queue client:

- grounded model search, protected by the existing provider reservation and
  usage-event service;
- search-API requests, protected by a durable quota/usage service; and
- browser-proxy sessions, protected by durable lease and health-event state.

Removing the old queue client without replacing those controls would silently
remove budget, failover, lease and usage guarantees. Retaining the client as a
wrapper would preserve the queue implementation that Stage 5 exists to delete.
Taskq admission reservations are also the wrong authority: they bind job
creation, not provider spend, search quota or proxy leases.

ADR-022 already supplies the trusted attempt-bound reporter boundary, and
ADR-031 proves the queue-independent reserve/settle pattern for model work.
The remaining controls need the same ownership and replay discipline without
becoming public taskq commands or arbitrary external-service proxies.

## Decision

1. `032` is the next free ADR identity. This decision amends only QDarte's
   private trusted-reporter contract. It creates no taskq Protocol route,
   Function Manifest function, SQL-contract revision or taskq migration.
2. Grounded region-rescue model work joins ADR-031's closed provider-control
   catalog as lane `region_rescue_grounded`, operation `grounded_search`.
   Provider execution remains in the worker.
3. ADR-022's private reporter gains exactly two closed control members:
   - `search_api_control` with `reserve | settle`; and
   - `browser_proxy_control` with `claim | settle`.
   They are reusable by a future native integration only after its lane,
   operation and authoritative input validation are added docs-first. They are
   not generic HTTP, search or proxy APIs.
4. A handler supplies only a closed lane/entity/operation identity and the
   bounded service option already present in its stored strict task input.
   Search reserve additionally carries a bounded positive unit estimate.
   Proxy claim carries only the planned proxy pool/member constraints.
   Settle accepts one closed outcome and bounded usage/health facts.
   No queue, job, attempt, worker, timestamp, caller idempotency key,
   credential, URL, header, body, arbitrary metadata or exception text is
   accepted from the handler.
5. The reporter binds the current taskq job, attempt and worker. The host
   authenticates, resolves the authoritative queue, authorizes `run` before
   body decode, and validates the member, lane, entity, operation and option
   against the stored strict native input before any control mutation.
6. PostgreSQL time owns reservation, lease, expiry, event and settlement
   instants. Stable logical identities derive from current job plus closed
   member/lane/entity/operation. Positive generations distinguish renewed
   authority after an expired unknown outcome. Same-attempt response replay is
   byte-stable. A different attempt cannot inherit live egress authority.
7. Reserve/claim and settle are row-locked. Exact canonical replay returns the
   same bounded receipt; a mismatched request fails closed. Settlement and its
   search-usage or proxy-health event commit in one transaction.
8. Worker loss after egress but before settlement expires by database time.
   The hold or lease is released, but the durable posture is
   `expired_unsettled` with unknown usage/health, never silently zero or
   healthy. A later attempt may acquire the next generation while the expired
   generation remains immutable.
9. Region rescue uses:
   - ADR-031 provider control for grounded model search;
   - `search_api_control` for metered search-API calls; and
   - `browser_proxy_control` for browser-proxy work.
   Inspect-before-egress and committed-effect replay skip external work.
   No old queue job, attempt, client, lifecycle service or table enters the
   native handler graph.

## Consequences

- Extraction must preserve the old search quota, failover, usage-event,
  proxy-lease and health-event behavior with before/after parity vectors.
- Catalog equality, authentication/authorization ordering, stale-attempt
  refusal, exact replay, mismatch, concurrency, database-time expiry,
  generation rollover, secret/error redaction and resource cleanup require
  executable evidence.
- A real hard-kill history must conserve each externally observed call as one
  settled event or one retained `expired_unsettled` generation. This does not
  waive FR-04's per-wave side-effecting hard-kill gate.
- No integration may add a new lane or option by passing arbitrary service
  data through these members. Such growth is a docs-first closed-catalog
  amendment.
