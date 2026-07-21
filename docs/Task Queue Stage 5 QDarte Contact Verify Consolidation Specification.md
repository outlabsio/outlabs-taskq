# taskq — Stage 5 QDarte contact-verify consolidation specification

> **Status:** Tier-3 decision proposal. It selects a destination and freezes the
> compatibility, ownership, failure, rollback, and evidence rules for QDarte's
> incumbent direct contact-verification queue. It authorizes **no** source,
> database, credential, worker, route, deployment, production, or queue-state
> change. A targeted review must accept this document before a later
> side-effecting-lane implementation plan opens.
>
> **Authority:** subordinate to the Transport Protocol v1, Function Manifest
> 0.1.4, ADR-006, ADR-007, ADR-010, ADR-011, ADR-020, the Build Plan, and the
> Stage 5 QDarte Pilot Specification. The isolated P0–P5 pilot proved package
> fit for one pure lane; it neither migrates nor proves this lane.

## 1. Decision

QDarte's direct-SQL `contact_verify_scope` queue remains authoritative while
this proposal is reviewed and while a future migration gate is prepared. The
selected destination is a **one-way, single-publisher migration** of that lane
to the package queue after all gates in this document pass. Retirement of the
incumbent direct surface happens only after the package lane has independently
proved its side-effect, recovery, rollback, and production evidence.

This is deliberately not a compatibility shim. The incumbent and package have
different SQL catalogs, privilege models, Python clients, and wire surfaces.
They must never share a database, publish the same planned contact-verification
scope simultaneously, or fall back from one queue into the other after an
ambiguous enqueue or execution outcome.

Until a later approved implementation plan exists:

- `qdarteapi_dev.taskq` and its copied `/ops/taskq/*` and `/worker/taskq/*`
  routes remain unchanged;
- `qdarte_pilot_dev` remains an isolated package-pilot database only;
- no contact-verification job is enqueued, claimed, mirrored, imported,
  rewritten, or retired by this decision; and
- the pure-lane P5 hard-kill proof does not satisfy the contact-verification
  hard-kill gate.

## 2. Reconstructed incumbent inventory

This inventory was derived on 2026-07-21 from the current QDarte API and worker
sources plus read-only inspection of local `qdarteapi_dev`; it is not inferred
from the isolated pilot.

| Concern | Current incumbent | Consequence for convergence |
| --- | --- | --- |
| Catalog owner | API migration `alembic/versions/20260709_0061_add_taskq_schema.py` creates schema `taskq`, six tables, and thirteen direct functions. The local database is PostgreSQL 18.4 and currently has no direct queue rows or queues. | It is a separate, mutable host-owned catalog, not a package migration ledger. It cannot be adopted in place as package SQL. |
| Privileges | Source creates `taskq_worker` and grants `PUBLIC EXECUTE` on all thirteen direct functions. The inspected local cluster currently has the package capability roles but no `taskq_worker`; the direct path still works through `PUBLIC EXECUTE`. | This violates the package capability model. C1 must record the measured source-versus-live role/grant posture before any implementation. The package must be installed into its own database; a grant rewrite is not a migration strategy. |
| Producer | `app/domains/workers/api/routes.py` plans the scope from live QDarte rows, then calls `TaskqClient.enqueue` into queue `comms`, type `contact_verify_scope`, with a caller key or a derived scope key. `/ops/cutover/jobs/contact-verify-scope` chooses direct or `qdarte_ops` only from the current setting. | A future package producer must choose exactly one backend before planning/enqueueing. It must preserve the public caller response intentionally, not accidentally depend on a copied route. |
| Worker transport | `qdarte-workers/src/qdarte_workers/worker_loop.py` polls `/worker/taskq/jobs/claim`, heartbeats, completes, fails, and releases through the API; it refuses types other than `contact_verify_scope`. | The future package worker must use the package HTTP worker path and a closed registry. It must not receive a database password or reuse the copied direct worker loop. |
| Domain effect | `handle_contact_verify_scope` calls a network verifier for each planned entity, then submits the result to `/worker/taskq/jobs/{id}/contact-verify-results`. The API updates a place, optional contact method, and a monthly probe-usage counter. | This is a side-effecting lane. Queue settlement alone is not its correctness oracle. |
| Result idempotency | `apply_contact_verify_result` currently derives an application key from job id, **attempt id**, and entity key. A changed attempt id can therefore bypass the existing applied-result marker. | Before migration, result application must be made stable across retries/reclaims, or the lane remains ineligible. This is a host integration requirement, not a new package contract. |
| Existing QDarte ledger | `qdarte_ops.worker_jobs` remains the generic legacy worker ledger; the direct contact-verify queue is separate from it. | Neither ledger is retired by this work. The package migration is scoped to one direct contact-verify lane only. |

