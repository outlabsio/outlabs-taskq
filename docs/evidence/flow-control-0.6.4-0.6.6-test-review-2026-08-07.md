# Flow-control 0.6.4–0.6.6 test and load-harness review — 2026-08-07

- **Scope:** commits `a165839..d8302af` — the tests and harness, not the SQL
  (the SQL review is the companion document
  `flow-control-0.6.4-0.6.6-review-2026-08-07.md`). Audited:
  `tests/test_contract_0_6_4.py`, `test_contract_0_6_5.py`,
  `test_contract_0_6_6.py`, `test_breaker_settle_write_skip.py`, the anchor
  edits across `test_cli_v1` / `test_s2_sql_transport` / `test_manifest_parity`
  / `test_verify_manifest` / `test_installer_matrix` / `test_bench_smoke` /
  `test_contract_0_2_0` and the 0.6.x version-string tests, and the load
  harness changes in `src/taskq/loadlab/` (`d8302af`).
- **Method:** independent skeptical reproduction on throwaway PostgreSQL 16.14
  and 18.4 (Docker, fresh clusters), plus **mutation testing**: deliberately
  broken variants of `taskq._breaker_on_settle` and `taskq.prune_queue_audit`
  were installed in a migrated scratch database and the relevant test files
  re-run, to measure what the tests can actually detect. Load scenarios were
  run at toy (via the suite), small (all ten), and full (the four touched
  ones), at HEAD and — for the changed scenarios and the surfaced defect — at
  baseline `a165839` via a git worktree.

## Verdict

**The suite-green claim reproduces — and is in fact better than claimed** (no
B4 failure exists; see T5). The new contract tests are genuine, not
tautological: the latency simulation measures exactly what the trigger
measures, the audit tests pin actor and before/after values, and the write-skip
xmin probe demonstrably catches a reverted optimization. The four load-harness
changes are legitimate calibrations to public API bounds and the worker's
documented fail-closed budget — none masks a plane defect, and the old L5/L10
full-tier assertions are proven unrunnable at baseline.

Two things temper that. First, mutation testing escaped five times: the new
tests cannot see an inverted trip-reason precedence, a latency window fed only
by successes, a latency window that never rolls over, a write-skip that drops
rate-success persistence (demonstrated live as a false rate trip), or a prune
cutoff off by a factor of 3600. Second, running the small tier — which this
range finally made runnable — surfaced a **real, pre-existing single-flight
violation in the breaker's half-open probe election** that the always-green toy
tier cannot see. The green suite is real; its resolution has limits that are
now measured.

| # | Severity | Finding |
|---|---|---|
| T1 | major (plane defect, pre-existing, surfaced here) | breaker half-open admits 2 probes under a concurrent fan — single-flight spec guarantee violated; reproduces at baseline |
| T2 | major (test gap, demonstrated) | write-skip's rate-success axis has zero coverage — a skip-list regression causes false rate trips and all 43 breaker tests stay green |
| T3 | medium (test gaps, demonstrated) | trip-reason precedence, latency-fed-by-failures, and window rollover are all unpinned — mutations of each pass every breaker test |
| T4 | minor (test gap, demonstrated) | prune cutoff unit is under-constrained — hours→seconds mutation passes all 0.6.6 tests |
| T5 | claim discrepancy | the "single documented B4 failure" does not exist: 0 failures in 36 B4 executions across majors and commits, and nothing documents a flake; a plausible planner-flake mechanism exists but is pre-existing and unobserved |
| T6 | calibration confirmed | all four loadlab changes are legitimate; old code proven unrunnable at full tier; L5's proof scope honestly narrowed (documented below) |
| T7 | process gap | load invariants are only gated at toy tier; the tier that catches T1 (~36% per run) is not run by any CI gate |
| T8 | anchors verified | every anchor recomputed independently and correct; none missed |

## T1 (major) — half-open single-flight violated under a concurrent fan

`src/taskq/loadlab/_scenarios.py:1536` (`half_open_single_flight_one_probe`),
defect in `src/taskq/sql/migrations/0031_circuit_breaker.sql:81-93`
(`taskq._breaker_gate`).

L8 at small tier (fan of 8 fresh attested runner connections claiming
concurrently after cooldown) recorded `probes_admitted=2` — two claims returned
`claimed` while the breaker was half-open. Reproduction: **2/7 runs at HEAD,
2/4 runs at baseline `a165839`** (PostgreSQL 16.14). The gate SQL is byte-
identical across the range, so this is pre-existing 0.6.0 behavior surfaced by
the now-runnable small tier — not a regression of these commits.

