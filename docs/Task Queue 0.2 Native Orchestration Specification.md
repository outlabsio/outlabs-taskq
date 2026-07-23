# Task Queue 0.2 — native orchestration specification

> **Status:** frozen by S5-QD-FR-02-SPEC on 2026-07-22; FR-02A is
> complete and ADR-026 freezes FR-02B at Protocol 1.0.10 / SQL 0.2.1.
>
> **Authority:** Tier 3. This narrows ADR-007/009/011 and the Unified Design
> Spec to the minimum reusable surface demonstrated by QDarte FR-01. Tier-0
> contracts and ADRs win every conflict. Each capability still requires its
> own docs-first ADR, Protocol and Manifest amendment before implementation.
>
> **Scope:** native queue orchestration only. No QDarte adapter, database
> mutation, legacy-row import, compatibility mode or production action.

## 1. Outcome

SQL 0.2 supplies four independently activated capability families:

1. lossless settlement-transaction follow-ups;
2. dependency graphs and workflow identity;
3. delayed and recurring schedules; and
4. finite running, finished, workflow and exact-job timeline projections.

These are general taskq primitives. QDarte is the evidence source and first
consumer, but no function, route, type, outcome or column carries a QDarte name
or reproduces its retired job shape.

Replace/by-arguments uniqueness, arbitrary reporting, completion handles,
streaming events, partitioned archive, redirect dead letters, blueprints,
namespaces and rate/resource admission remain separate growth decisions.

## 2. Program invariants

1. PostgreSQL is the coordination authority and its clock is the only lease,
   due-time and expiry clock.
2. Existing 0.1 identities remain source-compatible. Evolution is additive or
   a Manifest-versioned body replacement.
3. A capability returns typed `TQ501` until immutable metadata activates it.
4. ADR-020 applies to every revision: a bridge runtime accepting old and new
   metadata ships before migration, and migration raises the runtime floor.
5. SQL and HTTP have identical bounded semantics. HTTP adds authenticate-first
   authoritative queue authorization, never a wider projection.
6. Producer, runner, observer, housekeeper and operator powers stay separate.
   No long-lived runtime gets owner/operator power.
7. Every expected race is typed and every public field keeps existing bounds.
8. Capability metadata is verified by exact equality. Activation/deactivation
   occurs only through immutable migrations, never manual DML.
9. Every slice proves fresh and full upgrade chains on PostgreSQL 16 and 18,
   independent catalog and SQL/HTTP parity, races, artifacts and bounded plans.

## 3. Ordered revisions

| Slice | SQL revision | Migration | Capability |
| --- | --- | --- | --- |
| FR-02A | `0.2.0` | `0008_followups.sql` | `followups` |
| FR-02B | `0.2.1` | `0009_workflows.sql` | `dependencies_workflows` |
| FR-02C | `0.2.2` | `0010_schedules.sql` | `schedules` |
| FR-02D | `0.2.3` or later | proof-backed index/metadata migration | only projections whose own B9 evidence passes |

Protocol major remains v1; each visible slice increments its document revision
and amendment log. Package and SQL versions remain distinct.

## 4. FR-02A — atomic follow-ups

ADR-007 remains authoritative. Migration 0008 replaces the body of the existing
`complete_job` identity and adds owner-private helpers, not another settle verb.

### 4.1 Closed child specification

A non-empty list has at most 20 items. Each item allows only:

- `step`: required unique ASCII label, 1–64 bytes, matching
  `[A-Za-z0-9][A-Za-z0-9._-]*`;
- `job_type`: required bounded task type;
- `queue`: optional target, inheriting the parent queue when absent;
- `payload` and `headers`: optional bounded objects, default `{}`;
- bounded `priority`, `max_attempts`, and `lease_seconds`; and
- optional `scheduled_at` under ordinary enqueue rules.

Unknown keys, duplicate steps, malformed fields, unknown queues and undeclared
child targets are deterministic `TQ422` settlement failures. The worker then
terminal-fails the parent as `invalid_followup` and soft-stops on contract skew.
Nothing is truncated or skipped.

