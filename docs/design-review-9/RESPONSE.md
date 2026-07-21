# External targeted review — Read-model activation — Response

> **Reviewed:** the complete read-model range `7826cbc..c1fac41` (20 commits, all trailered) plus
> gate commit `f1d1ab7`; source frozen at `c1fac41`.
> **Method:** reviewer-inline governance/contract audit and dual-PG evidence reproduction, plus an
> adversarial source audit of every implementation surface in the range; the finding-grade defect
> and every load-bearing claim re-verified by the reviewer in source before acceptance. Left
> uncommitted per the charter.

## 1. Verdict

**BLOCKED — by five small preconditions, all completable in one sitting.** The contract, the
immutable migration chain 0004→0005→0006, the activation mechanics, the authorization/cursor/ETag
implementation, the parity oracle, and the B9 structural evidence are sound and were reproduced on
both PostgreSQL majors. What blocks is one latent runtime defect on an untested wire path, one
stale evidence-gate assertion that contradicts the accepted 0006 post-state, the fact that the
entire range is unpushed and has never met CI, and two bundles of missing contracted vectors —
including one breached standing condition. None is architectural.

## 2. Independently derived identities and evidence

- Range derived from Git: 20 commits `7826cbc..c1fac41`, every one trailered; the only source
  paths entered are `http/facade.py`, `http/client.py`, `http/runtime.py`, `protocol.py`,
  `sql/manifest.py`, and migrations 0005/0006 — no host, UI, Tier-4, alias, index, or
  base-table-projection change. The working tree adds only the user-owned uncommitted ADR-018
  batch (not absorbed — verified) and the committed round-9 request.
- **Reviewer-reproduced, 2026-07-21:** full suite **465 passed / 1 opt-in skip** on fresh
  PostgreSQL 18.3 **and** on a disposable exact-minor 16.14 container (fresh install + full
  0001→0006 chain via the migration drills), with a CI-shaped Redis. The opt-in million-row plan
  gate ran on **both majors**: every structural assertion passed — `ready` on `jobs_claim_idx`,
  ≤ 101 candidate rows, no sort or sequential scan; `running`/`finished` proven to sort and
  overscan (the negative shapes); claim/heartbeat hot-path bindings intact — and the single
  failure on both majors is the stale capability assertion (R9-02), not a plan regression.
- Governance: docs-first ordering held for ADR-019/020/021 (each contract commit precedes its
  implementation); migrations 0004/0005/0006 are append-only with recorded checksums and an
  executable immutability vector; 0006 is strictly metadata-only (a `DO`-block guard requiring
  contract exactly 0.1.4 with `FOR UPDATE`, then a wholesale capability replacement to exactly
  `{"active":["read_model_list_ready"]}`), citing the B9 evidence commit in its header.
- `verify()` asserts the meta table by **dict equality** against the manifest seeds — a mutated
  capability value, extra key, or missing key fails, with an executable negative vector; the
  43-function catalog is closed and drift-tested across shape/owner/grant/search-path/index
  dimensions.
- ADR-020 set: `frozenset({"0.1.2","0.1.3","0.1.4"})`, closed, with the pre-bridge `{0.1.2}`
  rejection negative retained and activation-requires-0.1.4 both executable (the 0006 guard) and
  frozen in Manifest §13, including the 0007-class deactivation rule.

## 3. Finding registry

**R9-01 · MEDIUM · Conditional PUT on an unknown queue crashes to TQ500 instead of the
queue-missing outcome.**
Evidence (reviewer-verified in source): `update_queue_profile` returns a NULL composite for an
unknown queue by design (`0004_read_models.sql:166`); the SQL transport unconditionally executes
`int(row["current_version"])` on that all-NULL row (`sql/transport.py:635-641`) → `TypeError` →
TQ500 INTERNAL; the facade CAS branch has no absent-queue handling (unlike the GET path's clean
404), and the manifest's PUBLIC_ERRORS row for the function lists only TQ422. No test covers
If-Match PUT against a nonexistent queue.
Impact: an authorized caller doing CAS against a deleted/mistyped queue receives an opaque
internal error rather than the contracted missing-queue semantics; TQ500s page operators.
Remediation: transport maps the NULL composite to the absent result; the facade CAS branch mirrors
the GET's authorized-missing behavior (TQ001/404); the manifest PUBLIC_ERRORS row gains
NOT_FOUND (docs touch of the 40aa9b5 class); one wire vector. Owner: pre-adoption fix in this
slice.

