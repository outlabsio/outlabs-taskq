# taskq — Stage 5 QDarte pilot specification

> **Status:** Tier-3 local-pilot design — P0/P0B/P1/P2 accepted. The isolated
> P2 database/IAM posture is complete; P3 alone may add the deterministic pure
> adapter, while P4 alone may start the dedicated pilot worker. Round 11
> accepted P0–P5 against a stale source
> inventory; its safety findings remain binding, while current QDarte
> direct-taskq co-residency is isolated by database. It is
> subordinate to the [Transport Protocol v1](./Task%20Queue%20Transport%20Protocol%20v1.md),
> [Function Manifest 0.1.4](./Task%20Queue%200.1%20Function%20Manifest.md),
> ADR-006, ADR-011, ADR-020, and the [Build Plan](./Task%20Queue%20Build%20Plan.md).
> It changes no contract, library package, QDarte source, database, IAM,
> deployment, worker fleet, or production queue state until the targeted review
> accepts it.

## 1. Purpose and boundary

QDarte already has a durable PostgreSQL worker ledger in `qdarte_ops`, an
HTTP-controlled worker fleet, and current staging also carries a separate
direct-SQL `taskq` implementation for its contact-verify lane. It is not a
safe or useful first move to replace either incumbent wholesale. The first
pilot instead proves that taskq can be mounted inside QDarte, provisioned with
least privilege, driven by a real QDarte worker process, and recovered after
worker loss **without** altering an existing content, provider, browser,
communication, writeback, or contact-verify lane.

The pilot is local to the isolated `qdarte-dev` compose project. It uses one
new queue, `qdarte_pilot`, and one non-chaining task type,
`qdarte.cluster_research.pilot`. Its handler is a thin adapter over the
existing deterministic `cluster_research_scope` calculation: the input has no
candidate regions and no external configuration, so it performs no network
request, browser action, provider call, media write, site mutation, or
`qdarte_ops` write. Its compact result is a taskq result only.

This is intentionally narrower than a migration of the existing
`cluster_research_scope` lane. The legacy QDarte queue remains its sole owner;
the pilot never dual-publishes, shadows by enqueueing the legacy job, or lets a
taskq worker claim a legacy job.

## 2. Source-backed starting state

The plan is based on the current authoritative QDarte staging sources inspected
on 2026-07-21:

- `qdarteAPI@9364dd0` owns `qdarte_ops.worker_jobs`, attempts, events, generic
  enqueue, claim, heartbeat, completion, failure, release, and maintenance
  routes. It also contains a direct-SQL `taskq` migration/client and copied
  `/ops/taskq/*` plus `/worker/taskq/*` routes for contact verification.
- `qdarte-workers@02ea8fe` polls that API, owns concrete handlers, and includes
  the matching direct HTTP contact-verify worker loop. Its
  `cluster_research_scope` handler is pure for the fixed synthetic payload.
- `qdarte-runtime` owns the shared payload registry and the isolated compose
  harness. That harness already proves a no-network `cluster_research_scope`
  completion in `qdarte-dev` without production mounts or secrets.
- The isolated dev stack runs its own PostgreSQL 18 and Redis services. Its
  API/workers are denied the Docker socket and production backup paths; source
  environment files are masked. It is the only permitted initial target.

The package is additive only in a newly created disposable database named
`qdarte_pilot_dev` on that same local PostgreSQL cluster, under its fixed
`taskq` schema. QDarte's incumbent direct queue remains in `qdarteapi_dev` and
is never migrated, queried, routed through, or credential-shared by the pilot.
Separate databases give the immutable package migration chain its own catalog
and ledger without renaming its schema or changing anything QDarte owns. There
is no cross-database transaction claim: the pure pilot has no QDarte domain
write to pair with enqueue.

## 3. Artifact, topology, and privilege model

The first integration pins immutable `outlabs-taskq` `v0.1.0a3` by exact release
URL and SHA-256. It is the ADR-020 bridge, supports the closed SQL-contract set
`{0.1.2, 0.1.3, 0.1.4}`, and contains migrations `0001`–`0005`. Migration
`0006` and read-model activation are neither needed nor permitted for this
pilot.

