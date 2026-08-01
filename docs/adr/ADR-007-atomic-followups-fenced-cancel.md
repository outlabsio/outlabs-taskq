# ADR-007 — Lossless atomic follow-ups; fenced handler cancel

**Status:** Accepted 2026-07-18
**Resolves:** D-04, D-05; **supersedes** the spec's savepoint-per-followup design (v1.1 graft) and the v1.4 SQL-side truncation guard; resolves feature 03's hedged Cancel mapping

## Context

`complete_job` wrapped each follow-up enqueue in a savepoint (bad spec → `followup_failed` event, parent still succeeds) and v1.4 added truncation past 20 specs (`followups_truncated` event). Both make a *successful* parent compatible with silently missing children — violating "accepted work is never silently discarded." The original motivation (a bad spec must not wedge a finished job into re-execution/poison) is real and must survive the fix. Separately, feature 03 hedged whether a handler's `Cancel` maps to the unfenced operator `cancel_job` — which would let a stale worker cancel a job now owned by another attempt.

## Decision

**Follow-ups (contract design; activates with the 0.2 contract per ADR-009):**

1. **Validate before any parent state change:** array shape, count ≤ 20 (reject, never truncate — wide fan-out uses a planner task), per-spec `job_type` present and target queue exists. Deterministic invalids raise typed `TQ422` before the CAS.
2. **Atomic:** parent completion and every child enqueue commit in one transaction — no savepoints. Any residual child failure unwinds the whole settle.
3. `created` and idempotent `existed` are both success (derived `chain:{job_id}:{step}` keys make transport retries safe). The depth gate stays skipped for followups (`p_internal`) — child backpressure never fails a parent settle.
4. **Anti-wedge escape:** on a deterministic `TQ422`, the worker terminal-fails the parent — `fail_job(retryable=false, error='invalid_followup: …')` — visible in dead letters, redrivable after the code fix. At-least-once already requires idempotent handlers, so a redrive re-running side effects is the documented contract, not new risk. Transient errors retry the settle normally (replay-safe per ADR-003).
5. `0.1` workers and the `0.1` contract **reject non-empty followups** with a clear not-enabled error; the typed model reserves the field.

**Handler cancel:** new fenced verb with ADR-003 semantics —

    taskq.cancel_running_job(p_job_id, p_attempt_id, p_worker_id, p_reason) -> taskq.settle_result

Accepts only the matching running attempt; replays → `already_settled`; stale fence → `lost`; lands `cancelled` with outcome `canceled`, budget untouched. The Python `Cancel(...)` result maps here and only here; `cancel_job` remains operator-only.

## Consequences

- Spec §5.5 rewritten (validate-first, no savepoints, truncation guard removed); `followups_truncated`/`followup_failed` events deleted from the vocabulary; feature 03's mapping table updated.
- If a future case genuinely needs fail-open settlement, it requires a durable `followup_intents` outbox + reconciler — an event log alone is insufficient (recorded as the bar for reopening).
