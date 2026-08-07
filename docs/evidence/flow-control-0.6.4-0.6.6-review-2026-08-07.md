# Flow-control 0.6.4–0.6.6 adversarial SQL review — 2026-08-07

- **Scope:** commits `a165839..d8302af` on `main` — SQL contract 0.6.3 → 0.6.6;
  migrations `0036_breaker_latency_tripping`, `0037_queue_audit`,
  `0038_breaker_settle_write_skip` (body-only, no contract bump),
  `0039_queue_audit_prune`; plus the accompanying transport/CLI/runbook changes.
  - `9978610` Breaker latency tripping: slow-but-succeeding trip (0.6.4)
  - `b6ed026` Queue-scoped operator audit log: manual-verb events + config-history (0.6.5)
  - `f04d1f8` Flow-control follow-ups: breaker settle write-skip + audit prune (0.6.6)
  - `d8302af` loadlab: full scale tier end-to-end + L5 calibration (harness-only, no SQL surface)
- **Method:** independent adversarial review. Nothing was taken on trust: fresh
  0001→0039 chains were installed on throwaway PostgreSQL 16.14 and 18.4
  (Docker), `verify()` and the complete suite ran on both majors, and the
  breaker/audit behavior was probed live — including chain-stopped databases at
  migration 0035 (contract 0.6.3) and 0037 (0.6.5, which still carries the
  0.6.4 trigger body) driven through identical settle scenarios so the three
  contract states could be diffed field-by-field, with write behavior observed
  via `queue_flow.xmin` churn.
- **Reviewed base evidence:**
  - `taskq.sql.verify()`: all 18 probes green on both majors (function catalog,
    hardening, PUBLIC-revoke, grants, roles, relations, composites, table
    shapes, constraints, indexes, views, triggers, relation privileges, seeds,
    external FKs). `src/taskq/sql/manifest.py` exactly matches the live catalog
    on PostgreSQL 16.14 and 18.4.
  - `pytest tests/ -q`: **808 passed, 2 skipped** on PostgreSQL 16.14 and
    **808 passed, 2 skipped** on PostgreSQL 18.4. Both skips are
    environment-gated (`TASKQ_PLAN_CHECKS` 1M-row EXPLAIN suite;
    `TASKQ_TEST_REDIS_URL` OutlabsAuth integration), not failures.

## Verdict: FIX-THEN-SHIP

The trigger math, hardening, grants, manifest parity, and migration mechanics
all held up under adversarial probing on both PostgreSQL majors. One in-range
operational defect is real, reproducible, and sits on the exact verb an
operator uses mid-incident (F1). It should be fixed before this slice is
released; the remaining findings are follow-up material.

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | major | `force_close_breaker` leaves stale rate/latency windows; breaker re-trips immediately after forced recovery | open |
| F2 | minor (design) | `prune_queue_audit` erases audit history with no actor and no trace | open |
| F3 | minor (consistency) | `list_queue_audit` returns an empty page for an unknown queue instead of TQ001 | open |
| F4 | minor (docs) | 0.6 spec never amended for 0.6.3–0.6.6; two stale runbook statements | open |
| F5 | minor (audit fidelity) | before-snapshots in the six replaced verbs are read without a lock | open |
| F6 | observation | lease-expiry terminal failures never feed the latency window (undocumented) | document |
| P1 | major, pre-existing (0031) | half-open wedge: a probe that settles outside failed/succeeded strands the breaker in `half_open` forever | escalate to backlog |

## F1 (major) — `force_close_breaker` does not reset the rate/latency windows

`src/taskq/sql/migrations/0037_queue_audit.sql:184` (and `trip_breaker` at
`:159`). The automatic close path resets streak, probe counters, **and both
windows** (`0038_breaker_settle_write_skip.sql:53-59`). `force_close_breaker`
resets only streak and probe counters — its reset list dates from 0.6.0,
before the 0035 rate window and 0036 latency window existed. The trip UPDATE
persists the tripping window state, opens do not decay it, and the manual
recovery verb does not clear it.

Reproduced both ways on a fresh 0.6.6 install:

