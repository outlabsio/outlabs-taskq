# Round 20 response — QDarte direct contact retirement design

## Verdict

**BLOCKED.** The producer-before-consumer architecture, shared-ledger
preservation, authority boundary, no-claim guard, two observation windows, and
paired zero-DML rollback floors are sound. C8-R1 remains closed until two
coupled production-enablement gaps and one caller-contract ambiguity are
corrected docs-first.

This response is owner-authorized, internal, and **not independent**. The same
session froze the proposal and performed this review because the usual
separate reviewer is unavailable. I regenerated the Git/source inventories,
fetched the current admin integration ref, inspected the current caller and
worker execution paths, and reran taskq's repository gates rather than treating
the proposal as independent evidence.

No source, configuration, service, IAM, database, queue, worker, deployment,
or production state was changed by this review.

## Source identities and independently derived inventory

The reviewed taskq commit `fbdd579bd1a465326036faf401ac09c2f7c625da`
is docs-only over `2a11ed448862564597b5a703b33b1974a7cf6fda` and
contains the Tier-3 proposal, tier registration, Build Plan update, and
same-commit board record. The commit has the required trailer. Tier-0, ADR,
SQL, migration, source, configuration, service, IAM, database, deployment, and
prior Tier-4 paths are unchanged.

The source review used:

- QDarte API `78d5ce5b8d731fda71d590fbde03d4b4a434bf78`;
- QDarte workers `0c795d69c3605cab5a7d133dce8159d9b11e3994`;
- QDarte runtime `17e78a4e077bc9c238dbcca8f97a9d386a4331f5`;
- freshly fetched QDarte admin `origin/staging@ae83558`; and
- the taskq contract/source at the reviewed main tip.

The independently derived executable inventory matches the proposal:

1. `qdarte-admin` posts `ContactVerifyScopeJobCreateRequest` to
   `/ops/jobs/contact-verify-scope`, decodes `WorkerJobDetail`, derives planned
   counts from the legacy payload, polls the generic direct-job list by
   `contact_verify_scope`, and exposes generic direct cancellation.
2. QDarte API has three contact producer routes: the historical
   `qdarte_ops` producer, the older host-taskq producer, and the C6/C7 cutover
   route. The first calls `WorkerJobService`; the second calls the host
   `TaskqClient`; the third selects direct versus package from the mode and in
   package uses ADR-023 reserve-before-plan/finish.
3. Direct result mutation exists in both the `qdarte_ops` worker route/service
   and older host-taskq result route. The direct ordinary worker has the type
   in defaults, handler map, verifier, result client, and generic claim loop.
   The optional old taskq worker loop is contact-only.
4. The API verification lane pairs contact with website verification. Removing
   the whole lane or shared generic worker machinery would therefore be wrong.
5. The three `qdarte_ops` relations are shared across many job types. The old
   host-taskq schema also has separately retained history. Neither is eligible
   for schema/data removal.
6. Planning, contact result application, stable application rows, contact
   methods, usage counters, payload/result models, and the closed package
   worker remain package dependencies. Their contact names do not make them
   direct-only.
7. No contact caller appears in the public site or intake source. The proposal
   correctly refuses to treat repository search as proof of no out-of-tree
   caller and requires deployment/access-log evidence before C8-R1.

## Attack-program dispositions

### A. Executable inventory — PASS

Every currently identified producer, consumer, admin, script/config, shared
ledger, and package dependency has a disposition. The current admin ref was
fetched rather than inferred from the stale checkout. The pre-R1 live caller
and access-log sweep remains binding.

### B. Full-replacement claim — PASS

The destination uses no direct/package mapping, mirrored row, row copy,
cross-backend retry, reservation cache, or direct status projection. QDarte's
retained endpoint owns only host authorization and candidate planning; the
package admission/job ledgers remain authoritative. That is a legitimate host
integration, not preservation of the old queue behind a wrapper.

