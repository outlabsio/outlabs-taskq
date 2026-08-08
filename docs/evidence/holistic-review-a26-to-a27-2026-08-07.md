# Holistic pre-release review: v0.1.0a26 → 0.1.0a27 (contracts 0.3.1 → 0.6.6) — 2026-08-07

- **Scope:** the full delta `v0.1.0a26..HEAD` (19 commits, ~97 files, +13.5k
  lines): migrations 0022–0041, the flow-control plane end to end, the runtime/
  CLI/loadlab/bench changes, tests, docs, and the release packaging for the
  unpublished 0.1.0a27. One reviewer, all dimensions, single report.
- **Method:** everything executed, nothing taken from prior reviews on trust.
  Fresh PostgreSQL 16.14 and 18.4 (Docker). Full suite on both majors. Fresh
  0001→0041 installs + `verify()` on both majors. Side-by-side a26-contract
  databases (chain-stopped at 0021) for behavioral and plan comparison.
  auto_explain capture of the live claim path. Loadlab L8 at small tier ×8.
  Wheel built, installed into a clean venv outside the checkout, artifact
  smoke (`--mode core`, fresh + a17-upgrade legs) run green. The three prior
  segmented reviews (SQL, tests, docs — same folder) covered 0.6.4–0.6.6; this
  pass re-verified their fixes (0040/0041) and covered the rest fresh.

## Overall assessment

The plane is well built. Twenty migrations apply cleanly and immutably on
both majors with the manifest exact; the state machines (breaker, GCRA,
counters) are correct under the interleavings I could construct; the 0040/0041
fixes for the previously-found breaker defects are correct and complete; the
packaging story (wheel → clean install → migrate 0001→0041 → verify, plus an
a17-upgrade leg) is genuinely release-grade. But the holistic pass found one
defect every earlier, narrower pass was structurally unable to see: **priority
aging (0.6.1) silently changed the claim candidate plan for every queue —
configured or not — from an O(1) index-ordered pick to an O(ready-depth)
scan-and-sort**. On a 20k-deep unconfigured queue that is a ~100× per-claim
regression versus a26, it grows with backlog, and every plan gate in the
repository checks the pre-aging query shape, so nothing red-flags it. For a
queue library whose core promise is cheap claims under load, that is
release-blocking.

## RELEASE VERDICT: **DO-NOT-SHIP** 0.1.0a27 until H1 is fixed

H1 alone blocks. It breaks the release's own headline invariant ("a queue that
configures nothing behaves exactly as before") in the dimension that matters
most for this library — claim cost under backlog — on both PG majors, for
every consumer. The fix is small and local (a branched ORDER BY), the
re-verification path is already built (the plan gates just need to test the
real query), and everything else in the delta is ship-quality. Fix, re-run the
gates, then ship.

## Findings (most severe first)

### H1 — CONFIRMED, release-blocking: claim candidate selection is O(ready-depth) for every queue since 0.6.1

**Where:** `src/taskq/sql/migrations/0033_priority_aging.sql:154-157` and
`:176-179` (the live `_claim_jobs_unattested` overload-1 body);
gates that miss it: `src/taskq/bench.py` `_representative_claim_plan` and
`tests/test_plans.py:44,135`.

**What:** 0033 rewrote the candidate ORDER BY to
`j.priority - CASE WHEN v_aging_seconds IS NULL THEN 0 ELSE LEAST(1000, floor(age/aging)) END, j.scheduled_at, j.id`.
The migration header claims "A CASE guard makes an unconfigured queue
byte-identical." Ordering-wise, true. Plan-wise, false: PostgreSQL folds the
CASE to `0` but does not simplify `priority - 0` to `priority`, so the sort
key no longer matches `jobs_claim_idx (queue, priority, scheduled_at, id)`.
The planner must materialize every ready row and top-1 sort it — per candidate
pick, inside the claim loop.

**Measured (identical 20k-ready unconfigured queue, same seed):**

| | a26 contract (0021) | HEAD (0041) |
|---|---|---|
| PG 16.14, claim 1 job | **0.47–0.5 ms** | **47–53 ms** (first call 1055 ms) |
| PG 18.4 (10k rows) | — | ~15 ms |

auto_explain of the live nested statement:

```
LockRows
  -> Sort  (actual rows=1)
       Sort Key: ((j.priority - 0)), j.scheduled_at, j.id
       Sort Method: quicksort  Memory: 3660kB
       -> Seq Scan on jobs j (actual rows=20000)
```

**Failure scenario:** any queue that develops a backlog. At 100k ready rows a
single-job claim costs hundreds of ms of sort CPU; workers claim serially
against the same table; claim throughput collapses exactly when the queue is
deepest — a metastable regime where backlog makes claims slower, which deepens
backlog. No configuration triggers it and none avoids it.