- **Latency leg.** `set_breaker_latency(q, 1000, 300, 3)`; three 5 s settles
  trip the breaker (window `sum_ms=15000, count=3`, reason `latency`).
  `force_close_breaker` → `closed`. One **10 ms successful** settle →
  `sum_ms=15010, count=4`, avg 3752 ≥ 1000 → breaker re-opens
  (`breaker_opened_total` 1→2, reason `latency`). The queue is effectively
  down again until the stale window expires (`window_seconds` is configurable
  up to 86400) unless the operator also re-runs `set_breaker_latency` to reset
  state — which nothing documents.
- **Rate leg.** `set_breaker_rate(q, 0.5, 300, 4)`; fail, fail, succeed, fail
  trips it (window 3F/1S, reason `rate`). `force_close_breaker`, one success,
  then a **single failure** → window 4F/2S = 0.67 ≥ 0.5 → instant re-open
  (`breaker_opened_total` 1→2).

The runbook sells `close-breaker` as "force closed + slow-start"; the operator
intent is "resume work now". The rate half of the stale-window state predates
this range (0035), but 0036 (in range) added the latency window without
extending the verb's reset list, and 0037 (in range) re-authored the verb
verbatim. None of the new tests cover force-close-after-window-trip.

**Fix:** make `force_close_breaker` (and arguably `trip_breaker`) zero
`breaker_window_start/failures/successes` and
`breaker_latency_window_start/sum_ms/count` exactly like the settle-close path,
plus a contract test for force-close-then-one-settle on both window types.

## F2 (minor, design) — `prune_queue_audit` erases audit history without attribution

`src/taskq/sql/migrations/0039_queue_audit_prune.sql:22`. Three compounding
choices:

- It is the only mutating verb in the plane with no `p_actor` — the CLI
  enforces `--actor`/`TASKQ_ACTOR` on every other mutation and even marks this
  command `destructive=True`, yet no actor exists anywhere in its path.
- It writes no self-audit row, so pruning the audit log leaves no trace in the
  audit log.
- `prune_queue_audit(1)` is legal: an operator can reduce retained history to
  the last hour, silently.

0.6.5's stated purpose is operator attribution; 0.6.6 adds an
attribution-free eraser for it. Bounds themselves verified correct
(NULL/0/−5 → TQ422; 2,400,000 hours does not overflow `make_interval`).
Also noted: the DELETE is unbounded (no batch cap), unlike every janitor
pass — acceptable at operator-verb volume but a deviation from the repo's own
bounded-maintenance convention.

**Suggested:** add a `p_actor`, self-record an `audit_pruned` row (cutoff +
count + actor), and/or a retention floor.

## F3 (minor, consistency) — `list_queue_audit` unknown queue → silent empty page

`src/taskq/sql/migrations/0037_queue_audit.sql:80-90`. Every other
queue-scoped read model (ADR-019/021 discipline; `list_jobs`) raises the TQ001
marker for an unknown queue before anything else. Here a typo'd queue name
reads as "no operator history" — a bad failure mode for an audit surface
specifically. It is declared honestly (`PUBLIC_ERRORS` lists TQ422 only) and
encoded in tests, so it is a deliberate-looking but discipline-breaking
choice. Verified live: unknown queue returns 0 rows, no error.

## F4 (minor, docs) — spec drift and stale runbook statements

- `docs/Task Queue 0.6 Circuit Breaker Specification.md` still says
  (line 6) "streak-based tripping only … no latency-based tripping" and §6
  lists rate and latency tripping as out of scope — while its status line
  promises "amendments discovered during implementation are recorded here
  before they land". Rate (0.6.3), latency (0.6.4), the audit table (0.6.5),
  and prune (0.6.6) all shipped with excellent migration headers but zero spec
  amendments. The spec is now materially wrong about the contract surface it
  governs.
- `docs/Flow Control Operator Runbook.md:221-223` still says manual
  trip/force-close leave no record and "a queue-scoped audit table that would
  cover those too is a documented follow-up" — contradicting the §12 audit-log
  section added in the same range. `:240` (§10 step 7) still says "there is no
  config-history view yet"; 0.6.5 is that config history.

