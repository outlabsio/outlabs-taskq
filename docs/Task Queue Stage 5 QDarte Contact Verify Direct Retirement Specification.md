# Task Queue Stage 5 — QDarte contact-verify direct retirement specification

> **Status:** proposal frozen by S5-QD-C8-SPEC, amended docs-first for
> R20-01/02/03, and accepted by the owner-authorized internal Round-20 delta.
> C8-R1 still requires every §4 eligibility item, including the next naturally
> scheduled 03:15 backup, before any implementation or production action.
>
> **Authority:** Tier 3, subordinate to Transport Protocol v1 revision 1.0.8,
> Function Manifest / SQL contract 0.1.5, ADR-020, ADR-022, ADR-023, the
> accepted C6 compatibility/cutover specification, the accepted C7 environment
> plan, and the owner-authorized internal Round-19 response.
>
> **Scope:** retire only the incumbent direct `contact_verify_scope` producer
> and consumer after the package-backed `qdarte.contact_verify.scope` lane has
> an independently accepted caller floor and two separate observation windows.

## 1. Outcome and non-negotiable boundary

C7 proved that the package lane can admit, execute, recover, settle, back up,
restore, and roll back without changing the incumbent direct contact ledger.
It did **not** authorize removal. This specification defines the smallest safe
removal sequence.

The end state is:

- the retained operator contact-verification request is planned by QDarte and
  admitted only through ADR-023's package reservation primitive;
- no route, service method, script, admin client, worker setting, or controller
  can create or claim a direct `contact_verify_scope` row;
- the package facade and closed worker remain the only contact queue runtime;
- the shared `qdarte_ops` job tables, migrations, generic routes, unrelated
  worker types, and all historical direct rows remain intact and readable;
- package jobs, admissions, attempts, events, and domain effects remain intact;
- rollback uses paired immutable images and configuration only—never row copy,
  queue DML, cross-backend replay, or a compensating enqueue; and
- another QDarte lane and Stage 6 remain closed.

This is a full backend replacement for this lane, not a durable compatibility
wrapper. QDarte retains a host-domain endpoint because QDarte alone owns
candidate planning and operator authorization. It stores no direct/package
mapping, shadow job, mirrored status row, payload copy, or reservation cache.
The taskq admission and job records remain authoritative.

## 2. Source-backed retirement inventory

The inventory below is derived from the accepted C7 worktrees plus the current
remote QDarte admin integration line. Commit-message similarity and old design
text are not evidence.

