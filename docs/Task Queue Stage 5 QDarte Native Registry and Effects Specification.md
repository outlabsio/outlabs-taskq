# Task Queue Stage 5 — QDarte Native Registry and Effects

> **Tier:** 3 — host implementation specification  
> **Status:** Active; FR-03 release and contract-relocation increments complete  
> **Authority:** Transport Protocol v1 revision 1.0.13, Function Manifest /
> SQL contract 0.2.3, ADR-007, ADR-020, ADR-022, ADR-023, ADR-024,
> ADR-026, ADR-027, ADR-029, ADR-030, and the Stage-5 QDarte Full
> Replacement Specification win every conflict.

## 1. Purpose and boundary

FR-03 replaces the two QDarte queue-domain registries with one native task
catalog and one trusted domain-effect protocol. It does not migrate a lane,
start either old worker, publish to two systems, import old queue rows, modify
production, or retain a translation wrapper as the destination.

The accepted source inventory remains the closed oracle:

- 23 declared task types;
- 21 executable task types;
- 2 non-executable declarations;
- 5 resource-isolated queues;
- 12 old result-route families, which are a lower bound rather than the
  complete native effect surface; and
- 266 queue-sensitive files across the four QDarte repositories.

`content_assembly_scope` and `communication.email_delivery` are not registered
in the native executable registry. Historical task literals remain readable
only until FR-06 deletes the old queue reader.

## 2. Immutable package floor

QDarte consumes only the published `outlabs-taskq==0.1.0a7` wheel by immutable
release URL and SHA-256. The artifact resolves to taskq source `1be3b65` and
contains Protocol 1.0.13, SQL contract 0.2.3, migrations 0001–0013, and the
exact 65-function catalog.

No QDarte repository may use a path, branch, range, unpublished wheel, or
locally edited package for FR-03 evidence.

## 3. Canonical task contracts

All QDarte task payload, result, and shared projection models live under:

```text
qdarte_runtime.core.tasking
```

The retiring `qdarte_runtime.core.worker_api.models` module does not exist.
Native source must not import payloads or results through
`qdarte_runtime.core.worker_api`. The old HTTP client may temporarily consume
the canonical models while it remains executable, but it does not own or wrap
them and is deleted under FR-06.

Every public task input and output is a frozen, extra-forbid Pydantic model.
Unbounded arbitrary result dictionaries are forbidden from the native
registry. A task that intentionally has no domain result uses an explicit
empty result model rather than `None`.

## 4. One task definition

QDarte runtime owns one queue-neutral `NativeTaskDefinition` for each of the 21
executable task types:

- canonical task name;
- exact queue;
- input model;
- output model;
- priority;
- lease duration;
- closed retry policy;
- finite follow-up targets;
- effect families; and
- resource classes.

QDarte workers bind exactly one native handler to each definition and build one
taskq `TaskRegistry`. Construction fails when:

- a definition has no handler or a handler has no definition;
- a non-executable or historical declaration is present;
- a task appears twice or through an alias;
- a queue differs from the frozen five-queue map;
- an input/output annotation differs by identity from its definition;
- a follow-up target is missing, on another queue, or outside the declared
  graph; or
- a task declares an effect family it cannot report.

The machine inventory, native definitions, taskq registry, and handler map are
four independently derived sets and must be exactly equal to the 21 executable
types. Message/name similarity is not evidence.

## 5. Native handler boundary

The only accepted handler shapes are:

```python
def handler(context: JobContext, payload: InputModel) -> OutputModel | HandlerResult: ...
async def handler(context: JobContext, payload: InputModel) -> OutputModel | HandlerResult: ...
```

A native handler never receives or constructs an old job, attempt, queue
client, fence, or settlement request. It never calls claim, heartbeat,
complete, fail, release, redrive, or old follow-up enqueue. `WorkerService`
alone owns those operations.

