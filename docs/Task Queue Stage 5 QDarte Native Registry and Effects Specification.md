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

Every native input also owns its complete authoritative QDarte scope identity:

```text
scope_kind: ScopeKind
scope_key: bounded non-empty string
```

Existing task contracts whose `scope_kind` is already narrower retain that
narrow literal. The nine former payloads that relied on the old job envelope
(`content_enrich_scope`, `content_synthesis_scope`,
`editorial_enrich_scope`, `frontend_deploy_scope`,
`listing_research_scope`, `photo_find_scope`, `photo_verify_scope`,
`region_rescue_scope`, and `review_scope`) use the closed `ScopeKind` union and
the same bounded key in their native models. A producer may narrow a task
further, but may not omit either field.

Taskq headers contain diagnostics only. They never carry QDarte scope
authority. Runtime settings provide process dependencies only. A native
follow-up copies its parent's scope identity unless the target's typed input
defines and validates an explicit derived identity; no current FR-03 graph
uses such a derivation. Missing or conflicting scope therefore fails typed
payload/follow-up validation before settlement. Reporters obtain scope from
the authoritative stored native payload, never a handler echo.

### 3.1 Fully planned coordinator inputs

Native coordinators cannot depend on the removed API enqueue layer to expand
selectors after claim. `content_enrich_scope` therefore replaces its legacy
target-flag input with a closed, maximum-20 tuple of fully planned children:

```text
ContentEnrichPhotoFollowup
  kind = "photo"
  step = stable bounded step
  payload = NativePhotoFindInput

ContentEnrichEditorialFollowup
  kind = "editorial"
  step = stable bounded step
  payload = NativeEditorialEnrichInput
```

The union is discriminated by `kind`, every step is unique, and each child
payload's `(scope_kind, scope_key)` must exactly equal the parent. The QDarte
API producer owns the future planning query and supplies complete child
payloads before enqueueing the coordinator. Entity-key/content-id selectors,
unknown fields, and handler-time planning reads are forbidden.

The native handler performs no I/O. It converts the already-validated tuple to
taskq `Followup` values and returns one `Complete`, making validation and child
insertion atomic with parent settlement. An empty tuple returns bounded
`no_change`; more than 20 children, duplicate steps, or conflicting scope
fails before settlement.

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
| `photo_verify_scope` | `qdarte_media` | metered provider/filesystem verification plus closed `photo_verification` domain effect and preplanned native follow-up selection |
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

#### Photo verification effects and structural retry ladder

`photo_verify_scope` is not read-only. Its closed `photo_verification` family
has exactly three mutually exclusive operation keys per planned entity:

- `verified` updates the authoritative photo gate;
- `retry_selected` records the failed verdict whose already-planned
  `photo_find_scope` retry branch is selected; and
- `terminal_blocker` records the launch-pipeline
  `photo_verify_failed` blocker.

The terminal blocker is an ordinary closed family member with the same stable
`(taskq job id, family, entity key, operation key)` identity as every other
effect. It is not an event, settlement hook or side channel. Before applying
one operation, the handler inspects all three. At most one may be committed;
the host rejects a second operation for the same job/entity even if its
operation key differs. A committed operation is therefore also the durable
branch-selection receipt after response loss.

Each input entity has one strict producer-built branch plan. A pass plan may
carry one fully planned `review_scope` child. A failure plan either carries
one fully planned `photo_find_scope` retry child or carries no retry and is
terminal. The handler may only select a child already present in that entity's
plan. It never calls a planner, increments or derives a retry counter, asks the
reporter to enqueue, or synthesizes a child payload after provider work.
Across the whole parent, planned and selected children are capped at 20,
steps are unique, child scope equals parent scope, and native follow-ups remain
atomic with taskq settlement.

This is a structural retry ladder: a producer recursively plans each permitted
level, and the final level omits the retry branch. First failure selects the
present retry; terminal failure applies `terminal_blocker`; replay of either
returns the identical receipt and identical child set. Replay cannot add a
branch or extend the ladder.

