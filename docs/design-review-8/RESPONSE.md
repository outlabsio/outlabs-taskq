# External design review — Round 8 Response

> **Reviewed:** Host Branch Reconciliation Specification + Legacy Tools Path Retirement
> Specification (both frozen by S4-POST-00)
> **Range:** taskq `fef775e..9feaf79` (+ gate commit `44ab30b` carrying the request); host
> read-only at `9348f85`
> **Method:** reviewer-inline authority reads and graph derivation, an independent host source
> sweep of the complete legacy tools surface, and reproduced gates. Left uncommitted per the
> charter.

## 1. Verdict

**READY.** Branch reconciliation may begin at S4-POST-R1. The reconciliation plan is executable
as frozen — its ledger schema, two-parent exact-tree construction, fast-forward advance, tag
discipline, cutover-as-deployment, and rollback answers every attack in the charter. The
retirement plan's gates are independently falsifiable, and its four definitional gaps (findings
R8-02..R8-05) all sit downstream of reconciliation: their remediations are docs-first amendments
that must land **before S4-POST-L1 opens**, and none blocks R1.

**Scope of this READY (explicit):** it authorizes reconciliation planning and execution only —
not tools retirement (L1..L3 remain gated as above), not branch deletion (archival only, after
two healthy cycles, deletion deferred), not any side-effecting-lane migration (still gated on the
REQUIRED hard-kill lease-expiry drill, unchanged), and not Stage 5.

## 2. Independently derived host identities and surfaces

Graph (derived by the reviewer from the fetched host repo, not copied):
`merge-base(main, staging-prep)` = `merge-base(main, codex/s4-03-cycle1)` = `a0019cd`;
`origin/staging-prep` = `3f50b7d` (deployed); `origin/codex/s4-03-cycle1` = `9348f85` with
exactly five evidence/harness commits above `3f50b7d` (`97b154c`, `1fd5050`, `5a8cb78`,
`7c60229`, `9348f85` — no runtime source); `main` = `7df6b7f` with exactly three main-only
commits (`ef084ab`, `90fa63d`, `7df6b7f` — the Stage-4 intent later reimplemented on the
production line). `main` is **not** an ancestor of the evidence tip, so the two-parent
construction is necessary; once the candidate carries both parents, advancing `main` is a true
fast-forward exactly as §4 specifies. No shallow clone, missing ref, or rewritten history was
found; all tips match the frozen table.

Legacy tools surface (derived from host source before consulting the retirement spec's
inventory): the only runtime producer chain is the queued route's legacy branch →
`enqueue_tool_task` → shared `_enqueue_task` → shared queue service; selection is
`uses_taskq` = enabled AND mode==taskq AND allowlisted, so the legacy branch serves **three**
postures (disabled, legacy mode, and non-allowlisted registered tool). The standing worker's
claim loop is kind-agnostic (no kind filter); tools dispatch lives solely in the shared
processor's `TOOL_RUN` branches; retry/terminal handling is kind-agnostic; contact, analytics,
and newsletter kinds share the same loop and must survive. One non-route producer exists:
`scripts/verify_restricted_runtime.py` enqueues and settles a real legacy tools row and is a
documented pre-production gate. After a naive consumer-side removal, a leftover pending tools
row is **silently claimed and marked done** by the unknown-task fall-through — the spec's LR-07
quarantine/fail-loud requirement is therefore load-bearing and must be explicitly built, not
inherited. Tools rows are distinguishable (`kind='tool_run'`, indexed), and the rehearsal's
terminal failed row is inert (claim filters `pending` only).

## 3. Finding registry

**R8-01 · LOW · The BR-02 tree manifest is named but not constructed.** §4 requires "an
independent tree-hash manifest" in one sentence. Smallest remediation (bindable via this
response, no re-freeze needed): the manifest is (a) the candidate commit's tree hash asserted
equal to the tree obtained by applying exactly the ledger's forward-port commits onto `9348f85`,
recomputed independently by the auditor, plus (b) a recursive diff listing between the candidate
tree and the `9348f85` tree asserting that every differing path is named by a forward-port row —
zero unclassified paths. Git tree hashes cover content, mode, symlinks, and deletions by
construction. Owner: S4-POST-R1/R-AUDIT.