The direct catalog's `taskq` schema contains `queues`, `jobs`,
`job_attempts`, `job_events`, `schedules`, and `control_state`. Its function
identities and result shapes differ from the immutable package catalog. The
package's fixed schema name and immutable migrations therefore make a shared
database a catalog collision, not a deployment convenience.

The incumbent worker URL is also an inventory item, not an implementation
detail to inherit by assumption. C1 must freeze this effective-base-path
matrix and prove an authenticated claim and result submission in each
supported topology before any direct-worker compatibility code is changed:

| Supported topology | Configured worker base URL | Joined claim path | Joined result path |
| --- | --- | --- | --- |
| Direct API origin | `http://<api-origin>` | `/worker/taskq/jobs/claim` | `/worker/taskq/jobs/{job_id}/contact-verify-results` |
| Admin-proxy origin | `http://<admin-origin>/content-api` | `/content-api/worker/taskq/jobs/claim` | `/content-api/worker/taskq/jobs/{job_id}/contact-verify-results` |

The direct client appends the worker-relative path once. The source or its
tests may be corrected only after this matrix determines which supported
topology each setting represents; a root-relative test expectation is not a
reason to erase the documented proxy prefix.

## 3. Target topology and ownership

The future package lane uses a **separate package-owned database** on the
chosen QDarte cluster. It does not reuse `qdarteapi_dev`, its direct `taskq`
schema, or the pilot database. Its exact database name, server version,
connection ceiling, backup posture, and credentials are a later preflight
decision; they are not created here.

```text
QDarte planner/API ---- one selected producer ----> package facade /taskq
                                                       |
                                                 package database
                                                  (package taskq)
                                                       |
                                         run-scoped package HTTP worker
                                                       |
                                  QDarte result endpoint / domain write

qdarteapi direct taskq  <---- unchanged until drain and final retirement
qdarte_ops worker ledger <---- out of scope
```

The host owns planning, its public/API compatibility layer, service-token
authentication, result application, and the external-effect oracle. The
package owns queue state, fencing, lease/reclaim, settlement replay, and the
capability-limited runtime. Ownership never transfers through a direct SQL
grant to the worker.

Required identities are distinct:

| Identity | Allowed responsibility | Never allowed |
| --- | --- | --- |
| Package owner/admin | package migration and verification only | API/worker runtime or QDarte domain writes |
| Package operator | queue profile and IAM-like queue provisioning only | application runtime, producer fallback, or host schema migration |
| Package facade | capability-sized package transport pool | superuser, operator, base-table bypass, or QDarte domain session reuse |
| Package worker | `run` on the one package queue via HTTP | direct database password, queue administration, or a broad QDarte worker allowlist |
| QDarte result service | validates a fenced, planned result and applies it idempotently | trusting caller-supplied queue/type or treating a lost queue settlement as proof of no write |

The package database starts from a future reviewed immutable release and its
declared supported SQL-contract set. Applying a package migration raises that
database's rollback floor under ADR-020; it does not change any existing
QDarte database. The release chosen for this side-effecting lane must be both
the deployed and rollback baseline before the package database is migrated.

