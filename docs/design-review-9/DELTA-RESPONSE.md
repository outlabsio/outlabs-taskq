# Round 9 targeted delta acceptance — Response

> **Scope:** only R9-01..R9-05, per the delta request. Nothing accepted in substance is reopened;
> nothing further is authorized.
> **Range verified:** `8b1547a..1610b5a` on `main` (+ gate commit `9664ccb` carrying the request),
> pushed and in sync with `origin/main`; all commits trailered; the user-owned ADR-018 batch
> remains outside the committed range, unstaged and untouched. The registered round-9 response is
> byte-identical (SHA-256 `4087e6524ff45d2ae0e38400dcc41d575a37891a1a609a631d37da23ad0846d9`,
> recomputed against both the committed blob and the working file).

## Verdict

**ACCEPTED — the read-model library slice is complete.**

## Check results

1. **R9-01 — PASS.** The transport now normalizes the SQL NULL composite to a typed `"missing"`
   result (`("missing", None, None)`, return type honestly widened), the facade CAS branch raises
   the standard missing-queue outcome (`TaskqNotFoundError` → TQ001/404) mirroring the GET path,
   and the manifest PUBLIC_ERRORS row for `update_queue_profile` gains `TQ001` — conformance to
   the frozen contract's existing outcome, not new wire behavior. Transport-level and wire-level
   vectors added.
2. **R9-05 — PASS.** The facade re-raises the SQL capability error with
   `details={"reason": "read_model_view_inactive", "view": <requested>}` built from its own
   request knowledge, cause-chained — sanitizer-safe with no SQL text; the contracted details now
   cross the wire for `running` and `finished`, vector-covered.
3. **R9-02 — PASS.** The plan gate's final assertion is now the exact 0006 equality
   (`{"active": ["read_model_list_ready"]}`). Reviewer-executed with the million-row fixture:
   **2 passed on PostgreSQL 18.3 and 2 passed on exact-minor 16.14**, preserving the bounded
   `ready` plan (named index family, ≤ limit+1 candidates, no sort/seq-scan) and the rejected
   `running`/`finished` negative shapes.
4. **R9-04 — PASS.** Three new protocol-surface regressions (mounted facade + official clients,
   not helpers): the queue-profile wire conflict + weak-ETag exactness vector (stale exact
   `If-Match` → TQ409 with current-version-only details; weak tags rejected); the list-jobs
   cursor-validation and keyset pagination round-trip vector (malformed / foreign-queue /
   foreign-view / oversized / duplicate rejection); and the published-client canonical
   `{"profile": {...}}` decode vector proving `profile_version` arrives intact — the previously
   breached S5-CQ-02 condition-4 vector, now delivered.
5. **R9-03 — PASS.** The delta is pushed (`main` ≡ `origin/main`); `ruff format --check` is clean
   (70 files, the two drifted paths formatted in `a115062`); and the artifact smoke ledger now
   asserts immutable migrations 0001–0006 plus the 43-function catalog (`1610b5a`). CI run
   `29830113693` independently inspected: **success at exactly the delta tip `1610b5a`**, with
   lint, migrations, sql-contract (16) and (18), stage3-audit, built-artifacts (3.12/3.13),
   import-isolation (3.12/3.13), unit (3.12/3.13), bench-smoke, and races all green; the
   million-row CI job is skipped by design and is covered by item 3's independent dual-major
   execution.

Reviewer-executed evidence: full suite **469 passed / 1 opt-in skip** on fresh PostgreSQL 18.3
and on a disposable exact-minor 16.14 container (full 0001→0006 chains, CI-shaped Redis); both
plan gates as above; Ruff lint and format clean. Environmental note: one local PG16 attempt
collided with an unrelated container's port and was rerun cleanly on another port; no shared
infrastructure was touched. No Tier-0, ADR, or migration file changed in the delta range
(fix/test/style/docs paths only — derived from Git). **No Contract questions.**

## Effect

The H-08/H-11 read-model slice is complete and independently accepted: contract revisions 1.0.5–
1.0.7 with Manifest 0.1.4, immutable migrations 0004–0006, the bridge compatibility set, the
generated SQL/HTTP/client surfaces, the parity and wire-vector suites, and dual-major B9 evidence
with only `ready` active. This acceptance opens **only** a future, separately specified
host-adoption decision for the active `ready` view and profile surfaces. It does not authorize
production migration of 0004–0006 (which keeps its ADR-020 rollback-floor decision), activation
of `running`/`finished` (each needs its own B9 proof and immutable migration), UI work, any
retirement action, or the Stage-5 pilot.