Photo model work uses ADR-031's shared `llm_provider_control` with lane
`photo_verification` and operation `verify`. The provider call stays in the
worker. A hard kill after provider egress must converge to exactly one usage
event or the typed `expired_unsettled` unknown-cost posture, together with one
mutually exclusive photo effect. This evidence is required at the photo gate
and does not waive FR-04.

#### Completion-hook sweep for remaining unbound families

The old completion/result paths were re-derived before binding photo. The
following follow-up decisions are not read-only behavior and must be replaced
by producer-planned native branches when their family binds:

| Native family | Legacy completion behavior that must not disappear | Native disposition |
|---|---|---|
| `buzz_discover_scope` | region-rescue handoff | already-carried closed native follow-up |
| `region_rescue_scope` | photo-find handoff/autocontinue | preplanned photo-find child |
| `photo_find_scope` | photo-verify or repair-review handoff | preplanned mutually exclusive child |
| `editorial_enrich_scope` | repair-review handoff | preplanned review child |
| `listing_research_scope` | synthesis handoff or pipeline refresh/blocker | closed effect plus preplanned synthesis branch |
| `content_synthesis_scope` | review/repair-review handoff | preplanned review child |
| `translation_scope` | review/repair-review handoff | preplanned review child |
| `review_scope` | publish, repair or translation handoffs and blocker updates | closed review effect plus preplanned branches |

The machine effect inventory records these source-derived dispositions and its
oracle equality-checks them. A future binding stops if its current source
contains a completion mutation or child-selection path absent from that
record. This sweep authorizes no sibling implementation in the photo slice.

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

### 6.1 Private reporter transport

The QDarte host exposes exactly one internal effect endpoint:

```text
POST /internal/taskq/native-effects/{job_id}
```

It is not part of taskq Protocol v1 and is never mounted as a public operator
or producer route. The host uses the same `QueueAuthorizer` supplied to the
native taskq facade. It authenticates, resolves the current running job's
authoritative queue and closed native task type from the path `job_id` without
fetching its payload, then authorizes `run` on that queue before reading or
decoding the body. A missing, terminal, or non-effect task fails with the same
fixed conflict before body decode. The full stored payload and current attempt
are re-read and revalidated after decode; route authorization is not effect
authorization. This two-phase lookup is required once the closed union spans
more than one queue and must never fall back to a body-supplied queue or task
type. The body is streamed under the 64KB effect-request ceiling and
contains only:

- reporter-owned `attempt_id` and `worker_id`;
- one discriminated request from the closed native effect union.

The handler never supplies or sees the attempt identity. The transport binds
it from `WorkerEffectAttempt`; the API revalidates the current heartbeat,
queue, task type and stored strict payload before ledger inspection or domain
mutation. Invalid JSON/shape returns a fixed non-echoing validation error;
stale authority or canonical-intent mismatch returns a fixed conflict. No
request field, credential, domain error text or task fence is echoed.

The active union members are `contact_verification`,
`website_verification`, `tripadvisor_classification`, `photo_verification`,
`editorial_enrichment`, `listing_research`, `content_synthesis`, and the private
`llm_provider_control` member. Future families
extend the same union docs-first; they do not add arbitrary paths or a generic
method selector. SQL-only tests may call the adapter directly, while HTTP
parity must prove bad credentials and authoritative-queue denial happen before
body decode and produce zero ledger/domain writes.

The private reporter's aggregate body and envelope ceiling is 64KB, equal to
the executable native-model ceiling. Authentication and authoritative
queue-scoped `run` authorization still complete before reading or decoding the
body. Every closed member retains its own narrower field and collection
bounds; the larger aggregate ceiling does not admit arbitrary JSON.

#### Listing-research member

