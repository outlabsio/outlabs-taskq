# outlabs-taskq 0.1.0a27 release notes

**Base release:** 0.1.0a26
**SQL contract:** 0.6.6 (from 0.3.1)
**Protocol document:** 1.0.17
**Packaged migrations:** 0001–0042 (0022–0042 new)

## The flow-control plane

a27 is the first published artifact to carry the flow-control plane — per-queue
overload protection built across contracts 0.4.0–0.6.6. **Every feature is off by
default and opt-in per queue; a queue that configures nothing keeps its pre-plane
control behavior** (see the one mechanical caveat below). When a gate declines work it
returns a typed `throttled` claim verdict with `retry_after_seconds`, so workers sleep
exactly as told.

- **Queue counters + health (0.4).** Trigger-maintained per-queue counters, snapshot-delta
  rates and drain ETA, and a seven-verdict `queue health` view (`paused / no_consumer /
  choking / behind / starved / inactive / ok`).
- **Rate limits, caps, key fairness (0.5).** Per-queue GCRA claim-rate limiting, `max_running`
  concurrency caps, and a `flow_limits` key-space for tenant/politeness fairness — with a
  ramp (slow-start) after a queue resumes.
- **Schedule smear (0.5.2).** `set_schedule_smear` disperses co-due schedule firings to
  break stampedes; operational tuning kept out of the schedule definition and manifest hash.
- **Circuit breaker (0.6.0–0.6.4).** Per-queue breaker that trips on a consecutive-failure
  streak, a sustained failure rate, or slow-but-succeeding latency; while open it returns
  `throttled` and burns no jobs; after cooldown exactly one half-open probe is admitted
  (single-flight), and recovery slow-starts the fleet back in.
- **Priority aging (0.6.1).** Per-queue opt-in fairness so low-priority work cannot starve
  under a high-priority flood.
- **Operator audit log (0.6.5–0.6.6).** `queue_audit` records every queue-scoped operator
  verb with actor and before/after detail; `prune-audit` caps its growth.

## Hardening in this release

- **Claim path stays index-backed for unconfigured queues.** The aging order is applied only
  when a queue opts in; unconfigured queues use the bare, `jobs_claim_idx`-matching order, so
  the core claim remains a one-row index scan regardless of backlog depth. Configured aging
  inherently prices claims at O(ready depth) — the documented cost of enabling it.
- **Breaker recovery correctness.** Forced/ manual recovery resets the rate and latency
  windows, and the half-open probe election is a single atomic transition (no double-probe
  race) with a wedge deadline that re-opens a stranded half-open queue.
- **`db migrate` on an unbound target** now surfaces the contract's real "run `taskq target
  bind` first" guidance instead of an opaque internal error.

## One mechanical caveat to "off by default"

Since 0.4 every queue keeps an exact `queue_counters` accounting row updated on each status
transition. This is bookkeeping the health views depend on, not a gate — an unconfigured
queue is never declined work — but a single queue under many simultaneous settles can briefly
serialize on its counter row. Size accordingly for very-high-concurrency single-queue loads.

## Rollout

Package-first is safe: the runtime accepts contracts 0.3.1 through 0.6.6, so a27 binaries
run against a not-yet-migrated 0.3.1 database.

1. Stop every worker and scheduler sharing the target TaskQ database.
2. Pin every process and deployment command to the exact 0.1.0a27 artifact.
3. Start against the existing contract database and run target, doctor, and compatibility
   checks.
4. Review `db plan`, then apply migrations 0022–0042 to activate contract 0.6.6. All new
   features remain off until a queue is explicitly configured.
5. Start one worker and one scheduler clock, observe one complete job, then restore normal
   supervision.
6. Opt queues into flow-control features one queue and one feature at a time, using the
   Flow Control Operator Runbook.

Production mutations still require the exact installation ID, `--allow-production`, and
`--yes`. Runtime credentials remain separate from owner/migration credentials.

## Known issues

- The breaker's half-open wedge deadline is anchored to the trip time rather than the probe
  election time. On an idle queue where a probe is elected more than two cooldowns after the
  trip, that probe can be re-opened before it settles — one wasted open cycle, self-correcting.
  Deferred (a fix needs a dedicated timestamp column / contract bump); tracked for a future
  breaker touch-up.