| Surface | Source identity and behavior | Retirement disposition |
| --- | --- | --- |
| QDarte admin caller | `qdarte-admin` `origin/staging@ae83558` calls `POST /ops/jobs/contact-verify-scope`, decodes `WorkerJobDetail`, lists direct jobs by `job_type=contact_verify_scope`, and offers the generic direct cancel action. | Migrate first to the backend-neutral admission response and exact-ID package status. It becomes the rollback caller floor before any direct producer is removed. Package cancellation remains operator-only and is not granted to the API runtime. |
| Canonical cutover caller boundary | QDarte API `78d5ce5` implements `POST /ops/cutover/jobs/contact-verify-scope`; in package mode it calls `ContactVerifyPackageAdapter`, which reserves before planning and finishes one package admission. | Retain as the package-only admission boundary through the retirement and rollback windows. Remove its `legacy | draining | package` dispatch only after the producer floor is accepted. The path name does not imply a second ledger. |
| Historical direct producer | `POST /ops/jobs/contact-verify-scope` calls `WorkerJobService.queue_contact_verify_scope_job`, writing `qdarte_ops.worker_jobs` plus shared attempts/events later. | Make unreachable only after the admin caller floor is deployed. No redirect to a second backend after an ambiguous request. |
| Incumbent host-taskq contact producer | `POST /ops/taskq/jobs/contact-verify-scope` calls the host's older `TaskqClient` against the `qdarteapi` database's `taskq` schema and is gated by `QDARTE_TASKQ_*`. | Retire with the historical direct producer. Preserve its schema, migrations, and history; remove only contact-specific executable reachability and settings. |
| Direct result mutations | `/worker/jobs/{job_id}/contact-verify-results`, `/worker/taskq/jobs/{job_id}/contact-verify-results`, `WorkerJobService.submit_contact_verify_result`, and the host-taskq result bridge accept current direct attempts. | Keep throughout producer observation. Remove or return a fixed retired response only after zero active direct attempts and the consumer rollback floor are proven. |
| Direct ordinary worker | `qdarte-workers@0c795d6` includes `contact_verify_scope` in defaults, handler map, direct verifier implementation, result client, and generic claim loop. The API verification lane catalog pairs it with `website_verify_scope`. | Remove contact only; preserve website verification and every unrelated handler. Add an API-side retired-type claim guard so stale worker configuration cannot claim a resurrected contact row. |
| Incumbent host-taskq worker | The optional `QDARTE_TASKQ_WORKER_*` loop supports only `contact_verify_scope` in its first lane and uses the host-taskq claim/result/settlement routes. | Retire its contact-only executable path and examples after producer observation. Preserve the old taskq schema/history until a distinct data-retention decision. |
| Shared legacy ledger | `qdarte_ops.worker_jobs`, `worker_job_attempts`, and `worker_job_events` contain many job types. The accepted production contact subset is six jobs, six attempts, and 2,264 events at the Round-19 baseline. | Never drop, rename, truncate, rewrite, or weaken these tables, indexes, migrations, generic reads, or unrelated dispatch. Historical contact rows remain visible. |
| Shared contact domain code | `build_contact_verify_scope_payload`, `apply_contact_verify_result`, `contact_verify_result_applications`, place contact methods, and usage counters are used by package planning/effect application as well as the retiring direct path. | Retain. Function or module names containing `contact_verify` are not evidence that they are legacy-only. |
| Shared contracts/models | `ContactVerifyScopePayload`, result models, `JobType` history, runtime registry metadata, and admin display labels decode retained history and package work. | Retain unless a source-backed use sweep proves a symbol is direct-only. Historical decode must not break to make a catalog look smaller. |
| Package runtime | Separate `qdarte_contact_verify` database, private facade, admission ledger, package job history, closed worker, private reporter, stable domain-effect ledger, egress gateway, backup/restore coverage, and scoped IAM. | Retain unchanged except for the smallest read grant and host status adapter required by the migrated admin caller. No public generic taskq surface. |
| Operational scripts/docs | C6/C7 smoke and evidence scripts contain historical direct routes and contact settings. Backup/restore and privilege manifests cover the package database. | Classify each as active gate, historical evidence helper, or obsolete executable. Active gates must test the package-only posture; historical Tier-4 evidence is never edited. Backup/restore coverage remains mandatory. |

No other active caller was found in the public site, intake service, or runtime
source. That negative result must be regenerated immediately before caller
migration. A route-access log and the current admin deployment identity are
required because repository search alone cannot prove that an out-of-tree
caller is absent.

## 3. Data and authority that must remain

Retirement is executable-path removal, not data destruction. Every slice must
preserve all of the following:

1. every row and column in the three shared `qdarte_ops` job relations;
2. every unrelated worker spec, desired-state record, handler, result route,
   migration, index, lease/retry rule, and controller behavior;
3. the complete older host-taskq schema and its history;
4. package SQL contract 0.1.5, migrations 0001–0007, exact active
   capabilities, queue profile, IAM, and all package history;
5. `contact_verify_result_applications`, place-contact methods, usage counters,
   and the package reporter's idempotent effect behavior;
6. the paused/`draining` C7 rollback evidence and immutable C7 source/image
   identities until a later baseline explicitly supersedes them; and
7. host-only owner/operator credentials, private network isolation, connection
   budget, recurring atomic backup, and restore procedures.

