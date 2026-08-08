# H1 fix review (migration 0042) — 2026-08-08

- **Scope:** the fix for holistic-review finding H1 only — commits
  `711ffa5` + `2f87897` (`d959f48..HEAD`): migration
  `0042_claim_order_index_restore.sql`, `tests/test_claim_order_plan.py`, the
  `test_plans.py` claim drift-binding, ledger anchors, README/runbook/release-
  notes edits. Companion: `holistic-review-a26-to-a27-2026-08-07.md`.
- **Method:** reproduce, don't reason — fresh PostgreSQL 16.14 and 18.4;
  chain-stopped pre-0042 vs post-0042 databases; live-catalog
  `pg_get_functiondef` diff; the real function under `auto_explain`
  (nested statements) on identical 20k-ready seeds; behavioral aging probe;
  gate mutation (old body reinstated live); full-catalog ORDER BY sweep;
  full suite on both majors; `verify()` on fresh 0001→0042 installs.

## VERDICT: **SHIP 0.1.0a27**

The fix is correct, complete, faithful, and gated. The unconfigured claim
path is measurably back to index-backed one-row picks on both majors, aging
semantics are intact for queues that opt in, the no-bump/body-only claim
holds, no sibling instance of the H1 pattern exists anywhere in the live
contract, and the drift tether that would catch a reintroduction is in the
normal lane and demonstrably bites. The remaining findings are minor and none
blocks: a one-observation bench flake that predates the fix, a proxy test
that should self-tether, and a dropped boilerplate self-check.

**0033→0042 diff is faithful: YES.** Proven from the live catalog, not the
source: `pg_get_functiondef` of `taskq._claim_jobs_unattested(…,boolean)` on a
chain-stopped 0041 database vs a 0042 database differs in **exactly two
hunks** — the affinity-variant and main-variant candidate SELECTs, each
becoming `IF v_aging_seconds IS NULL THEN <bare> ELSE <aged> END IF`. Within
each hunk the bare branch's WHERE is line-identical to the aged branch's and
to 0033's; the aged ORDER BY is exactly 0033's expression with the
`CASE WHEN … THEN 0 ELSE … END` wrapper removed
(`LEAST(1000, floor(extract(epoch FROM (now() - j.scheduled_at)) / v_aging_seconds)::integer)`
— safe, since the branch guarantees `v_aging_seconds IS NOT NULL`). Zero
differences anywhere else in the ~190-line body: validation, pause/unknown
handling, ramp/cap/rate gates, saturated-key logic, scan-loop control,
claim UPDATE/attempt INSERT/event emit, and returns are untouched.

## The fix works (CONFIRMED, both majors)

Identical 20k-ready, zero-config queues, claims through the real public
`claim_jobs` under `auto_explain (log_nested_statements)`:

| | pre-fix (0041) | post-fix (0042) |
|---|---|---|
| PG 16.14 candidate plan | Seq Scan 20000 rows + `Sort Key: ((j.priority - 0))`, quicksort 3660 kB | `Index Scan using jobs_claim_idx … (actual rows=1)`, **no Sort** |
| PG 16.14 claim latency | 47–53 ms | **~1.3 ms** |
| PG 18.4 candidate plan | (same pathology) | `Index Scan using jobs_claim_idx (actual rows=1.00)`, no Sort |
| PG 18.4 claim latency | ~15 ms @10k | **~0.7 ms** @20k |

**Aging preserved (CONFIRMED):** on a queue with `set_priority_aging(q, 60)`,
a priority-200 job backdated 2 h (effective 200−120=80) is claimed ahead of a
fresh priority-100 job — through the real claim path, post-fix. The aged
branch's sort is the documented opt-in cost (now stated in the 0042 header,
the runbook, and the release notes).

**Contract integrity (CONFIRMED):** fresh 0001→0042 `migrate()` + `verify()`
green on both majors with the unchanged manifest — the body-only/no-bump
claim is legitimate (identity, attributes, owner, ACL unchanged; ownership
and ACL survive CREATE OR REPLACE).

## Findings (most severe first — none blocking)