### C. Caller floor and exact-ID status — PASS WITH MEDIUM R20-03

The exact-ID design fixes queue/type authority, uses the official client,
omits payload/error/fence/worker data, hides absent/wrong-type/denied cases,
and grants only enqueue+read. It correctly refuses runtime operator authority
and removes direct cancellation for package jobs.

The remaining ambiguity is product-visible: the current admin can rediscover
recent direct work by scope after reload and can request cancellation; the new
design keeps only a client-side last-job hint and deliberately drops runtime
cancellation. That can be an acceptable least-privilege trade, but the proposal
does not make acceptance of the reduced rediscovery/cancel behavior an explicit
C8-R1 eligibility row. It must be owner-accepted and UI-vector-pinned before
the caller floor is declared compatible; it must not emerge accidentally from
an implementation diff.

### D. Producer-first ordering — PASS EXCEPT BLOCKER R20-01

The caller floor precedes producer removal, old callers become forbidden, both
direct producers receive a fixed unreachable posture, the cutover boundary
becomes package-only, and direct consumers remain through the first window.
The producer rollback floor—migrated caller plus C7 API in package mode—is
coherent and requires no row mutation.

However, the execution starting point is misstated. Round 19's accepted final
production posture is API mode `draining`, package queue paused, package worker
absent, and gateway absent. C8 §5.3 says to deploy the caller while the accepted
C7 API “remains in package” and then requires a real created/existed/terminal
proof. It does not specify or authorize the transition from the actual safe
posture to a serving package topology. An implementer would have to improvise
mode, queue, worker, gateway, failure unwind, and request-admission ordering at
the first production step.

### E. First observation window — PASS EXCEPT HIGH R20-02

The seven-day/two-API-cycle window starts after producer removal and cannot
borrow C7 time. Full-row direct hashes, package/effect/egress reconciliation,
real admitted replay, scheduled backups, and reset conditions are strong.

But the workload entering that window is not bounded by the accepted evidence.
The live admin caller submits `limit: 500`; C7 production authorized and proved
one exact allowlisted place with `limit: 1`. The C8 example freezes
`planned_entities: 1`, while the prose simultaneously proposes migrating the
current broad caller. Nothing states whether the candidate rejects, clamps, or
accepts 500, and nothing defines a staged cohort or accepted operational cap.
Caller migration therefore risks silently becoming a provider/effect expansion
two orders of magnitude larger than the evidence.

### F. Consumer retirement and stale images — PASS

The proposed server-side claim exclusion is the right invariant. The current
generic worker derives the claim type list from settings and handlers, but a
stale image can still request a supported type; filtering in the API's
candidate selection prevents that stale client from leasing a retired row.
The disposable restored-database synthetic-row vector is the correct proof,
and production receives no synthetic data. The proposal explicitly preserves
website verification and unrelated lanes and prevents unknown-task settlement.

### G. Consumer rollback and second window — PASS

The second floor is correctly stronger than the first: producer-retired
API/admin plus the final direct-capable API/worker/controller pair. Restoring
the complete pair, never an isolated old worker, is necessary because the
candidate API's no-claim guard would correctly block it. The second seven-day
window is distinct and falsifiable.

### H. Shared-ledger and operational preservation — PASS

The proposal protects shared `qdarte_ops`, old host-taskq history, package SQL
0.1.5/migrations/capabilities/history, contact-domain effects, private network,
privileges, connection budget, backup, and restore. Full-row ordered digests
cover inserts, updates, and deletes. No data/schema deletion is implied.

### I. Scheduled backup — PASS

R19-01 is bound before C8-R1. The proposal explicitly rejects a manual wrapper
run as a substitute and blocks on an unexplained scheduled failure until a
later scheduled success. The next scheduled run has not yet occurred, so this
is an eligibility precondition, not current evidence.

### J. Governance and gates — PASS