`listing_research_scope` uses the existing private reporter route and
`qdarte_content` queue authorization. Before enqueueing the parent, the
producer mints one stable UUID `bundle_id` for each planned entity and may
attach one fully materialized `content_synthesis_scope` child that references
that same bundle. Entity and branch-plan sets are equal, bundle identities are
unique, child scope equals parent scope, steps are unique, and the job carries
at most 20 children. A handler never mints an artifact identity, calls a
planner, or constructs synthesis input after provider work.

The handler may report only `inspect` or `apply` for family
`listing_research`, operation key `apply`, the planned entity key, stable
`bundle_id`, and one bounded research result. A ready result carries the exact
canonical writer-input bundle accepted by QDarte's writer-input firewall,
including its synthesis-ready exact-place evidence threshold; an underfilled
result carries no bundle. The result includes only bounded
warnings, source-discovery mode, input fingerprint and guard reason needed by
the authoritative transition. The complete reporter envelope remains at most
64KB, collections are finite, strings are bounded, and provider/model labels,
caller timestamps, filesystem paths, credentials, diagnostics and arbitrary
result metadata are absent.

The API re-derives content and plan authority from the stored strict task
input, revalidates the writer-input firewall, and persists the artifact under
the producer-minted `bundle_id`. It evaluates current content and launch state
at database time and commits artifact, pipeline mutation and effect receipt in
one transaction. Its family-specific response returns the ordinary stable
receipt plus exactly one authoritative disposition:
`synthesis_ready`, `curated_hold`, or `blocked_exhausted`. Inspect and exact
apply replay return the same disposition and receipt.

After a committed apply, the handler selects the already-materialized
synthesis child only for `synthesis_ready`. The other dispositions select no
child. The synthesis domain reader later resolves the stable `bundle_id`;
application artifacts are not copied into taskq, and no old queue/client or
completion hook remains in that path. Response-loss replay returns the
identical receipt, disposition and child.

#### Content-synthesis member

`content_synthesis_scope` uses the same private reporter route and
`qdarte_content` authorization. Its producer-built input maps each entity to
exactly one stable listing `bundle_id` and at most one fully materialized
`review_scope` child. The branch is labelled `review` or `repair_review` by the
producer from authoritative origin before enqueue; the handler never derives
origin or calls a planner. Entity, bundle and plan sets are equal and unique,
child scope equals parent scope, steps are distinct, and the job-wide child cap
is 20.

The closed family has a bounded `prepare` request and mutually exclusive
terminal operations `synthesized`, `bundle_blocked`, `geo_blocked`, and
`writer_blocked`. `prepare` authorizes the current task and exact planned
bundle before reading the artifact. It returns either the already-committed
receipt/outcome or the exact synthesis-ready writer bundle under the same
64KB response ceiling. It never accepts an arbitrary artifact id, returns a
filesystem path, or exposes a generic artifact reader. Missing, expired,
superseded, entity-mismatched or firewall-invalid bundles return a typed
blocked preparation; they never reach a model.

The handler performs no discovery. It prepares before provider work, uses
ADR-031's `content_synthesis` lane for every metered model/repair call, and
applies exactly one terminal operation. `synthesized` carries one strict
bounded draft whose entity and canonical locale match stored authority.
Blocked operations carry only a closed bounded reason and, for
`geo_blocked`, bounded matched terms. The API applies the draft or pipeline
blocker at database time and records the stable effect receipt in the same
transaction. Bundle usage is refreshed only through this authoritative path.

Only a committed `synthesized` outcome selects the already-planned review or
repair-review child. Blocked outcomes select no child. Inspect/prepare and
apply response-loss replay return the identical receipt, outcome and child;
replay cannot invoke a provider, change branch kind, or extend the graph.

#### Editorial-enrichment member

`editorial_enrich_scope` uses the existing private reporter route and
`qdarte_content` queue authorization. The stored strict
`NativeEditorialEnrichInput` is authoritative for the content item, entity,
current source material and exactly one optional preplanned review branch per
entity. Plan keys cover the entity keys exactly, every child has the same
canonical scope as its parent, steps are distinct, and the complete job carries
at most 20 children.

