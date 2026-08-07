# TaskQ Flow Control — Operator Runbook

How to operate the flow-control plane (SQL contracts 0.4.0–0.6.1): circuit breaker,
rate limits, in-flight caps, slow-start ramps, job TTL, redrive/schedule smear, and
priority aging. This is the *operational* companion to the design specs
(`Task Queue 0.4/0.5/0.6 …`); it says **when to turn each knob, what to set it to,
how to see it working, and how to respond when it misbehaves.**

## 0. First principles

- **Everything here is off by default, per queue.** A queue that configures nothing
  behaves exactly as it did before the flow-control plane existed. You opt in one
  queue and one feature at a time. There is no global switch and no default policy.
- **The contract declines work with a typed verdict, never a guessed sleep.** When a
  gate says "not now," the claim returns `throttled` with `retry_after_seconds`, and
  the worker sleeps exactly that long (plus its own jitter). You do not tune worker
  sleep — you tune the queue.
- **Config is set by an operator verb, over direct SQL or the CLI** (`taskq queue …`).
  All verbs require the `taskq_operator` role and a mutation-safety environment ack
  on the CLI (`--expected-environment <env>`).
- **Roll out in this order for any feature:** (1) read the queue's current behavior
  (`taskq queue health`, `taskq queue show`, and the queries in §9); (2) set a
  conservative value; (3) watch for a full failure/load cycle; (4) tighten. Never
  set an aggressive value on a queue you have not watched.

## 1. Circuit breaker (0.6.0)

**What it does.** Trips a queue "open" after N *consecutive terminal failures*, so the
whole fleet stops claiming from a dying downstream instead of each worker burning
jobs rediscovering the outage. After a cooldown it admits exactly one probe; a
successful probe closes it (and slow-starts via the ramp), a failing probe re-opens it.

**When to use it.** A queue whose jobs call a single external dependency that can go
hard-down (a provider API, a scraper proxy pool, a downstream service). The canonical
case is the proxy-blip-burns-the-day's-scrape scenario.

**Two trip triggers — pick per failure mode.** A configured breaker trips on
**either**:
- **Streak** (always on) — N *consecutive* terminal failures. Fast, catches a
  hard-down downstream. But a single success resets it, so it never catches a
  *flaky* downstream (fail, succeed, fail, succeed at a high rate).
- **Rate** (optional, 0.6.3) — a sustained failure *ratio* over a rolling window.
  Add it for downstreams that fail intermittently rather than going hard-down.

It is still blind to slow-but-succeeding downstreams (latency tripping is a
documented follow-up).

**Configure.**
```bash
taskq queue set-breaker <queue> --failure-threshold 5 --cooldown-seconds 30 --half-open-successes 1
# Optional rate trip: trip if >=50% of the last 60s' settles failed (min 20 settles).
taskq queue set-breaker-rate <queue> --failure-ratio 0.5 --window-seconds 60 --min-volume 20
taskq queue set-breaker-rate <queue> --off     # remove the rate trip (keep streak)
taskq queue set-breaker <queue> --off          # disable the whole breaker
taskq queue trip-breaker <queue>               # force open (e.g. known maintenance)
taskq queue close-breaker <queue>              # force closed + slow-start
```
SQL: `SELECT taskq.set_breaker_config('<queue>', 5, 30, 1, '<actor>');`
and `SELECT taskq.set_breaker_rate('<queue>', 0.5, 60, 20, '<actor>');`

**Recommended starting points.**
- `failure-threshold`: start **conservative (5–10)**. Too low false-trips a
  heterogeneous queue on normal sporadic failures. Raise it if you see nuisance trips.
- `cooldown-seconds`: match the downstream's realistic recovery time (30–120s for a
  proxy blip; longer for a provider outage).
