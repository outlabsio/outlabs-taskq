# ADR-014 — HTTP worker-presence command

**Status:** Accepted 2026-07-18
**Resolves:** S3-CQ-01; amends Transport Protocol v1 as document revision 1.0.1

## Context

The 0.1.2 Function Manifest has always exposed the runner command
`taskq.worker_heartbeat(text, text[], text, integer, text, jsonb)`. The completed worker service
uses that command before its first claim and periodically thereafter to publish advisory process
presence and receive a targeted shutdown signal. The Stage-3 remote-worker transport guarantee
also requires the HTTP facade to invoke the same command on behalf of remote workers; ADR-011 owns
the facade credential split, not this worker-presence command.

Protocol v1 defines per-job heartbeat and operator shutdown commands, but omitted the HTTP
worker-presence command. Route shape, authorization input, outcomes, and HTTP mapping belong to
the Tier-0 protocol under ADR-005; Stage-3 design cannot invent them.

## Decision

1. Transport Protocol v1 document revision **1.0.1** adds canonical
   `POST /taskq/v1/workers/heartbeat`. Its body carries `worker_id`, a non-empty distinct `queues`
   list, and optional bounded `hostname`, `pid`, `version`, and safe operational `meta`. It never
   accepts actor, credentials, job ids, attempt ids, payloads, headers, progress, results, errors,
   or fences.
2. The facade authenticates first, validates the complete body, authorizes `run` for **every
   distinct declared queue**, and only then invokes `taskq.worker_heartbeat` once. Authorization is
   an all-or-nothing preflight; no presence row changes when any queue is denied.
3. `worker_id` is a validated advisory label of 1–200 characters. It is not the principal. The
   authenticated subject remains the actor for authorization, audit, rate limiting, and logs; the
   request cannot supply or override that actor.
4. Success is HTTP 200 with exactly one typed protocol outcome: `continue` when the SQL response is
   false, or `shutdown_requested` when it is true. Both are successful command results, not errors.
5. **Worker presence and per-job heartbeat are different commands.** Worker presence extends no
   lease and carries no attempt fence. It exists only for advisory observability and drain
   signalling, is never a reclaim input, and must never be combined with or drift toward the
   fenced per-job heartbeat.
6. With a shared fleet credential, a caller can assert another advisory `worker_id` and may observe
   that label's targeted shutdown signal. This is benign by design: attribution remains advisory,
   responding to drain is always safe, and shutdown releases are budget-free. Deployments needing
   stronger attribution use per-worker credentials and bind labels to authenticated subjects as
   already required by Protocol H-04.
7. H-13's generated command table, OpenAPI, sync and async clients, conformance vectors, and
   SQL-versus-HTTP parity vectors include this command. Hand-written shadow route/client tables are
   forbidden.
8. This is additive. The protocol wire-major header remains `1`; the canonical document revision
   advances to `1.0.1`. SQL contract 0.1.2, the Function Manifest, function identity, grants, and
   migration chain do not change.

## Consequences

- S3-00 can freeze the facade, remote-worker client, and authorization design against a canonical
  presence command instead of improvising one.
- A remote worker receives the same typed drain signal as a database-direct worker without making
  advisory presence authoritative for ownership or lease recovery.
- Stage-3 acceptance must prove authenticate-before-authorize ordering, all-queue preflight,
  actor/label separation, both 200 outcomes, safe field bounds, fence absence, and SQL/HTTP parity.