Mechanism (from the gate's shape): the state read and the probe election are
not atomic. `_breaker_gate` reads `breaker_state` with a plain SELECT, then
calls `pg_try_advisory_xact_lock`. Probe #1 wins the lock, flips the row to
`half_open`, performs its claim, and commits — releasing the transaction-scoped
advisory lock at the instant its `half_open` write becomes visible. A
concurrent claim whose gate SELECT ran on a pre-commit snapshot (still sees
`open`, cooldown elapsed) but whose lock attempt executes after that commit
acquires the freed lock and becomes probe #2. The fan makes the timing hit
often. During the fan no settles occur, so no other path explains a second
`claimed`.

Blast radius is bounded — the settle trigger's state machine stays safe (a
second failing probe finds the breaker already re-opened and is ignored; a
second succeeding probe lands as a normal closed-state success) — but the 0.6
spec §2.4 ("at most one probe claim outstanding") and the L8 gate text
("a concurrent fan does not admit a second") are both violated, and a dead
downstream sees k probes per cooldown instead of one. Remediation shape: make
the election atomic — re-check state under the row lock after acquiring the
advisory lock, or elect via
`UPDATE ... SET breaker_state='half_open' WHERE breaker_state='open' AND breaker_tripped_at + cooldown <= now()`
and probe only when the UPDATE takes the row. Belongs in the same follow-up
slice as the probe-wedge finding (P1) of the companion SQL review — half-open
is the weak corner of the breaker.

## T2 (major, test gap) — the write-skip's rate-success axis is uncovered

`tests/test_breaker_settle_write_skip.py` proves three axes: a healthy no-op
success does not write (xmin stable — and this genuinely detects, see the M0
control below), a latency-window advance still writes, and streak trips still
fire. It never exercises a **rate-configured** breaker, and no 0.6.3 rate test
covers a success-diluted window.

Mutation **M4** removed `breaker_window_start`/`breaker_window_successes` from
the 0038 skip conditional — so successes stop being persisted into the rate
window — and **all 43 breaker tests passed**
(`test_contract_0_6_0..0_6_6`, `test_breaker_settle_write_skip`,
`test_flow_control_composition`). Demonstrated consequence on the mutated
database: with `set_breaker_rate(q, 0.7, 300, 3)`, the settle sequence
S, S, F, F, F opens the breaker with reason `rate` at window state 3F/0S — the
true ratio is 3F/2S = 0.60 < 0.70. A refactor of exactly the conditional 0038
introduced could ship false rate trips with a fully green suite.

The shipped conditional is correct (verified in the companion review); this
finding is about detection power. **Fix:** one test — rate breaker configured,
interleave successes and failures below the ratio, assert no trip AND assert
`breaker_window_successes` advanced (or xmin churn on success under rate
config).

## T3 (medium, test gaps) — three more escaped mutations

Each mutation was installed in the migrated scratch database and run against
all 43 breaker tests; each passed 43/43.

- **M1 — precedence inverted** (`CASE` reordered so `latency` outranks
  `streak`): no test creates a settle where two triggers fire at once, so the
  documented streak > rate > latency reason precedence
  (`0038_breaker_settle_write_skip.sql:132-134`) is pinned by nothing. A live
  probe in the companion review shows the shipped code reports `streak` when
  streak+latency trip together; no test would notice a regression.
- **M2 — latency fed only by successes** (`AND NEW.status='succeeded'` added
  to the feed guard): the 0.6.4 header's "fed on EVERY terminal settle" —
  i.e. slow *failures* raise the latency average too — is untested.
  `test_latency_trips_on_slow_successes` uses only successes;
  `test_no_latency_config_behaves_as_0_6_3` uses failures but with latency
  unconfigured.
- **M3 — window never rolls over** (time-based reset removed, window becomes
  all-time): no test crosses `breaker_latency_window_seconds`, so tumbling
  semantics are unpinned. An all-time average breaker (trips on ancient
  history) would pass the suite.

## T4 (minor, test gap) — prune cutoff unit under-constrained

Mutation **M5** changed `make_interval(hours => …)` to
`make_interval(secs => …)` in `taskq.prune_queue_audit` — a 3600× retention
error — and all three `test_contract_0_6_6.py` tests passed. The age fixture
backdates rows 100 days and prunes at 720 hours; any cutoff between ~minutes
and ~99 days yields the same observable result (`deleted == 2`, recent rows
survive). **Fix:** one boundary case — a row aged ~2 hours must survive
`prune_queue_audit(720)` and be deleted by `prune_queue_audit(1)`.

What the prune tests do prove well: age-scoped global deletion with exact
counts, idempotent re-run, TQ422 bounds (0/−5/NULL), and grants
(operator+housekeeper allowed, runner denied at 42501).

## T5 — the B4 claim does not reproduce, in either direction

The claim under review was "full suite green except a single documented
failure `test_bench_smoke.py::…[B4]`, a pre-existing non-deterministic planner
flake."

