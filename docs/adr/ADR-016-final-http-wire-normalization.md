# ADR-016 — Final HTTP wire-model normalization

**Status:** Accepted 2026-07-18
**Resolves:** S3-CQ-03; amends Transport Protocol v1 as document revision 1.0.3

## Context

The final H-13 derivation of Stage-3 request and response models found three details the adopted
Protocol-v1 text did not make implementable:

1. every response requires a `request_id` sourced from a validated correlation header or minted by
   the server, but the header name, grammar, length, and echo behavior were absent;
2. queue ensure promised a profile version even though SQL contract 0.1.2 returns no such value and
   H-11 defers optimistic concurrency; and
3. worker list promised a safe presence response without freezing a bounded projection, while the
   observer view contains operator-supplied metadata and process/network labels that cannot be
   forwarded blindly.

These are wire-contract decisions. Tier-3 facade code cannot choose them, and the Function Manifest
wins where the adopted Protocol draft promised SQL output that 0.1 does not have.

## Decision

1. **Canonical request correlation.** The optional request header is `Taskq-Request-Id`. An inbound
   value must be 1–128 ASCII characters and match `[A-Za-z0-9._:-]+`; invalid input is `TQ422`. If
   absent, the server mints a lowercase UUID string. The selected value appears in every JSON
   envelope's `request_id` and in the response `Taskq-Request-Id` header. It may flow only through
   bounded diagnostic fields and structured logs; it is never copied into an unbounded durable
   field. Official clients generate or accept it through the H-13 model rather than an ad-hoc
   header hook.
2. **Queue ensure has no 0.1 version.** Active `PUT /taskq/v1/queues/{queue}` returns its typed
   `created | updated | unchanged` outcome and the exact canonical profile from
   `taskq.ensure_queue`, with no version or ETag. This is a visible amendment to the adopted row,
   not silent field deletion. Supplying `If-Match` while H-11 is inactive returns `TQ501` rather
   than being ignored. H-11's future read-model ADR owns the version/ETag representation,
   comparison semantics, observer projection, and migration if one is required.
3. **Worker list stays declared but capability-gated.** `GET /taskq/v1/workers` remains a reserved,
   generated Protocol-v1 operation because its resource, global `read`/`control` authorization,
   and safe-presence purpose are settled. In 0.1 it has no success model and always returns typed
   `TQ501` until Growth §4/R2-16 freezes a bounded observer projection, redaction rules, cursor,
   authorization behavior, and query-plan evidence. Raw `worker_status` rows—especially hostname,
   pid, and arbitrary `meta`—must never be forwarded by the facade.
4. **Why the two read deferrals differ.** ADR-015 deferred queue detail out of H-13 entirely because
   the read model itself and its observer SQL backing were both absent. Worker list already has a
   settled command identity and an observer view, but lacks the public-safe projection. The reusable
   rule is: an undesigned command is deferred out; a settled command awaiting a safe projection may
   remain declared behind a typed capability gate. Thus H-13 excludes queue detail, while it emits
   the worker-list route/client method/OpenAPI error contract and negative `TQ501` conformance
   vector, but no success schema.
5. Operators needing interim worker visibility may query `taskq.worker_status` directly over SQL
   with the observer role under the database trust boundary. That does not make the raw view an HTTP
   response contract.
6. Protocol document revision advances to **1.0.3**; the wire-major header remains `1`. SQL contract
   0.1.2, the Function Manifest, grants, function count, and migrations 0001–0003 remain unchanged.

## Consequences

- Every generated response and client now has one bounded correlation carrier and deterministic
  absent-case behavior.
- Queue bootstrap reflects the exact SQL response instead of fabricating an optimistic-concurrency
  token.
- Worker visibility cannot leak raw presence metadata, while the stable declared capability gives
  future clients a typed feature-detection path.
- S3-00 can generate its active and gated HTTP surfaces without inventing wire semantics.
