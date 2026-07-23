# Task Queue Stage 5 — QDarte full replacement specification

> **Status:** frozen by S5-QD-FR-00 on 2026-07-22. This is the current QDarte
> destination and supersedes the earlier pilot, contact-only compatibility,
> cutover, environment, and direct-retirement plans wherever they preserve a
> second queue implementation or compatibility mode. Those documents remain
> evidence of decisions and production discoveries; they are not the target
> architecture.
>
> **Authority:** Tier 3. Subordinate to Transport Protocol v1 revision 1.0.8,
> Function Manifest / SQL contract 0.1.5, and ADR-001..023. Contract-visible
> capability work follows the normal ADR → Protocol/Manifest → immutable
> migration order before QDarte may consume it.
>
> **Scope:** replace QDarte's queue implementation completely in local
> development, preserve business-domain content, discard queue execution
> history at cutover, and leave a production-ready one-time transition. This
> specification authorizes no production mutation by itself.

## 1. Outcome

QDarte ends with `outlabs-taskq` as its only durable queue implementation.
There is no long-lived compatibility adapter, mode switch, shadow ledger,
dual publisher, old worker loop, host-owned taskq clone, or fallback path.

The final source tree must make these statements true by construction:

1. Every supported QDarte task is registered in one typed task registry and
   executed by `taskq.WorkerService`.
2. Every producer uses the official taskq producer/admission capability. No
   QDarte service inserts or updates queue rows.
3. Taskq owns claim, lease, heartbeat, cancellation, retry, settlement,
   concurrency admission, queue events, and queue retention.
4. QDarte owns business planning and business effects. A domain endpoint or
   service is legitimate only when it expresses a QDarte business operation;
   it may not emulate the retired queue API or accept a caller-supplied fence.
5. Task execution history is operational data and is not migrated from the
   retired ledgers. Existing places, listings, content, photos, contact
   methods, discovery artifacts, provider accounting, publication state, and
   other domain facts remain authoritative and are preserved.
6. Fresh local installs never create the retired queue schema. Existing
   installations get one explicit destructive queue-retirement migration
   after the new runtime is proven and all old processes are stopped.
7. Final acceptance includes a machine-generated zero-legacy oracle across
   source, configuration, routes, dependency locks, database catalog, running
   processes, and the local application stack.

“No wrapper” does not mean handlers may bypass application policy. It means a
QDarte boundary must describe a domain action such as applying a photo result
or recording a publication. A boundary whose purpose is to translate old job,
attempt, lease, event, or status semantics is forbidden in the final tree.

## 2. Source baseline and discovered surface

The initial inventory is pinned to these clean local revisions:

| Repository | Revision | Queue responsibility found |
| --- | --- | --- |
| `qdarteAPI` | `900449c4b77f` | producers, old SQL lifecycle, workflow/dependency/schedule logic, result application, operator routes, transitional package facades |
| `qdarte-workers` | `0c795d69c360` | 21-handler old claim loop, API queue client, progress/event calls, contact/pilot taskq proof paths |
| `qdarte-runtime` | `632139f52d45` | 23-type shared registry, queue wire models/client, worker progress helpers, environment/runtime orchestration |
| `qdarte-admin` | `786269826535` | job lists/details/actions and transitional contact status/admission coupling |
| `outlabs-taskq` | `c60a89880472` | accepted SQL 0.1.5 runtime and the current planning authority |

The shared QDarte registry declares 23 types. The old worker loop has concrete
handlers for 21:

`buzz_discover_scope`, `cluster_research_scope`, `contact_verify_scope`,
`discovery.import_batch`, `open_source_discover_scope`,
`website_verify_scope`, `content_enrich_scope`,
`content_synthesis_scope`, `editorial_enrich_scope`,
`listing_research_scope`, `photo_find_scope`, `photo_verify_scope`,
`publish_scope`, `frontend_deploy_scope`, `region_completion_scope`,
`region_rescue_scope`, `review_scope`, `translation_scope`,
`tripadvisor_classification_scope`, `tripadvisor_region_import`, and
`tripadvisor_session_prime`.