The private inserter derives the child key from parent job id plus `step`;
callers cannot override it. `created` and `existed` are success. Follow-ups are
depth-exempt because accepted work cannot make a completed parent re-execute,
but every other queue/profile/type/size bound still applies.

### 4.2 Atomicity and authority

Fence/replay resolution, validation, parent and attempt settlement, every child,
events and notifications commit once. Any child error rolls everything back.
Same-verb replay returns `already_settled` before child validation.

The worker remains runner-only. Its typed registry declares every allowed child
`(queue, job_type)` at startup; handlers cannot generically enqueue and workers
receive no producer grant. The database owns structural validation and queue
existence, while the closed runtime registry owns application target policy.

Evidence covers 0/1/20/21 children, every validation branch, same/cross-queue,
response loss, stale fences, child-key collision, concurrent completes, rollback
on the Nth child, depth exemption, and equivalent fake/SQL/HTTP child graphs.

## 5. FR-02B — workflows and dependencies

Dependencies coordinate jobs only. Business launch state remains in domain
tables and may use workflow id as correlation but never writes taskq graph rows.

### 5.1 Producer-safe workflow identity

A workflow stores UUID id, bounded idempotent key, finite kind, bounded params,
sorted declared queue set, creator, open/sealed state, optional cancellation
intent and timestamps. Status is a monotonic materialization as
`running | succeeded | failed | cancelled` from member jobs only after seal.

`create_workflow(workflow_key, kind, params, declared_queues, actor)` is
replay-safe and producer-granted. HTTP authorizes **every distinct declared
queue** before execution. Exact creation replay returns the original id;
different kind/params/queues under one key is a typed conflict.

Creation leaves membership open. Producer-granted
`seal_workflow(workflow_id, actor)` is the graph-closure linearization point:
the workflow row serializes member admission against sealing, and only sealed
workflows finalize. A sealed empty workflow succeeds. Exact replay of an
existing step remains valid after seal; new membership conflicts.

Enqueue with a workflow id must target one declared queue. Cancellation is
operator-only, implicitly seals, records durable intent, and advances members
through bounded passes; it never forges worker settlement. Individual member
redrive is rejected in 0.2.1 so terminal workflow state cannot reopen; corrected
execution uses a new workflow key.

This split is binding: creating an application graph cannot require a
long-lived operator credential, while cancelling a whole graph remains an
administrative action.

### 5.2 Edge admission and propagation

The existing enqueue identity activates its reserved `workflow_id`, `step_key`
and `depends_on` inputs. Every workflow member has a workflow-unique step and a
database-stored canonical intent hash. A dependent references 1–100 distinct
existing parents in the same workflow and is inserted atomically with its live
edges. Same-step/same-intent replay returns the original job even after edge
deletion; changed intent conflicts. Because a new dependent can reference only
existing parents and callers cannot supply its id, self-dependency and cycles
are structurally impossible. The workflow row locks first, then parent rows in
ascending UUID order.

Already-succeeded parents contribute no live edge. Failed/cancelled parents
produce a typed rejection and no job. Live edges create `blocked` with exact
`pending_deps`. Parent success deletes satisfied edges, decrements dependents,
and promotes zero-pending children in the same transaction with at most one
wake per queue.

Terminal failure/cancellation advances a bounded direct-descendant frontier,
marking blocked descendants `dep_failed` without consuming their budget.
Skipped and deeper descendants remain gated; an idempotent housekeeper
straggler pass completes the frontier using deterministic lock order. No
descendant may claim after a required ancestor fails.

Workflow finalization is sealed-only, derived/idempotent and runs no domain
hook. Whole-workflow cancellation wins; otherwise failed dominates cancelled,
then all-success. Domains learn terminal failure through bounded projections
and scheduled reconcilers.

Evidence covers enqueue-versus-terminal races, fan-out/fan-in/diamond and
multi-queue graphs, all zero-partial validation failures, sibling completes,
promotion/cascade exactly once, create/seal/enqueue races, exact step replay,
cancel-versus-promote/claim, response loss and bounded direct-edge/finalizer
plans without graph-wide scans.

## 6. FR-02C — schedules

