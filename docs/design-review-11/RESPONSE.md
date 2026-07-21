# External targeted review — Stage 5 QDarte local-first pilot — Response

> **Reviewed:** `docs/Task Queue Stage 5 QDarte Pilot Specification.md` (frozen proposal) against
> the accepted library artifact and the four authoritative QDarte repositories.
> **Method:** reviewer-inline authority reads + independent verification of the a3 artifact from
> Git tags/source + a full adversarial source audit of the four QDarte repos (handler purity,
> legacy topology, isolated compose, credentials, registry). The purity claim was traced in source
> line-by-line, not accepted from a test name. Docs-only; left uncommitted.

## 1. Verdict

**READY — to begin P0–P5 in the isolated disposable `qdarte-dev` environment only.** The pilot is
safe and falsifiable: the handler is source-confirmed pure and deterministic on the empty-input
path, the legacy `qdarte_ops` fleet is structurally unable to touch a separate `taskq.qdarte_pilot`
queue (and vice versa), the a3 bridge is the correct route-free artifact for a local 0001–0005
install, and the acceptance oracles rest on durable ledger state rather than sleeps or logs. Four
findings (one MEDIUM, three LOW) are docs-first spec refinements to fold in before P1; none is a
safety hole, and the plan's own P2 negative vectors already backstop the most important one.

## 2. Independently verified identities

- **Library artifact:** tag `v0.1.0a3` → `899defc`; version `0.1.0a3`; supported set
  `{"0.1.2","0.1.3","0.1.4"}`; migrations **0001–0005 only** (no 0006); `LIST_JOBS`/`GET_QUEUE`
  are `HttpSurface.DEFERRED`; `META_SEEDS` capabilities `{"active": []}`. A local 0001–0005 install
  therefore lands at contract 0.1.4 with ready **inactive**, and a3's `verify()` expects exactly
  that — matching the pilot's P2/QP-03 precisely. a3 is the correct route-free bridge; 0006 and
  read-model activation are neither present nor needed, and no production rollback-floor is
  implicated (the pilot is local/disposable).
- **QDarte repos** (branch `staging`, clean): `qdarteAPI@62d41c5`, `qdarte-workers@b063ea5`,
  `qdarte-runtime@5c71aa2`, `qdarte@9bd9d4f`. No occurrence of `taskq` in any of them —
  `taskq.qdarte_pilot` is greenfield with no schema/route collision.
- **Range hygiene:** `c0092f2`+`4c12316` are docs-only (spec + review gate + board/index),
  trailered, pushed; the round-10 remediation and delta acceptance landed correctly (a3 = route-
  free bridge, a4 pinned to `1610b5a`); ADR-018 is now committed. Not absorbed into this range.

## 3. Findings

**R11-01 · MEDIUM · The taskq facade must use a dedicated non-superuser role and its own
connection, explicitly NOT the QDarte API's existing `postgres` (superuser) session.**
Evidence: QDarte's dev API connects as role `postgres` (`qdarteAPI/.env.example:8`,
`postgresql+asyncpg://postgres:...`), which is superuser in the stock image; the facade mounts
inside qdarteAPI and the naive path is to reuse the API's single DB session. §3 correctly *requires*
a non-superuser runtime login with capability memberships only — but the spec does not state that
the facade must construct its **own** engine/pool on a dedicated role rather than inherit the API's
superuser session. A superuser session silently defeats the entire capability GRANT boundary (it
bypasses `SET ROLE`, base-table, and role-creation restrictions) — the exact class as the
outlabsAPI S4-CQ-02 blocker. Note the plan is self-protecting: P2/QP-03's required non-superuser
negative vectors (operator, role-creation, base-table reads must be *denied*) would **fail** under a
superuser facade session, correctly blocking the pilot — so this cannot silently pass. Remediation
(docs-first, before P1): §3/P1 state that the taskq facade runtime uses a distinct non-superuser
DSN/pool, separate from QDarte's `postgres` API session, and P0 records the actual role attributes
of both connections. Owner: spec amendment before P1.

**R11-02 · LOW · "Empty input" is not literally `{}`; pin the exact synthetic constants.**
`ClusterResearchScopePayload` requires `scope_key` and `country_code` (min length 2); the existing
smoke supplies `scope_key:"ar"`, `country_code:"AR"` with empty region/cluster lists. The spec's
"no candidate regions and no external configuration" is accurate for purity but under-specifies the
canonical input. For QP-05's "same canonical digest" shadow comparison to be reproducible, the spec
should name the exact synthetic constants (matching the existing smoke). Missing them raises a pure
`ValidationError`, not an I/O — so this is precision, not a purity hole. Owner: spec, before P3.