`content_assembly_scope` is already a retired compatibility declaration whose
producer selects listing research or synthesis instead. It must not enter the
new registry. `communication.email_delivery` has a declared payload/result but
no executable worker handler in the audited fleet; it remains out until a
source-backed product owner and implementation exist. The two historical
Welcome Argentina literals exist only to decode old rows and disappear when
the old execution ledger disappears.

The old queue surface is substantially larger than a worker loop. It includes:

- `qdarte_ops.worker_jobs`, `worker_job_attempts`, `worker_job_events`,
  `workflow_runs`, `worker_job_dependencies`, `worker_job_schedules`, and
  `worker_concurrency_pools`;
- QDarte's embedded `taskq` schema from Alembic revision
  `20260709_0061`, including its queues, jobs, attempts, events, schedules,
  functions, and direct-SQL client;
- producer methods, generic enqueue, worker follow-up enqueue, claim,
  heartbeat, progress/event append, complete/fail/release/cancel, maintenance,
  schedule promotion, dependency unlock/cancel, workflow finalization, and
  queue administration in `WorkerJobService`;
- operator and worker HTTP routes that expose those operations;
- the old `WorkerApiClient`, `worker_loop`, queue settings, process controls,
  and admin data models; and
- the contact/pilot/C6/C7/C8 modes, controllers, bridges, proof registries,
  routes, environment variables, and harnesses created to establish safe
  compatibility. They remain useful evidence but are not product architecture.

This inventory is a floor. FR-01 regenerates it mechanically and fails on any
unclassified symbol before implementation starts.

## 3. Data boundary

### 3.1 Preserve

Preserve business truth regardless of which old job produced it:

- geography, places, listings, content items/locales/reviews, sources and
  discovery artifacts;
- photos, media objects, derivatives, attribution and verification results;
- contact methods and stable contact-verification applications;
- publication/deployment facts and domain workflow state that remains useful
  without an old queue row;
- provider identities, leases where they govern external provider resources,
  usage/accounting events, reservations, limits and health;
- business communication intents/deliveries if product use is confirmed; and
- object-store/media files and their domain metadata.

Preservation is decided by business meaning and foreign-key/source use, not by
the current module name. A model located in `domains.workers` may move to its
real domain rather than being deleted.

### 3.2 Delete, without row migration

Delete queue execution truth after the cutover stop:

- all old jobs, attempts, queue events, dependencies, schedules and workflow
  rows;
- old queue profiles, concurrency-pool rows, worker specs/status that only
  describe the retired fleet, and old taskq-clone rows;
- all queued, waiting, running, failed and terminal old work, including job
  payloads and result summaries;
- every mapping, hint, compatibility receipt or shadow status whose only
  purpose is to relate old and new jobs; and
- old taskq contact/pilot evidence rows in disposable or package databases
  when their databases are retired into the one canonical taskq deployment.

There is no active-job import and no terminal-history import. Before deletion,
the old producers and consumers are stopped and a final catalog/count digest
is retained as an operational artifact only. Work that is still desired is
requested again through a domain command after cutover with a new native taskq
identity.

### 3.3 Classify before removal

`WorkerArtifact`, launch-pipeline tables, discovery drafts/artifacts, provider
control tables, browser/search resource tables, and communication tables are
not deleted merely because they share the old module. FR-01 classifies every
relation as `domain-retain`, `domain-relocate`, `queue-delete`, or
`historical-migration-only`, with a source owner and a wrong-classification
oracle.

## 4. Final architecture

### 4.1 Deployment

QDarte uses one canonical taskq database and one private taskq HTTP facade.
Long-lived processes have dedicated non-superuser credentials:

- API producer: producer capability on declared queues; observer capability
  only where QDarte exposes a bounded domain status view;
- worker fleet: runner capability only, reached through the private facade;
- housekeeper: housekeeper capability only;
- operator automation: operator capability, never present in API or workers;
  and
- migration owner: short-lived owner/admin execution only.

QDarte's business database remains separate. No worker gets either database
password. Workers call the taskq facade for queue lifecycle and authenticated
QDarte domain endpoints for planning inputs/effects. The trusted effect
reporter binds the active attempt internally; handlers never see a fence.

