# External design review — Round 8

## Assignment

Perform an adversarial, source-backed review of the two frozen post-Stage-4 plans:

1. `docs/Task Queue Host Branch Reconciliation Specification.md`; and
2. `docs/Task Queue Legacy Tools Path Retirement Specification.md`.

Return **READY** only if branch reconciliation can safely begin without importing stale host state
and the later tools-only retirement has independently falsifiable no-dual-execution, data-retention,
and rollback gates. Otherwise return **BLOCKED** with the smallest explicit preconditions.

This is a specification review. Do not implement either plan, modify production, move branches,
create tags, edit source, change SQL/IAM, or retire anything.

## Repository identities and review range

- Library: `~/Documents/projects/outlabs-taskq`
- Review the taskq range `fef775e..9feaf79`.
- The range must contain only the board/plan/tier-map updates and the two new Tier-3 specifications.
- Host: `~/Documents/projects/outlabsAPI`, read-only for this review.
- Independently confirm—not merely copy from the specification—the host graph identities:
  common ancestor `a0019cd`, default `main` at `7df6b7f`, deployed `origin/staging-prep` at
  `3f50b7d`, and accepted Stage-4 evidence tip `9348f85` on
  `origin/codex/s4-03-cycle1`.
- Stage-4 production is accepted. Treat its immutable evidence as provenance; do not edit it.

The taskq working tree may contain a separate user-owned ADR-018 batch. Confirm the reviewed commit
does not absorb or alter that batch. Do not modify, stage, or adjudicate it in this review.

## Authority order

Read in this order:

1. `AGENTS.md` hard rules;
2. `docs/README.md` tier map;
3. Tier-0 Protocol v1 document revision 1.0.4 and Function Manifest 0.1.2;
4. accepted ADRs, especially ADR-005, ADR-006, ADR-010, ADR-011, and ADR-017;
5. `TASKS.md` and `docs/Task Queue Build Plan.md`;
6. the two frozen specifications;
7. Stage-4 specification and immutable round-7 acceptance evidence; and
8. the actual host Git graph, source, migrations, tests, runtime configuration, and Stage-4 packet.

Tier 0 and ADRs win conflicts. If a plan requires a contract change, report a **Contract question**;
do not propose a Tier-3 workaround. Never name third-party queue projects in the repository or the
response.

## Audit A — governance and scope

Verify independently:

- one board task, one commit, required trailer, and same-commit `TASKS.md` update;
- both new documents are registered as Tier 3 and no Tier-0, Tier-1, Tier-4, SQL, migration,
  package source, test source, host, deployment, branch, or production state changed;
- the specifications are genuinely separate and ordered: reconciliation first, retirement later;
- neither plan authorizes a side-effecting-lane migration or claims to satisfy the deferred
  hard-kill lease-expiry drill; and
- shared legacy infrastructure is not mislabeled as tools-only or scheduled for premature removal.

## Audit B — independently derive the host graph

Before trusting §2 of the reconciliation specification, derive:

- exact merge bases among `main`, `origin/staging-prep`, and
  `origin/codex/s4-03-cycle1`;
- the full left/right commit sets and changed-surface inventory since the common ancestor;
- default-only Stage-4 intent and its production-line replacements;
- production-only domains, removals, migrations, configuration, dependencies, API/worker topology,
  tests, and evidence; and
- which tip is deployed, which tip contains only post-deploy evidence, and which branch is stale.

Attempt to falsify every frozen identity. Record any shallow clone, missing remote ref, rewritten
history, or ambiguous deployed revision as a blocker.

## Audit C — reconciliation safety

Attack the proposed ledger and two-parent exact-tree construction:

1. Can a commit be marked `present` or `superseded` without a behavioral oracle?
2. Can a default-only security, migration, route, or dependency fix disappear behind a broad
   production-tree preference?
3. Can stale files enter through conflict resolution, generated lock output, renames, or deleted
   domains despite a matching summary diff?
4. Does the exact-tree manifest detect content, executable-bit, symlink, deletion, and generated
   artifact drift?
5. Can `main` advance by fast-forward once the candidate contains both histories, without force or
   rewriting either accepted tip?
6. Do immutable annotated tags and remote commit checks make every ref movement recoverable?
7. Can the API and standing worker accidentally deploy different revisions or settings when the
   platform branch changes?
8. Does an identical source tree still require a real deployment/health/drain rehearsal?
9. Is rollback genuinely a branch/image change with zero database or IAM mutation?
10. Do two healthy authoritative-line deploys precede archival status, with deletion left out?

Require a ledger schema and acceptance evidence strong enough for another agent to execute without
inventing merge policy. If the two-parent strategy can conceal an unreviewed semantic loss, specify
the smallest stronger oracle or construction.

## Audit D — independently derive the legacy tools surface

