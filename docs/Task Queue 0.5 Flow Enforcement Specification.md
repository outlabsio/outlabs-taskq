# Task Queue 0.5 Flow Enforcement Specification

**Status:** docs-first slice S5 of the flow-control effort (Wave 2b — enforcement). Per the 2026-08-06 governance decision, interactive review is waived; this spec is still the contract the build follows, and amendments discovered during implementation are recorded here before they land.
**Sources:** research report proposals P7, P8, P9, P11, P12, P13, P14; the S4 delegated-default decisions (both rate-limit keyspaces; everything off-by-default per queue; breaker deferred to S6); the L4 harness finding (idempotent replay of an admitted key is rejected at `max_depth` because the depth probe precedes the idempotency check).
**Dependency:** SQL contract 0.4.0 (`queue_counters` active) — enforcement reads levels O(1).
**Scope boundary:** no breaker, no aging (S6). No AIMD. Wire changes are additive and capability/flag-gated.

## 1. Design principles

1. **Off by default, per queue.** Every enforcement feature activates only when its queue-profile (or key-registry) field is set. A 0.4-shaped queue behaves identically under 0.5. The hot claim path pays for enforcement only on queues that configured it.
2. **Typed verdicts, never guessed sleeps.** When the contract declines work it says when to come back (`retry_after_seconds`). Workers and producers sleep exactly as told, with client-side jitter applied on top.
3. **Advisory under concurrency, exact in aggregate** — the `max_depth` posture extends to `max_running` and rate buckets: brief over/under-admission in races is accepted; systematic correctness is asserted by the harness.
4. **Old callers degrade to today's behavior.** Workers that do not declare `accept_throttled` receive `empty` where new workers receive `throttled`; the legacy `enqueue` keeps its exception shape; `redrive_failed` without smear behaves exactly as before.

## 2. Contract surface (migration 0024, SQL contract 0.5.0, capability `flow_control`)

### 2.1 Typed claim throttle verdict (P8-claim)