No taskq SQL, migration, Protocol, Function Manifest, capability, queue
profile, or package schema change is expected. Discovery of such a need is a
stop-and-record contract question, not permission to widen this slice.

## 4. Eligibility before any implementation

Before C8-R1 changes a caller or production setting, record:

1. the next naturally scheduled 03:15 production backup after Round 19,
   including API/package/Intake databases plus globals, checksum parity,
   Server87 copies, object-store result, and no unexpected retention deletion;
2. current live API/admin/worker/runtime/image/config identities and a clean
   health/readiness posture;
3. a fresh complete direct/package/domain full-row baseline using the §9
   oracles, with zero active direct contact jobs and zero running direct
   attempts;
4. package queue/profile/capability/IAM/readiness and one exact authorized
   package job read without unpausing or creating work;
5. the complete caller sweep, including current QDarte admin source and
   deployment, scripts, access logs, and any manual operational client;
6. the exact caller response and status behavior to be preserved or
   deliberately retired; and
7. immutable rollback images/configuration for the current C7 API, admin,
   ordinary worker, package facade, package worker, and gateway.

The docs-only Round-20 remediation measured only bounded production metadata
from the six retained direct jobs. Their planned entity counts are exactly
`[1, 25, 86, 100, 176, 293]`: minimum 1, maximum 293, total 681, average
113.50. Five are completed and one cancelled. No payload value, place, phone,
credential, or provider result was read into evidence. The current admin asks
for `limit: 500`; that is larger than both the proven package cohort (1) and
the observed direct maximum (293). C8 therefore targets a hard supported
maximum of **300 planned entities**, but it may not claim that envelope until
the staged gates in §5.5 pass.

R19-01 is closed only by item 1. A manual invocation of the wrapper is not the
next scheduled-run evidence. A failed scheduled run blocks implementation even
if an on-demand retry succeeds, until the scheduler defect is explained and a
subsequent scheduled run passes.

## 5. C8-R1 — caller floor before producer removal

### 5.1 Admission behavior

Migrate every active caller to the retained package-capable cutover boundary
and its existing backend-neutral response:

```json
{
  "job_id": "<opaque package job id>",
  "disposition": "created | existed",
  "idempotency_key": "contact_verify_scope:<scope_kind>:<scope_key>",
  "planned_entities": 7
}
```

`planned_entities` is a positive integer no greater than the currently
accepted stage cap; `7` is illustrative, not a fixed cohort size.

The admin client must not decode that response as `WorkerJobDetail`, infer a
legacy payload, call the old direct cancel route, or use a route discriminator.
It keeps the returned job ID as a non-authoritative client-side navigation
hint; loss of browser state can lose the convenience link but cannot lose or
duplicate durable work.

### 5.2 Exact-ID status without a shadow ledger

Add one authenticated, contact-specific host status boundary for a known job
ID. It uses the official taskq HTTP client with the QDarte API service
principal and returns a bounded projection of the package job's authoritative
status, outcome, attempt/failure counts, timestamps, and safe result summary.
It must:

- accept no queue, job type, payload, fence, worker identity, or projection
  flags from the caller;
- force queue `qdarte_contact_verify` and verify the returned job type is
  `qdarte.contact_verify.scope`;
- request neither payload nor error text;
- expose no list, search, arbitrary filter, base-table read, or server-side
  key-to-job mapping;
- return one indistinguishable not-found posture for absent, wrong-queue,
  wrong-type, and unauthorized jobs; and
- grant the API service principal only queue-scoped `read` in addition to its
  existing `enqueue`, never `run` or `operator`.

Package cancellation stays an explicit operator action. The runtime API is
not granted `taskq_operator`, and the admin removes its generic direct cancel
button for package contact jobs. Adding runtime cancellation would require a
separate authority design.

