# ADR-003 — Attempt fencing and typed replay outcomes

**Status:** Accepted 2026-07-18
**Resolves:** reaffirmation of Unified Spec §3/§5/§7 (adversarially reviewed v1.1), so implementation cites one decision record

## Context

The fencing model is the design's core asset and survived a 35-finding adversarial review. It is restated here as an accepted decision so later proposals cannot re-litigate it piecemeal.

## Decision

1. Every claim creates an attempt row whose **server-generated uuid is the fence**; `jobs.current_attempt_id` is CAS-checked on every mutation, backstopped by the partial unique index `uq_job_attempts_running` — a double-claim is a hard database error for any writer.
2. All lease/retry/schedule math uses the **database clock** exclusively.
3. Settles return **typed results** (`ok | already_settled | lost | retry_scheduled | dead`), never exceptions for expected races. A network-retried settle of any verb resolves through the attempt ledger to `already_settled`; a genuinely superseded attempt gets `lost`.
4. Budget semantics per spec §3.3: failures and lease expiries consume `failure_count`; releases and snoozes never do; three consecutive expiry deaths quarantine as `failed/poison`.
5. Attempt fences are **capabilities**: carried only on the claim/settle channel, never in read models, list responses, OpenAPI examples, metrics labels, or logs.

## Consequences

- The fenced verb set gains `cancel_running_job` (ADR-007) with identical replay semantics.
- Harness suites T2–T5 assert every clause above; §17's failure audit remains the acceptance checklist.
- Any transport (SQL, HTTP, future) must surface these outcomes losslessly (ADR-005).
