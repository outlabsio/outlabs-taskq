# ADR-022 — Trusted worker side-effect reporter

**Status:** Accepted 2026-07-21
**Resolves:** S5-QD-CV-CQ-01

## Context

The worker kernel deliberately keeps the active attempt identity and fence out
of `JobContext`. A handler can observe its job, payload, headers, progress,
and cancellation state, but cannot inspect or serialize the current attempt.
That boundary prevents handlers from retaining, logging, or replaying a fence.

Some host integrations have a server-owned result boundary which must verify
that the package attempt is still current before it mutates a host domain. The
QDarte contact-verify bridge is one such boundary: it heartbeats the package
attempt, loads the authoritative package projection, then applies an
idempotent host-domain effect. A regular handler cannot call it without an
attempt identity. Replacing `WorkerService` with a host-owned raw claim/settle
loop would duplicate cancellation, heartbeat, replay, and unsafe-sync exit
semantics that the worker kernel is intended to own.

## Decision

1. `WorkerService` may be constructed with an optional **trusted side-effect
   reporter**. It is runtime-owned, not a task handler, is not registered in a
   `TaskRegistry`, and receives the active package attempt only inside the
   worker process. The reporter receives an immutable internal attempt record
   (`job_id`, `attempt_id`, authenticated worker id, queue, and job type) plus
   a bounded JSON-object request. It never receives a database credential.
2. A `JobContext` gains one async `report_effect(request)` capability only when
   the service has configured a reporter. It exposes neither an attempt id nor
   a fence, and rejects use after cancellation, ownership loss, or settlement
   terminal state. The worker validates the request's JSON-object shape and
   size before invoking the reporter. Handlers retain the existing
   fence-free/public context projection.
3. The reporter may retry a transport response loss for the *same* report
   request while the worker still owns the attempt. It must not settle the job;
   `WorkerService` remains the sole owner of terminal settlement and its
   verb-fixed replay policy. A report failure is normalized to the handler's
   declared retry/non-retry policy, never to a different settlement verb.
4. A host side-effect handler must make its own durable effect identity and
   replay probe authoritative. Before it invokes an external effect it asks the
   reporter for the host's stable-effect state; after the effect it reports the
   result through the same reporter capability. A replayed/reclaimed job can
   therefore observe a committed prior host result without repeating the
   external action. The reporter validates any host request against the
   authoritative package row; request data never chooses queue, type, entity,
   or place authority.
5. This is a Python worker lifecycle extension only. It changes no SQL
   function, migration, grant, Protocol-v1 command, HTTP facade path, or
   client wire model. It does not weaken the `JobContext` fence boundary.

## Consequences

- The extension requires deterministic unit tests for fence absence, reporter
  ordering, report response-loss retry, cancellation/ownership loss, terminal
  settlement replay, and resource cleanup; real SQL/HTTP evidence is required
  before a host uses it for an external-effect lane.
- A host can expose only its own internal result adapter behind this reporter;
  it must not add a generic worker-fence endpoint or a raw database credential.
- QDarte CV-04 may implement exactly one closed contact reporter and local
  harness after this decision. CV-05 must prove both committed-report response
  loss and hard-kill/reclaim with the stable-effect probe, including no duplicate
  observable provider action.