This is the adopted C8 product posture: exact-ID taskq status plus a
client-side persisted last-job hint replaces automatic scope rediscovery, and
cancellation remains an explicit one-off operator action. The admin must label
that behavior honestly. Losing or clearing the hint may remove the convenient
link, but cannot create, cancel, lose, or misstate durable work. A reload with
no hint shows no inferred package job; it never queries the direct list and
never re-submits merely to rediscover an ID.

### 5.3 Caller-floor evidence

Deploy the migrated caller while the accepted C7 API remains in `package`.
Prove a keyed `created` then `existed` pair, exact-ID terminal read, same job
ID, one package admission/job/effect, and no direct insertion. Rehearse caller
rollback only before producer retirement. After C8-R1 acceptance, the migrated
caller image is the minimum rollback floor: an older caller that posts to the
direct route may not be deployed while later C8 slices are active.

Stop for targeted acceptance before making a direct producer unreachable.

### 5.4 Transition from the accepted safe posture

Round 19 left production in `draining`, with the package queue paused and the
closed worker and gateway absent. C8-R1 must not assume a serving package lane.
The exact transition is:

1. **API source/config owner:** add a separate
   `QDARTE_CONTACT_VERIFY_SUBMISSION_ENABLED` gate, default false. False returns
   the existing bounded unavailable response before drain proof, reservation,
   planning, or either producer. Deploy the new API with mode still `draining`.
2. **Admin owner:** deploy the migrated admission/status client while the gate
   is false. The UI reads the host readiness posture and keeps submission
   disabled; a forged request still receives the same server-side refusal.
3. **Package/operator owner:** verify facade, domain/auth dependency, exact
   private origin, SQL 0.1.5 capabilities, enqueue+read IAM, queue profile,
   `max_depth=1`, and the named concurrency limit. The old unlimited profile
   is superseded once; the safe limit is retained by every rollback image and
   is not reverted during rollback.
4. **Runtime owner:** start the bounded egress gateway, prove the closed worker
   has no direct route, then start exactly one closed worker. It may poll the
   paused queue but cannot claim work.
5. **API owner:** replace only the API into configured `package`; startup earns
   a fresh same-process direct-drain attestation before serving. Submission
   remains false.
6. **Operator owner:** unpause the package queue only after API/facade/gateway/
   worker health and the direct hashes are rechecked. No job exists yet.
7. **API owner:** replace only the API with submission true. Recheck health,
   then enable the admin control. The first request is the exact authorized
   bounded stage in §5.5, never an ambient user race.
8. **Evidence owner:** reconcile admission/job/attempt/event, stable
   application, contact method, usage, egress, and unchanged direct hashes
   before the next stage or normal caller use.

Failure unwinds in the opposite safety order: submission false first, admin
control disabled, queue paused, worker stopped, gateway stopped, API returned
to `draining`. Package history remains. There is no direct fallback, row copy,
cross-backend retry, or queue DML other than the typed pause/unpause controls
and the one accepted profile/concurrency administration. A failed replacement
cannot leave the UI enabled against an unknown readiness posture.

### 5.5 Server-enforced workload envelope and staged proof

The package boundary enforces the workload independently of the UI:

- `limit` is mandatory for production package admission and must be 1–300;
- more than 300 `place_ids` is rejected;
- over-limit or absent-limit input is rejected before drain authorization,
  reservation, planning, or provider work—never silently clamped;
- the completed plan is checked again and a plan over the current stage cap is
  cancelled at the reservation layer without creating a job or provider call;
- every package job carries one fixed contact concurrency key whose operator
  limit is exactly 1;
- queue `max_depth` is exactly 1; and
- exactly one closed worker exists. No second worker or queued backlog is a
  way around the envelope.

The admin changes its request from 500 to the currently accepted stage cap.
The cap advances only through these gates:

