# ADR-031 — Queue-independent LLM provider control

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-CQ-12, S5-QD-FR-CQ-13; amended by the approved
S5-QD-FR-CQ-11 photo-verification binding
**Amends:** ADR-022

**Amended 2026-07-23:** CQ-13 distinguishes same-attempt response replay
from cross-attempt provider authority and freezes generation rollover after
unknown-cost expiry.

**Amended 2026-07-23:** CQ-11 adds the closed `photo_verification` lane with
operation `verify`; it uses the same reserve/settle state machine and does not
create a lane-specific provider wrapper.

## Context

QDarte's native task replacement must preserve its durable provider-budget,
failover and usage-event guarantees without retaining the old queue client.
The first affected native family, `tripadvisor_classification_scope`, performs
a metered model call between a stable domain-effect inspection and application.
The old worker reaches provider admission and settlement through its retiring
queue API surface.

Neither an unmetered direct call nor reuse of taskq admission reservations is
valid. The former removes an existing cost-control guarantee. The latter binds
job creation, not provider/token authority. Moving the provider call into
taskq settlement would also violate the separation between queue state and
host-domain or provider effects.

ADR-022 already supplies the correct trusted boundary: the worker owns the
active attempt identity and lets a handler submit a closed bounded request
without seeing an attempt id or fence.

## Decision

1. `031` is the next free ADR identity. ADR-031 amends the private reporter
   contract only; Protocol v1 stays at document revision 1.0.13, so creating a
   Protocol amendment-log number would falsely imply a public wire change.
2. ADR-022's trusted reporter may carry a closed
   `llm_provider_control` member in addition to host domain effects. It is a
   QDarte-private reporter contract, not taskq Protocol v1, not an arbitrary
   provider proxy, and not a new SQL function or migration.
3. The member has exactly two operations:
   - `reserve`: closed lane, entity and operation identity, provider, model,
     request fingerprint, and a bounded positive token estimate;
   - `settle`: the opaque reservation receipt plus one closed
     `success | transport | capacity` outcome and bounded usage.
   It accepts no queue, job, attempt, worker, timestamp, idempotency key,
   credential, prompt, provider body, header, arbitrary metadata, or exception
   text from the handler.
4. The reporter binds the current job, attempt and worker. The host
   authenticates, resolves the current task's authoritative queue, and
   authorizes `run` before reading or decoding the body. After decode it
   revalidates the current attempt and validates the closed lane, entity,
   operation, provider, model, fingerprint and token estimate against the
   stored strict task input.
5. The host derives a stable logical control identity from the current taskq
   job, closed lane, entity and operation. Each provider-call generation has
   its own reservation and idempotency identity derived from that control
   identity, its positive generation number, the reporter-owned attempt and
   canonical reserve request. PostgreSQL `clock_timestamp()` owns reservation,
   expiry, event and settlement time. Exact reserve replay by the **same**
   attempt returns the same receipt; a changed canonical request fails closed.
   A different current attempt cannot inherit live provider-egress authority:
   it receives the typed retryable `reservation_pending` receipt carrying only
   the reservation identity and database-stamped expiry, and performs no
   provider call.
6. Settlement row-locks the reservation, verifies ownership and stores a
   canonical settlement hash in the existing bounded reservation metadata.
   The reservation transition and exactly one provider usage event occur in
   the same database transaction. Exact replay returns the same bounded
   receipt; a hash mismatch fails closed.
7. A reservation that passes its database-stamped expiry without settlement
   becomes `expired_unsettled`. Its budget hold is released so it cannot
   orphan capacity, but its usage posture remains **unknown cost**, never zero
   usage. It is retained for audit and is not rewritten as a successful,
   failed, or free provider call. The first current attempt that observes the
   expiry is stored as its expiry observer and receives the typed
   expired-unsettled receipt; exact replay by that attempt returns the same
   receipt. A later attempt may create the next numbered generation and spend
   a new budget unit, while the expired generation remains immutable.
8. Native LLM handlers use this order:
   inspect domain effect → reserve provider → call provider in the worker →
   apply domain effect → settle provider. A committed domain apply discovered
   after response loss skips the provider call. A process lost after provider
   egress but before domain apply cannot transfer its live reservation to a
   replacement attempt. Reclaim waits through `reservation_pending`, observes
   the old generation as `expired_unsettled`, then may reserve a new generation.
   Taskq makes no exactly-once claim for external provider reads, but QDarte
   never silently rewrites possibly incurred cost as known or zero usage.
9. The closed lane/operation pairs currently are
   `tripadvisor_classifier/classify` and `photo_verification/verify`. The
   control family is shared by every native LLM lane. Per-lane provider
   wrappers, old queue job/attempt/client/lifecycle imports, caller clocks and
   caller idempotency are forbidden.

## Consequences

- Extraction must preserve the existing reservation, failover and usage-event
  behavior with before/after parity vectors.
- Authentication and authoritative-queue authorization precede body decode.
  Wrong task, stale/cancelled attempt, lane, entity, operation, provider,
  model, fingerprint or ownership produces no reservation or event mutation.
- Same-attempt reserve replay, cross-attempt pending refusal, expiry-observer
  replay, next-generation admission, settle replay, a concurrent settlement
  race, provider failure, response loss, secret/error redaction and resource
  cleanup require executable evidence.
- A hand-derived reporter catalog must equality-check the closed member and
  both operations. Direct service and authenticated HTTP paths must run the
  same reserve/settle/expiry histories and compare typed receipts plus raw
  reservation/event state.
- Classification must prove a real hard-kill/reclaim history through this
  control: exactly one provider event or one typed `expired_unsettled`
  unknown-cost outcome, no double-spend and no silent loss.
- This decision does not waive FR-04's per-wave side-effecting hard-kill
  obligations. A pure-lane hard-kill record cannot satisfy that later gate.
- No taskq Protocol revision, Function Manifest revision, SQL contract bump,
  migration, grant or public client surface is created by this decision.