The handler may report only `inspect` or `apply` for family
`editorial_enrichment`, operation key `apply`, the planned entity key, a
strictly bounded editorial draft, and status `enriched | unchanged`. The draft
allows only the canonical title, excerpt, meta description, body, bounded
source URLs and notes, and bounded `es | en` locale projections. It contains no
content-item id, attempt/worker identity, caller timestamp, arbitrary result
metadata, provider diagnostic, credential, filesystem path, or request echo.
The API re-derives the content identity from the stored entity, applies locale
and review-state mutation at database time, and commits the domain mutation
plus receipt in one transaction.

The handler inspects before model work. A committed inspection skips model
work. Otherwise a metered model call uses ADR-031
`llm_provider_control`, then the identical effect intent is applied and
replayed after an ambiguous response. Only after a committed receipt may the
handler return the entity's already-materialized `review_scope` child.
Replayed effects return the same receipt and the same child; settlement never
plans or extends the graph.

#### Website-verification member

`website_verify_scope` uses the same private reporter route and
`qdarte_verification` queue authorization as contact verification. Its stored
`NativeWebsiteVerifyInput` is authoritative for the planned entity,
`contact_point_id`, `content_item_id`, submitted website, venue identity, and
browser plan. The handler supplies only the planned `entity_key`, fixed
operation key `verify`, and this bounded provider result:

- final verdict: `verified | blocked | needs_review | unreachable`;
- network verdict: `reachable | blocked | unreachable | needs_review`, fixed
  verifier version, optional final URL/status/content type/page title, at most
  six bounded redirect hosts, and one bounded reason;
- optional markdown extraction result: `success | error`, with only bounded
  method/final URL/status/character-count/reason fields; and
- optional identity judgment: `yes | no | maybe | error`, bounded confidence,
  reason, model, elapsed milliseconds and error class.

The result contains no caller-supplied contact-point id, content-item id,
original URL, worker timestamp, arbitrary evidence dictionary, page body,
prompt, response body, credential, or provider error text. PostgreSQL
`clock_timestamp()` supplies the persisted `checked_at` and mutation
timestamps. The domain owner constructs the exact
`contact_meta_jsonb.website_verification` projection from the typed result,
updates exactly the authoritative planned website contact point, and records
the stable effect receipt in the same transaction. A missing or non-website
contact point fails the transaction rather than recording an effect.

The bounded result enforces the old lane's decision table rather than accepting
contradictory evidence:

- blocked or unreachable network evidence yields `unreachable` with no
  extraction or identity result;
- a network `needs_review` result yields `needs_review` with no extraction or
  identity result;
- reachable plus identity `yes` at confidence `>= 0.9` yields `verified`;
- reachable plus identity `no` yields `blocked`; and
- reachable with extraction failure, no configured judge, `maybe`, `error`, or
  a lower-confidence `yes` yields `needs_review`.

Inspect must complete before network, browser, or model work. A committed
inspection skips every provider operation. Ambiguous apply response replays
the byte-identical typed apply and must converge on the same receipt without a
second provider call. Direct SQL and HTTP vectors prove authoritative entity
selection, wrong task/queue/entity refusal before ledger access, missing-row
rollback, canonical metadata, and provider-call conservation.

#### TripAdvisor-classification member

`tripadvisor_classification_scope` uses the private reporter route with
authoritative `qdarte_discovery` queue authorization. Its stored strict input
is the complete provider plan: each target contains the authoritative place
and source-record identities plus the bounded name, location, source labels,
Google place types/name/address, description, website, detail URL, rating,
review count, current subtype and force-reclassification posture needed for
classification. The handler never fetches mutable business rows to assemble a
provider prompt and never sends database identities to a provider.