| Stage | Maximum planned entities | Required evidence |
| --- | ---: | --- |
| C7 retained | 1 | Existing accepted one-place job and `3 attempts / 2 failures / 0 releases` truth; no new request. |
| C8-E25 | 25 | One operator-selected natural unverified scope, created/existed replay, exact-ID terminal read, one-at-a-time egress/effect arithmetic, direct hashes unchanged, safe unwind rehearsed. |
| C8-E100 | 100 | C8-E25 accepted first; one natural scope of 26–100 entities plus the same oracles, bounded duration/resource measurements, backup continuity, and no queue depth above one. |
| C8-E300 | 300 | C8-E100 accepted first; a production-clone no-network/sink proof plans and executes exactly 300 through the real package worker/reporter with zero external/domain effect, then one natural production scope of 101–300 entities proves real sequential egress/effect behavior. |

No synthetic filler, re-verification of already verified contacts, or
`require_unverified_only=false` may manufacture a production cohort. If a
natural stage does not exist, the accepted cap remains at the prior stage and
the admin exposes that smaller limit. Retiring the direct producer requires
explicit owner acceptance that the current cap is the supported caller
contract. Claiming full parity with the measured historical maximum requires
C8-E300; otherwise the narrower cap is documented as a deliberate product
constraint, never hidden as an implementation detail.

Each stage is a separately accepted production-evidence increment. On any
provider, effect, rate, latency, lease, settlement, backup, or rollback
disagreement, submission is disabled and the safe unwind in §5.4 runs. C8-R2
cannot start merely because the one-place C7 proof exists.

## 6. C8-R2 — retire every direct producer

After C8-R1 is accepted:

1. make the historical `/ops/jobs/contact-verify-scope` producer and the
   incumbent `/ops/taskq/jobs/contact-verify-scope` producer unreachable with
   one fixed bounded retired response; neither may redirect, plan, enqueue, or
   fall back;
2. make the retained cutover boundary package-only: remove legacy admission
   and request-time backend selection while retaining reserve-before-plan,
   exact key/intent behavior, fixed unavailable response, and no fallback;
3. remove the direct producer service methods only where a call-site and
   import sweep proves they are direct-only; retain the shared planner;
4. remove contact from every old host-taskq producer selector and active smoke
   path without changing unrelated taskq history;
5. keep the direct ordinary and incumbent host-taskq consumers temporarily so
   a pre-existing row can finish during this window; and
6. keep the production environment's old mode value available to the immutable
   rollback image even if candidate code no longer interprets it.

The producer-removal deployment order is caller first, then API. The rollback
pair is the accepted C8-R1 caller plus the immutable C7 API configured in
`package`; it continues to publish only package work. Rolling the caller back
below the C8-R1 floor is forbidden. The rehearsal must prove both directions
with zero DML and unchanged direct high-water.

### C8-R2 observation window

Consumer retirement remains closed for at least seven consecutive production
days after producer removal, including two normal API replacements. The window
must show:

- zero new or changed direct contact jobs, attempts, or events from the frozen
  full-row hashes—not merely zero current depth;
- zero active/running direct contact work at every sample;
- at least one real authorized package admission and exact-ID terminal read,
  with an `existed` replay that does not replan;
- package job/admission/attempt/event arithmetic matching stable application,
  contact-method, usage, and bounded egress counters;
- scheduled atomic backups continuing to include package state; and
- no call to either retired producer in access logs or executable smoke paths.

Any direct insert, update, delete, active row, old-caller request, unexplained
counter, or fallback resets the window and blocks consumer removal.

## 7. C8-R3 — retire direct consumption

Only after independent acceptance of the producer observation:

1. add a server-side retired-type claim guard before candidate selection so
   `contact_verify_scope` cannot be claimed from `qdarte_ops` even by a stale
   or misconfigured worker image;
2. remove `contact_verify_scope` from the ordinary worker handler map, default
   supported-type settings, verification-lane desired state, active worker
   specs, controller examples, and production worker environment while
   retaining `website_verify_scope` and every unrelated lane;