1. **Minor / pre-existing — one `test_bench_smoke[B13]` failure observed in
   full-suite context (PG16, first run: `1 failed, 819 passed`).** Isolated
   B13: 3/3 passed. Full-suite reruns: PG16 **820 passed** and PG18
   **820 passed**, so the ship claim reproduces, but not unconditionally.
   `bench.py` is untouched in `d959f48..HEAD`, so this is not a fix
   regression; it is the pre-existing bench plan-probe nondeterminism family
   (the thing historically blamed on "B4"), evidently not fully closed by the
   `e693fc0` dedicated-probe-queue work. The failing run's traceback was lost
   to this reviewer's own tail-truncated capture — honestly: the exact assert
   was not identified. Suggested: make the bench probe print the offending
   plan JSON on assertion failure so the next occurrence self-diagnoses.
   Label: CONFIRMED (occurrence), PLAUSIBLE (mechanism).
2. **Minor — `tests/test_claim_order_plan.py` is an untethered proxy
   (CONFIRMED by mutation).** With the pre-fix 0033 body reinstated live via
   CREATE OR REPLACE, `test_claim_order_plan` **passes** (it EXPLAINs its own
   hand-copied strings) while
   `test_plans.py::test_plan_binding_detects_rollback_only_function_drift` —
   which is ungated and greps the live `pg_get_functiondef` for both new
   ORDER BY fragments — **fails** exactly as designed. So the tether exists,
   runs in the normal lane, and bites; but it lives entirely in the binding
   test. Residual risk: a future coordinated edit of function + binding
   fragments with a stale proxy string would ship an index-defeating order
   with all gates green. One-line fix: have `test_claim_order_plan` also
   assert `_UNCONFIGURED_ORDER` / the aged shape appear in the live
   functiondef, making the proxy self-tethering.
3. **Nit — 0042 omits the function-hardening self-check DO block** that every
   sibling body-only migration carries (0028/0038/0040/0041), and the
   trailing `ALTER … OWNER` / `REVOKE` statements. Harmless here — replace
   preserves owner/ACL and `verify()` enforces hardening on both majors
   (confirmed green) — but the convention existed for defense-in-depth;
   restore it next body-only migration.

## Sibling sweep (#4): none found

Swept every `ORDER BY` in every live 0.6.6 function body (via
`pg_get_functiondef` over the whole `taskq` schema) plus the hot-path WHERE
predicates. Everything orders/filters on plain indexed columns except the
deliberate opt-in aged branch. Explicitly checked: both `claim_jobs`
overloads (normal + continuation frontier — `continuation_policy_hash,
priority, scheduled_at, id` matches `jobs_claim_policy_idx`),
`_claim_schedules_unattested` (`next_fire_at <= v_now ORDER BY next_fire_at,
id` — 0029's smear rides as a claim field, not in the scan predicate),
`reap_expired` (`lease_expires_at`), `expire_ttl` (`expires_at`), the janitor
retention passes (bounded LIMIT batches), all six `list_jobs` views,
`list_workflows` / `list_schedules` / `list_worker_presence` /
`list_queue_audit` / `list_job_events`, `purge_queued`, `redrive_failed`,
`finalize_workflows` / dep-stragglers / cancel-stragglers, workflow
cancellation advance, and the enqueue dedup lookups. The breaker gate's
`tripped_at + make_interval(…)` comparisons are single-row pkey lookups, not
scans.

## Deferred items (#6): both acceptable

- **M1** (half-open deadline anchored to trip time, not election) remains
  unfixed and is **disclosed in `docs/RELEASE-0.1.0a27.md`** as a known
  issue. It is minor by construction: worst case is one invalidated probe and
  one extra open/cooldown cycle on an idle queue; no state corruption, no
  wedge (0041's deadline still guarantees recovery). Deferral is fine for
  a27.
- **H2 doc correction is adequate:** the runbook now carries the
  counter-accounting caveat up front ("since 0.4 every queue keeps an exact
  `queue_counters` row … simultaneous settles can briefly serialize on that
  counter row") — the "off-by-default" claim is now honest about the
  observation plane.

## Verified clean (bounds of this review)

Full suite: PG16 **820 passed / 12 skipped** (after one B13-flake run —
finding 1), PG18 **820 passed / 12 skipped**. Fresh 0001→0042 migrate +
verify() green on both majors. Live-catalog faithfulness diff (two hunks
exactly). Real-function plans and latency on both majors, pre vs post. Aging
behavior probe. Gate mutation (binding bites, proxy does not). Full-catalog
ORDER BY sibling sweep. Release notes exist and disclose M1 + the aging cost;
runbook and README updated. Not verified: the gated 1M-row plan families
(`TASKQ_PLAN_CHECKS`) — its claim binding is the same fragment set the
ungated test enforces; the loadlab tiers (unchanged by this fix; covered in
the holistic review); the B13 flake's exact assertion (traceback lost).
