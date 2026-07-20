# taskq — outlabsAPI Host Branch Reconciliation Specification

> **Status:** Frozen by S4-POST-00 — 2026-07-20
> **Tier:** 3 implementation design; subordinate to Protocol v1 document revision 1.0.4,
> Function Manifest 0.1.2, and ADR-001..018
> **Scope:** outlabsAPI Git lineage, deployment-branch identity, evidence, and rollback only. This
> specification authorizes no source change, production deployment, legacy-path retirement, SQL,
> migration, taskq contract, or capability change.

## 1. Outcome

Stage 4 completed on a production lineage that diverged from the repository's default branch. The
reconciliation slice makes one reviewed host line authoritative without replaying stale code into
production or hiding either history.

Completion means:

1. the accepted production tree and its Stage-4 evidence are the starting truth;
2. every default-only and production-only commit since the common ancestor is classified;
3. the reconciled commit has both histories as ancestors but retains only explicitly accepted tree
   content;
4. the repository default branch and Coolify deployment branch resolve to the same commit;
5. branch protection, release identity, suites, image, migrations, and live health are proven; and
6. the prior branch tips remain immutable rollback references until a later cleanup window.

This is lineage repair, not a feature merge. A green textual merge is insufficient evidence.

## 2. Frozen baselines and observed graph

The S4-POST-00 inventory observed:

| Identity | Commit | Meaning |
|---|---|---|
| common ancestor | `a0019cd` | last shared default/production history before the host rewrite |
| current default `main` | `7df6b7f` | accepted disabled taskq integration on the stale host shape |
| deployed `staging-prep` | `3f50b7d` | production source and dependency tree accepted by Stage 4 |
| accepted evidence tip | `9348f85` | deployed lineage plus local gate and immutable Stage-4 records |

The production lineage contains the host's Postgres-backed `outbound_tasks` rewrite, removals and
runtime changes absent from `main`, followed by production-specific taskq integration and fixes.
The default line contains two Stage-4 commits whose intent was reimplemented on the production
line, but whose patches are not safe to replay mechanically. The diff spans host domains,
dependencies, migrations, worker topology, configuration, tests, and documentation.

These facts make all of the following forbidden:

- merging `main` into the production line with ordinary conflict choices;
- rebasing or force-pushing either accepted tip;
- cherry-picking default-line Stage-4 commits merely because their messages resemble later work;
- treating patch-id inequality as proof that behavior is missing;
- changing Coolify's branch before both names resolve to the reviewed tree; or
- combining reconciliation with legacy retirement, dependency upgrades, migrations, or cleanup.

## 3. Independent commit and surface ledger

Before constructing a reconciliation commit, S4-POST-R1 writes a host-side ledger from Git—not
from this specification—with one row per commit reachable from exactly one baseline. Each row has:

- full commit id and side (`main-only` or `production-only`);
- files and operational surfaces affected;
- disposition: `present`, `superseded`, `retain`, `forward-port`, or `reject`;
- source evidence for semantically equivalent behavior;
- the exact test, migration, route, setting, or runtime oracle that detects a wrong disposition; and
- reviewer plus review date.

`present` and `superseded` require semantic evidence. Commit-message similarity and a clean merge
do not qualify. `forward-port` creates a new, narrowly reviewed commit on the accepted production
tree; it never imports a broad stale patch. `reject` records why the old behavior must stay absent.

The ledger must cover at least application routes, auth initialization, database migrations,
runtime grants, API and standing-worker images, taskq integration, tool registry, removed domains,
configuration, dependency lock, deployment documentation, and all tests added or removed on either
side.

## 4. Reconciliation construction

S4-POST-R2 starts from accepted evidence tip `9348f85`, after fetching and pinning both remote
baselines. Any `forward-port` rows land one per normal host commit with their own tests. When every
ledger row is adjudicated, construct a two-parent reconciliation commit whose parents include the
accepted production line and the exact old default tip.

The reconciliation commit's tree must be byte-identical to the reviewed production-derived tree
plus only the ledger's explicit forward ports. An independent tree-hash manifest asserts this. The
second parent records that the old default history was considered; it does not authorize stale tree
content.

Before moving any remote branch:

1. create immutable annotated rollback tags for `7df6b7f`, `3f50b7d`, and `9348f85`;
2. push the candidate under a non-deploying `codex/` branch;
3. require a review that independently regenerates the ledger and tree manifest;
4. run the complete host suite, Ruff, MyPy, lock check, offline full Alembic upgrade, image build,
   taskq local production-shape gate, and artifact/import checks; and
5. prove the candidate introduces no taskq SQL, migration, wire, permission, or IAM change.

After acceptance, advance `main` only by fast-forward to the accepted two-parent commit. Never
force-update it. `staging-prep` remains the Coolify source until `main` and the candidate resolve to
the same commit and tree.

## 5. Deployment-branch cutover and rollback

Changing the Coolify branch is its own deployment event even when the tree is identical.

1. Record current app/worker branch, commit, image identifiers, settings digest, health, taskq
   active depth, legacy active depth, worker presence, migration head, and connection ceiling.
2. Point the API and standing worker to reconciled `main` without changing any environment value.
3. Require the built source revision and image tree to match the accepted candidate before traffic.
4. Verify startup, `/health`, taskq readiness, authenticated host request, one keyed read-only taskq
   invocation with canonical readback, and one non-tools legacy-path probe that causes no external
   side effect.
5. Observe one complete normal rolling deployment with bounded taskq drain and no manual DML.

Rollback is a branch flip to the immutable `staging-prep` rollback tag plus restart. Database
schemas, IAM, queue profiles, and rows remain untouched. A rollback rehearsal must prove the old
branch can boot against the unchanged database and that the same producer-mode settings remain
valid.

Do not delete or repoint the old branches/tags during this slice. After two healthy deploy cycles on
the authoritative line, mark old branches archival and non-deploying; deletion is a later repository
maintenance decision.

## 6. Acceptance matrix

| ID | Required evidence |
|---|---|
| BR-01 | Independently generated left/right commit ledger from `a0019cd`, with every row adjudicated |
| BR-02 | Exact tree manifest proves no unclassified default-line content entered the candidate |
| BR-03 | Both accepted histories are ancestors; `main` advances without force |
| BR-04 | Full host gates, Alembic, image, local taskq production-shape, and import/artifact checks green |
| BR-05 | No taskq SQL/migration/Tier-0/IAM/capability delta |
| BR-06 | API and worker deploy the same accepted commit and unchanged settings digest |
| BR-07 | Health, auth, taskq canonical path, non-tools legacy path, worker presence, and pool budget green |
| BR-08 | Normal rolling drain stays within platform grace with zero manual DML |
| BR-09 | Branch-flip rollback boots against unchanged database and restores the accepted posture |
| BR-10 | Rollback tags retained; old branches archival only after two healthy cycles |

S4-POST-R-AUDIT independently regenerates BR-01/02 and verifies every other row. Legacy retirement
cannot begin until that audit is accepted.

## 7. Stop conditions

Stop and record a host design question if the ledger finds default-only behavior that production
still needs, if the reconciled tree cannot include both histories without an unreviewed change, if
the deployment platform cannot prove the running revision, or if rollback needs database mutation.
Stop under the taskq contract process if any proposed resolution changes SQL, wire behavior,
permissions, or capability identities.