Domain/provider dependencies are injected through typed factories fixed at
registry construction. Pure handlers need none. Side-effecting handlers use
only `context.report_effect()` for authoritative QDarte writes. Native
follow-ups are returned as taskq `Followup` values and validated by the
registry before settlement.

Temporary conversion from a taskq claim into an old worker model is forbidden.
That would be a compatibility wrapper and fails FR-03 even if its tests pass.

## 6. Closed effect and operation protocol

The old result routes are not an effect inventory. Source inspection also
finds authoritative writes hidden inside settlement, direct discovery/import
writes, filesystem artifacts, provider operations, proxy/session mutation, and
deployment subprocesses. The native catalog therefore classifies every
executable task, including tasks with no authoritative domain write:

| Task | Queue | Effect/operation disposition |
|---|---|---|
| `buzz_discover_scope` | `qdarte_discovery` | provider reads plus `buzz_discovery` domain effect |
| `cluster_research_scope` | `qdarte_discovery` | pure CPU; no effect |
| `contact_verify_scope` | `qdarte_verification` | provider read plus `contact_verification` domain effect |
| `content_enrich_scope` | `qdarte_content` | native follow-ups only |
| `content_synthesis_scope` | `qdarte_content` | metered model call plus `content_synthesis` domain effect |
| `discovery.import_batch` | `qdarte_discovery` | bounded filesystem read plus `discovery_import` domain effect |
| `editorial_enrich_scope` | `qdarte_content` | metered model call plus `editorial_enrichment` domain effect |
| `frontend_deploy_scope` | `qdarte_publish` | separately idempotent `frontend_deploy` operation and bounded route verification |
| `listing_research_scope` | `qdarte_content` | provider reads plus `listing_research` domain effect |
| `open_source_discover_scope` | `qdarte_discovery` | provider/filesystem reads, `open_source_import` domain effect, native follow-up |
| `photo_find_scope` | `qdarte_media` | provider/filesystem operation plus `photo_application` domain effect |
| `photo_verify_scope` | `qdarte_media` | provider/filesystem verification; no domain mutation |
| `publish_scope` | `qdarte_publish` | `publish` domain effect; never settlement-triggered |
| `region_completion_scope` | `qdarte_content` | metered model call plus `region_completion` domain effect |
| `region_rescue_scope` | `qdarte_discovery` | provider reads plus `media_application` and `region_rescue` domain effects |
| `review_scope` | `qdarte_content` | metered model call plus `review` domain effect |
| `translation_scope` | `qdarte_content` | metered model call, bounded source-file read, `translation` domain effect |
| `tripadvisor_classification_scope` | `qdarte_discovery` | metered model call plus `tripadvisor_classification` domain effect |
| `tripadvisor_region_import` | `qdarte_discovery` | provider/filesystem operation plus `tripadvisor_import` domain effect |
| `tripadvisor_session_prime` | `qdarte_discovery` | separately idempotent `tripadvisor_session` operation plus native follow-up |
| `website_verify_scope` | `qdarte_verification` | provider read plus `website_verification` domain effect |

This table, the checked-in machine effect manifest, and the source call graph
must agree exactly. A newly observed client mutation, subprocess, provider
request, durable filesystem write, or direct database call is unclassified
until all three are amended.

Each family defines a discriminated inspect/apply request and a bounded result.
The handler supplies only:

- the effect-family discriminator;
- one bounded entity key;
- the domain payload required by that family; and
- an operation key when one job may perform multiple operations for the same
  entity.

The trusted runtime adds the active job/attempt identity. The API obtains
queue/type/entity authority from the authoritative task payload, not from a
handler echo.

The stable effect identity is:

```text
(taskq job id, effect family, entity key, operation key)
```

It never includes attempt id. Every apply is one database transaction that
both changes the domain and records/reuses the stable result. The same identity
plus the same canonical request returns the committed result; the same
identity plus a different canonical request fails closed. `inspect` performs
no domain mutation and returns only `pending` or the committed bounded result.