## 4. Compatibility and cutover protocol

### 4.1 Public compatibility

Before implementation, the QDarte owner must freeze the contact-verify caller
contract: request grammar, validation, response shape/statuses, idempotency
key semantics, authorization, and operational status routes. A package backend
may sit behind a host adapter, but callers must not be required to know a queue
implementation changed unless an explicitly approved API revision says so.

The future host adapter receives the authoritative planned scope and constructs
the package command. It must not expose a generic public package producer,
copied direct worker model, fence, or internal package error detail.

### 4.2 Exactly one publisher

The host configuration is an explicit closed mode, validated at startup:

| Mode | Producer | Worker | Allowed use |
| --- | --- | --- | --- |
| `legacy` | incumbent direct queue only | incumbent direct HTTP loop only | default until migration evidence is accepted |
| `draining` | no new contact-verify enqueue | incumbent worker only, until its direct queue is terminal | cutover preparation or rollback-safe pause |
| `package` | package facade only | package HTTP worker only | after the approved cutover switch |

There is no dual mode, shadow publish, bridge consumer, automatic retry into
the other backend, or ambiguous-enqueue fallback. A mode is sampled once per
request and emitted in bounded diagnostics only. The launch vector must prove
that a keyed request creates at most one job in exactly one backend.

### 4.3 Drain before package publish

The direct queue must reach a documented terminal drain before the first
package contact-verification job is admitted. The drain oracle covers every
direct `contact_verify_scope` job: zero `queued`, `blocked`, or `running`
rows; no leased attempt; and a stable high-water record for its jobs, attempts,
and events. Existing direct work is never copied into the package database.

The direct producer is disabled before the drain begins. If a direct job cannot
complete safely, its owner resolves it inside the direct system; it is not
translated to a package job. This prevents double verification and makes
history attributable to one ledger.

## 5. Side-effect and failure model

Contact verification has two independent durable effects:

1. a package job is claimed, fenced, and settled; and
2. one or more QDarte domain results update place/contact/usage state after a
   network verification.

### 5.1 Server-owned package result bridge

The package database is separate from QDarte's domain database. A worker
therefore never validates or applies a result through direct package SQL, and
the QDarte result service must not trust a worker-supplied queue, job type,
payload, planned entity, or attempt as authority. The eventual host result
endpoint uses this fixed order:

1. authenticate the caller as `run` only for the one package queue;
2. use a server-owned, capability-sized package runtime to heartbeat the
   supplied `(job_id, attempt_id, worker_id)`, thereby fence-checking and
   extending the current attempt before any QDarte write;
3. use a separate server-owned observer capability to read the authoritative
   package job projection, then verify its queue, job type, planned entity,
   and QDarte place identity against the result request without trusting a
   request echo;
4. apply the domain effect under the stable job-id-plus-entity key in the
   QDarte transaction; and
5. return to the worker, which alone performs terminal settlement and its
   existing replay policy.

The result service owns neither the worker's credential nor a direct package
database grant. A rejected/lost/settled heartbeat, an absent job, or an
authoritative projection mismatch performs no QDarte domain write. If the
domain transaction commits but the response is lost, the same current attempt
may retry this sequence; the stable effect key makes that replay one domain
application. If the lease expires and another worker reclaims the job, the old
attempt's heartbeat is rejected and it performs no write. Terminal package
state is never used as evidence that the domain effect did not commit.

The later implementation must prove their relationship with a stable
application-effect key derived from the package **job id and planned entity
identity, never the attempt id**. Repeated delivery, response loss, reclaim,
or a second worker must yield one domain application and bounded usage-counter
change per planned entity. If the existing result endpoint cannot provide that
property, migration stops before any package enqueue.

The external-effect oracle has three layers:

- a package raw-ledger oracle for job, attempt, event, release, expiry, and
  fence-conserving settlement;