- **The suite is fully green.** Six complete suite runs across this and the
  companion review (three per major, fresh clusters): **808 passed, 2 skipped
  every time** — the two skips are environment-gated
  (`TASKQ_PLAN_CHECKS`, `TASKQ_TEST_REDIS_URL`), not failures. B4 passed in
  every run.
- **B4 would not fail in isolation either:** 30 dedicated executions — 15 at
  HEAD on PG16, 5 at HEAD on PG18, 10 at baseline `a165839` on PG16, each on a
  fresh bench database — 30/30 passed.
- **Nothing documents a flake.** No xfail/skip marker, no comment, no doc
  mentions B4 or a bench flake anywhere in the tree.
- **The mechanism is plausible but pre-existing:** every bench scenario ends
  with `_representative_claim_plan` (`src/taskq/bench.py:302-327`), which
  `EXPLAIN (ANALYZE)`s the claim-candidate query and asserts `jobs_claim_idx`
  is used and **no Seq Scan on jobs** occurs. On a near-empty toy-tier table
  that is a legitimate planner choice away from failing (stats/autovacuum
  timing), so a rare flake is credible — but the assertion dates to the
  initial public release (`a16ff39`) and is untouched by `a165839..d8302af`.

Conclusion: if B4 ever failed for the team it was environmental and
pre-existing, **not a regression of these commits** — but describing it as a
standing "documented failure" is wrong on both words, and no such
qualification of the green claim is needed for this range.

## T6 — load-harness changes: legitimate calibration, not masking

All four `d8302af` changes align the harness with bounds the plane has always
enforced, or with the worker's documented design budget:

- **`enqueue_many` chunking** (`_chassis.py:242-257`): `taskq.enqueue_many`
  rejects >1000 specs (TQ422, manifest bound). Full-tier cohorts (5000)
  previously made the seeding step an invalid API call.
- **L10 `pairs = min(50, …)`** (`_scenarios.py:934`): `pairs` doubles as the
  claim batch; `claim_jobs` bounds batch at 1..50. Full tier
  (`storm_workers=20` → 60) was an invalid call. The fairness gate's
  semantics are unchanged (50 distinct keys remain ample against a burst-1
  bucket); both key-level checks are database-truth counts.
- **L7 `min(50, cohort)`** (`_scenarios.py:1201`): same bound; the enclosing
  `while failed < cohort` loop verifiably drains the remainder in batches.
- **L3 `claim_limit = min(100, cohort*2+10)`** (`_scenarios.py:1357`):
  `claim_schedules` bounds limit at 100. The single-`as_of` invariant the
  scenario depends on is preserved because the due cohort is ≤ 80 at every
  defined scale (`storm_workers*4`), which the comment states explicitly.
- **L5 window bound** (`_scenarios.py:530-537`): the substantive one. The
  worker's documented contract (`src/taskq/worker.py:665-671`) is
  bounded-retry-then-fatal for **every** claim-path error class: no TaskqError
  is instantly fatal, and a persistent consecutive streak
  (`claim_fatal_threshold`, here 8) fails the service closed **by design** —
  retryable errors included. The old Phase-1 window (`nudges × 0.15s` = 6s at
  small, 15s at full) outlasts the ~5.4–6.4s it takes 8 backoff-paced errors
  (0.2 base/1.0 cap) to accumulate, so the old assertion "worker survives the
  retryable window" contradicted the worker's own design at scale.
  **Baseline evidence:** running the old L5 at full tier, the worker went
  fatal during Phase 1 and 0/100 jobs ever settled. The fix bounds the window
  to ≤2.5s at every tier, inside the budget.

  What honestly changed: L5's Phase 1 now proves *a sub-budget transient
  window is survivable* (measured at full tier: 5 consecutive errors against
  the budget of 8), not *retryable windows in general are survivable* — the
  latter was never true and never can be under the documented fail-closed
  posture. Phase 3 still proves sustained corruption fails closed, and the
  post-chaos conservation checks (`attempts == {1: nudges+2}`, all delivered)
  still hold. Note the margin is 5-of-8, not generous; jitter keeps it safe
  but the 2.5s constant deserves a comment tying it to the threshold
  arithmetic.

**Invariant strength:** the checks are database-truth conservation checks, not
harness self-reports — per-job attempt counts from `job_attempts`
(`attempts == {1: N}` catches both loss and duplicate execution), status
counts from `jobs`, worker-fatal from service snapshots. They caught T1, which
is the strongest possible evidence they bite. Two soft spots: L5's
`claim_backoff_active` upper bound (`max(3, nudges*0.6)`) is only tight at toy
scale and near-vacuous at small/full, and L8's Phase-4 leaked second probe
(when T1 fires) is not itself detected by the row-count check.