For each target the handler first inspects stable operation key `classify`.
A committed inspection skips both deterministic classification and provider
work. A pending inspection follows this closed decision:

- supported Google place types produce a bounded deterministic assessment with
  source `google_place_types` and no provider call;
- an already classified target under non-force mode produces a typed
  `skipped_existing` result;
- disabled or unavailable provider configuration produces
  `skipped_unavailable`;
- otherwise the worker calls one configured provider plan under the existing
  metering/failover policy and reports only classification
  `attraction | experience`, confidence, a bounded reason, at most twelve
  bounded signals, provider/model labels and an optional bounded endpoint
  label; or
- exhausted provider failures produce a retryable handler failure and no
  apply request. Provider exception text, headers, bodies and credentials never
  enter the effect request, receipt or task result.

The apply adapter revalidates the current task, stored target and live
source-record/place relationship before ledger access. It derives the exact
domain mutation from the stored plan plus bounded assessment, applies the
classification/alignment transaction at database time, and records the stable
effect receipt in that same transaction. Request fields cannot select another
source record or place, change force posture, set timestamps, supply raw JSON,
or request an arbitrary alignment. A missing or drifted authoritative target
rolls the reservation back.

The native output contains only bounded aggregate outcome/replay counters,
stable effect receipts and the input warnings. Provider/model/endpoint labels
are deliberately excluded: the generic replay receipt exposes only the stable
result digest, so repeating those labels after reclaim would require either
guessing the failover winner or widening every family's receipt. Provider
identity remains inside the hashed effect intent and authoritative domain
metadata. The output carries no old attempt/event projection and creates no
follow-up. Response-loss replay must conserve one provider invocation and one
mutation per target. Direct SQL, HTTP and fake vectors cover
deterministic/provider/skipped outcomes, queue denial before malformed-body
decode, wrong task/entity refusal, authoritative target drift, replay, and raw
source-record/alignment conservation.

### 6.2 Non-domain operations

Provider/search/model reads may repeat only inside their existing metered
reservation and retry policy; they never claim exactly-once behavior.
Filesystem artifacts use a job-scoped immutable content digest and atomic
publish/rename so reclaim can reuse or replace incomplete attempt-local data.

`frontend_deploy` and `tripadvisor_session` are not disguised as ordinary
database effects. Each gets its own inspect/execute/record state machine with a
stable job/operation identity and an independently observable receipt.
Ambiguous execution is inspected before retry. Neither may execute from
settlement or from a generic reporter escape hatch.

#### Queue-independent LLM provider control

ADR-031 adds exactly one closed reporter member,
`llm_provider_control`, used by every native LLM family. It has no arbitrary
method selector. A reserve request contains only:

- `operation:"reserve"`;
- the closed lane plus stable entity and operation identity;
- one provider/model option from the stored strict task input;
- a lowercase SHA-256 request fingerprint; and
- a positive estimated token count bounded by the provider-control model.

A settle request contains only `operation:"settle"`, the opaque reservation
receipt, one closed `success | transport | capacity` outcome, and bounded
request/response token counts. Neither request accepts a timestamp,
idempotency key, queue, job, attempt, worker, prompt, provider payload,
credential, header, exception text, or arbitrary metadata.

The existing reporter-owned attempt envelope supplies attempt authority. The
host authenticates, resolves the current task's authoritative queue, and
authorizes `run` before body decode. It then validates the request against the
stored strict task target and provider plan. PostgreSQL derives a stable
logical control identity plus a positive reservation generation and stamps all
times.

Same-attempt reserve replay is byte-stable. A different attempt cannot inherit
a live provider-call generation: it receives typed retryable
`reservation_pending` with the opaque reservation identity and database expiry
and performs no provider call. Settlement row-locks the reservation and stores
a canonical settlement hash in its bounded metadata while recording the
provider event and reservation transition in the same transaction. Exact
settlement replay returns the same receipt; mismatch fails closed. Database
expiry releases the budget hold but leaves a retained
`expired_unsettled` unknown-cost posture. The first observing attempt is stored
for exact expiry-response replay. Only a later attempt may create the next
numbered generation and consume a new budget unit; the old generation remains
immutable. Unknown cost never implies zero usage.

