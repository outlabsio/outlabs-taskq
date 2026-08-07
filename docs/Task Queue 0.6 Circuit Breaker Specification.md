# Task Queue 0.6 Circuit Breaker Specification

**Status:** docs-first slice S6 of the flow-control effort (Wave 3). Per the 2026-08-06 governance decision, interactive review is waived; this spec is the contract the build follows, and amendments discovered during implementation are recorded here before they land.
**Sources:** research report proposal **P10** (shared circuit breaker per queue); the S4 delegated defaults (breaker per-queue, opt-in, off-by-default, **streak-based not rate-based initially**, queue granularity not job_type); the premier-refresh incident (a dying downstream kept being hammered because nothing gave the fleet shared memory of the failure).
**Dependency:** SQL contract 0.5.2 — the breaker reuses the `queue_flow` row (0024), the typed `throttled` claim verdict + `retry_after_seconds` (0.5.0), the slow-start ramp (`ramp_started_at`, 0.5.0), and the counter triggers (0.4.0).
**Scope boundary (as first shipped, 0.6.0):** streak-based tripping only. No failure-rate/window tripping, no latency-based tripping, no cross-queue/global breaker. Aging (P15) is specified separately in §7 as the lighter second feature of this line; it shares no state with the breaker. **Amendments since:** failure-rate (0.6.3) and average-latency (0.6.4) tripping, the operator audit log (0.6.5), and audit prune (0.6.6) all subsequently landed as opt-in, off-by-default extensions — recorded in the Amendments section at the end. Latency tripping was tracked as "P16" in early drafts; that collided with the research report's P16 (fair cohorts) and is relabelled **P10b** (an extension of the P10 breaker).

## 1. Design principles

1. **Shared memory of downstream death.** Today each worker independently rediscovers an outage by burning real jobs (and paid proxy spend). The breaker makes one worker's discovery pause the whole fleet on that queue, then recover automatically. This is the fail-case half of "don't hammer an overloaded system" — the steady-state half (rate limits, in-flight caps, ramps) already shipped in 0.5.0.
2. **Off by default, per queue.** The breaker activates only when a queue sets `breaker_failure_threshold`. A queue that never sets it behaves exactly as under 0.5.2. Queue granularity, not job_type — matching the isolation unit consumers already use.
3. **Streak-based, conservative first.** Trip on N *consecutive* settle failures, not a failure ratio over a window. Simplest correct signal; hardest-to-tune part of the report, so start where false-trips are least likely. A single success resets the streak. (Rate/window tripping is a future amendment, not this slice.)
4. **Typed verdicts, never guessed sleeps.** An open breaker returns the existing `throttled` claim state with `retry_after_seconds` = time left on the cooldown. Workers already feed that into their retry map (0.5.0); no worker change is required.
5. **Advisory single-flight recovery.** Half-open admits probes one at a time via `pg_try_advisory_xact_lock` (the deadlock-free try-lock posture already used in the claim path) — one worker tests recovery while the rest stay parked, so a still-dead downstream sees at most one probe per cooldown, not a fleet-wide retry.
6. **Recovery slow-starts.** Closing the breaker stamps `queue_flow.ramp_started_at`, so the fleet ramps back in rather than stampeding a just-recovered downstream — the breaker and the 0.5.0 ramp compose for free.

## 2. Contract surface (migration 0031 install, 0032 activate; SQL contract 0.6.0; capability `circuit_breaker`)

### 2.1 State (on the existing `queue_flow` row)

`queue_flow` gains, all breaker state co-located with the ramp/GCRA state it interacts with:
- `breaker_state text NOT NULL DEFAULT 'closed' CHECK (breaker_state IN ('closed','open','half_open'))`
- `breaker_failure_streak integer NOT NULL DEFAULT 0`
- `breaker_tripped_at timestamptz`
- `breaker_probe_successes integer NOT NULL DEFAULT 0`
- `breaker_opened_total bigint NOT NULL DEFAULT 0` (observability: lifetime trips)

A queue with no `queue_flow` row is `closed` by definition (breaker off / never engaged); the row is upserted lazily on first trip or config touch, exactly as the ramp does.

### 2.2 Config (on the queue profile, off by default)

- `breaker_failure_threshold integer NULL CHECK (> 0)` — consecutive settle failures that trip the breaker. **NULL = breaker disabled** (the whole feature is gated on this being set).
- `breaker_cooldown_seconds integer NULL CHECK (BETWEEN 1 AND 86400)` — open duration before the first half-open probe is allowed. Default when threshold set but cooldown NULL: 30.
- `breaker_half_open_successes integer NULL CHECK (BETWEEN 1 AND 100)` — consecutive probe successes required to close. Default 1 (single successful probe closes).

### 2.3 Trip / recover at settle (fed by the settle path, breaker-configured queues only)

A queue whose profile has `breaker_failure_threshold` set updates its breaker at every terminal settle:

- **`fail_job` (terminal failure — retries exhausted or non-retryable):**
  - `closed`: `breaker_failure_streak += 1`; if it reaches the threshold → **trip**: `breaker_state='open'`, `breaker_tripped_at=now()`, `breaker_opened_total += 1`, emit `breaker_opened` job/queue event.
  - `half_open`: **re-open** immediately (`breaker_state='open'`, `breaker_tripped_at=now()`, `breaker_probe_successes=0`) — the probe proved the downstream is still dead.
- **`complete_job` (success):**
  - `closed`: `breaker_failure_streak=0` (a success breaks the streak).
  - `half_open`: `breaker_probe_successes += 1`; if it reaches `breaker_half_open_successes` → **close**: `breaker_state='closed'`, streak and probe counters zeroed, and **stamp `queue_flow.ramp_started_at=now()`** so the fleet slow-starts. Emit `breaker_closed`.
- A retryable failure that does **not** terminate the job (still has attempts) does not feed the streak — only terminal outcomes do (the breaker is about work *completing* badly, not about a job mid-retry).

Settle updates take the `queue_flow` row `FOR UPDATE` (same row the claim path reads); trips are exact under concurrency because settles for one queue serialize on that row.

### 2.4 Claim-path gate (before the rate / max_running gates)

The claim reads `queue_flow.breaker_state` O(1) (the row it already touches for ramp/GCRA). Ordered first — an open breaker short-circuits every other gate:

- **`closed`** → proceed to the existing rate/cap/rate-limit gates unchanged.
- **`open`**:
  - `now() < breaker_tripped_at + cooldown` → `throttled`, `retry_after_seconds = ceil(tripped_at + cooldown - now())` (declaring callers; non-declaring get `empty` — the 0.5.0 posture).
  - `now() >= breaker_tripped_at + cooldown` → attempt the **single-flight probe**: `pg_try_advisory_xact_lock(hashtextextended('taskq.breaker:'||queue, 0))`.
    - lock acquired → transition `breaker_state='half_open'` and let this one claim through as the probe (subject to the normal gates).
    - lock not acquired → `throttled` with a short `retry_after` (another worker holds the probe).
- **`half_open`** → the probe is in flight; further claims `throttled` with a short `retry_after` until the probe settles (closes or re-opens). Single-flight: at most one probe claim outstanding.

Because the advisory lock is transaction-scoped, a probe claim that commits releases the lock; the probe's *settle* (§2.3) is what actually closes or re-opens the breaker. Between the probe claim and its settle, other claims stay throttled by the `half_open` state.

### 2.5 Operator verbs

- `taskq.set_breaker_config(queue, failure_threshold, cooldown_seconds, half_open_successes, actor)` → `text` (`created|updated|unchanged`), mirroring `set_flow_limit`/`set_concurrency_limit`; NULL threshold disables. Granted to `taskq_operator`.
- `taskq.trip_breaker(queue, actor)` → `text` — force `open` now (manual, e.g. a known downstream maintenance). Emits `breaker_opened` with an operator marker.
- `taskq.force_close_breaker(queue, actor)` → `text` — force `closed`, zero counters, stamp the ramp. Emits `breaker_closed`.

### 2.6 Capability and migrations

- **0031** installs everything inactive-safe: the `queue_flow` breaker columns, the profile config columns, the redefined settle (`fail_job`/`complete_job`) and claim path with the breaker branch gated on `breaker_failure_threshold IS NOT NULL`, and the new verbs raising TQ501 until active. A queue that never configures a breaker is byte-identical to 0.5.2.
- **0032** activates `circuit_breaker` (metadata-only, preflighted on the exact 0031 catalog, per the 0012/0015/0023/0027 precedent).

## 3. Runtime and wire

No new wire states. `breaker_open` surfaces as the existing `throttled` claim verdict with `retry_after_seconds`; the worker's 0.5.0 retry-map handling already sleeps exactly as told and surfaces it as throttle counts in the snapshot. `TaskQ` client + transport gain `set_breaker_config` / `trip_breaker` / `force_close_breaker` (transport methods + SQL, CLI/SQL-only initially, mirroring `set_flow_limit`). Optional: a `queue_health` verdict `breaker_open` (rides along only if trivial).

## 4. Gates (before the S6 breaker PR merges)

1. **New L8 (breaker):** a queue whose handler fails deterministically trips after exactly `threshold` consecutive terminal failures; while open, claims return `throttled` with a decreasing `retry_after` and **no jobs are burned** (running stays at zero); after cooldown, exactly **one** probe is admitted (single-flight — a concurrent fan does not admit a second); a still-failing probe re-opens; once the downstream "recovers," a probe closes the breaker and the fleet **ramps** back in (first-window admissions bounded by the ramp, reusing the L7 measurement). Invariants: no lost/duplicate jobs; a breaker-off control queue is byte-identical.
2. **Contract tests** (`test_contract_0_6_0.py`): trip streak exactness, success resets streak, open→cooldown→half_open transition, single-flight probe, re-open on probe failure, close-stamps-ramp, all three verbs + TQ501 gating + operator-only grants; manifest parity; dual-major suite green.
3. No existing test, B-scenario, or L-scenario regresses; `taskq verify` green on a fresh 0.6.0 install both majors.