**R11-03 · LOW · State the closed-literal / shared-registry non-touch as an explicit stop
condition.** The legacy `JobType` is a closed `Literal` and the shared `_REGISTRY`
(`qdarte-runtime`) is the legacy fleet's dispatch source; `cluster_research_scope`'s entry has
`allowed_child_job_types=frozenset()` (spawns nothing). Adding the pilot type to either would
couple the fleets. §1/§2 imply separation; make it explicit: `qdarte.cluster_research.pilot` is
registered **only** in the taskq facade's own type map, never in the shared `JobType` literal or
`_REGISTRY`. Owner: spec, before P3.

**R11-04 · LOW · QP-09's legacy-drift oracle must include an update detector, not only count +
max-id.** The charter requires detecting inserts, updates, **and** high-water drift. Row count +
`max(id)` (uuidv7, monotonic) detect inserts; they do not detect an in-place UPDATE. Remediation:
QP-09 snapshots `count(*)`, `max(id)`, **and `max(updated_at)`** across the six `qdarte_ops` tables
(`worker_jobs`, `worker_job_events`, `worker_job_attempts`, `worker_artifacts`, `workflow_runs`,
`worker_job_dependencies`); each must be byte-identical after the pilot. Owner: spec, before P4.

## 4. Contract questions

**None.** The pilot uses existing queue, authorization, worker, and settlement contracts; no taskq
SQL, wire, permission-grammar, or capability change is proposed, and the spec's own stop rule
(record a conflict rather than adapt) is correct. All findings are host-integration precision.

## 5. Attack-program dispositions

1. **Legacy boundary — PASS (structural).** The legacy claim query reads only
   `qdarte_ops.worker_jobs` and can never see a `taskq.*` row; the fleet always type-filters and
   refuses to run with an empty supported-type set; the pilot type is triple-blocked from the
   legacy ledger (closed `JobType` literal + registry `KeyError`→404 on the non-public generic
   enqueue + `allowed_child_job_types=frozenset()`). Oracle per R11-04.
2. **Artifact/migration bridge — PASS.** a3 verified: correct route-free bridge, supported set
   accepted, migrations 0001–0005, no 0006, no read-model surface, `verify()` posture matches a
   0001–0005 install; owner/admin-only migration and zero-manual-DML rule are sound; no production
   rollback floor is implicated by a local install.
3. **Credentials and topology — PASS with R11-01.** Workers are HTTP-only with a Bearer token and
   hold no `qdarte_ops` DB password; the queue-scoped token gets only `taskq_qdarte_pilot:run`; the
   read principal only `:read`; no wildcard/global-browser/public-enqueue is introduced. The one
   gap is the superuser-session trap (R11-01); connection arithmetic is correctly deferred to
   measurement (P0/P1), not asserted.
4. **Pilot behavior — PASS.** Handler is source-confirmed pure/deterministic (no I/O to branch to
   on the empty path); keyed `created`→`existed`, response-loss single-invocation replay, and
   the hard-kill same-id lease-expiry/reap conservation rest on durable attempts/events/budget
   state; the P5 hard-kill is explicitly evidence for this pure lane only and does **not** waive the
   future side-effecting-lane hard-kill gate.
5. **Isolation and rollback — PASS.** The `qdarte-dev` stack runs its own PG18/Redis, denies the
   Docker socket and prod backup paths, and masks source env with a self-checking guard; the
   existing empty-input smoke is real and isolation-gated; disable is zero-DML; no dual-publish,
   copied surface, external effect, chaining/followup, or read-model/UI activation.
6. **Stage boundaries — PASS.** Does not waive the side-effecting hard-kill gate, does not disturb
   the outlabsAPI L1 observation (a different host), and authorizes no production, existing-lane
   migration, retirement, or Stage 6.

QP-01..QP-10 dispositions: all supportable as written, with QP-05 pinned by R11-02, QP-03/QP-04
strengthened by R11-01, QP-09 by R11-04, and the P3 registry guard by R11-03.

## 6. Commands and limits

Read-only: a3 tag/artifact derivation from the outlabs-taskq repo; four-repo QDarte source trace
(handler body + transitive imports, legacy claim loop, generic-enqueue gating, isolated compose
and smoke, DB roles, registry). No repository was modified, no compose was started, no database or
package action was taken — appropriate for a docs-only plan review. The connection budget and exact
role attributes are correctly left to P0/P1 measurement in the live isolated stack.

## 7. What READY does and does not open

READY authorizes **only** P0–P5 in the isolated disposable `qdarte-dev` environment, after the
R11-01..R11-04 spec refinements land docs-first. It does **not** authorize any production, Mac-mini,
or cloud target; migration or retirement of QDarte's existing `qdarte_ops` ledger; a side-effecting
or chaining lane (the hard-kill gate for those remains required and unwaived); read-model or UI
activation; the outlabsAPI read-model rollout or its L1 tools-retirement observation; or Stage 6.
Each later lane, host, or production step is its own bounded decision with its own review.