**R9-02 · MEDIUM · The million-row plan gate's final assertion contradicts the accepted 0006
post-state and is red on both majors.**
`tests/test_plans.py:352-353` asserts `capabilities == {"active": []}` — written pre-activation
and not updated by the 0006 slice (the parity test's equivalent was updated; this one was missed
because the gate is opt-in and the slice ran only the ordinary suites). The required B9 evidence
gate therefore fails at tip on PG16 and PG18 even though every structural plan assertion passes
(reviewer-reproduced). Remediation: assert equality with the contracted post-0006 state (aligned
with `verify()`), rerun the gate green on both majors. Owner: this slice.

**R9-03 · MEDIUM · The entire range is unpushed; CI has never executed on it, and the range
introduced formatter drift misreported as pre-existing.**
`origin/main` is 22 commits behind the local tip; the green CI runs predate the range. The
completion report's note that `ruff format --check` fails on "untouched, pre-existing"
`http/facade.py` and `tests/test_s3_facade.py` is incorrect: the range-start versions of those
files format clean under the same ruff (0.15.22, unchanged in the lock) — the drift was
introduced by this range's own commits, and CI's format line would fail at tip. Remediation: one
mechanical `ruff format` commit for the two files; push the full range; CI green at tip. Owner:
this slice.

**R9-04 · MEDIUM · Contracted evidence vectors are missing — including one breached standing
condition.**
(a) Zero HTTP cursor-negative vectors: the malformed / foreign-queue / foreign-view / oversized /
duplicate-parameter TQ422 paths and any pagination round-trip are untested over the wire (the
only cursor assertion anywhere is `next_cursor is None`). (b) No HTTP-level stale-`If-Match` →
TQ409 vector (the facade's conflict branch is untested over the wire — R9-01 demonstrates exactly
why wire-level vectors matter) and no weak-`W/"…"` rejection vector; SQL-level conflict is
covered. (c) **The S5-CQ-02 condition-4 published-client compatibility vector — proving the
official a2 client decodes the version-bearing `{"profile":{...}}` envelope unchanged — was a
standing condition of the envelope decision and is absent.** (d) There is no facade-level
`list_jobs` success vector (a dead fake exists); the full-stack page is covered only by the
parity test. Remediation: add the vector set. Owner: this slice.

**R9-05 · LOW · The wire TQ501 for inactive views arrives typed but without the contracted
reason/view details.**
Protocol §2.5's per-view rows contract `TQ501` with reason `read_model_view_inactive` and the
view name; the SQL raises exactly that in its DETAIL, but the message-free error normalizer
(correctly) drops SQL text, so the HTTP envelope's details are empty. Remediation: the facade
supplies `{"reason": "read_model_view_inactive", "view": <requested view>}` from its own request
knowledge (sanitizer-safe — no SQL text involved) plus a vector. Owner: this slice.

Notes recorded without findings: the SQL-level cursor-validation-before-existence nuance (a
direct SQL caller with a mismatched cursor on an unknown queue gets TQ422, not TQ001 — the
Manifest's contracted ordering "existence precedes the capability gate" holds; noted for the
Manifest's SQL-caller commentary); the `exclude_none` GET/list serialization asymmetry (client
models tolerate both; cosmetic); the now-dead DEFERRED guard in `_request` (no DEFERRED specs
remain — removable housekeeping); the 0005 error-message string drift vs 0001 phrasing
(normalized away client-side).