Provider calls follow inspect-before-act:

1. inspect;
2. if committed, return it without the provider;
3. perform the external operation;
4. apply the result;
5. on ambiguous apply response, replay the identical apply; and
6. settle only after the stable committed result returns.

No generic arbitrary-method/path/SQL reporter exists. Adding an effect family
is docs-first and extends the closed union, registry declaration, authorization
matrix, idempotency ledger, machine effect manifest, and vectors together.

### 6.1 Non-domain operations

Provider/search/model reads may repeat only inside their existing metered
reservation and retry policy; they never claim exactly-once behavior.
Filesystem artifacts use a job-scoped immutable content digest and atomic
publish/rename so reclaim can reuse or replace incomplete attempt-local data.

`frontend_deploy` and `tripadvisor_session` are not disguised as ordinary
database effects. Each gets its own inspect/execute/record state machine with a
stable job/operation identity and an independently observable receipt.
Ambiguous execution is inspected before retry. Neither may execute from
settlement or from a generic reporter escape hatch.

### 6.2 Settlement separation

The old `publish_scope` mutation inside `complete_job` is a deletion target.
The native publish handler obtains the stable `publish` receipt before it
returns completion; taskq settlement then changes only taskq state. The same
rule applies to every family: no QDarte domain mutation is triggered merely
because taskq receives complete/fail/release.

## 7. Evidence increments

### FR-03A — release and relocation

- publish and independently hash-verify a7;
- exact-pin API and workers;
- move canonical models out of the old worker API namespace;
- keep the 266-file inventory exact; and
- prove runtime, API boundary, and worker suites without running a worker.

### FR-03B — native definitions and typed outputs

- add the exact 21-definition catalog and five-queue map;
- give every task a strict output model;
- encode retry, lease, resource, follow-up, and effect metadata;
- reject both non-executable declarations; and
- prove four-way set equality and serialization stability.

### FR-03C — native handler bindings

- refactor handlers by pure, read-only, and effectful cohorts;
- prohibit old job/client/settlement imports from the native module graph;
- prove every handler through `taskq.testing`;
- prove follow-up graphs and bounded concurrency; and
- keep the old worker stopped and unchanged except for canonical model imports.

### FR-03D — general domain effects

- add the closed reporter union;
- implement each family’s authoritative-plan validation and stable
  idempotency transaction;
- prove inspect/apply replay, request mismatch, stale attempt, cancellation,
  response loss, and no duplicate domain effect; and
- keep reporter credentials separate from worker credentials.

### FR-03E — disposable SQL/HTTP completion

- install a7 into a fresh isolated taskq database;
- provision all five queues and least-privilege credentials;
- execute all 21 registered handlers through the real SQL and HTTP paths;
- prove raw task, attempt, workflow, schedule, and domain-effect oracles;
- prove no old worker process or old queue row changed; and
- repeat from the sanitized production-shaped business database.

## 8. Stop conditions

Stop and record a Contract question before implementation if:

- taskq cannot express one required typed handler, follow-up, workflow,
  schedule, or trusted effect without exposing old queue authority;
- an effect cannot be made idempotent in the same transaction as its domain
  mutation;
- one active task has no bounded input or output contract;
- the source-derived queue/follow-up/effect graph conflicts with Tier 0; or
- a proposed shortcut constructs an old queue object or calls an old queue
  lifecycle method.

## 9. FR-03 exit gate

FR-03 is complete only when the canonical definitions, native taskq registry,
handler bindings, effect families, inventory, and tests agree exactly; all 21
handlers pass fake and disposable real SQL/HTTP evidence; no old worker ran;
no old queue row changed; and no native source imports the retiring queue
models or lifecycle client.

That gate opens FR-04 lane migration. It does not authorize production,
old-code deletion, database contraction, or queue-history import.
