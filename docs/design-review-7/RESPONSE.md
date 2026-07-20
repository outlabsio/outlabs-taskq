# External Design Review Round 7 — Response

> **Reviewed:** Stage-4 production completion evidence (S4-AUDIT gate)
> **Ranges:** library `cc0d03a..f868c09` (main); host `d1b00fe..7c60229` (`codex/s4-03-cycle1`);
> production revision `3f50b7d` (= `origin/staging-prep`); artifacts `v0.1.0a2` + OutLabs Auth
> `0.1.0a24`
> **Method:** three independent source/evidence audit passes (deployed host source + connection
> arithmetic; production evidence packet; governance/hygiene) plus reviewer-inline reproduction —
> both PostgreSQL suites, host gates, artifact hash, live production probes. Every finding-grade
> claim re-verified by the reviewer before acceptance.

## 1. Verdict

**BLOCKED — by exactly two near-mechanical preconditions (§7); everything else is accepted in
substance.** The deployed integration is source-faithful to the frozen plan, the boundary held
(no SQL, migration, Tier-0, ADR-001..017, or prior-Tier-4 change anywhere in either range), the
evidence packet is unusually honest, both self-adjudicated contract questions are ratified, and
the recomputed connection arithmetic holds. What blocks acceptance is not risk but exactness: the
cycle-2 canary's canonical production closure is currently a local-environment record presented in
one packet sentence as production history, and the controlled-failure drill exercised a different
(honestly disclosed) recovery mechanism than the frozen §6 letter names. Both close with minutes
of work. After they land, a targeted delta check accepts Stage 4; legacy-path retirement and
branch reconciliation remain unauthorized by this response either way.

## 2. Reproduced evidence (reviewer's own)

- **Ranges/hygiene:** library range = 12 commits, 12 paths, zero under `src/taskq/sql/`, zero
  Tier-0/ADR-001..017/prior-review paths (recomputed by the reviewer); host range = 11 commits,
  no host migration (`alembic/versions/` byte-identical across the range), no deletions (the
  legacy path is fully present at tip). All 23 commits carry the required trailer. Production
  revision `3f50b7d` = `origin/staging-prep`; the four commits above it are harness/docs only.
- **Suites:** taskq on PostgreSQL 18.3 and on 16.14 (exact-minor disposable container): 449
  passed / 1 failed / 1 opt-in skip in the reviewer's environment — the single failure is the new
  CQ-04 regression test, which hard-depends on an unauthenticated local Redis; with a throwaway
  reachable Redis the test **passes**, and CI (which provisions `redis:8` services) is **green on
  all three tip commits** (checked via the CI API). The a2 fix is sound; the test is
  locally fragile (R7-03). Host tip: **72 passed / 5 pre-existing opt-in skips**, Ruff clean,
  MyPy clean across 64 files. Library Ruff clean.
- **Artifact:** the `v0.1.0a2` wheel downloaded from the release URL hashes to exactly
  `d3c37b0e30dbc75cbbb279c3e3f64a7df7416bf51ca1acfd016544c03e745f42`; host pyproject and lock pin
  that URL+hash; the tag points at `36db7cf`; version strings agree; OutLabs Auth stays exactly
  `0.1.0a24`.