**R8-02 · MEDIUM · The retirement spec never defines the post-retirement posture for a
registered, non-allowlisted tool — the legacy branch's third posture.** Today such a request
silently takes the legacy path; after L2 the spec defines replacements only for
disabled/not-ready. Undefined: does `TASKQ_TOOLS_ALLOWLIST` survive, and what does the queued
route return for a registered tool outside it (typed rejection? enrollment-required error?).
Any newly registered tool would otherwise land in undefined behavior. Smallest remediation:
one docs-first paragraph in §3/L2 fixing the allowlist's fate and the exact typed response for
non-allowlisted queued requests, plus an L2 test row. Owner: retirement-spec amendment before
S4-POST-L1.

**R8-03 · MEDIUM · LR-01's "external invocation ledger" has no defined mechanism for the
third-party flight lane.** The first lane's target service is operator-controlled (its access
log is a true external counter); the flight lane's is not, and the charter forbids a
self-referential application counter. Smallest remediation: docs-first definition before L1 —
either an egress-level counter outside the application process (deployment-platform proxy or
sidecar egress log for the flight API host) or an explicit, recorded downgrade of the flight
lane's oracle to taskq attempt/event arithmetic plus bounded application HTTP metrics with the
independence limitation stated. Owner: retirement-spec amendment before S4-POST-L1.

**R8-04 · MEDIUM-LOW · Producer removal breaks a documented pre-production release gate.**
`scripts/verify_restricted_runtime.py` enqueues a legacy tools row (`enqueue_tool_task`) and
settles it, and the deployment doc mandates it pre-production. The spec's L2 inventory does not
mention it. Smallest remediation: rework the script to a non-tools kind (or a taskq-path proof)
inside L2, named in the spec. Owner: S4-POST-L2.

**R8-05 · LOW · Out-of-repo caller sweep is implied but not a gate row.** The legacy queued
response is HTTP 200 `{status, tool_name}`; the taskq path is 202 with different fields. Any
out-of-repo automation tolerating only the 200 shape (operator CLIs and personal tooling exist
for the first lane) breaks silently at L2. The stop condition "any active caller still depends
on legacy `tool_run`" gestures at this without an owning row. Smallest remediation: add the
caller sweep to L1 eligibility (enumerate operator-owned callers of `/runs/queued` and their
tolerated response shapes). Owner: retirement-spec amendment before S4-POST-L1.

**R8-06 · LOW · Mode-enum removal interacts with stale platform/doc baselines.** L2 "reject or
remove `TASKQ_TOOLS_MODE`" will hard-fail any environment still carrying `legacy` at boot
(fail-closed, as rehearsed — but the deployment doc currently documents `legacy` as the baseline
env, and L3 defers variable removal until after both images run). Production currently carries
`taskq`, so the live path is safe; the trap is documentation and fresh/rollback environments.
Smallest remediation: L2 updates the deployment-doc baseline in the same change and states the
compatibility rule (the prior image accepts `legacy`, which is exactly the rollback pair).
Owner: S4-POST-L2.