### 4.2 Producers

Each public/operator QDarte command performs domain authorization and bounded
planning, then calls taskq directly. Cheap deterministic requests use keyed
enqueue. Planning whose result is expensive or whose duplicate execution
would be material uses ADR-023 reserve → plan → finish. A producer may return a
domain response containing the native job id and disposition, but may not
return or persist an old-job-shaped projection.

Ambiguous producer failure is retried only through the official idempotent
operation. There is no attempt to fall back to another backend.

### 4.3 Workers and handlers

`qdarte-workers` exports one registry factory containing every active native
task. The normal process entry point constructs the official HTTP worker and
`WorkerService`; it contains no claim/heartbeat/settlement loop.

Each handler:

- accepts its typed payload plus optional `JobContext`;
- returns only taskq's typed handler result;
- uses `ctx.checkpoint()` for compact resumable progress;
- uses `ctx.report_effect()` for authoritative QDarte domain effects that must
  bind to the active attempt;
- returns atomic taskq follow-ups when a successful result creates later work;
  and
- never calls an old queue route, writes queue SQL, or settles its own job.

Provider/resource permits, browser sessions, filesystem/media access, and
other domain execution controls remain QDarte concerns. Queue ownership does
not absorb unrelated domain infrastructure.

### 4.4 Domain effects

The contact proof becomes a general QDarte effect protocol rather than 21
lane-specific queue adapters. The request vocabulary is closed and typed by
task kind/operation. The reporter derives queue, task type, job and attempt
from its trusted runtime record, and the API validates those against the
authoritative taskq projection before an idempotent domain mutation.

Effects are keyed by native taskq job id plus a bounded operation/entity key.
They support inspect-before-act and replay-after-response-loss. Effect records
contain domain outcomes, not a second copy of task status. Taskq alone settles
the job.

### 4.5 Operator surface

QDarte's admin may present domain-specific submission and status pages. Queue
state comes from generated taskq clients and finite taskq read models; it is
not copied into QDarte tables. Generic operator mutations remain behind an
operator-only service/CLI unless a separately authorized UI action exists.

The final admin contains no legacy status vocabulary, list endpoint, attempt
shape, event parser, route discriminator, mode switch, or local-storage bridge
for a transitional lane.

## 5. Queue and cohort layout

Queue names follow the locked lowercase/underscore grammar. FR-01 validates
the following initial resource-isolation map against production settings and
handler behavior before provisioning:

| Queue | Initial task families | Reason |
| --- | --- | --- |
| `qdarte_discovery` | cluster/buzz/open-source discovery, rescue, imports and TripAdvisor acquisition/classification | browser/network-heavy acquisition and recovery |
| `qdarte_content` | listing research, synthesis, content/editorial enrich, translation, review, region completion | model/content pipeline with cross-task follow-ups |
| `qdarte_media` | photo find and verify | media/provider limits and filesystem effects |
| `qdarte_publish` | publish and frontend deploy | deployment side effects and low concurrency |
| `qdarte_verification` | contact and website verification | bounded external verification effects |

A queue is an isolation and backpressure unit, not a domain status. Moving a
task between queues is a provisioning/release decision with compatibility
evidence. Task names remain stable, lower-case, typed registry identities.

No queue is provisioned for an obsolete/dormant type. Communication work gets
its own queue only after its owner and handler are established.

## 6. Native capability prerequisites

SQL 0.1.5 is deliberately insufficient for the full QDarte graph. The missing
features are taskq product work, not permission to keep QDarte's implementation.

| Capability | QDarte evidence | Required taskq work |
| --- | --- | --- |
| lossless atomic follow-ups | completion paths enqueue review, publish, translation, photo and rescue children | activate the already-designed `followups` capability with contract/migration/parity/race evidence |
| dependencies and workflows | `workflow_runs`, dependency edges, waiting promotion, cancellation propagation and workflow finalization are active service concepts | ship native dependency/workflow identities and typed projections; remove QDarte graph mutation code |
| delayed and recurring schedules | `scheduled_at`, recurring interval, schedule promotion and janitor paths exist | ship native schedule functions/worker and database-time behavior; no host cron row mutation |
| finished/running operator pages | QDarte lists active and historical jobs and attempts | make each finite read view pass B9 before activation; add an exact bounded attempt/timeline projection only through a new contract if product acceptance needs it |
| trusted domain effects | side-effecting handlers submit results then settle | generalize ADR-022's typed reporter use in QDarte; no SQL/wire change unless source proof finds the reporter contract insufficient |
| planning idempotency | contact proved duplicate planning under retries | use ADR-023 for every expensive planner; no host mapping table |