## F5 (minor, audit fidelity) — unlocked before-snapshots in the replaced verbs

For example `src/taskq/sql/migrations/0037_queue_audit.sql:126-129`. All six
CREATE OR REPLACE'd verbs read their `before` snapshot with a plain SELECT
before the guarded UPDATE/upsert. Two concurrent operators can interleave so
one audit row records a `before` that skips the other's committed change (the
chain shows x→z and x→y, with no y→z). Config state itself is safe — the
upsert serializes on the row — only the audit narrative can misreport, and
only under concurrent operator writes on the same queue.

## F6 (observation, document it) — reap-terminal failures are invisible to the latency window

`0036_breaker_latency_tripping.sql:161-162` samples latency via
`NEW.finished_by_attempt_id`, which `reap_job` never stamps
(`0001_initial.sql:591`). Verified live: a lease-expiry terminal failure lands
with `finished_by_attempt_id = NULL`; streak and rate count it, the latency
sample is silently skipped. Defensible — latency tripping targets
slow-*success*, and failures have their own triggers — but nothing documents
the exclusion.

The dangerous variant was hunted and **ruled out**: a stale
`finished_by_attempt_id` from a previous job life cannot feed a bogus
multi-day sample, because `redrive_job` explicitly nulls the field
(`0009_workflows.sql:1279`) and no other terminal→requeue path exists.

## P1 (major, pre-existing 0031 — escalate) — half-open wedge

The settle trigger only reacts to `failed`/`succeeded`
(`0031_circuit_breaker.sql:168-172`) and `_breaker_gate` throttles
unconditionally while `half_open` (`0031:100`). Reproduced live: trip → probe
claimed → probe settles as **cancelled** (cancel-pending fail path) → breaker
stuck `half_open`; every subsequent claim returns `throttled retry=1`,
forever. No automatic recovery exists; the manual exits are
`force_close_breaker` (unreliable per F1 when windows are stale) or
`trip_breaker` to restart the cycle. The same wedge follows a released,
snoozed, or expired-with-budget probe — the requeued probe job can never be
re-claimed because all claims are throttled.

Out of range (0031 state machine), but the range rewrote the whole trigger
twice (0036, 0038) and preserved it. Suggested follow-up slice: treat a
non-terminal probe outcome as probe failure, or add a half-open deadline in
`_breaker_gate`.

## Explicit confirmations (what was verified and held)

- **0038 write-skip correctness.** The seven-field `IS DISTINCT FROM` guard
  (`0038:154-160`) cannot starve a required write: failures always change the
  streak (old+1); any window advance changes its start or a counter; rollover
  timestamps cannot collide with the old start (window ≥ 1 s). Trip
  evaluation runs before the write decision and depends only on state that,
  when unchanged, was legitimately not written. Empirically: identical state
  trajectories and identical event streams across 0.6.3 / 0.6.5 / 0.6.6 for a
  scripted streak drive (trip at exactly threshold, `throttled` with correct
  decreasing retry, single-flight probe, close stamps `ramp_started_at`),
  with `queue_flow.xmin` **stable** across healthy successes on 0.6.3 and
  0.6.6 and **churning per success** on the 0.6.4/0.6.5 body — i.e. the
  regression the 0038 header describes is real, and 0038 restores the 0.6.3
  write pattern exactly.
- **The 0.6.4/0.6.5 intermediate regression never shipped.** No release
  covers 0.6.4/0.6.5 standalone; a normal `migrate()` run applies 0036→0038
  in one pass, so only a deployment cut from mid-range `main` ever executed
  the per-success write body.
- **Trip-reason precedence** streak > rate > latency confirmed in code
  (`0038:132-134`) and live (a settle that streak-trips and latency-trips
  simultaneously reports `streak`).
- **Latency math.** The integer division `v_lat_sum / v_lat_count >=
  threshold` is exactly equivalent to the true-average comparison for an
  integer threshold (floor(x) ≥ T ⟺ x ≥ T); division-by-zero is unreachable
  (count ≥ 1 whenever evaluated); the tumbling window rolls over correctly
  (verified: count resets to 1, no trip across a window boundary). Note the
  trip may legitimately fire on a *fast* sample that completes `min_volume`
  while the average is over threshold — inherent to average-based tripping.
