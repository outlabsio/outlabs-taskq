# External targeted review — first-host read-model adoption — Response

> **Reviewed:** `docs/Task Queue Stage 5 Read Model Host Adoption Specification.md` (frozen
> proposal) against the accepted library tip and the authoritative host `main`.
> **Method:** reviewer-inline authority reads plus independent derivation of the host pin, facade
> posture, and — per the charter — the a2/a3 artifact contents and route surfaces from Git source
> rather than from the plan. Docs-only; left uncommitted.

## 1. Verdict

**BLOCKED — by one concrete, plan-invalidating migration/artifact mismatch (R10-01) plus three
smaller docs-first preconditions.** The plan's architecture is otherwise sound and verified: the
two-artifact expand→migrate→contract concept, the ADR-020 rollback floor, the owner-vs-runtime
credential separation, the host's zero-new-surface posture, and the acceptance-oracle discipline
all hold against source. The blocker is that the **a3 bridge artifact cannot execute Step C as
written** — it does not contain migration 0006 and its verifier expects the pre-activation state.
The fix re-sequences one migration and is a docs amendment, not a re-architecture. A targeted
delta review of the amended spec returns READY.

## 2. Independently derived identities (from source, not the plan)

- **Host** `origin/main` = `2ed736b`; taskq pin = immutable **`0.1.0a2`** by exact release
  URL + SHA-256 `d3c37b0e…`. Verified in `pyproject.toml`.
- **a2 rejects 0.1.4 (confirmed):** the a2 wheel source (`36db7cf`) hard-pins startup to
  `meta.contract_version != "0.1.2"` (`http/runtime.py:413`) — exact, not a set. So a2 cannot
  boot a migrated database; the rollback-floor premise is real.
- **a3 bridge source (`40aa9b5`):** `SUPPORTED_SQL_CONTRACT_VERSIONS = frozenset({"0.1.2",
  "0.1.3","0.1.4"})` (`http/runtime.py:36`) — accepts the migrated database. Its
  `GET_QUEUE`/`LIST_JOBS` HTTP command specs exist but are `HttpSurface.DEFERRED`
  (`protocol.py:1459-1470`): the facade raises TQ501 for a non-ACTIVE surface
  (`facade.py:302`) and excludes DEFERRED from OpenAPI (`facade.py:917`); the client skips
  DEFERRED, so no generated method. **So a3 exposes no read-model success route regardless of
  database state** — the operative Step-A safety claim holds (see R10-04 on wording).
- **Host facade posture (verified):** `create_taskq_app(runtime, authorizer=…,
  not_found_on_forbidden=True, poll_interval=1.0)` with **no `operator_transport`** and
  `operator_pool_max=0` (`app/core/taskq_integration.py:277,310-315`). The only `/taskq/v1/jobs`
  string in host source is a `status_url` literal in the tools route response
  (`app/domains/tools/api/routes.py:139`) — not a host-owned route. No direct `taskq.*` table
  SELECT exists in host source. Adoption adds no read route, SQL projection, operator transport,
  or global list.

## 3. Finding registry

**R10-01 · BLOCKER · The a3 artifact cannot perform Step C: migration 0006 is absent from it, and
a3's verifier expects the pre-activation capability state.**
Evidence (reviewer-verified in Git): a3's source `40aa9b5` bundles migrations **0001–0005 only** —
`0006_activate_ready_read_model.sql` does not exist at `40aa9b5` and first appears at `c1fac41`.
a3's `META_SEEDS` is `capabilities: {"active": []}` (`sql/manifest.py:329` at `40aa9b5`). Step C,
however, directs `taskq migrate` **under the exact a3 artifact** to "apply only immutable 0004,
0005, and 0006," to prove post-state `capabilities exactly {"active":["read_model_list_ready"]}`,
and to run `verify()` twice. This is unsatisfiable: (a) a3 cannot apply a migration file it does
not contain — its migrate stops at 0005, leaving contract 0.1.4 with capabilities `{"active":[]}`
(ready **inactive**); (b) if 0006 were applied by other means, a3's own `verify()` would then
**fail**, because its seed equality expects `{"active":[]}`, contradicting the "verify twice"
evidence. The plan's own text ("a3 … predates the H-08/H-11 facade/client addition"; "audited
source 40aa9b5") pins a3 to a tree that structurally cannot produce the ready-active post-state.
Impact: the central production step is not executable as specified; an implementer would either
improvise (manual 0006 DML — explicitly forbidden by the plan) or discover the gap mid-migration.
Smallest remediation — **re-sequence one migration so activation rides with route exposure:**
- **Step C, under a3:** apply **0004 + 0005 only** → contract 0.1.4, capabilities `{"active":[]}`,
  no route. a3's `verify()` expects exactly this and passes twice. This is the new rollback floor.
- **Step D, under a4:** apply **0006** (metadata-only activation) as part of route exposure, since
  a4 is the artifact whose facade serves the `ready` route **and** whose `META_SEEDS` expects
  `{"active":["read_model_list_ready"]}`. Capability activation and route exposure then happen
  together, and every artifact's `verify()` is self-consistent with its own served surface.
This is strictly safer than the plan as written (it never activates a capability the deployed
artifact cannot serve) and needs only spec-text changes to Steps C/D and §6B. Owner: spec
amendment before A.