**Why every gate missed it:** `tests/test_plans.py` (the 1M-row structural
EXPLAIN suite) is env-gated and skipped in every lane, and its claim probe
(`:44`) asserts the **pre-aging** ORDER BY; the bench
`_representative_claim_plan` gate likewise EXPLAINs the old query text —
after `e693fc0` it does so deterministically, which makes it a deterministic
assertion about a query the claim path stopped running at 0.6.1. Functional
tests use tiny queues where 50 ms is invisible; loadlab asserts conservation
invariants, not claim latency (its tiers passed *with* the O(n) claim —
throughput was simply lower).

**Fix:** branch the candidate query on the config:
`IF v_aging_seconds IS NULL` use the original
`ORDER BY j.priority, j.scheduled_at, j.id` (restores the index plan for
unconfigured queues — the overwhelmingly common case); use the expression
ORDER BY only when aging is configured. Ship as a body-only migration (0042,
0028/0038/0040 precedent). Then: point `_representative_claim_plan` and
`test_plans.py` at BOTH real query shapes (plain asserts index order;
aging-configured asserts whatever plan is accepted and documents its cost),
and run at least the plain-shape plan check in the normal CI lane. Document in
the runbook that aging, when configured, prices the claim path at
O(ready-depth) — the 0033 header's "no writes, no settle-path work" is true
but silent about claim-path cost even for the configured case.

### H2 — CONFIRMED, medium (design honesty, not a bug): the counters plane is an unconditional per-queue cost on every settle

**Where:** `src/taskq/sql/migrations/0022_queue_counters.sql:133-136` (trigger
on every status transition), enabled for all queues by 0023.

**What:** the "everything is off-by-default; an unconfigured queue is
byte-identical to a26" invariant holds for every *control* feature (verified:
no throttle/trip/aging verdict without config) but not for the *observation*
plane: since 0023, every queue pays a `queue_counters` row UPDATE per status
transition, and concurrent settles on the same queue serialize on that row for
the trigger-to-commit window. Measured: two concurrent `complete_job` on one
unconfigured queue — a26: second settle waits **0.001 s**; HEAD: **1.004 s**
(the full artificial hold; i.e., fully serialized). The accounting itself is
correct (clamped decrements, totals monotonic, DELETE handled, L6 conservation
checks green at small tier), and the design is deliberate (health verdicts
need the projection). But the README/spec/runbook "off-by-default,
byte-identical" language overstates; for queues with high concurrent settle
rates and long settle transaction tails (dependent promotion, follow-up
fan-out) this is a real throughput ceiling a26 did not have.

**Fix (docs now, design later if it bites):** state the observation-plane
cost explicitly wherever the invariant is claimed; if a consumer ever hits the
serialization, a statement-level transition-count trigger is the escape hatch.
Not release-blocking: the cost is bounded, documented-adjacent, and two
releases of soak (0.4.0 shipped it internally) show no incident.

### M1 — CONFIRMED, minor: 0041's half-open deadline is anchored to the trip, not the election

**Where:** `src/taskq/sql/migrations/0041_breaker_half_open_atomic.sql:74-80`.

**What:** the wedge deadline is `now() > breaker_tripped_at + 2*cooldown`, and
election does not restamp `breaker_tripped_at`. A probe elected later than
`2*cooldown` after the trip (idle queue, first claim arrives late) is
instantly invalidated: the next claim re-opens the breaker while the probe is
still running, and the probe's eventual settle is ignored (state is `open`).
Reproduced: trip → idle 2.5×cooldown → claim elects (half_open) → next claim
immediately re-opens (`opened_total` 2) with the probe in flight. The job
itself completes and settles normally — only its breaker signal is discarded —
and the cycle self-corrects on the next cooldown, so the cost is one wasted
open cycle on idle queues. **Fix:** stamp the election time (reuse
`breaker_tripped_at = now()` at election, or a dedicated column) so the probe
always gets a full window. Also worth one line in the runbook: cooldown must
exceed typical probe-job runtime, or automatic recovery cannot complete
(slow-probe re-opens are by design, but the constraint is currently
undocumented).

### Small notes (non-blocking)

- **No `docs/RELEASE-0.1.0a27.md`.** The a-series has release notes through
  a26; the version is bumped but no notes exist for the first artifact to
  carry the plane. Write them at tag time — this release changes operator
  surface (14 new commands) and migration count (+20).
- **Dev-setup drift:** `uv sync --extra dev --extra http` (the documented dev
  setup) leaves `outlabs_auth` missing and the suite fails collection on
  `tests/test_s3_outlabs.py`; the working set is `--extra dev --extra http
  --extra outlabs`. One line in CONTRIBUTING/CI docs.
- **Runbook aging section** (`§7`) should carry the H1-fix follow-through:
  after the fix, configured-aging queues still pay O(ready-depth) claims;
  operators sizing deep queues should know.

## Re-verification of the prior findings' fixes (all hold)

- **0040 (F1 — stale windows):** re-ran the original reproductions. Rate leg:
  after `force_close`, windows are zeroed; one success then one failure leaves
  the breaker **closed** (window restarts clean). Latency leg: post-close fast
  success stays **closed**, `opened_total` unchanged, no second
  `breaker_opened` event. Fixed, and byte-identical to the 0037 bodies
  otherwise (audit rows, TQ001 guards, before-capture intact).