## 5. Priority aging (P15) — the lighter second feature (§7 detail)

Specified in §7; built after the breaker lands. Per-queue opt-in effective-priority boost computed in the claim ORDER BY (no writes, no new state), gated by L9 (low-priority work under a high-priority flood keeps a bounded max wait). Shares nothing with the breaker; can ship in the same 0.6 line or a follow-on point release.

## 6. Out of scope

Cross-queue or global breakers; adaptive concurrency (P17, standing revisit); half-open with K>1 concurrent probes (single-flight first — K>1 is a later amendment if a slow-recovering-but-healthy downstream needs faster re-entry). *(Failure-rate and latency tripping were out of scope for 0.6.0 but shipped as amendments in 0.6.3/0.6.4 — see Amendments.)*

## 7. Priority aging (P15) detail

- **Config:** queue profile `priority_aging_seconds integer NULL CHECK (> 0)` — seconds of waiting that raise effective priority by one step. NULL = off (strict priority, today's behavior).
- **Effect:** the claim candidate scan orders by an **effective priority** = `priority + LEAST(aging_cap, floor(extract(epoch from now() - scheduled_at) / priority_aging_seconds))`, then the existing tiebreak (scheduled_at, id). Purely computed in the claim query — no columns written, no settle-path work, no extra state. `aging_cap` bounds the boost (default 1000 so a very old low-priority job can reach but not exceed the top band).
- **Rationale:** strict priority starves low-priority work under a sustained high-priority flood (e.g. a burst of `render.execute` vs `render.letter_batch`). Aging gives waiting work a monotonically rising floor so its max wait is bounded, without inverting priority for fresh work.
- **Gate (L9):** under a continuous high-priority producer plus a few low-priority jobs, with aging off the low-priority jobs starve indefinitely (observed), and with aging on their max wait stays bounded (≤ a function of `priority_aging_seconds × aging_cap`) while high-priority throughput is not materially reduced. Contract test: effective-priority ordering flips for a sufficiently-aged low-priority job; off-by-default byte-identical.
- **Migration:** a single additive profile column + redefined claim ORDER BY (install), no activation split required if it rides the `circuit_breaker` capability window; otherwise its own no-bump body migration. Decided at build time.

## Amendments (recorded per the status line)

The 0.6.0 breaker shipped streak-only as specified above. These extensions and fixes landed subsequently and are the current contract — each a per-queue, opt-in, off-by-default addition on the same `queue_flow` row, with the same claim-gate + settle-trigger blast radius.

- **Failure-rate/window tripping (SQL 0.6.3, migration 0035).** Optional rolling-window failure-*ratio* trip alongside the streak: `set_breaker_rate(queue, failure_ratio, window_seconds, min_volume, actor)`. A configured breaker trips on streak OR rate. Tumbling window; trips when total ≥ `min_volume` and failures/total ≥ `failure_ratio`. The `breaker_opened` event gains a `reason` (`streak`|`rate`).
- **Average-latency tripping (SQL 0.6.4, migration 0036; report "P10b", relabelled from the P16 that collided with the report's fair-cohort P16).** Optional rolling-window average-execution-*latency* trip: `set_breaker_latency(queue, threshold_ms, window_seconds, min_volume, actor)`. Catches a slow-but-succeeding downstream. Fed on **every** terminal settle (`now() - claimed_at` for the settling attempt), successes and failures alike. Trip precedence is streak > rate > latency; `reason` gains `latency`.
- **Operator audit log (SQL 0.6.5, migration 0037).** Every queue-scoped operator verb (breaker config/rate/latency, manual trip/force-close, aging) records a `taskq.queue_audit` row with its actor and a `{before, after}` detail; read via `list_queue_audit` / `queue audit` (operator + observer).
- **Settle write-skip + audit prune (SQL 0.6.6, migrations 0038/0039).** 0038 restores the 0.6.3 no-op-write skip in the settle trigger (a healthy no-op success no longer rewrites `queue_flow`). 0039 adds `prune_queue_audit(older_than_hours)` (+ `maintenance prune-audit`, housekeeper/operator) to cap the append-only audit log.
- **Manual-recovery window reset (migration 0040, review finding F1).** `force_close_breaker` and `trip_breaker` now reset the rate and latency windows like the settle-close path, so a forced recovery does not immediately re-trip off stale window state.
- **Half-open atomic election + un-wedge (migration 0041, §2.4 correctness — review findings T1/P1).** The half-open probe is elected with an atomic `UPDATE ... WHERE breaker_state='open'`; the previous read-then-advisory-lock election admitted a second probe under a concurrent fan (~36% at loadlab small tier). A half-open deadline (2×cooldown) re-opens the breaker if a probe resolves outside succeeded/failed (cancelled/released/snoozed/lease-expired), which previously stranded it in `half_open` forever.

Still out of scope: cross-queue/global breakers; adaptive concurrency (P17); half-open with K>1 concurrent probes (§6).
