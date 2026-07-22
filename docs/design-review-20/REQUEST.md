# Round 20 request — QDarte direct contact retirement design

## 1. Assignment and authority

Perform a targeted adversarial review of the frozen QDarte contact-verify
direct-retirement specification. This is a design gate, not an implementation
review. Derive the current caller, producer, consumer, shared-ledger, package,
and rollback facts from source before trusting the specification's inventory.

Authority order:

1. `AGENTS.md` and `docs/README.md`;
2. `docs/Task Queue Transport Protocol v1.md`;
3. `docs/Task Queue 0.1 Function Manifest.md`;
4. `docs/adr/ADR-020-supported-sql-contract-sets.md`,
   `ADR-022-trusted-worker-side-effect-reporter.md`, and
   `ADR-023-durable-two-phase-admission.md`;
5. `docs/Task Queue Stage 5 QDarte Contact Verify Consolidation Specification.md`;
6. `docs/Task Queue Stage 5 QDarte Contact Verify Compatibility and Cutover Specification.md`;
7. `docs/Task Queue Stage 5 QDarte Contact Verify C7 Environment Plan.md`;
8. immutable Round-19 request and response; then
9. `docs/Task Queue Stage 5 QDarte Contact Verify Direct Retirement Specification.md`.

Higher tiers win every conflict. If the review finds a Tier-0 or ADR conflict,
report a Contract question and return BLOCKED. Do not propose code around it.

The reviewed taskq range is:

```text
2a11ed448862564597b5a703b33b1974a7cf6fda..fbdd579bd1a465326036faf401ac09c2f7c625da
```

The range must be docs-only. The source inventories use these named snapshots:

- QDarte API `78d5ce5b8d731fda71d590fbde03d4b4a434bf78`;
- QDarte workers `0c795d69c3605cab5a7d133dce8159d9b11e3994`;
- QDarte runtime `17e78a4e077bc9c238dbcca8f97a9d386a4331f5`;
- current remote QDarte admin integration line, recorded by the proposal as
  `origin/staging@ae83558`.

Re-fetch the admin remote and record the reviewed tip. A stale local checkout
is not sufficient evidence. Review source and executable configuration, not
commit messages or prior test names.

The usual external reviewer is unavailable. If this review is executed by the
implementation session under the owner's standing authorization, the response
must say plainly that it is internal and not independent, and must regenerate
the evidence rather than describing itself as independent.

## 2. Scope and prohibited actions

This review may create only `docs/design-review-20/RESPONSE.md`. Modify nothing
else. Leave the response uncommitted so the recording flow can preserve it
immutably.

Do not:

- edit the frozen specification, contracts, ADRs, prior review files, source,
  configuration, tests, migrations, IAM, databases, services, or deployment;
- start/unpause a worker or queue, admit a job, make a provider request, or
  mutate production;
- treat Round-19 READY as removal authorization;
- authorize producer removal, consumer removal, history/schema deletion,
  another QDarte lane, or Stage 6; or
- name any third-party queue project.

A READY verdict opens only C8-R1 caller migration, and only after the
specification's eligibility gate—including the next naturally scheduled 03:15
backup—has passed and been recorded. Each later C8 slice retains its own stop
and acceptance boundary.

## 3. Review questions

### A. Independently derive the executable inventory

Search the current QDarte API, admin, workers, runtime, public site, and intake
repositories for every contact-verification caller and executable reference.
At minimum, determine:

1. every route that can create a direct `qdarte_ops` contact job;
2. every route that can create a contact job in the older host `taskq` schema;
3. the retained package admission route and its exact response;
4. every admin, script, smoke, or manual client and the response/status/cancel
   behavior it expects;
5. every ordinary and host-taskq worker claim/handler/result path;
6. every worker-spec, job catalog, environment, desired-state, and controller
   reference; and
7. every shared model/planner/effect function that the package path still
   needs.

Produce your own inventory first, then compare it to §2 of the proposal. A
missing active caller or a direct producer not assigned a disposition is a
BLOCKER. Repository search alone cannot prove out-of-tree absence; verify that
the proposal requires a current deployment/access-log sweep before C8-R1.

### B. Challenge the claim of full replacement

Decide whether the end state is truly package-only for this lane or merely a
durable wrapper around the old queue. Reject the design if it requires:

- a direct/package job mapping;
- a mirrored or synthetic direct row;
- payload/result copying between ledgers;
- a fallback or retry through the other backend;
- a server-side reservation cache; or
- direct rows to represent package progress.

Confirm that the retained host endpoint has a legitimate domain responsibility
(authorization and candidate planning) and that taskq remains authoritative
for admission/job state.

### C. Attack the caller floor and exact-ID status design

The current admin caller expects a legacy `WorkerJobDetail`, direct list by
scope, and generic direct cancellation. Verify that C8-R1 explicitly migrates
those assumptions before producer removal.

Challenge the exact-ID status boundary for:

- caller-supplied queue/type/projection authority;
- payload, error, fence, worker, or credential leakage;
- absent/wrong-queue/wrong-type/denied enumeration differences;
- a hidden list/search or durable shadow mapping;
- use of the official HTTP client rather than package base-table access; and
- the exact queue-scoped permission set.