- `half-open-successes`: `1` is fine to start. Raise it if a downstream tends to
  "recover then immediately die again" (a single good probe isn't enough signal).
- **Rate trip** (if used): `failure-ratio` = the fraction you consider "broken" (0.5
  is a reasonable start); `min-volume` high enough that a quiet queue's few failures
  don't trip (≥ 10–20); `window-seconds` a few multiples of the downstream's blip
  duration. The `breaker_opened` event's `reason` field says which trigger fired
  (`streak` vs `rate`).

**How to tell it's working.** A tripped breaker shows as the **`breaker_open`
verdict** in `queue_health` (`taskq queue health <queue>`), with breaker state in the
`detail`. Every automatic transition also emits a **job event** (`breaker_opened`,
`breaker_reopened`, `breaker_closed`) on the job that drove it — see §9.

**Incident response — a stuck-open breaker.** If a breaker is open but the downstream
is actually healthy (false trip, or it recovered during a long cooldown):
`taskq queue close-breaker <queue>` forces it closed and stamps the recovery ramp. If
it keeps re-tripping on a healthy downstream, the threshold is too low — raise it or
`--off` while you investigate.

## 2. In-flight cap: `max_running` (0.5.0)

**What it does.** Caps concurrently-running jobs per queue. Claims return `throttled`
once `running >= max_running`.

**When to use it.** A downstream with a hard concurrency limit (a DB connection pool,
an API that 429s past N in-flight). **Use this, not worker concurrency,** when the
limit is a property of the *downstream*, not the worker fleet — it holds across all
workers regardless of how many you run.

**Configure** (via the queue profile, not a dedicated verb):
`taskq queue update <queue>` with `max_running` in the profile JSON, or
`SELECT taskq.ensure_queue('<queue>', '{"max_running": 8}'::jsonb, '<actor>');`

**Note on races.** The cap is advisory under concurrency — a simultaneous burst of
claims can briefly overshoot by up to the number of concurrent claimers, then settles.
It is exact in aggregate, not instantaneously.

## 3. Rate limits (0.5.0)

**Two keyspaces:**
- **Queue-level** (`claim_rate_per_minute`, `claim_burst` on the profile): a GCRA rate
  limit on *claims* (work starts) for the whole queue.
- **Key-level** (`taskq.set_flow_limit` / `taskq queue set-flow-limit`): a limit on a
  `flow_key` that jobs carry, orthogonal to the queue. **Unknown keys are unlimited** —
  a politeness limiter must not serialize the world by default.

**When to use it.** Politeness toward a downstream that rate-limits you (per-provider,
per-tenant). Queue-level for "this whole queue is polite"; key-level for "all jobs
touching provider X across queues share a budget."

**Configure.**
```bash
taskq queue set-flow-limit <flow-key> --rate-per-minute 120 --burst 10
```
SQL: `SELECT taskq.set_flow_limit('<key>', 120, 10, '<actor>');`

## 4. Slow-start ramp (0.5.0)

**What it does.** `ramp_seconds` on the profile: after a resume (or a breaker close),
the queue's effective `max_running` and `claim_rate` scale from near-zero up to full
over the ramp window, so a just-recovered downstream isn't stampeded.

**When to use it.** Any queue with a `max_running` or rate limit whose downstream is
fragile right after recovery. It composes automatically with the breaker (a breaker
close stamps the ramp).

## 5. Job TTL (0.5.0)

`default_ttl_seconds` on the profile, or `p_ttl_seconds` at enqueue. Expired
`blocked|queued` jobs are settled `cancelled / outcome='expired_ttl'` by the tick.
Running jobs are never TTL-killed — the lease governs in-flight work. Use for work
that is worthless if stale (a refresh that a newer one supersedes).

## 6. Redrive & schedule smear (0.5.0–0.5.2)

- **Redrive smear:** `taskq.redrive_failed(queue, limit, actor, smear_seconds)` spreads
  redriven jobs' `scheduled_at` across `[now, now+smear)` instead of releasing them all
  at once — avoids a re-arrival thundering herd after a bulk redrive.
- **Schedule smear:** `taskq queue`… no — schedules: `taskq schedule set-smear <name>
  --smear-seconds 300`. Applies a deterministic per-schedule offset so co-scheduled
  jobs (many cron entries at `:00`) de-align instead of firing in a stampede. Set it on
  any group of schedules that share a firing instant.

## 7. Priority aging (0.6.1)

**What it does.** A waiting job's effective claim priority improves with age, so a
sustained high-priority flood cannot starve low-priority work forever. Opt-in per queue.

**When to use it.** A queue with a mix of priorities where low-priority work can be
buried indefinitely (e.g. `render.execute` at high priority vs `render.letter_batch`
at low priority under sustained load).

**Configure.**
```bash
taskq queue set-aging <queue> --aging-seconds 60     # +1 priority step per 60s waited
taskq queue set-aging <queue> --off                  # strict priority
```
SQL: `SELECT taskq.set_priority_aging('<queue>', 60, '<actor>');`

**Tuning.** `aging-seconds` = how many seconds of waiting buys one step of priority
improvement. Smaller = low-priority work jumps the queue faster. Set it so the *worst
acceptable* wait for a low-priority job ≈ `aging-seconds × (its priority number)`.

**Limitation.** Aging applies to the **normal claim path only** — workflow
*continuation* claims keep strict priority. A low-priority continuation can still
starve under a high-priority flood.

## 8. `notify_mode` (0.5.0)

`notify_mode = 'on_idle_transition'` on the profile fires the wake-up NOTIFY only when
the queue was idle before an enqueue (instead of every enqueue), cutting notify volume
on busy queues. The greedy claim loop + poll backstop cover mid-drain arrivals. Safe to
leave `'always'` (default) unless a queue's NOTIFY volume is itself a problem.

## 9. Observability

As of 0.6.2 a tripped breaker surfaces two ways: the `breaker_open` **health verdict**
(with breaker state in the health `detail`), and **job events** on transitions. Use the
health surface for "is it open right now" and events for "when did it trip."

```sql
-- Is any breaker open right now? (health verdict + breaker detail)
SELECT queue, verdict, detail -> 'breaker' AS breaker
  FROM taskq.queue_health(NULL) WHERE verdict = 'breaker_open';   -- or: taskq queue health <queue>

-- Breaker timeline (automatic transitions): opened / reopened / closed.
SELECT e.created_at, e.event_type, e.data
  FROM taskq.job_events e JOIN taskq.jobs j ON j.id = e.job_id
 WHERE j.queue = '<queue>' AND e.event_type LIKE 'breaker_%'
 ORDER BY e.created_at DESC;

-- Full flow state for a queue (breaker + rate + aging + ramp config and state):
SELECT breaker_state, breaker_failure_streak, breaker_opened_total, breaker_tripped_at,
       breaker_failure_threshold, breaker_cooldown_seconds,
       priority_aging_seconds, ramp_started_at
  FROM taskq.queue_flow WHERE queue = '<queue>';

-- Live levels + cumulative throughput (0.4.0 counters); configured key flow limits:
SELECT * FROM taskq.queue_counters WHERE queue = '<queue>';
SELECT * FROM taskq.flow_limits;
```

Manual `trip_breaker` / `force_close_breaker` do not emit events (they are
operator-initiated; the verb's actor is the record). A queue-scoped audit table that
would cover those too is a documented follow-up.

Worker-side, `throttled` verdicts (from any gate — breaker, rate, cap) surface as
throttle counts in the worker's snapshot; they are not errors and do not trip the
claim-error backoff.

## 10. Rollout checklist for turning a feature on

1. **Pick one queue and one feature.** Never batch.
2. Capture the baseline: `taskq queue health <queue>` and the §9 queries.
3. Set a **conservative** value (high breaker threshold, generous rate, long
   aging-seconds).
4. Watch through at least one real failure or load cycle — for the breaker, that means
   an actual downstream wobble; for aging/rate, a real high-load window.
5. Confirm via §9 that state moved the way you expected (breaker tripped and recovered;
   rate held; low-priority work drained).
6. Tighten one step if needed; re-watch. Stop at "good enough," not "maximally tight."
7. Record what you set and why (there is no config-history view yet).

## 11. Known operational gaps (as of 0.6.3)

- **Breaker is blind to slow-but-succeeding downstreams** — no latency-based tripping
  yet (streak + rate cover consecutive and intermittent *failures*, not slowness).
- **Manual trip/close emit no events** — only automatic transitions do; a queue-scoped
  audit table would cover manual verbs too.
- **Aging skips workflow continuations.**
- **No config-history view** — record changes yourself.

*Closed recently:* the `breaker_open` health verdict + breaker events (0.6.2, §9); the
streak-only intermittent-failure blindness (0.6.3 — add a rate trip with `set-breaker-rate`, §1).

See the vault's *TaskQ Flow Control Implementation Plan* (Known Gaps backlog) for the
plan to close these.