- a QDarte domain oracle for the exact affected place/contact/usage rows using
  stable primary-key ordered digests plus the expected per-entity change; and
- a bounded network/provider observation appropriate to the verifier, with
  secrets and raw credentials excluded from logs and evidence.

A committed-result-response-loss drill must prove: one network invocation
where the provider outcome is observable, one durable domain application, one
terminal package job, and no producer retry into the direct queue. A hard-kill
drill must interrupt a held side-effect boundary, show whether the result
application had committed, reclaim the same package job id, and demonstrate
the stable application-effect key prevents duplicate domain/usage mutation.

## 6. Rollback rules

Rollback is a producer/consumer configuration action, never a row copy or
cross-backend replay.

- **Before the first package publish:** restore `legacy` mode and prove no
  package contact-verify row exists. This is zero-DML for the direct queue.
- **After package publish but before external work:** stop package admission,
  drain or cancel within the package according to its typed controls, and do
  not recreate the job in the direct queue.
- **After external work can have occurred:** do not switch back to `legacy`
  automatically. Preserve the package ledger, use the stable effect oracle to
  resolve each job, and require an operator decision before any later producer
  change.

The direct catalog is retired only after an independently accepted observation
window proves the package lane, caller compatibility, direct zero-insert
high-water, and rollback behavior. Dropping or renaming the incumbent `taskq`
schema, deleting its history, or deleting a QDarte ledger is a separate
post-retirement change with its own backup/restore proof.

## 7. Required implementation gates

No gate is implicitly satisfied by P0–P5.

| Gate | Required evidence | Blocks |
| --- | --- | --- |
| C1 — host contract inventory | caller/API/worker route map, the effective-base-path matrix with authenticated claim/result vectors for each supported topology, model comparison, direct data high-water, measured source-versus-live role/grant inventory, and explicitly named compatibility delta | all source changes |
| C2 — package database preflight | disposable same-topology database, immutable package migrate/verify twice, owner/operator/runtime negative vectors, connection budget, backup/restore rehearsal | package database creation in a lasting environment |
| C3 — result idempotency | server-owned bridge in §5.1; stable job-plus-entity key; wrong-fence, reclaimed-old-attempt, wrong-planned-entity, same-job retry, and committed-domain-write/lost-response vectors; and an exact domain and usage-counter oracle | any external verification |
| C4 — isolated side-effect canary | one bounded synthetic/controlled real entity with an explicit provider/effect oracle and no direct queue mutation | broader local cohort |
| C5 — hard kill | process termination past grace at the result boundary, same-id reclaim, no duplicate effect or usage increment | staging or production-like use |
| C6 — compatibility and rollback | caller suite, exact mode exclusivity, direct drain, package-only keyed pair, pre/post-publish rollback exercises | cutover |
| C7 — production evidence | approved environment, least-privilege credentials, backup/restore, bounded cohort, independent counter, two normal cycles, and a zero-insert direct ledger window | direct retirement |

The hard-kill proof in C5 is required before any side-effecting lane. It is not
waived by a pure pilot, local convenience, or a successful normal canary.

## 8. Acceptance and review boundary

A targeted review must independently derive the incumbent catalog and route
map from QDarte sources, verify the schema collision rather than trust this
document, and challenge every claim of idempotency at the QDarte domain-write
boundary. It must reject a plan that:

- reuses `qdarteapi_dev` or any database containing the direct `taskq` schema
  for package migrations;
- grants a package worker direct database access or broad QDarte worker scope;
- dual publishes, imports active direct jobs, or performs automatic
  cross-backend fallback;
- treats an attempt id as a stable domain-effect idempotency identity; or
- treats P5's pure recovery proof as evidence for external side effects.

Only an accepted review authorizes a subsequent, separately boarded
implementation specification. It does not authorize package migration,
contact-verification execution, direct queue retirement, a cloud target, or a
production rollout by itself.