```text
isolated QDarte planner/CLI --producer--> package taskq.qdarte_pilot
                                          |
QDarte API: mounted package facade <------+---- HTTP, queue-scoped token ---- pilot worker
                                          |
                           `qdarte_pilot_dev` (package-owned `taskq` schema)

QDarte service-token verifier + additive `outlabs_auth` catalog
                           |
                     `qdarteapi_dev.outlabs_auth` only

`qdarteapi_dev` direct-SQL `taskq` + `qdarte_ops` <--- unchanged; no bridge or dual publish
```

The QDarte API mounts the package-owned lifespan-free `/taskq` facade. It owns
no copied routes, SQL, wire models, or queue read models. Its pilot-specific
`QueueAuthorizer` is host-owned: it authenticates with QDarte's supported
service-token verifier rather than the generic OutLabsAuth dependency, then
authorizes only queue-scoped taskq names. The pilot worker uses the taskq HTTP
worker/client path with a distinct service token; it does not acquire a
PostgreSQL password or direct table grant.
It is a distinct pilot-only process whose allowlist is constructed as exactly
`{"qdarte.cluster_research.pilot"}` for its whole lifecycle. It must never
reuse QDarte's broad legacy worker configuration, and no smoke/drill cleanup
may recreate it with a wider allowlist.

The local owner/admin identity alone runs `taskq migrate` and `taskq verify`.
The facade has a **dedicated non-superuser taskq DSN and its own pool**: it
must never reuse QDarte API's existing `postgres` superuser engine/session.
Its long-lived runtime login has only the required
producer/observer/housekeeper/runner memberships, has no operator membership,
cannot `SET ROLE taskq_operator`, and never performs migrations or
`ensure_queue`. P0 records the actual role attributes for both the existing
QDarte API connection and this distinct facade connection; P2's negative
vectors must prove the facade identity has no superuser, operator,
role-creation, or base-table-read bypass. Operator-only provisioning is a
one-off local command using a separate credential. The final capability set and
actual connection arithmetic are measured from the resulting compose
configuration; they are not inferred from this document.

Queue data and authorization are deliberately split: package roles, migrations,
metadata, and the sole `qdarte_pilot` queue live only in `qdarte_pilot_dev`.
QDarte's existing service-token verifier remains the host-authentication
boundary, but P2 writes **no** `qdarteapi_dev.outlabs_auth` record; its sole
permitted schema access is QP-03's bounded read-only digest.
Its service tokens are self-contained signed credentials with the exact
`taskq_qdarte_pilot:run` or `taskq_qdarte_pilot:read` scope embedded at
issuance; the host-owned adapter checks that embedded scope through QDarte's
supported verifier. The pilot does not add a permission catalog row, role,
API key, or persisted token record. It may not modify or delete any existing
QDarte auth record, nor access `qdarteapi_dev.taskq` or `qdarte_ops`. The worker
token receives only `run`; the read-only local acceptance principal receives
only `read`. No wildcard, global queue browser, operator permission, or public
enqueue route is introduced. P4 issues ephemeral local credentials only after
P3 opens; P5 disposes them with the local pilot configuration, while QP-03's
auth digest remains byte-identical because the pilot never mutates that schema.

## 4. Controlled implementation sequence