- **Live production probes (reviewer's own):** `/health` 200; unauthenticated
  `GET /taskq/v1/meta` returns the exact Protocol-1.0.4 error envelope
  (`protocol_version: 1`, minted lowercase-UUID `request_id`, `AUTH401`, `retryable: false`);
  unauthenticated `GET /taskq/v1/jobs` (deferred reserved path) returns 401 —
  authenticate-before-capability with no enumeration oracle. The running facade is
  wire-conformant.
- **Packet/addendum:** read in full at `7c60229`/`f868c09`; the addendum's read-only production
  oracles (drill-job counters/event chain; legacy row natural terminal state) are as summarized
  below.

## 3. Finding registry

**R7-01 · MEDIUM · The controlled-failure drill exercised worker-shutdown release, not the frozen
§6 lease-expiry/housekeeper-reap letter.**
Authority: Stage-4 spec §6 ("terminate the old API process… allow the database lease to expire
and the housekeeper to reap it"). Evidence: the production-table oracle shows attempt outcomes
`released/worker_shutdown` → `succeeded/success`, `release_count=1`, `expiry_streak=0`, event
chain `enqueued → claimed → released → claimed → succeeded` — the SQL contract distinguishes this
release path from `reap_expired`'s `expired/lease_expired` path, so the mechanism determination
is exact. What WAS proven in production: same-id conservation, budget-free release, two distinct
worker actors across the process replacement, eventual success on attempt 2 with failure count 0,
old-container removal in 25.434478 s inside the 35 s grace, zero manual DML, probe disabled and
absent afterward. What was NOT exercised in production: lease expiry + housekeeper reap — that
path remains library-proven (Stage-2/3 race, crash, and reap suites plus the live-ASGI
process-exit evidence). Root cause is a spec-level tension, not an evidence defect: a
well-behaved async `hold` job is *released* by the worker contract during a graceful rolling
replacement; the §6 letter is only reachable by SIGKILL past platform grace (or an unsafe-sync
thread), which the drill as written never states. The packet discloses the actual mechanism
honestly rather than claiming reap.
Failure mode if unremediated: the acceptance record implies the ungraceful-death recovery path
was production-proven when it was not; a future side-effecting lane inherits that assumption.
Smallest remediation: docs-first §6 correction stating the graceful-release reality and the
SIGKILL-past-grace precondition for a true lease-expiry drill, plus a REQUIRED hard-kill drill
(event chain showing `lease_expired`/reap → reclaim) gated before any side-effecting lane
migrates, with a named owner. Oracle: that future drill's read-only event chain.

**R7-02 · MEDIUM · The cycle-2 canary's canonical production closure is a local record; one
packet sentence presents it as production history.**
Evidence: cycle 2's production runs surfaced the (disclosed, fixed, and source-verified) defect
chain — credential-field serialization, gateway fingerprint rejection, then a real external 200
whose oversized body failed settlement closed and flipped health to 503, fixed by the 7 KiB
omission record. The post-fix canonical keyed 202 → authorized GET `succeeded` for the second
tool is recorded only "in a fresh isolated local environment"; production keyed-replay `existed`
conservation is likewise local-only. The packet sentence "Both selected read-only tools returned
canonical 202 acceptance and authorized result readback" therefore overstates for the second
tool. The frozen §7.2 step 4 requires repeating the canonical evidence in cycle 2 after the
second tool joins the allowlist.
Smallest remediation (precondition 1): one recorded production run for the second tool — the same
`Idempotency-Key` submitted twice yielding `created` then `existed` with the same job id,
followed by the authorized canonical GET reaching `succeeded` — appended to the packet, and the
"both tools" sentence corrected to the actual history. Oracle: the recorded pair plus the job's
read-only counters (one execution, attempt arithmetic consistent).

**R7-03 · LOW · The CQ-04 regression test hard-requires a live unauthenticated local Redis.**
Default `redis://localhost:6379/15` with no gate or skip; on a machine with a secured or absent
Redis the required-green SQL lanes go red (they did in this review's environment). CI provisions
Redis and is green; the harness's own gated-lane rule (§1.1) is what the test violates.
Remediation: skip (or gate on `TASKQ_TEST_REDIS_URL`) when the default endpoint is
unreachable/unauthenticated, or use an ephemeral fixture. Oracle: full suite green-with-skip on a
Redis-less machine.

**R7-04 · LOW · The packet frames connection headroom against the ceiling instead of
ceiling-minus-reserve.**
The recomputed arithmetic itself is correct and source-derivable (taskq 4+1+2+0 pools + 0
listeners = 7; host engine 15; auth engine 15; standing legacy worker 15; total 52). The frozen
requirement measures against ceiling − reserve = 80, so honest headroom is **28**; the packet's
"48 below the measured ceiling" counts the reserve as headroom. Remediation: one-line packet
wording fix (fold into the precondition-1 packet edit). Oracle: the corrected sentence.

**R7-05 · LOW · The two ×15 host engine figures rest on unpinned SQLAlchemy defaults.**
Neither the host engine nor the auth engine passes pool arguments; a future SQLAlchemy default
change silently moves the deployment maximum. Remediation: pin `pool_size`/`max_overflow`
explicitly (or assert the effective values in the local production-shape gate). Oracle: the gate
assertion.

**R7-06 · LOW · No production external-invocation counter exists for either canary.**
Local harnesses provide a genuine handler-independent counting oracle for the convergence and
response-loss rows (spec-permitted "harness-level equivalent"), and production has strong
indirect external evidence for the second tool; but no target-service count corroborates
production invocations. The first tool's target service is operator-controlled, so its access log
can corroborate cheaply. Remediation (post-acceptance): record that corroboration once, or state
the absence explicitly in the packet. Oracle: the access-log line count for the recorded window.

**R7-07 · LOW · The library worktree carries uncommitted post-range work (ADR-018 + six doc
edits), and ADR-018 claims "Accepted" while having no git identity.**
Outside the pinned range and referenced by nothing inside it (verified); the operator-console
stack choice is orthogonal to Stage 4. Remediation: commit the ADR-018 batch separately after
this review is recorded so an "Accepted" ADR has a citable commit. Oracle: `git log` for the ADR
path.

Notes recorded without findings: the drill job's hold duration is unstated (~300 s implied); the
disabled-window schema/history preservation is proven inferentially (convergence without repair +
post-re-enable read of prior history) rather than by a during-window check; the ephemeral-key
lifecycle is narratively recorded with no repo-verifiable identifiers; the `fail_once` probe mode
was exercised locally only.

## 4. Contract questions

**None.** The two self-adjudicated questions from the audited window are **ratified** by this
review as operational resolutions inside the accepted contracts:

- **S4-CQ-03 (migration execution identity):** migration 0001 must create/alter the capability
  roles, which `SET ROLE taskq_owner` correctly cannot do (the owner role rightly lacks
  CREATEROLE); executing migrate/verify directly as the cluster owner login while immutable SQL
  assigns object ownership to `taskq_owner`, with `verify()` as the independent
  ownership/grant oracle, is the honest posture and changes no SQL, role, or ADR. The earlier
  condition wording (this reviewer's) embedded the unreachable `SET ROLE` assumption; the
  correction is accepted.
- **S4-CQ-04 (production auth blocker):** the adapter's eager `require_auth()` construction froze
  the pre-initialization service graph (hosts mount the facade before their lifespan initializes
  the auth system), so a valid key received typed 503s. The `36db7cf` lazy first-request binding
  under a lock, with the session-parameter guard retained, stays entirely within the accepted
  Stage-3 architecture — no wire, SQL, or permission-grammar change — and ships as immutable
  `v0.1.0a2` with a real-schema regression test (and the superseded a1 left immutable). The fix
  is verified present in the pinned production artifact. Both resolutions were disclosed as
  pending this external check rather than presented as externally approved; that discipline is
  noted favorably.

## 5. Acceptance matrix (Stage-4 exit rows)

- **Artifact/lock immutability** — oracle: tag→commit, release-wheel hash recomputed, pins/lock
  read; **PASS** (reviewer-reproduced).
- **Authorization allow/deny/hiding, fail-closed 429/503** — oracles: S4-02/S4-03 vectors, the
  production CQ-04 episode itself (typed TQ503 while unavailable = fail-closed in production),
  post-fix production probes (200 queue-scoped stats; 403 undeclared global; 401s), reviewer's
  live envelope probes; **PASS**.
- **No dual publish / no ambiguous fallback** — oracle: single-decision-point source proof at the
  deployed revision + S4-02 spy vectors; **PASS** (source-verified).
- **Concurrency + settlement-response-loss invocation conservation** — oracle: local harness
  counting-tool + raw-table arithmetic (20-way keyed convergence; committed-response-loss replay
  with `calls == 1`); production-side corroboration indirect only; **PASS with R7-06 note**.
- **Result/durable-error secrecy** — oracles: source (credential-key rejection, 64 KiB cap,
  sanitized classifications, 7 KiB omission record with hash), packet's negative claims;
  recorded examples themselves are production-side; **PASS (recorded-not-reproduced for the
  example texts)**.
- **DB disconnect, stop, cancellation, cleanup** — oracle: library suites + local
  production-shape gate + live drain evidence; **PASS**.
- **Two normal deployment cycles** — oracle: three healthy deployment records + the disclosed
  fail-closed invalid candidate (rejected at settings validation before readiness, old container
  retained); **PASS**, with the cycle-2 canary closure gap tracked as **R7-02 (precondition)**.
- **Controlled same-job process failure** — oracle: read-only production event chain;
  same-id/attempt/actor arithmetic **PASS**; mechanism-vs-letter tracked as **R7-01
  (precondition: docs-first correction + future hard-kill gate)**.
- **Zero-DML rollback/re-enable** — oracle: recorded sequence mapping 1:1 to §7.3 with
  before/after ledgers, natural-terminal legacy row (attempts exhausted, unleased, no
  acceleration), ephemeral-key trail, `/taskq` absent while disabled (404) and conformant after
  re-enable; **PASS**.
- **Provisioning/honesty** — oracles: cluster facts, restricted-login separation with negative
  vectors, migrate/verify ×2, IAM 14→14, profile created→unchanged, backup recorded with
  restore/PITR caveat preserved, no performance claims, defect chain disclosed; **PASS**.
- **Connection budget** — oracle: reviewer-independent recomputation from source (52 ≤ 80,
  headroom 28); **PASS** with R7-04/R7-05 wording/pinning notes.
- **Legacy retirement forbidden before acceptance** — oracle: no deletions in the host range; the
  legacy queue, worker, and docs fully present at tip; **PASS**.

## 6. Scope and hygiene result

Library range: exactly the board/Tier-2 records, Tier-3 spec updates, the two new Tier-4
design-review-7 files, and the single sanctioned source commit (`36db7cf` + its tests/CI/version)
— no SQL, Tier-0, ADR-001..017, or prior-review modification (recomputed independently; deletions
zero). Host range: sanctioned runtime fixes (each with tests: role-provisioning scripts with
reserved-role rejection, Redis log redaction, a2 repin, 202 serialization union, absent-credential
omission, flight-API request identification, 7 KiB result bound), the local production-shape
harness, and evidence docs — no host migration, no lane migration, no retirement, no
reconciliation; `main` untouched. `docs/design-review-7/` contained exactly the request and
addendum before this response. Zero third-party queue-project names in any audited document (the
retained mechanism is referred to only as the legacy path). The library worktree's uncommitted
post-range ADR-018 batch is outside the pinned range and referenced by nothing in it (R7-07).

## 7. Preconditions to acceptance (shortest ordered list)

1. **Close the cycle-2 canonical production gap (R7-02):** record one production keyed pair for
   the second tool — same `Idempotency-Key` twice → `created` then `existed`, same job id →
   authorized canonical GET → `succeeded` — append it to the audit packet, correct the "both
   tools" sentence to the actual history, and fold in the R7-04 headroom wording fix (28 against
   ceiling-minus-reserve) in the same packet edit.
2. **Correct the frozen drill text (R7-01):** docs-first §6 amendment stating that a graceful
   rolling replacement produces worker-shutdown release (as recorded), that the lease-expiry/reap
   letter requires termination past platform grace, and that a hard-kill drill (read-only event
   chain showing lease expiry/reap → reclaim) is REQUIRED before any side-effecting lane
   migrates, with a named owner.

On completion, a targeted delta check (packet edit + spec edit + the one production record)
converts this verdict to ACCEPTED without a further full round. Legacy-path retirement and branch
reconciliation remain closed until that acceptance is recorded, and are not authorized by it —
each requires its own specification.

## 8. Deferred follow-ups (post-acceptance, named owners)

- R7-03 Redis-gated regression test skip/gate — library test hygiene (next library slice).
- R7-05 pin or assert host engine pool sizes — host production-shape gate.
- R7-06 one-time target-service access-log corroboration for a canary window, or an explicit
  packet statement of its absence — host evidence hygiene.
- R7-07 commit the ADR-018 batch so the "Accepted" ADR has a git identity — library housekeeping.
- Hard-kill lease-expiry drill — required gate before any side-effecting lane (per precondition
  2's amendment); owner: the future lane-expansion slice.
- Restore/PITR test for the internal PostgreSQL service — host backlog (previously flagged;
  unchanged).