- **No-latency/no-rate breaker ≡ 0.6.3** in both state trajectory and write
  pattern (the 0036 header's claim is true only as of 0038; see above).
- **0038's "no contract bump" is legitimate.** Matches the 0028 precedent
  (body-only CREATE OR REPLACE, no meta write, no precondition block).
  `verify()` digests trigger *definitions* and function *identities*, never
  bodies, and passed with the unchanged manifest on both majors. The 0034
  counter-precedent (body-only but bumped) is distinguished by wire-visible
  vocabulary changes; 0038 changes nothing application-visible — application
  roles have no direct `queue_flow` reads, and nothing consumes
  `queue_flow.updated_at` as a per-settle freshness signal.
- **0037 verb replacement fidelity.** Signatures, defaults, volatility,
  owner, and grants byte-identical (verify green); `created`/`updated` return
  semantics proven equivalent to the 0031/0033 `EXISTS` originals
  (`v_before IS NOT NULL` is true exactly when the row existed, including the
  all-NULL-config row case). Live audit probes: correct `{before, after}` for
  fresh row (`before: null`), update, rate/latency/aging setters, and
  trip/force-close state transitions with actor attribution; failed verbs
  (TQ001/TQ422) leave **zero** audit rows (atomicity verified); keyset
  pagination exact; bounds TQ422 on limit 0/101, NULL queue, before_id 0.
- **Hardening.** Every new/replaced function: SECURITY DEFINER, owner
  `taskq_owner`, pinned `search_path`, PUBLIC revoked, grants exactly per
  manifest — enforced by `verify()` on both majors and probed live
  (`_audit_queue` and direct `queue_audit` INSERT denied to `taskq_operator`
  with 42501; observer can list, producer cannot; prune is
  housekeeper+operator only). Note the migrations' self-check DO blocks cover
  only secdef + pinned path; ownership/PUBLIC-revoke/grants are enforced by
  `verify()`, which the suite runs.
- **Hot-path cost.** A queue with no `queue_flow` row pays ~nothing (the
  trigger's SELECT finds no row, takes no lock). The latency feed is one
  `job_attempts` pkey lookup per terminal settle, strictly opt-in
  (`breaker_latency_threshold_ms IS NOT NULL`). The trigger's `FOR UPDATE`
  adds **no new serialization point**: measured concurrent settles on the
  same queue block identically (~1.0 s hold → ~1.0 s wait) for a queue with
  no `queue_flow` row, a flow-row-without-breaker queue, and a breaker queue
  alike — the 0.4 `queue_counters` trigger already serializes settles
  per queue, so the breaker lock is strictly inside the existing envelope.
  0037 writes only on operator verbs; 0039 is maintenance-only.
- **Error codes.** `set_breaker_latency` mirrors `set_breaker_rate` exactly
  (TQ501 capability gate → TQ422 bounds → TQ001 no-configured-breaker; all
  probed). `prune_queue_audit` and `list_queue_audit` raise TQ422 as declared.
  `PUBLIC_ERRORS` matches observed behavior for every probed verb.
- **CLI/transport wiring.** Ranges match SQL bounds; `--off`/`--threshold-ms`
  mutually exclusive; `set-breaker-latency` passes the enforced CLI actor;
  new verbs are SQL-transport-only by design (HTTP raises the capability
  error); no argument-order defects.

## Not verified

- The environment-gated suites: `TASKQ_PLAN_CHECKS=1` (1M-row structural
  EXPLAIN checks) and the Redis-backed OutlabsAuth integration test.
- Full loadlab L-tier runs — only the packaged smoke tests ran (the `d8302af`
  loadlab changes are harness-only; no SQL surface).
- pg_dump/pg_restore equivalence beyond what `test_installer_matrix`
  exercises (it ran green on both majors).
- The TQ501-inactive branch of the 0.6.x verbs is dead code on any
  legitimately migrated database (the `circuit_breaker` capability activates
  at 0032, before 0035/0036 exist); it is defensive posture only and was not
  separately exercised.