3. remove the direct contact handler/result client and direct result-mutation
   routes only after the zero-running-attempt gate is rechecked immediately
   before deployment;
4. remove the contact-only incumbent host-taskq worker loop/routes/settings
   without dropping its schema, migrations, functions, or historical rows;
5. retain shared contact payload/result models, planner, domain application,
   stable effect ledger, admin history display, and package closed worker; and
6. expose a critical safe metric/log if a retired direct contact row is
   observed. It stays unclaimed and visible for operator investigation; it is
   never marked done by an unknown-task fallback.

Before production deployment, a disposable restored database must inject one
synthetic direct contact row and prove that current and stale-worker claim
requests cannot lease it, while all unrelated direct worker types still claim,
heartbeat, retry, and settle normally. Production receives no synthetic row.

The consumer rollback baseline is the accepted producer-retired API/admin plus
the last direct-capable worker/controller pair. Rollback restores that complete
pair and configuration; it does not mutate a direct row or recreate package
work. A rollback below the producer-retired API floor is forbidden after C8-R3
begins.

### C8-R3 observation window

Run a second seven-consecutive-day window with two normal worker/controller
replacement cycles. Require:

- the exact direct contact hashes and zero-active posture remain unchanged;
- package admission, exact-ID read, closed-worker execution, result replay,
  and stable effect convergence remain green;
- unrelated verification and at least one additional unrelated worker lane
  claim and settle under their unchanged rules;
- the retired-type metric remains zero in production;
- backup/restore coverage and connection/privilege boundaries remain green;
  and
- the paired consumer rollback rehearsal passes with zero DML.

Only C8-AUDIT may close the retirement after this window.

## 8. Configuration and cleanup order

Settings are removed only when doing so does not invalidate an available
rollback image:

1. C8-R1 adds package read authorization and caller/status configuration;
2. C8-R2 code stops reading the mode for dispatch, but the deployed environment
   retains `QDARTE_CONTACT_VERIFY_MODE=package` for the C7 rollback image;
3. C8-R3 removes direct-worker contact selections from active settings but
   retains the prior settings snapshot with the rollback images; and
4. only after C8-AUDIT and expiration of the named rollback window may a
   separate configuration-cleanup task delete dormant direct/mode variables.

The package queue, facade, worker, gateway, DSNs, tokens, IAM, and backup
variables are not retirement residue. The C7 `draining` evidence remains
historical truth even after candidate code becomes package-only.

## 9. Exact data, effect, and rollback oracles

At every gate, compute count plus SHA-256 of canonical ordered full-row JSON
for:

| Oracle | Exact scope |
| --- | --- |
| Direct jobs | `qdarte_ops.worker_jobs WHERE job_type='contact_verify_scope'`, ordered by primary key |
| Direct attempts | attempts joined to those direct job IDs, ordered by primary key |
| Direct events | events joined to those direct job IDs, ordered by primary key |
| Package jobs/attempts/events | all rows in the dedicated package database, ordered by primary key |
| Package admissions | all durable admission rows, ordered by admission identity |
| Stable applications | all `contact_verify_result_applications`, ordered by `(job_id, entity_key)` |
| Contact methods | all place contact-method rows, ordered by primary key |
| Usage | all discovery usage-counter rows, ordered by primary key |

Diagnostics additionally record status counts, active/running leases, maxima,
database instance IDs, source/image identities, settings digests, and backup
timestamps. Maxima are never substitutes for full-row hashes because updates
and deletes must be detectable.

The package/effect counters may grow only by explained package operations.
The direct three hashes must remain byte-identical throughout both windows.
No evidence command may print payloads, candidate phone values, credentials,
fences, provider bodies, or unbounded errors.

## 10. Acceptance matrix