Ignore the retirement specification's inventory at first. From host source derive every path that
can create, claim, recover, dispatch, retry, finish, or observe `outbound_tasks`, and every use of:

- the queued tools route and its response models;
- the taskq mode/enablement/allowlist settings;
- `enqueue_tool_task` and any producer abstraction;
- the `tool_run` kind in the API, standing worker, processor, tests, scripts, and documentation;
- non-tools legacy kinds and callers; and
- migration `20260616_0005`, its table/indexes, and downgrade behavior.

Prove whether the proposed boundary is exact: tools producer first, tools consumer later, while the
shared table/service/migration/worker and non-tools lanes remain. Flag any inseparable dispatch,
hidden caller, alternate route, startup fallback, or operational script that the plan misses.

## Audit E — observation and no-dual-execution oracles

Try to defeat the seven-day/two-deploy eligibility gate and LR-01..06:

- construct a legacy insert that current depth, status counts, or a maximum timestamp alone misses;
- test clock boundaries, pre-existing rows, retries, rolled-back transactions, id reuse, and rows
  inserted then rapidly completed;
- distinguish producer admissions from handler invocations and taskq attempts;
- test committed-response-loss, timeout, cancellation, auth 429/503, taskq 5xx, worker restart,
  deployment drain, and invalid settings;
- prove an ambiguous taskq admission never causes legacy fallback;
- require keyed `created`/`existed` plus canonical readback and an independent external invocation
  counter for both read-only lanes; and
- ensure the evidence avoids payloads, results beyond bounded safe projections, fences, credentials,
  upstream bodies, and personal data.

Decide whether the frozen high-water-plus-count/creation-time oracle is sufficient under the actual
schema. If not, require the smallest reliable append-only audit or database statistic before L1;
do not accept a self-referential application counter.

## Audit F — deployment and rollback choreography

Model at least these mixed-version windows:

| API | standing worker | Expected posture |
|---|---|---|
| old producer-capable | old `tool_run` consumer | rollback baseline only |
| new producer-retired | old `tool_run` consumer | required L2 deployment window |
| old producer-capable | new consumer-retired | must never receive traffic |
| new producer-retired | new consumer-retired | L3 terminal posture |

Verify deploy order and rollback retain a compatible pair. Attack removal of platform variables,
old image retention, taskq disablement, worker readiness, queue depth, and a late historical
`tool_run` row. The unexpected-row behavior after L3 must fail loudly without execution or silent
loss and without mutating history outside the normal legacy contract.

Confirm the rollback proof itself does not recreate permanent dual-publish risk: it must be a
bounded side-effect-free rehearsal, fully settled before the taskq candidate resumes, with exact
mode and image identities recorded.

## Audit G — security, data, and remaining lanes

Verify retirement preserves:

- authenticate-first `tools:run` and queue-scoped canonical reads;
- sanitized 401/403/429/503 behavior and queue hiding;
- no generic enqueue, wildcard permission, operator credential, or direct SQL result path;
- the non-superuser runtime and existing connection ceiling/reserve arithmetic;
- non-tools enqueue, claim, retry, lease recovery, and terminal behavior;
- all legacy rows, indexes, migration history, and taskq history; and
- explicit ownership of the untested restore/PITR backlog.

Any proposed table drop, row rewrite, broad worker removal, permission change, or side-effecting lane
expansion is out of scope and blocks READY.

## Audit H — reproducible gates

Run or independently inspect enough to substantiate:

- taskq full suite with a real PostgreSQL DSN and CI-shaped Redis, plus Ruff;
- host full suite, Ruff, MyPy, lock integrity, offline Alembic full-upgrade compile, and image build;
- local taskq production-shape harness and restricted-runtime negative capabilities;
- exact taskq artifact and OutLabs Auth pins; and
- absence of source changes in the review range.

Do not touch production. If an infrastructure-heavy gate is not safely runnable locally, verify its
standing evidence and label the limit instead of inventing a pass.

## Required response

Create only `docs/design-review-8/RESPONSE.md`; modify nothing else. Include:

1. verdict: **READY** or **BLOCKED**;
2. independently derived host identities and surface inventories;
3. findings numbered `R8-01...`, each with severity, authority, executed/source evidence, impact,
   smallest remediation, and owning slice;
4. Contract questions in a separate section, even if none;
5. an explicit disposition for every BR-01..10 and LR-01..12 row;
6. exact commands/gates run and honest environmental limits;
7. preconditions to open S4-POST-R1; and
8. explicit confirmation that READY authorizes only reconciliation planning/execution—not tools
   retirement, branch deletion, side-effecting-lane migration, or Stage 5.

Leave the response uncommitted so the implementation agent can preserve it byte-for-byte with the
same board discipline used in prior rounds.