Each SQL capability is independently gated. No QDarte branch may emulate a
missing capability while waiting. If source audit finds a behavior not
producible from the accepted taskq contracts, work stops and records a
Contract question in `TASKS.md`.

## 7. Execution sequence

### FR-00 — freeze destination and source floor

Land this specification, register it in the tier map, supersede the active C8
strangler sequence in the board/build plan, and record the only goal as full
replacement. No implementation or database change.

### FR-01 — executable inventory and deletion manifest

Generate checked-in machine-readable manifests for:

- task types, handlers, producer routes/services/scripts, worker lifecycle
  calls, result/effect calls, follow-up edges, dependencies and schedules;
- every old queue model/table/index/function/migration/config variable;
- every admin/runtime/infra consumer; and
- every database relation classified under §3.

CI fails on an unclassified queue symbol or a registry/handler mismatch. This
slice also validates the queue map and records feature use per lane.

### FR-02 — taskq 0.2 contract program

Freeze and implement the smallest ordered native capabilities required by the
inventory: follow-ups first, then dependencies/workflows, then schedules, then
only the operator projections that pass their own plan gates. Each sub-slice
uses docs-first ADR/Protocol/Manifest changes, immutable migrations, full
fresh/upgrade chains on PG16/PG18, generated SQL/HTTP clients, fakes, race and
artifact evidence, and a bridge release before each database contract bump.

### FR-03 — QDarte native domain/effect layer

Move payload/result types out of the old queue API namespace, build the one
native task registry, generalize the trusted effect boundary, and pin domain
idempotency for every side-effecting operation. The old worker loop stays
untouched and stopped while the native registry is tested through
`taskq.testing` and disposable real SQL/HTTP runs.

### FR-04 — migrate all lanes locally

Move lanes by dependency wave, not by compatibility mode:

1. pure/no-network tasks;
2. leaf verification and classification tasks;
3. media and content leaf effects;
4. chained content/review/publish pipelines;
5. discovery/import/rescue graphs and scheduled work.

Within the disposable local stack, only the native producer and native worker
run. The old queue remains stopped; there is never dual publication. Every
wave proves keyed replay, response loss, hard kill/reclaim, cancellation,
bounded concurrency, effect conservation, and expected follow-up graph.

### FR-05 — replace QDarte API/admin/runtime surfaces

Rewrite domain submission/status routes and admin pages against the native
surface. Remove old queue lifecycle routes and clients as soon as their last
caller moves. Replace old process/fleet controls with taskq presence,
shutdown, queue profiles and runtime health; retain separate resource/provider
controls that are not queue lifecycle.

### FR-06 — delete executable legacy code

Delete the old models/services/routes/client/loop/config and every transitional
pilot/contact/C6/C7/C8 adapter, mode, controller and product route. Historical
evidence documents and immutable published artifacts remain untouched.
Checked-in executable harnesses are either rewritten against the final system
or deleted; nothing imports them from product source.

### FR-07 — database contraction and clean baseline

Create two explicit database paths:

1. **Existing-install contraction:** one Alembic migration drops only relations
   classified `queue-delete`, after foreign-key and retained-domain assertions.
   It never migrates queue rows.
2. **Fresh-install baseline:** a new canonical QDarte baseline represents only
   the post-replacement business schema and never creates the old queue or
   embedded taskq clone. The previous chain remains an immutable upgrade
   artifact, not the fresh-install path.

The contraction also drops obsolete grants/roles/functions and proves taskq's
separate database remains valid. Backup/restore tests cover business content
and taskq independently.

### FR-08 — one-time production cutover package

Prepare, but do not execute without explicit authorization:

1. backup and tested restore;
2. stop/disable every old producer and worker;
3. record final old-ledger counts/digest, without exporting payloads;
4. apply QDarte contraction and provision/migrate canonical taskq;
5. deploy native API/admin/runtime/workers disabled;
6. prove credentials, registry, queues, health and zero old processes;
7. enable producers, then bounded workers; and
8. validate representative domain facts and native tasks.

Rollback before contraction restores the prior application/config. Rollback
after destructive contraction restores the complete database backup; there is
no mixed-schema fallback and no reverse row translation.

### FR-AUDIT — local production-readiness gate

Acceptance requires §8 and §9 in a fresh disposable stack seeded from the
sanitized production-shaped business backup, plus a second run from the clean
fresh baseline. Only then is production testing ready.

## 8. Zero-legacy acceptance oracle

The final audit fails if any non-historical product path contains or exposes:

- the old queue models/tables/functions or embedded schema migration as a
  fresh-install dependency;
- `WorkerJobService` queue lifecycle, `WorkerApiClient` queue lifecycle, the
  old `worker_loop`, or a host-owned claim/heartbeat/settle implementation;
- `/worker/jobs/*` queue lifecycle routes, generic old job list/detail/action
  routes, `/ops/taskq/*`, `/worker/taskq/*`, `/ops/cutover/*`, or product
  contact/pilot facade routes;
- `legacy`, `draining`, `package`, pilot, cutover or dual-backend settings;
- a taskq facade implemented by QDarte SQL rather than the package;
- a direct queue-table grant for API, worker, admin or domain roles;
- a wrapper translating a native job into an old `WorkerJobDetail` or writing
  shadow status/mapping rows; or
- a supported task type without one native registry entry and tested handler.

Allowed occurrences are limited to immutable historical migrations, archived
evidence documents, the one-time contraction script, and explicit negative
tests that assert the symbol is absent. The machine-readable manifest lists
those paths exactly; substring allowlists are not accepted.

## 9. Local completion evidence

The final local gate must prove:

1. all taskq tests and contract/plan/artifact lanes on PostgreSQL 16 and 18;
2. complete QDarte API, worker, runtime and admin suites and static gates;
3. fresh business DB + fresh taskq DB install with no old queue relations;
4. sanitized production-shaped business restore followed by contraction, with
   domain table counts and content/media object manifests conserved;
5. one typed execution for every active handler and real representative
   end-to-end executions for every queue/resource family;
6. chained graph, dependency failure/cancel, delayed/recurring schedule,
   response-loss, retry exhaustion, soft stop and hard-kill/reclaim scenarios;
7. one observable domain effect under response loss and hard kill for every
   effect class, proving exactly one durable business outcome;
8. queue authorization allow/deny, no superuser/runtime operator, secret-safe
   logs/images, exact connection budget and clean resource ledgers;
9. native admin submission/status behavior with no old endpoint traffic; and
10. two clean stack restarts, backup/restore, and the complete zero-legacy
    oracle.

Performance remains evidence, not aspiration: claim/settle and the largest
QDarte planner/follow-up wave get fixed-data before/after reports, with no
regression envelope invented after results are known.

## 10. Stop conditions

Stop and record before proceeding if:

- a required behavior cannot be expressed by an accepted taskq capability;
- a retained domain table has an undeclared foreign key or runtime dependency
  on an old job/attempt/event row;
- a handler can commit an external/domain effect without replay-safe identity;
- an old and native producer or consumer can be active together;
- a fresh install creates an old queue relation;
- any long-lived process needs owner, operator, superuser, database-creation,
  role-creation or RLS-bypass power;
- a lane needs raw taskq table access or a caller-supplied attempt/fence;
- contraction would delete domain content or the only copy of a business fact;
  or
- the local cutover cannot be rolled back from the tested backup.

## 11. Definition of complete

QDarte replacement is complete only when the final audit passes and the four
repositories contain one native queue path, zero compatibility paths, zero
old queue runtime code, and a clean database baseline. A successful contact
lane, a migrated majority of tasks, an inactive fallback, preserved old
tables, or a wrapper hidden behind a domain route is not completion.