The TripAdvisor classification order is inspect classification effect,
reserve provider, invoke the provider in the worker, apply the classification
effect, then settle provider. A committed effect skips provider work on
reclaim. A hard kill after egress must converge to exactly one provider event
or the typed expired-unsettled posture, with no double-spend and no silent
loss. This evidence does not waive FR-04's later per-wave hard-kill gates.

Photo verification uses the same control in lane `photo_verification`,
operation `verify`. Its combined bounded verification request is fingerprinted
from the authoritative stored entity and provider plan. Extraction parity
must preserve the old reservation decision, provider failover and usage-event
guarantees; a photo-specific proxy or use of taskq admission is forbidden.

Extraction parity pins the pre- and post-extraction budget decision,
reservation, failover and usage-event behavior. No native handler imports the
old job, attempt, client, lifecycle service, reservation route or execution
table.

Translation uses the same control in lane `translation`, operation
`translate`. Its strict input contains the producer-read source locale,
revision digest and bounded source fields, so the native handler performs no
application-database or arbitrary filesystem read. Before provider egress a
closed `translation` prepare request authorizes the current task and confirms
that the current canonical source still has the planned revision. It returns
committed terminal truth, `ready`, or the fixed `source_stale` posture; it
never returns arbitrary domain content because the bounded source is already
in the task input.

The producer also materializes at most one `review_scope` child labelled
`review | repair_review` before enqueue. A successful bounded translation
effect upserts the exact target locale, stamps provenance and clears only the
satisfied locale hold at PostgreSQL time; only then may the handler return
the planned review child atomically with task settlement. `source_stale`
performs no provider call, locale mutation or child insertion. Provider
failure remains a retryable task failure with no translation effect. Exact
prepare/apply replay returns the same terminal outcome, receipt and child.
The old translation result route, provider-admission client, attempt/event
objects and completion-time review planner are forbidden from the native
module graph.

#### Completion-hook reclassification sweep

The CQ-11 source sweep is complete before the remaining FR-03 bindings. The
old completion hooks establish these required native dispositions:

- `buzz_discover_scope`: bounded result effect plus its already planned rescue
  branch; no completion-time planner survives;
- `region_rescue_scope`: authoritative rescue/media effects plus a preplanned
  photo-find branch;
- `photo_find_scope`: authoritative media effect plus mutually exclusive
  preplanned verify or repair-review branches;
- `translation_scope`: authoritative locale effect plus one preplanned normal
  or repair-review branch, as frozen above;
- `review_scope`: authoritative review effect plus closed preplanned publish,
  repair and stale-translation branches; the current completion hook is not
  read-only;
- `region_completion_scope`, `publish_scope`, `discovery.import_batch`, and
  `tripadvisor_region_import`: authoritative effect only, with no
  completion-time child;
- `open_source_discover_scope` and `tripadvisor_session_prime`: their
  immutable artifact/operation receipt plus already-declared preplanned
  children; and
- `frontend_deploy_scope`: a separately inspected operation receipt with no
  domain-effect or child-planning shortcut.

Each binding must cite the old source marker in the machine effect inventory
and replace it with the native handler/domain owner when implemented. Any
later source trace that contradicts this table is a Contract question; it may
not be coded around.

### 6.3 Settlement separation

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
- prove four-way set equality, required scope identity on all 21 inputs, and
  serialization stability.

### FR-03C — native handler bindings

- refactor handlers by pure, read-only, and effectful cohorts;
- prohibit old job/client/settlement imports from the native module graph;
- prove every handler through `taskq.testing`;
- prove fully planned follow-up graphs, atomic child insertion, and bounded
  concurrency; and
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