| Slice | Permitted work | Required evidence | Stop condition |
|---|---|---|---|
| P0 — preflight | Reproduce the guarded isolated core stack and the existing no-network QDarte-only worker drill; inventory API/runtime/worker package pins, DB version, connection ceiling, actual role attributes, and existing QDarte queue high-water state. | PG18, Redis, qdarteAPI, and MinIO are healthy; source env is masked; Docker socket and production backups are absent; the pure `cluster_research_scope` drill passes with no production path or mount. The unrelated multi-worker smoke is explicitly excluded: it requires `intake-worker`, whose broad non-pilot lanes retain un-sandboxed egress and storage/write effects. | Any unmasked source env, production volume/socket exposure, superuser runtime login, stale compose topology, or any attempt to start a non-pilot worker to satisfy this pilot baseline. |
| P1 — host boundary | Add the exact a3 dependency pin, a disabled-by-default package-taskq settings block, the mounted package facade, a capability-sized local runtime constructor, and a separate pilot worker whose fixed allowlist is only `qdarte.cluster_research.pilot`. Its dedicated non-superuser DSN names `qdarte_pilot_dev` only. | Core import remains optional outside the enabled integration; disabled boot leaves no contact with `qdarte_pilot_dev` or pilot worker task; the pilot worker cannot widen on restart/cleanup; API and worker resource budgets are measured. | A public producer endpoint, reuse of the copied direct-taskq route/model, direct worker database access, reuse of the broad legacy worker configuration, a widened runtime role, or any `qdarteapi_dev.taskq` / `qdarte_ops` mutation. P2 may not access QDarte's auth schema; its verifier-only exception is defined in §3. |
| P2 — local provisioning | Create `qdarte_pilot_dev` only, then run immutable 0001–0005 under the owner/admin; verify twice; provision `qdarte_pilot` and prove the host-owned verifier accepts only the exact self-contained pilot scopes. | Migration ledger/checksums in `qdarte_pilot_dev`; `verify: ok` twice; non-superuser negative vectors for operator, role creation, and base-table reads; a QDarte-auth before/after **read-only** digest is byte-identical, proving no QDarte auth record was added or changed. | Any mutation of `qdarteapi_dev.outlabs_auth`, or any access to `qdarteapi_dev.taskq` or `qdarte_ops`; manual metadata DML; a migration run as the app/worker identity; a permission wildcard; or any mutation of an incumbent QDarte queue state. |
| P3 — deterministic adapter | Register only `qdarte.cluster_research.pilot`; adapt the existing pure synthetic cluster calculation to a bounded result. Add an internal/local harness producer, never a user-facing generic enqueue route. | Pure shadow computation and taskq-handler computation have the same canonical result digest; the adapter has no followups and no external I/O. | A taskq job invokes a provider, browser, filesystem/media write, QDarte domain write, child job, legacy enqueue, or adds the pilot type to QDarte's legacy `JobType` literal or shared `_REGISTRY`. |
| P4 — worker canary | Start one uniquely named pilot worker using the HTTP transport and queue-scoped service token; enqueue one keyed pilot job through the internal harness. | `created` then `existed` yields the same id; exactly one handler invocation; canonical authorized read reaches `succeeded`; raw taskq ledger has one successful attempt and no secret/fence exposure. | A legacy `worker_jobs` row is inserted, a second producer path fires, or the worker can claim a queue/job type outside the pilot allowlist. |
| P5 — recovery and rollback | Exercise response-loss settlement replay and a local hard process termination while the pure pilot handler is held; let lease expiry/reap reclaim the same job id to a second worker; then disable the pilot and prove zero-DML rollback. | Same-id terminal convergence, correct budget/event accounting, no remaining owned resources, API/legacy worker health, and no `qdarte_ops` mutation. | Any result is non-deterministic, any side effect escapes, a rollback needs table edits, or taskq process exit is hidden/ignored. |

The P5 hard-kill vector is intentionally run on the pure lane. It is evidence
for this integration only; it does not satisfy or waive the separate hard-kill
gate for a future side-effecting QDarte lane.

The P3 synthetic payload is fixed, rather than an ambiguous empty object:
`scope_kind: "country"`, `scope_key: "ar"`, `country_code: "AR"`, empty
`candidate_regions`, `existing_clusters`, and `proposed_clusters`, empty
`warnings`, and `input_summary: {"pilot": "taskq-stage5"}`. The canonical
digest uses that exact value. The taskq adapter owns its type map exclusively;
`qdarte.cluster_research.pilot` must never appear in QDarte's legacy closed
`JobType` literal or the shared runtime `_REGISTRY`.

## 5. Local acceptance matrix