**Results:** HEAD small tier — L1–L7, L9, L10 all invariants green; L8 fails
~36% of runs on T1 (also at baseline). HEAD full tier — L3, L5, L7, L10 all
invariants green (L5 310s wall). `test_load_smoke` (toy, all ten scenarios,
invariants enforced) passed in all six suite runs.

## T7 — the tier gap

Only the toy tier runs under any gate (`test_load_smoke`). Toy's L8 fan is 6
and has never tripped T1 in any recorded run; small's fan of 8 trips it ~36%
of the time. The range's stated purpose ("make the full scale tier run
end-to-end") is achieved — but nothing schedules small/full runs, so their
findings only exist when someone runs them by hand. A periodic small-tier job
(even weekly) would have caught T1 at 0.6.0.

## T8 — anchors: complete and correct

Independently recomputed from the installed package, all matching the edited
assertions, with the arithmetic accounted for:

- `FUNCTIONS` 110 → **114** (+`_audit_queue`, `list_queue_audit`,
  `set_breaker_latency`, `prune_queue_audit`) — `test_verify_manifest.py:319`.
- `PUBLIC_FUNCTIONS` 72 → **75** (+3 granted verbs; `_audit_queue` is
  grantless and correctly excluded) — `test_s2_sql_transport.py:89`.
- `PUBLIC_ERRORS` covers exactly the 75 public functions — no missing, no
  extra (computed set difference: both empty).
- CLI `COMMAND_SPECS` 71 → **74** (+`queue.audit`,
  `queue.set-breaker-latency`, `maintenance.prune-audit`) —
  `test_cli_v1.py:71`.
- `METHOD_FUNCTIONS` stays **52**: the three new verbs joined the
  `direct_sql_only` set, matching the precedent of every prior breaker
  operator verb (the S2 protocol registry is distinct from the CLI registry).
- Version strings: `0.6.6` bumped in all eight anchor tests; runtime
  supported-version sets gained 0.6.4/0.6.5/0.6.6 in all five frozensets;
  migration-ledger lists gained the four new ids in all four sites
  (bench smoke, installer matrix ×3-in-file, contract 0_2_0);
  `test_manifest_parity` behavior groups gained all three new public verbs;
  `docs/CLI.md` documents all three new commands.
- Missed-anchor sweep: no stray `"0.6.3"` literals outside the runtime's
  historical supported lists and migration files.

## What the new tests prove well (credit where due)

- **0.6.4 latency:** the trip test isolates latency correctly — streak
  threshold 100, no rate config, successes only, so nothing else can trip;
  reason asserted `latency`. The slow-execution simulation (backdating the
  settling attempt's `job_attempts.claimed_at`) is exactly the quantity the
  trigger measures via `finished_by_attempt_id`, deterministic by
  construction. Caveat: it stubs the input signal — nothing end-to-end proves
  `claimed_at` stamping feeds real wall-clock latency, accepted for
  determinism.
- **0.6.5 audit:** actor values asserted exactly (four distinct actors),
  before/after asserted as exact dicts including the `before: null` create
  case, TQ001 and TQ422 failures each proven to leave zero audit rows,
  scoping/newest-first/keyset/limit-validation and role grants all covered.
- **0.6.6 write-skip:** the xmin mechanism genuinely detects — control
  mutation **M0** (skip removed, 0.6.4 behavior restored) fails
  `test_healthy_success_does_not_write_queue_flow` immediately and precisely.
  Failure-writes and latency-advance-writes are asserted. The gap is T2's
  axis, not the mechanism.

## Not verified / limits

- `TASKQ_PLAN_CHECKS=1` (1M-row EXPLAIN suite) and the Redis-backed OutlabsAuth
  integration remain environment-gated and unexercised.
- Full tier was run only for the four touched scenarios (L3/L5/L7/L10);
  L1/L2/L4/L6/L8/L9 ran at small, not full. L8 at full (fan 20) would likely
  reproduce T1 more often; not measured.
- An actual B4 failure was never observed, so the planner-flake mechanism is
  identified but its firing conditions are unconfirmed.
- The baseline old-code L5 full-tier run completed formally: fatal in Phase 1
  (0/100 settles observed mid-run), then
  `TimeoutError: queue 'load_l5' did not reach 100 succeeded jobs` after 617s.
  The old-L10 invalid-batch error (60 > the 1..50 claim bound) was not captured
  in that run's truncated output; the bound itself is contract-enforced and
  T2-tested, so the conclusion rests on that.
- Mutation coverage is targeted, not exhaustive: five semantic mutations plus
  one control across the trigger and prune; other functions (audit verbs,
  list/read paths) were not mutation-tested — their tests were judged strong
  by inspection.