The spec commit is trailered, docs-only, board-coupled, and Tier-3-registered.
The review-request commit is separately scoped. No forbidden peer name or
prior Tier-4 modification is present. With the authenticated Redis URL that
matches the local container, the full taskq suite is 505 passed with one
opt-in skip; Ruff and format are clean. One earlier invocation used an
unauthenticated Redis URL and produced the expected auth-regression failure;
the corrected affected test and complete suite passed.

## Findings

### BLOCKER — R20-01: no frozen transition from accepted safe posture to C8-R1 service

The accepted system is draining/paused with worker and gateway absent, not
already serving package work. Before C8-R1 can open, the specification must
freeze the exact order for:

1. deploying the migrated caller with submission still disabled;
2. verifying facade/domain/auth readiness and the exact private origin;
3. starting the gateway and proving the closed worker has no bypass;
4. entering package mode through a fresh direct-drain proof;
5. unpausing the package queue only after every dependency is ready;
6. admitting exactly the authorized bounded proof request;
7. reconciling package/effect/egress state; and
8. on any failure, stopping admission, pausing the queue, stopping worker and
   gateway, and returning to the recorded draining posture with zero direct
   fallback or DML.

The migrated caller must not expose an enabled control until step 6 is safe.
The sequence must name which source/config/service action owns each transition
and which earlier slice accepts it.

### HIGH — R20-02: caller workload is 500 while accepted package evidence is one

The current UI's `limit: 500` and C7's one-place proof are materially different
operational envelopes. The remediation must:

1. derive the historical direct workload envelope from retained job payloads
   without logging candidate data;
2. freeze a server-enforced package maximum and maximum concurrent contact
   admission independent of client input;
3. define staged production cohorts from the already-proven one-place case to
   the intended supported envelope, with per-stage effect/egress/rate/latency/
   failure/backup/rollback oracles;
4. make over-limit input reject atomically before reservation/planning/provider
   work—never clamp silently;
5. keep the direct producer available but caller-inaccessible during this
   scale proof; and
6. prohibit C8-R2 until the intended operational envelope, not merely one more
   one-place canary, is accepted.

If the owner deliberately chooses a smaller replacement cap than historical
direct behavior, that is a caller-contract decision and the admin request/UI
must change explicitly before producer removal. The response field must be
specified as a positive bounded integer, not hard-coded to one unless the
accepted cap is exactly one.

### MEDIUM — R20-03: package rediscovery/cancellation trade is not an explicit acceptance

Record the owner decision that exact-ID status plus a client-side hint replaces
scope rediscovery and that cancellation remains one-off operator-only. Add UI
vectors proving reload/loss of the hint cannot display a direct job as package
state, cannot call direct cancel with a package ID, and cannot create a new job
merely to rediscover the old one. If that UX is not accepted, stop for a
separate taskq read-model/authority design; do not add a shadow mapping.

No LOW finding is necessary. R19-01 remains a named eligibility condition, not
a review defect.

## Contract questions

None. The blockers concern the host production transition and accepted
workload envelope, not a conflict among Tier-0 contracts or ADRs.

## Preconditions for targeted delta acceptance

One docs-only remediation may update the Tier-3 specification, Build Plan, and
board. It must:

1. freeze R20-01's complete draining/paused → bounded package → safe-unwind
   choreography;
2. freeze R20-02's measured historical envelope, server-side caps, staged
   cohort gates, over-limit rejection, and C8-R2 dependency;
3. record and vectorize the R20-03 owner UX/authority decision; and
4. change no source, configuration, service, IAM, database, queue, worker, or
   production state.

A targeted delta review of only those passages may convert this verdict to
READY. The next naturally scheduled backup still has to pass afterward (or in
parallel) before C8-R1 production work.

## Scope opened and still closed

BLOCKED opens only the docs remediation and its targeted delta request. It
does not open C8-R1, package enablement, caller deployment, IAM, a cohort,
producer removal, consumer removal, data/schema deletion, another lane, or
Stage 6.