| ID | Required evidence |
| --- | --- |
| CR-01 | Round-19 R19-01 closed by the next scheduled 03:15 backup, not a manual substitute |
| CR-02 | Regenerated source/deployment/access-log caller inventory; no unclassified active caller |
| CR-03 | Admin caller accepts canonical admission and exact-ID status without legacy decode, list inference, cancel, or shadow mapping; reload/hint-loss vectors prove the adopted exact-ID/operator-only posture |
| CR-04 | Queue-scoped API principal has exactly enqueue+read; no run/operator/other-queue permission |
| CR-04A | Safe transition runs in the exact §5.4 order from draining/paused/no-worker/no-gateway, and every injected failure executes the inverse unwind before another request |
| CR-04B | Historical `[1,25,86,100,176,293]` envelope is reproduced; production rejects absent/over-limit input pre-reservation, keeps depth/concurrency/worker at one, and only accepted staged cohorts raise the caller cap |
| CR-05 | Historical and incumbent host-taskq direct producers are unreachable and never redirect/fallback |
| CR-06 | Seven-day/two-API-cycle producer window with exact unchanged direct hashes and explained package/effect counters |
| CR-07 | Producer rollback uses caller-floor + C7 API in package mode, zero DML, and no direct insertion |
| CR-08 | Server claim guard prevents current and stale workers from leasing a synthetic retired row in a disposable restore |
| CR-09 | Contact is removed from direct worker/catalog/controller/result surfaces while website and unrelated lanes remain green |
| CR-10 | Seven-day/two-worker-cycle consumer window with zero retired-row observation and exact unchanged direct hashes |
| CR-11 | Consumer rollback restores the producer-retired API plus direct-capable worker pair with zero DML |
| CR-12 | Shared `qdarte_ops` and old taskq schemas/migrations/indexes/history are byte-for-byte preserved |
| CR-13 | Package history, admission, reporter, domain-effect, private-network, privilege, connection, backup, and restore proofs remain green |
| CR-14 | API/admin/worker/runtime/taskq suites, lint, formatting, typing, builds, health, and production smoke are green at exact deployed tips |
| CR-15 | No taskq contract/SQL/migration/capability change, another lane, provider expansion, broad worker, or Stage-6 work |

## 11. C8-AUDIT and scope opened

C8-AUDIT must independently regenerate the source inventory, caller floor,
permission set, retired producer reachability, both observation windows, exact
hashes, synthetic stale-worker claim negative, unrelated-lane positives,
backups/restores, paired rollback rehearsals, refs/images/settings, and suite
gates. It must review implementation source, not infer retirement from an
environment variable or test name.

Acceptance closes only the direct `contact_verify_scope` executable path.
It does not authorize dropping history/schema, deleting rollback images,
removing shared contact models/domain code, migrating another QDarte lane, or
opening Stage 6. A later data-retention decision may archive old direct history
only if it treats the shared ledger and unrelated lanes as first-class owners.

## 12. Stop conditions

Stop before or during implementation if:

- the scheduled backup gate has not passed;
- any active caller still uses a direct route/shape or depends on direct list
  or cancel behavior without an accepted disposition;
- the UI can enable submission before the server gate and complete package
  topology are ready;
- a request omits `limit`, exceeds the accepted stage cap, can queue behind
  another contact job, or can bypass the one-job concurrency posture;
- a staged cohort is manufactured from already verified contacts or advances
  without its own counter/rollback acceptance;
- the API principal would need operator, raw SQL, or another-queue access;
- an active/running direct contact row exists;
- direct hashes change or a direct producer remains reachable;
- consumer removal can claim or silently settle a retired row;
- a supposedly direct-only symbol is used by package planning/effects or
  historical decode;
- rollback requires row mutation, cross-backend replay, or an older caller
  below the current floor;
- unrelated worker behavior or shared-ledger ownership cannot be separated;
- package/effect/egress counters disagree; or
- a Tier-0/ADR conflict or new taskq function/shape appears.

Contract conflicts go to `TASKS.md` and stop code. Host topology/caller facts
go to the C8 evidence packet and stop the owning slice. Neither is permission
to improvise a bridge.