Schedules turn static operator-approved templates into ordinary keyed jobs.
They never run business code or accept SQL/function names/payload factories.

### 6.1 Definition

An operator-managed schedule contains a bounded unique name, queue, job type,
static payload/headers, IANA timezone plus cron expression or positive interval,
database-stamped `next_fire_at`, `catchup_policy = skip | fire_once | fire_all`,
`max_catchup` in `1..100`, enabled/paused state, ordinary enqueue profile values
and monotonic definition version. Updates are conditional; stale versions use
the established non-retryable conflict posture.

Definition/update/delete are operator-only through the separate operator pool,
with target-queue authorization.

### 6.2 Claim and fire

The housekeeper claims due rows with `FOR UPDATE SKIP LOCKED`, bounded lease and
opaque token. The projection includes database due-time and static calendar data
for at most `max_catchup` occurrences. Client wall time never decides due truth.

`fire_schedule` checks the live token, bounded ordered fire list and strictly
advancing next-fire value, then atomically enqueues occurrences and advances the
row. Occurrence keys derive from schedule id plus due instant. Response-loss
replay cannot duplicate. `schedule_error` records bounded diagnostics and
releases/backs off without advancing.

Migration 0010 seeds the daily janitor schedule. Exact capability state disables
the 0.1 hardwired daily branch, so the two triggers cannot coexist.

Evidence covers timezone/DST edges, interval/cron, all catch-up policies after
long downtime, racing housekeepers, response/token loss, definition races,
skewed client clocks, occurrence keys, catch-up bounds, and one-only janitor
takeover.

## 7. FR-02D — finite projections

ADR-019 discipline applies: exact fields and cursor first, million-row plan
proof second, activation last. FR-01 justifies at most:

1. existing queue-scoped `read_model_list_running`;
2. existing queue-scoped `read_model_list_finished`;
3. `read_model_workflow`: exact workflow, bounded state counts and keyset member
   page without payload/result/error; and
4. `read_model_job_timeline`: exact queue plus job id returning public detail
   and bounded attempt/event metadata.

Timeline attempts contain id, ordinal, status, outcome, advisory worker label
and timestamps. Events contain type and timestamp only. Payload, headers,
result, progress, raw error/message, stats and event data are absent. Provider
trace and effect evidence remain in application domain ledgers.

No all-queue list, arbitrary predicate/status, offset pagination, payload search,
raw table/view grant or event-data projection exists. A view that fails B9 stays
independently `TQ501`; a host may not emulate it by widening another route.

## 8. Clients, runtime and testing

- Generated transports expose only metadata-active capabilities.
- `Complete.followups` becomes a typed tuple of the frozen child spec.
- The registry validates closed child targets at startup.
- Producer clients get typed workflow/dependency/schedule commands sized to
  their capability; runner transports do not inherit them.
- `taskq.testing` models atomic children, promotion, cascade and occurrence keys
  with bounded drains that fail loudly on runaway work.
- Protocol oracles are hand-derived before generated metadata imports.
- Core artifacts remain importable without HTTP/authorization extras.

## 9. Delivery gates

1. FR-02-SPEC — this program and source-evidence mapping.
2. FR-02A — complete docs/bridge/migration/runtime/client/fake/evidence chain.
3. FR-02B — the same chain for workflows/dependencies.
4. FR-02C — the same chain for schedules and janitor takeover.
5. FR-02D — independently plan-gated projections; only winners activate.
6. FR-02-AUDIT — combined graph/schedule/recovery/resource/artifact audit before
   FR-03 consumes any 0.2 capability.

## 10. Stop conditions

Stop and record before coding if a graph cannot use follow-ups plus edges;
workflow creation needs operator power; child targets cannot be predeclared; a
schedule needs a dynamic in-engine payload factory; client time affects due
truth; failure can race a dependent into execution; a projection needs sensitive
or arbitrary data; migration strands the rollback runtime; or QDarte's old
queue service would survive as an orchestration wrapper.

FR-02 completes only when every activated family is contract-frozen, immutable,
dual-major/plan/race/artifact proven, and usable by FR-03 without importing or
calling a QDarte legacy queue type or service.