## 4. Contract questions

**None.** R9-01 is an implementation/error-mapping defect, not a contract gap — the SQL's
NULL-composite convention is documented and correct; the transport and manifest error row
mishandle it. **S5-CQ-04's self-adjudicated numbering resolution is ratified**: revision 1.0.6 /
amendment 13 was already occupied by ADR-020's bridge amendment, so promoting the approved H-11
envelope correction to 1.0.7 / amendment 14 with every condition retained was the only sound
reading of the approval; nothing accepted was reused or edited.

## 5. Required dispositions

- **Audit-B four states — PASS** (reviewer-executed inside both suites): authorized-unknown →
  TQ001 on every view before capability evaluation; existing + `ready` at 0005 → TQ501; existing
  + `ready` after 0006 → bounded page including 200-empty; `running`/`finished` after 0006 →
  TQ501. The 0005 repair is real: the 0004 baseline had no existence check at all.
- **Three views — PASS**: only `ready` active; the post-state is asserted as an equality; no
  unauthorized role can alter capability metadata, execute a read-model function (observer-only
  grants, PUBLIC revoked), or reach a base table (executable negative).
- **Profile/ETag matrix — implemented correctly** (absent → bootstrap; exact → row-locked CAS;
  malformed/weak/wildcard → TQ422; stale → typed conflict with current-version-only details; no
  request echo — the response profile is always re-validated from the SQL row), **with the
  wire-evidence gaps of R9-04(b) and the R9-01 unknown-queue branch**.
- **SQL/HTTP parity — PASS**: generated-client → ASGI → SQL compared field-by-field against raw
  owner-only rows across all 12 projected columns; both official clients expose exactly the
  active methods, the gated worker-list returns typed TQ501 server-side, and no success escape
  hatch exists for `running`/`finished` (the view parameter travels to SQL, whose gate answers).
- **B9 on both majors — PASS structurally** (reviewer-reproduced: named index family, bounded
  candidates, no sort/seq-scan for `ready`; proven negative shapes for the other views; hot-path
  bindings unchanged), **with R9-02's stale final assertion to fix and re-run green**.

## 6. Commands and limits

Reviewer-executed: full suite + migration drills on fresh PG 18.3 and exact-minor PG 16.14
containers with CI-shaped Redis (465/1 each); `TASKQ_PLAN_CHECKS=1` million-row gates on both
majors (structural PASS, R9-02 failure isolated and diagnosed); `ruff format --check`
(two-file drift confirmed, provenance established via range-start formatting under the identical
ruff); `ruff check` clean; range/trailer/path-class derivation; remote/CI state via the GitHub
API (green runs predate the range). Limits: no wheel/sdist rebuild this round (no packaging
boundary crossed by the slice — the version is unchanged since a2); no host or production contact
of any kind.

## 7. Preconditions

1. Fix R9-01 (transport NULL-composite handling + facade CAS missing-queue branch + manifest
   PUBLIC_ERRORS row + wire vector).
2. Fix R9-02 (plan-gate capability assertion to the contracted post-0006 equality) and re-run the
   million-row gate green on both majors.
3. Fix R9-03 (mechanical format commit; push the full range; CI green at tip).
4. Add the R9-04 vector set (cursor negatives + pagination round-trip; HTTP stale-TQ409 +
   weak-ETag; the S5-CQ-02 published-client envelope vector; one facade-level list success
   vector).
5. Fix R9-05 (facade-supplied TQ501 reason/view details + vector).

A targeted delta check over the remediation range converts this verdict to READY.

## 8. Scope of a future READY

READY will authorize **only** a future, separately specified host-adoption decision for the
`ready` view and profile surfaces. It will not authorize production migration of 0004–0006 (which
retains its own rollback-floor decision under ADR-020), UI work, activation of `running`/
`finished` (each needs its own B9 proof and immutable migration), any retirement action, or the
Stage-5 pilot.