| ID | Vector | Required result |
|---|---|---|
| QP-01 | Guarded isolated core + QDarte-only pure drill | PG18, Redis, qdarteAPI, and MinIO are healthy; source env is masked; no Docker socket or production backup mount is visible; the existing no-network `cluster_research_scope` drill passes. `intake-worker` and the broad multi-worker smoke are excluded because their non-pilot lanes have un-sandboxed side effects. |
| QP-02 | Disabled application boot | No package migration, connection, listener, worker, or public route side effect against `qdarte_pilot_dev`; incumbent direct-QDarte behavior is neither exercised nor changed. |
| QP-03 | Owner/admin fresh install, rerun, and auth isolation | Immutable 0001–0005 ledger and `verify()` pass twice in `qdarte_pilot_dev`; app/worker identities are denied owner/operator actions. A canonical before/after digest of QDarte's `outlabs_auth` permissions, roles, API keys, service tokens, and users is byte-identical, proving the pilot added and changed no QDarte auth record. |
| QP-04 | Authorization | The host-owned service-token adapter authenticates through QDarte's supported verifier; worker token can run only `qdarte_pilot`, the acceptance principal can read only `qdarte_pilot`, and wrong queue/token follows the generated hiding/error posture. No wildcard scope or generic-dependency bypass is granted. |
| QP-05 | Shadow computation | Empty synthetic input produces the same canonical digest through the existing pure function and the taskq adapter. |
| QP-06 | Keyed canary | Two submissions with one key produce `created` then `existed`, one job id, one handler call, one successful attempt, and a canonical authorized read. |
| QP-07 | Failure/replay | A committed settlement response loss replays the original settlement only; no second handler invocation occurs. |
| QP-08 | Hard-kill recovery | A held pure job is terminated past its configured grace, reclaimed as the same id, and reaches one terminal success with audit-conserved attempts/events. |
| QP-09 | Legacy isolation | Before/after snapshots use a canonical in-database full-row content digest in stable primary-key order for `qdarte_ops.worker_jobs`, `worker_job_events`, `worker_job_attempts`, `worker_artifacts`, `workflow_runs`, and `worker_job_dependencies`; the digest is the primary oracle and must include every persisted column, so inserts, updates, and deletes cannot hide. Count/id/time high-waters are retained only as diagnostics (`updated_at` where present; `created_at` for append-only events). No existing worker process claims the pilot job. |
| QP-10 | Disable, teardown, and rollback | Turning off the pilot runtime/worker is zero-DML. Teardown disposes the local ephemeral pilot credentials and proves the QP-03 auth digest remains byte-identical; the disposable pilot database may then be dropped. The existing QDarte API, worker fleet, and isolated smoke remain healthy throughout. |

All QP evidence is local and disposable. A later production or side-effecting
lane needs its own specification, preflight, backup/restore evidence, external
effect oracle, hard-kill gate, and review; this plan grants none of those.

## 6. Explicit non-goals

This plan does not migrate, query, route through, or retire QDarte's incumbent
worker ledger or direct-SQL contact-verify queue; alter its generic enqueue or
worker API; add workflow/dependency/followup semantics; touch
content/provider/browser/communication/publish/translation lanes; build a UI;
expose taskq read models; activate a read-model capability; or deploy to a Mac
mini, cloud, or production database. It does not unblock the independent
outlabsAPI read-model rollout or the tools-retirement observation.

## 7. Review gate and next decision

Round 11 independently verified handler purity, legacy-ledger isolation,
artifact posture, role boundary, compose isolation, recovery semantics, and
the explicit absence of side effects against the source baseline it inspected.
Its four docs-first refinements remain binding. Current-source P0 inspection
then found the separate direct-SQL contact-verify surface in newer QDarte
staging sources, superseding Round 11's greenfield/no-collision premise but not
its safety findings. S5-QD-CQ-01 adopts Option B: P1–P5 are permitted only
through `qdarte_pilot_dev`, whose package `taskq` schema, credentials, routes,
and migration ledger cannot overlap the incumbent `qdarteapi_dev` surface.

The downstream question is deliberately separate: after P5 proves package fit,
QDarte owners may decide whether their direct-SQL contact-verify queue should
remain, retire, or migrate to taskq. The pilot neither answers nor starts that
consolidation work.

P1's isolated gate also repaired the current QDarte host's fresh-database
chain. Migration `20260715_0070_host_native_worker_lanes` now recognizes only
the exact inherited `media=1` seed from revisions 0044/0053 before performing
its already-contracted replacement with six zero-desired host-native lanes;
every other nonzero state still fails closed and needs an explicit scale-down.
A newly created disposable host test database reached head with that posture.
The repair is incumbent QDarte migration work, not a package or pilot-database
change: it neither creates nor authorizes `qdarte_pilot_dev`, and it does not
touch QDarte's direct contact-verify queue.

S5-QD-CQ-02/03 resolve together: QDarte's existing supported service-token
verifier is the pilot's authorization boundary, while package queue data remains
isolated in `qdarte_pilot_dev`. The generic OutLabsAuth dependency is not used
for pilot service tokens, and no QDarte auth catalog row is created: the exact
scope lives only inside each self-contained local token. This replaces CQ-02's
additive-catalog branch because the public permission lifecycle can archive but
cannot delete records or erase its history, making its former QP-10 oracle
unreachable. QP-03 and QP-10 instead require a byte-identical auth digest by
construction.