**R10-02 · MEDIUM · a4's source commit is not pinned with a3's rigor.**
Step A names a3's source exactly (`40aa9b5`); Step D says a4 is "from the independently accepted
library tip" with no hash. The accepted tip is the round-9 delta-accepted `1610b5a`. Remediation:
name a4's base commit exactly (`1610b5a`, plus its isolated version-release commit), so both
artifacts have identical provenance discipline. Owner: spec, before A.

**R10-03 · MEDIUM · The pre-C backup is "recorded," not proven restorable — for the first
production taskq contract migration, with restore/PITR still an un-discharged standing gate.**
§6B requires "a successful current backup/checkpoint is recorded" and honestly declines to claim
restore/PITR. The migration's *primary* rollback is the a3 bridge (application-level, zero-DML,
rehearsed), so restore is defense-in-depth, not the load-bearing path — but this is the first
taskq contract migration ever applied to the production database, and restore has never once been
exercised (open since S4-CQ-02). Remediation: elevate the pre-C evidence to a backup **test-
restored to a disposable target once** before C. It is nearly free (the backup is being taken
anyway), it converts "a backup we've never restored" into a real one, and it partially discharges
the long-standing durability gate at exactly the moment the risk concentrates. This gates Step C
only, not A/B. Owner: step-C evidence.

**R10-04 · LOW · Step A's "exposes no read-model route" is substantively correct but imprecise.**
a3's reserved `GET_QUEUE`/`LIST_JOBS` exist as **DEFERRED TQ501 responders** (verified above), not
as "no command." That is the ADR-015/017 deferred-out posture and is exactly what makes a3 safe on
any database/capability state — including the post-C, pre-D window where a3 runs against a 0.1.4
database and still serves TQ501 (route exposure is artifact-controlled, not database-controlled).
Remediation: a3's acceptance evidence should assert precisely this — the reserved paths return
**TQ501** (not 404/500), are **absent from OpenAPI**, and have **no generated client method** —
locking the property rather than asserting a looser "no route." Owner: a3 evidence.

## 4. Contract questions

**None.** Every finding is plan-executability or evidence discipline. The Tier-0 contract,
ADR-019/020/021, and immutable migrations 0004–0006 are accepted and unchanged; R10-01 is a
mis-sequencing of sound migrations across artifacts, not a contract defect. No setting activates or
deactivates a view; activation remains migration-only.

## 5. Attack-program dispositions

1. **Two-artifact ordering / rollback floor / credentials / immutable ledger — SOUND, except
   R10-01's Step-C sequencing.** a2 verifiably rejects 0.1.4; a3 accepts the closed set; post-0004
   the a2 floor is real and a3 is the only valid application rollback; owner/admin is used only for
   the migration and the runtime is non-superuser with no operator membership (host
   `operator_pool_max=0`, no operator transport). Pre-C `a3→a2` (0.1.2 DB) and post-migration
   `a4→a3` (0.1.4 DB) rollbacks are both zero-DML and rehearsed. The immutable 0004–0006
   ledger/checksum + double-`verify()` discipline is correct — once R10-01 assigns 0006 to a4.
2. **Host facade / OpenAPI / pools / worker / authorization — PASS.** Verified: no host-owned read
   route, no direct SQL projection, no operator transport, no global list, no producer action;
   OpenAPI is the merged generated facade schema only.
3. **`tools` permission/queue boundary — PASS.** `read(tools)` GET success; wrong-queue hiding via
   `not_found_on_forbidden=True`; unknown authorized queue → TQ001; malformed cursor/request-ID
   ordered after authenticate-then-authorize; `running`/`finished` typed TQ501 with only
   `reason`+`view`. PUT stays unavailable in the host (no operator transport → ensure_queue/
   update_queue_profile routes unmounted). All consistent with the accepted library.
4. **Acceptance oracles — PASS (strong).** The plan explicitly forbids injecting production jobs
   for pagination (local/disposable fixture only; production proves a single read), does **not**
   falsely claim restore/PITR (R10-03 only asks to opportunistically strengthen it), treats no host
   counter as an external invocation counter, and keeps the Stage-4 L1 legacy observation
   independent.
5. **Non-goals — PASS.** `running`/`finished` activation, production action beyond the specified
   sequence, UI, retirement, side-effecting lanes, and Stage 5 all remain explicitly closed.

## 6. Commands and limits

Reviewer-executed (read-only): host pin + facade derivation from `origin/main`; a2 exact-pin,
a3 supported-set, a3 route-surface (DEFERRED), and a3 migration-set derivation from the tagged
wheel source `36db7cf` and bridge source `40aa9b5`; migration-0006-absent-from-a3 confirmation
(`git cat-file -e`); a4-tip identity from the round-9 delta record. No package was published, no
host or database was touched, and no production request was made — appropriate for a docs-only
plan review. Prior-round dual-major 0001→0006 green evidence is relied upon as standing context;
this review adds no new execution gate beyond inspecting the plan.

## 7. What READY will and will not open

A future READY (after the amended-spec delta review) authorizes **only** beginning the
specified A→E sequence for the first host's `ready` view and profile GET. It does **not** authorize
publishing before the amendments land, activating `running`/`finished` (each needs its own B9 proof
and migration), any host-owned read route or operator surface, UI work, L1/L2 retirement, the
deferred credential (F01) or restore/PITR gates beyond R10-03's one test-restore, a side-effecting
lane, or the Stage-5 QDarte pilot. Production migration remains its own gated step within the
sequence, and each later host or view is its own bounded decision.