**R8-07 · LOW (notes, no action gate).** Two same-named `TOOLS_QUEUE` constants exist (legacy
label vs taskq queue name) — remove only the legacy one; the Prometheus `domain="tools"` series
of the shared publish counter disappears at L2 (dashboards note); two worker metrics are
pre-existing dead code unrelated to this retirement; the applied migration's docstring mentions
"tools" (cosmetic — never edit an applied migration); `.env.example` lacks the TASKQ block
(pre-existing gap, fold into L2's settings-example update).

## 4. Contract questions

**None.** Neither plan requires any taskq SQL, wire, permission-grammar, or capability change;
both explicitly stop under the contract process if one appears.

## 5. Acceptance-row dispositions

**BR-01 SOUND** (ledger schema per-row with side, surfaces, disposition, semantic evidence,
wrong-disposition oracle, reviewer; message-similarity disqualified; the main-only set is three
commits, production-only ~fifteen — genuinely executable). **BR-02 SOUND with R8-01's
construction bound.** **BR-03 SOUND** (verified: two-parent candidate makes the `main` advance a
true fast-forward; no force). **BR-04 SOUND** (gate list complete incl. offline Alembic and the
production-shape harness). **BR-05 SOUND** (docs-only proof class already demonstrated in prior
rounds). **BR-06 SOUND** (same-commit + settings-digest for API and worker addresses the
split-deploy hazard). **BR-07 SOUND** (adds the non-tools legacy probe — correct breadth).
**BR-08 SOUND** (grace-bounded drain, zero DML). **BR-09 SOUND** (branch-flip rollback with
boot-against-unchanged-database rehearsal). **BR-10 SOUND** (tags retained; archival after two
cycles; deletion out of scope).

**LR-01 SOUND after R8-03/R8-05 amendments** (window reset on any insertion is the right
fail-shut). **LR-02 SOUND** — the frozen high-water oracle (count + max creation time against a
table that retains terminal rows) is monotone and immune to insert-then-complete, clock
boundaries (count catches same-timestamp inserts), id reuse (UUIDs), and rolled-back
transactions (never visible); depth inference is correctly rejected. **LR-03 SOUND** (the
existing spy/fault-seam test pattern extends). **LR-04 SOUND** (canonical keyed pair per lane —
production-proven shape). **LR-05 SOUND.** **LR-06 SOUND** (paired-image rollback; the prior
image accepts the legacy mode value — the compatible pair per R8-06). **LR-07 SOUND and
load-bearing** — the current unknown-task fall-through silently completes leftover rows, so the
quarantine/fail-loud handler must be explicitly implemented and tested; drain-before-L3 is
already required. **LR-08 SOUND** (the shared-lane test inventory exists and must be preserved).
**LR-09 SOUND.** **LR-10 SOUND.** **LR-11 SOUND.** **LR-12 SOUND.**

Mixed-version windows (charter F): L2 deploys the producer-retired API against the old
`tool_run`-capable worker (the required window); the forbidden pair (old producer-capable API +
consumer-retired worker) is structurally avoided because L3 deploys the worker only after L2 is
accepted plus a healthy window, and any post-L3 rollback restores the **paired** prior images;
retired variables are deleted only after both accepted images run. Late historical `tool_run`
rows after L3 hit the LR-07 loud path, not silent execution or loss.

## 6. Gates run and environmental limits

Reproduced by the reviewer on 2026-07-20: taskq full suite on live PostgreSQL 18.3 with a
CI-shaped Redis service — **450 passed / 1 opt-in skip**, Ruff clean; host suite at `9348f85` —
**72 passed / 5 pre-existing infrastructure skips**, Ruff clean, MyPy clean across 64 source
files. Review-range hygiene verified: the range contains only board/tier-map/Build-Plan updates
and the two new Tier-3 specifications; both commits trailered; the host repo and production were
not touched by this review; the separate user-owned ADR-018 working batch is not absorbed by the
reviewed commits and remains untouched. Not run locally (standing evidence relied upon, per
charter): image builds, the live production-shape harness against production, PG16 (CI-owned;
last reviewer container run green at the same source).

## 7. Preconditions to open S4-POST-R1

**None.** R1 may open immediately, with R8-01's manifest construction binding on R1/R-AUDIT.
Before **S4-POST-L1** opens (not R1): the retirement-spec amendments for R8-02 (non-allowlisted
posture + allowlist fate), R8-03 (flight-lane external counter mechanism or recorded downgrade),
and R8-05 (caller-sweep eligibility row); R8-04 and R8-06 land inside L2 as specified.

## 8. Deferred follow-ups

Unchanged and carried: the round-7 follow-ups (Redis-gated test, engine pool pinning, one-time
access-log corroboration, ADR-018 commit, restore/PITR) retain their owners; the hard-kill
lease-expiry drill remains the REQUIRED gate before any side-effecting lane, owned by that
future expansion slice.