Confirm that the API service principal gains only `enqueue` + `read` on
`qdarte_contact_verify`, while `run`, `operator`, and every other queue remain
denied. Confirm that package cancellation remains operator-only and the admin
does not silently send a package ID to the direct cancel route.

Decide whether the client-side last-job hint is honest and safe: losing it may
reduce navigation convenience but must never lose, duplicate, cancel, or
misrepresent durable work.

### D. Attack producer-first ordering

Verify that an accepted/deployed caller floor precedes removal and becomes a
permanent minimum rollback caller. Then check that C8-R2 assigns every direct
producer—including the old host-taskq contact route—a fixed unreachable
posture with no redirect, planning, fallback, or ambiguous dual attempt.

The retained cutover boundary must become package-only. Confirm that direct
consumers remain during the first observation window and that candidate code
may stop interpreting the mode while the environment keeps the old value for
the immutable C7 rollback image.

Reconstruct both producer rollback directions. The only allowed rollback is
the migrated caller floor plus the C7 API in `package`. Reject any choreography
that can restore an old caller which posts to a direct route, or that needs DML,
row recreation, or backend replay.

### E. Falsify the first observation window

Verify the seven-consecutive-day/two-normal-API-cycle window is measured from
the producer-removal deployment, not borrowed from C7. Confirm its direct
oracle uses ordered full-row hashes for filtered jobs and joined attempts/events,
not current depth or maxima alone.

Require at least one real package admission plus admitted replay and exact-ID
terminal read. Package/effect/egress arithmetic must explain every growth. Any
direct insert/update/delete, active row, old caller, unexplained counter, or
fallback resets the window.

### F. Attack consumer retirement and stale images

Verify C8-R3 cannot start until the producer window is accepted and zero
active/running direct rows are rechecked immediately before deployment.

The API-side no-claim guard is load-bearing. Inspect the current generic claim
flow and decide whether the proposed server-side exclusion really prevents a
stale or misconfigured old worker from leasing a direct contact row. Require a
disposable restored-database vector with a synthetic retired row and both
current and stale-worker claim requests. Production must receive no synthetic
row.

Confirm that an unexpected row remains unclaimed, visible, and alerted; it may
not hit an unknown-task success/failure fallback. Confirm the removal is
contact-only: `website_verify_scope`, generic claim/lease/retry/settle,
unrelated worker specs, and at least one additional lane remain green.

Classify direct-only versus shared symbols. The package planner, domain effect
application, stable result ledger, payload/result decode, retained history,
and closed package worker must survive. A name containing `contact_verify` is
not proof that a symbol is safe to delete.

### G. Challenge the second rollback and observation window

Reconstruct the consumer rollback floor. It must be the producer-retired
API/admin plus the last direct-capable API/worker/controller pair, restored as
a complete compatible set with zero DML. A rollback below the producer-retired
API floor is forbidden.

Confirm the second seven-day window is distinct, includes two normal
worker/controller replacement cycles, retains exact unchanged direct hashes,
proves package effect/replay health, keeps unrelated lanes green, and records a
zero production retired-row alert count.

### H. Preserve every shared ledger and operational boundary

Verify the proposal never drops, renames, truncates, rewrites, or weakens:

- shared `qdarte_ops.worker_jobs`, attempts, events, migrations, indexes,
  history, or generic reads;
- the older host `taskq` schema/migrations/history;
- package migrations 0001–0007, SQL 0.1.5, capabilities, queue profile, IAM,
  admissions, jobs, attempts, events, or results;
- stable applications, contact methods, and usage counters; or
- owner/operator separation, private network, connection budget, egress
  gateway, atomic backup, and restore procedures.

Confirm the exact full-row oracle scopes are sufficient to detect inserts,
updates, and deletes without exposing sensitive payloads or credentials.

### I. Bind the scheduled backup prerequisite

R19-01 requires the first naturally scheduled 03:15 LaunchAgent run after
Round 19. Verify the spec does not permit an on-demand wrapper run to stand in
for it, and that an unexplained scheduled failure blocks implementation until
a subsequent scheduled run succeeds. This gate must precede C8-R1 production
or IAM action; it is not deferred to producer removal.

### J. Governance, hygiene, and scope

Verify:

- the reviewed commit is docs-only and carries the required trailer plus
  same-commit `TASKS.md` update;
- the new document is registered exactly once as Tier 3;
- no Tier-0, ADR, SQL, migration, source, configuration, service, IAM,
  database, worker, deployment, or prior Tier-4 file changed;
- no third-party queue project is named;
- taskq 505/1 and Ruff/format evidence is reproducible or definitionally
  inherited from source identity; and
- the board opens only the targeted review, then C8-R1 after READY and
  eligibility—not C8-R2/R3, schema deletion, another lane, or Stage 6.

## 4. Required response

Write `docs/design-review-20/RESPONSE.md` with:

1. `READY` or `BLOCKED` verdict;
2. independence/provenance statement;
3. source identities and independently derived inventory;
4. disposition for questions A–J;
5. findings ordered BLOCKER/HIGH/MEDIUM/LOW with executable counterexamples or
   exact source citations;
6. Contract questions, if any;
7. exact preconditions to open C8-R1; and
8. explicit scope opened and still closed.

READY requires no unresolved blocker or high finding. A lower finding may be
assigned to a named later C8 slice only when it cannot invalidate caller
migration or the producer/consumer ordering. Do not make implementation edits.