- `taskq.claim_batch` gains a trailing attribute `retry_after_seconds integer` (NULL except on `throttled`), following the additive-composite precedent (0003's `lease_seconds`).
- The claim functions gain `p_accept_throttled boolean DEFAULT false`. When a queue-level gate (max_running cap, dry queue bucket, ramp) declines the whole call: declaring callers get `state='throttled'` with the earliest computed retry hint; non-declaring callers get `state='empty'` (today's observable behavior). `paused` stays its own state.
- Per-candidate gates (dry `flow_key` buckets) skip candidates inside the scan — the SKIP-LOCKED/`v_skip` pattern used by `concurrency_key` — and never throttle the whole call while other work is claimable.

### 2.2 Producer verdict + check-order fix (P8-producer)

- New producer function **`taskq.try_enqueue(...)`** — same argument surface as `enqueue` — returning `(outcome text, job_id uuid, retry_after_seconds integer)` with `outcome ∈ accepted | existed | rejected_depth`. Never raises for backpressure.
- **Check order (the L4 fix): the active-idempotency check runs BEFORE the depth gate.** An `existed` result adds no depth and must succeed at cap, so deterministic reconciliation replay works against a frozen queue. The legacy `enqueue` keeps its existing order and TQ429 exception unchanged (compat); consumers migrate at their own pace.
- `retry_after_seconds` for `rejected_depth`: derived from the snapshot drain rate when available (`ready_overage / settled_per_s`, clamped to [1, 60]); otherwise the queue profile's `backpressure_retry_seconds` (default 5).

### 2.3 Per-queue in-flight cap (P7)

- Queue profile gains `max_running integer NULL CHECK (max_running > 0)` (NULL = unlimited). Claim gates on `queue_counters.running >= effective_max_running` — O(1) — returning `throttled` with `retry_after_seconds = 1` (running slots free on settle cadence; the hint is deliberately short and the worker's jitter spreads re-arrival).

### 2.4 Rate limits — queue level and key level (P9)

- **State:** new table `taskq.queue_flow (queue text PK FK, tat timestamptz, ramp_started_at timestamptz, updated_at)` (GCRA theoretical-arrival-time per queue), and for keys: registry `taskq.flow_limits (key text PK CHECK ~ '^[a-z0-9_.:-]{1,120}$', rate_per_minute integer CHECK (rate_per_minute > 0), burst integer NULL, note text, updated_at)` + state `taskq.flow_state (key text PK, tat timestamptz, updated_at)`. Operator verb `taskq.set_flow_limit(key, rate_per_minute, burst, actor)` mirrors `set_concurrency_limit`.
- **Queue-level:** profile gains `claim_rate_per_minute integer NULL` and `claim_burst integer NULL` (default = rate). GCRA with emission interval `T = 60.0 / rate`: the claim call computes the grantable count for the requested batch, caps the batch, and when zero is grantable returns `throttled` with `retry_after_seconds = ceil(tat - burst_window - now)`.
- **Key-level:** jobs gain `flow_key text NULL CHECK (char_length(flow_key) <= 120)` (enqueue parameter, orthogonal to `concurrency_key`; **unknown keys are unlimited** — unlike the fail-closed concurrency mutex, a politeness limiter must not serialize the world by default). Claim consumes one token per claimed candidate; dry keys are skipped in-scan.
- Semantics note: rate limits meter **claims** (work starts), the politeness-relevant instant. Retries and first attempts meter identically.

### 2.5 Job TTL (P11)

- Jobs gain `expires_at timestamptz NULL`; enqueue parameter `p_ttl_seconds integer NULL CHECK (1..31536000)`; queue profile `default_ttl_seconds integer NULL`. Stamped at enqueue: `expires_at = scheduled_at + ttl`.
- The claim scan skips expired rows (`expires_at IS NULL OR expires_at > now()` in the candidate predicate); a new tick pass `taskq.expire_ttl(p_limit DEFAULT 200)` settles expired `blocked|queued` rows as `cancelled / outcome='expired_ttl'` (typed, evented; counter triggers record them as cancellations). Running jobs are never TTL-killed — the lease governs in-flight work. Workflow members expire as cancelled members and flow through existing cancellation convergence.

### 2.6 Slow-start ramps and smears (P12, P13)

- Queue profile gains `ramp_seconds integer NULL`. `resume_queue` (and the future breaker close) stamps `queue_flow.ramp_started_at`; while `elapsed < ramp_seconds`, effective `max_running` and `claim_rate_per_minute` scale by `elapsed / ramp_seconds` (floor 1 slot / 1-per-minute). Purely computed — no extra writes.
- `taskq.redrive_failed` gains `p_smear_seconds integer DEFAULT 0 CHECK (0..86400)`: redriven rows get `scheduled_at = now() + random() * smear` and, when smeared, emit no per-row NOTIFY (future-dated rows are poll-discovered by the emit rules; the second sanctioned `random()` in the contract, recorded here).
- Schedules gain `smear_seconds integer NULL CHECK (1..3600)`: a **deterministic positive** per-schedule offset applied by the Python evaluator to every computed occurrence and `next_fire_at` consistently, shifting the recurrence lattice by a per-schedule constant so co-cron schedules de-align. SQL stores and exposes the column; firing validation is lattice-agnostic and unchanged.
  - **Activated in migration 0029 (contract 0.5.1), 2026-08-06.** 0024 shipped only the `schedules.smear_seconds` column; it was inert until 0029 appended `smear_seconds` to the `schedule_claim` composite and projected it through `_claim_schedules_unattested`, so the evaluator receives it. The offset is `blake2b(schedule_id.bytes) % smear_seconds` (a stable, process-independent hash — not the salted builtin `hash()` — so it is identical across processes and restarts; the spec's illustrative `hashtextextended` is superseded by this Python-owned computation). `evaluate_schedule` applies it as a **reference-frame shift**: inputs move into the un-smeared base frame, the pure evaluator runs there, every output instant shifts back. This is exact for interval and cron lattices and drift-free (stored `next_fire_at` is always `base_point + offset`, so successive claims recover the same base lattice). Offset 0 (smear NULL — every existing schedule) is an exact passthrough. Additive and non-breaking; a running scheduler picks up a schedule's smear on its next claim, no restart. **Known follow-up:** the write path (`put_schedule` / `put_managed_schedule` / the YAML manifest / `ScheduleDefinition`) does not yet expose `smear_seconds`, so it is currently configured by a direct `UPDATE taskq.schedules SET smear_seconds = …`; adding the manifest field + definition-hash coverage is a tracked follow-up.

### 2.7 Notify on idle transition (P14)

- Queue profile gains `notify_mode text NOT NULL DEFAULT 'always' CHECK (notify_mode IN ('always','on_idle_transition'))`. In `on_idle_transition`, enqueue-family NOTIFY fires only when the counter `queued + blocked` was zero before the insert (O(1) read); mid-drain arrivals are collected by the workers' greedy claim loops, and the poll backstop covers the documented saturated-key corner.

### 2.8 Capability and migrations

- **0024** installs everything inactive-safe: new tables/columns/functions, redefined claim/enqueue family, tick gaining the `expire_ttl` pass. New surfaces (`try_enqueue`, `set_flow_limit`) raise TQ501 until the `flow_control` capability activates; enforcement itself is gated by per-queue configuration (NULL = off), so 0024 changes nothing observable for existing queues.
- **0025** activates `flow_control` (metadata-only, preflighted on the exact 0024 catalog, per the 0012/0015/0023 precedent).

## 3. Runtime and wire (Protocol 1.0.17)

- `ClaimWireRequest` gains `accept_throttled boolean DEFAULT false`; the claim wire outcome adds `throttled` with `retry_after_seconds` in the data. The facade returns `throttled` immediately (never parks a long-poll on a throttled queue).
- The worker runtime always declares `accept_throttled`; a `throttled` verdict feeds the queue's existing P1b retry map (`_claim_retry_at`) with the server's hint × client jitter — no error streak, not a degraded state, surfaced in the snapshot as throttle counts.
- `TaskQ` client gains `try_enqueue` (typed result, retryable by idempotency key); HTTP facade route added under the existing enqueue authorization.

## 4. Gates (per feature, before the S5 PR merges)

1. **L4 flip:** typed-verdict assertions replace the exception-shaped defect observations — replay-of-existing succeeds at cap via `try_enqueue`; producers using verdicts converge without exception handling; the frozen-window observation becomes "typed with retry hints".
2. **L7 (resume/redrive stampede):** with and without ramp + smear — first-60s claim/settle spike measurably flattened, invariants hold.
3. **L3 (schedule co-due):** smeared schedules de-align (firing spread ≥ configured smear across a co-cron population); unsmeared behavior byte-identical.
4. **New L10 (rate conformance):** a rate-limited queue's observed claim rate converges to the configured rate ±10% at toy scale; key-limited candidates skip without starving unlimited work; `max_running` never exceeded by more than the documented race margin under concurrent claims.
5. Contract tests (`test_contract_0_5_0.py`) covering every new function, gate, TTL settlement, ramp math, notify_mode transitions, and TQ501 gating; manifest parity; dual-major suite green.

## 5. Out of scope

Breaker and aging (S6); AIMD (standing revisit); per-queue `health_lag_seconds` tuning (rides along only if trivial); HTTP surfaces for `set_flow_limit` (CLI/SQL-only initially, `set_concurrency_limit` precedent).