- **0041 (T1 — single-flight):** the atomic
  `UPDATE … WHERE breaker_state='open' AND tripped_at+cooldown<=now()`
  election closes the snapshot-vs-advisory-lock race. Loadlab L8 at small
  tier: **8/8 green** (pre-fix: ~36% failure, 2 probes admitted). Lock-order
  analysis: the election takes the `queue_flow` row lock in the claim
  transaction; because `jobs_breaker_trg` fires before
  `jobs_queue_counters_trg` (alphabetical trigger order), settle transactions
  acquire `queue_flow` before `queue_counters` exactly as the claim path does
  — no ABBA deadlock; none observed across all L8 runs.
- **0041 (P1 — wedge):** a probe cancelled mid-flight (settles `cancelled`,
  never firing the breaker trigger) no longer strands the breaker: the next
  claim past the deadline re-opens (`opened_total`+1), a fresh cooldown and
  probe follow, and a succeeding probe closes it. Reproduced end-to-end.
- **Punch-list tests landed** (`e693fc0`): force-close rate-window,
  wedge-recovers, precedence (streak > latency), latency-fed-by-failures,
  latency-rollover, write-skip rate-success axis, prune boundary — the exact
  five mutation-proven gaps from the test review, plus `tests/test_load_scale.py`
  and a scheduled `load-small` CI job (ci.yml:504).
- **Docs punch-list landed:** runbook header now 0.4.0–0.6.6; `prune-audit`
  examples carry `--yes` (:286); §9 now says "DBA or `taskq_observer`
  connection, not the operator role" with the grant caveat (:199-200); the
  stale "audit table is a follow-up" and "no config-history view" sentences
  are gone. `17c87d6` surfaces SQLSTATEs through the CLI — the
  migrate-on-unbound path now shows the "target bind" remediation (and the
  artifact smoke asserts exactly that).

## Verified clean (the boundary of this review)

- **Suite:** 819 passed / 12 skipped on PostgreSQL 16.14 AND 18.4 (fresh
  clusters; the 12 skips are exactly the 10 `TASKQ_LOAD_SCALE`-gated
  scale-tier tests + `TASKQ_PLAN_CHECKS` + Redis integration).
- **Contract:** fresh 0001→0041 installs green through `migrate()`; `verify()`
  all 18 probes green on both majors; manifest `CONTRACT_VERSION` 0.6.6;
  0040/0041 confirmed genuinely body-only (identities, attributes, grants,
  trigger definitions unchanged — verify green with the unchanged manifest);
  no released-migration edits (ledger checksums intact).
- **State machines:** breaker trip/reopen/close and streak>rate>latency
  precedence (now also pinned by tests); GCRA emission-interval math correct
  (idle clamp to one burst, ≥1s retry hints, lazily-created flow rows only
  when rate configured); counters accounting exact under the trigger's
  clamped transitions; `v_saturated` concurrency-key aggregate is pre-existing
  a26 behavior, not new cost.
- **Off-by-default (control plane):** no gate returns a throttle/trip/aging
  verdict for an unconfigured queue (a26-identical verdicts verified); the two
  mechanical exceptions are H1 (defect) and H2 (documented-adjacent design).
- **Loadlab:** L8 single-flight 8/8 at small post-0041; earlier small/full
  tier runs (L1–L10) hold all conservation invariants; the small tier now runs
  on a scheduled CI job.
- **Packaging (first-time-PyPI path):** `uv build` produces
  `outlabs_taskq-0.1.0a27` wheel+sdist; wheel carries all 41 migrations and
  both console scripts; clean-venv install outside the checkout works
  (`taskq version` → 0.1.0a27); `artifact_smoke.py --mode core` green,
  including fresh-install (plan → migrate → unbound checkpoint with surfaced
  remediation → bind → migrate → verify → activation asserts) and
  a17-checksum upgrade legs; extras isolation asserted (core install refuses
  `taskq.http` with the named extra hint). Runtime supported-contract sets
  span 0.3.1…0.6.6, so an a27 client against an un-migrated a26 database is
  accepted — upgrade order (package first, migrate later) is safe.
- **Security posture:** every new/replaced function SECURITY DEFINER, owner
  `taskq_owner`, pinned search_path, PUBLIC revoked, grants exactly per
  manifest (verify-enforced on both majors; spot-probed live in the prior
  reviews and unchanged since).

## Not verified

- Full-tier loadlab this round (earlier evidence covers L3/L5/L7/L10 at full;
  L8 at full — fan 20 — has never been run post-0041; worth one run before
  tag).
- `TASKQ_PLAN_CHECKS=1` 1M-row suite (env-gated; note it currently asserts the
  wrong claim shape — part of H1's fix).
- The HTTP facade surface (flow-control verbs are SQL/CLI-only by design).
- PyPI upload mechanics themselves (twine/trusted-publisher config) — outside
  the repo.
