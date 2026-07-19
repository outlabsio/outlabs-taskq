# outlabs-taskq external design review — round 5 request

> **Tier 4 — immutable review provenance.** Prepared 2026-07-18 after S3-00 froze the FastAPI and authorization integration design. This file records the request exactly; the reviewer may add only `RESPONSE.md` beside it. Accepted findings must subsequently follow the repository's ADR, contract, and task-board process.

Please perform an independent, adversarial review of the frozen Stage-3 design **before any Stage-3
implementation begins**. Determine whether the FastAPI facade, generated HTTP clients, queue
authorization, long-poll bridge, composable runtime, embedded worker, and OutLabs integration can be
implemented faithfully from the locked contracts and completed Stage-2 APIs without privilege
broadening, a second worker state machine, wire invention, secret/fence leakage, or dishonest
resource ownership.

Do not modify any existing repository file. Write the self-contained response as
`docs/design-review-5/RESPONSE.md` and modify nothing else. Do not infer intent from future code or
from lower-authority prose when a contract answers the question. Do not name external queue
projects in the response.

This is a specification gate. There is deliberately no Stage-3 source to review. A finding that
requires the implementer to guess a route, field, outcome, authorization input, lifecycle owner, or
acceptance oracle is a design finding now; a genuine Tier-0/ADR contradiction is a **Contract
question**, not permission to improvise during implementation.

## Authority and read order

Read in this order before evaluating the design:

1. `AGENTS.md` — hard repository rules.
2. `docs/README.md` — tier map and conflict rules.
3. `docs/Task Queue Transport Protocol v1.md` and
   `docs/Task Queue 0.1 Function Manifest.md` — locked Tier-0 contracts. The Protocol is document
   revision 1.0.3; SQL contract is 0.1.2.
4. `docs/adr/README.md` and ADR-001..016 — accepted decisions. ADR-014..016 are mandatory audit
   targets, not assumptions to trust.
5. `docs/Task Queue Authorization & Queue Permissions.md` — normative queue-action and OutLabs
   detail behind ADR-006/011.
6. `docs/Task Queue Stage 3 FastAPI and Authorization Specification.md` — the proposed Stage-3
   implementation contract.
7. `docs/Task Queue Stage 2A Typed Enqueue Specification.md`,
   `docs/Task Queue Stage 2B Worker Runtime Specification.md`,
   `docs/Task Queue Stage 2C Claim Loop and Worker CLI Specification.md`, and
   `docs/Task Queue Stage 2D Consumer Testing Specification.md` — completed APIs Stage 3 must
   compose rather than replace.
8. `docs/Task Queue Test & Benchmark Harness.md` and
   `docs/Task Queue Growth, Topology & Live Visibility.md` §4 — evidence requirements and the
   deferred R2-16 read-model home.
9. `TASKS.md` and `docs/Task Queue Build Plan.md` — claimed boundary and future slices.
10. Only then inspect current source, tests, packaging, and CI for feasibility and boundary claims.

Tier 0 wins every conflict, followed by accepted ADRs. The Function Manifest wins for 0.1 SQL
specifics. If two authorities cannot both be satisfied, identify the exact passages as a Contract
question and stop short of recommending a code workaround.

## Pinned review boundary

The Stage-2 baseline is commit `60fda06`. Review the design and contract work through commit
`a60c8a2`, including:

- `e384da1` / `f6c4137` — S3-CQ-01 and ADR-014 / Protocol 1.0.1 worker presence;
- `aeded3e` / `6899e09` — S3-CQ-02 and ADR-015 / Protocol 1.0.2 queue-detail deferral;
- `30cb436` / `ea93501` — S3-CQ-03 and ADR-016 / Protocol 1.0.3 final HTTP normalization; and
- `a60c8a2` — the frozen Stage-3 specification.

The request packet itself adds no design decision. Confirm with history and diffs that migrations
0001–0003, the Function Manifest, all Stage-2 runtime source, and Tier-4 history were unchanged by
this range. Confirm there is no `taskq.http` implementation, OutLabs adapter, FastAPI runtime, or
Stage-4 host code hidden elsewhere.

## Review set

Audit these documents and current implementation surfaces as one system:

- `docs/Task Queue Transport Protocol v1.md`
- `docs/Task Queue 0.1 Function Manifest.md`
- `docs/adr/ADR-005-transport-parity.md`
- `docs/adr/ADR-006-permission-grammar-authoritative-lookup.md`
- `docs/adr/ADR-008-fastapi-lifespan-process-model.md`
- `docs/adr/ADR-010-db-roles-security-definer-maintenance.md`
- `docs/adr/ADR-011-housekeeper-role-credentials.md`
- `docs/adr/ADR-014-http-worker-presence.md`
- `docs/adr/ADR-015-defer-queue-profile-read.md`
- `docs/adr/ADR-016-final-http-wire-normalization.md`
- `docs/Task Queue Authorization & Queue Permissions.md`
- `docs/Task Queue Stage 3 FastAPI and Authorization Specification.md`
- `docs/Task Queue Test & Benchmark Harness.md`
- `src/taskq/protocol.py`, `errors.py`, `transport.py`, `client.py`, `registry.py`, `execution.py`,
  `worker.py`, and `settings.py`
- `src/taskq/sql/manifest.py`, `transport.py`, `notifications.py`, migrations 0001–0003, and the
  installer/verifier
- Stage-2 unit, SQL, worker, lifecycle, packaging, and protocol-parity tests relevant to the
  claimed composition boundary
- `scripts/artifact_smoke.py`, `.github/workflows/ci.yml`, `pyproject.toml`, and `uv.lock`

Where locally available, inspect the supported installed or sibling OutLabs authorization source
read-only to validate names, signatures, validator behavior, permission checker semantics, service
tokens, API-key grant policy, seeding, and role services. Do not modify that project and do not treat
an unavailable external checkout as proof; distinguish source-verified from specification-assumed
claims.

## Required audit program

### 1. Contract governance and the three Stage-3 amendments

Independently reproduce S3-CQ-01..03 from the pre-amendment contracts and catalog. Do not accept the
board's descriptions as evidence.

For ADR-014, confirm the SQL function already existed, the route/action/outcome mapping is complete,
all declared queues are authorized before one call, worker label remains advisory, authenticated
subject remains the facade actor, shared-fleet drain observation is benign, and presence cannot
extend a job lease or carry a fence.

For ADR-015, confirm queue detail had neither an observer projection nor safe base-table grant and
that Manifest seniority makes the Protocol row the drafting error. Verify the route is visibly
deferred out of H-13, H-11 points to Growth §4/R2-16, and stats versus admin ensure is an honest 0.1
posture rather than a mutating GET or operator-pool fallback.

For ADR-016, confirm the request-id grammar/mint/echo/persistence rule is complete, queue ensure's
actual SQL result has no version, premature `If-Match` is typed `TQ501`, and raw worker presence
contains fields unsafe for an unbounded HTTP projection. Evaluate the reusable distinction:
undesigned/no-backing queue detail is deferred out, while settled worker-list semantics remain
declared with no success schema behind `TQ501`.

Derive the final amendment sequence 1–10 and active/gated/deferred command catalog yourself. Search
the entire adopted base for any remaining “open,” “proposed,” or unbacked wire claim that S3 would
still have to invent. A missed ambiguity here is a blocking finding or Contract question.

### 2. H-13 generation and capability protocols

Derive every active HTTP method/path, SQL/view backing, action, queue source, request/result model,
outcome, status, error, replay rule, and capability role from Tier 0. Compare that derivation with
the specification's active/gated/excluded table. Explicitly account for meta, queue ensure, single
and batch enqueue, claim, both heartbeat classes, every fenced settle, job detail/stats/metrics,
every Protocol-defined operator command, the gated worker list, and all deferred/internal commands.

Determine whether one Python source can drive route registration, OpenAPI, sync/async clients, and
SQL/HTTP vectors without making the Python table a second human-owned protocol. Require an
independent Tier-0-derived catalog oracle and deterministic generation-drift evidence.

Audit the proposed `ProducerTransport`, `RunnerTransport`, `ObserverTransport`,
`OperatorTransport`, and `HousekeeperTransport` split against current method use. Confirm it can
narrow `TaskQ` and workers without breaking existing structural callers, duplicating close
ownership, or making HTTP clients implement fake DB-only methods. Identify any method missing from
the minimum remote-worker path.

### 3. Wire models, fences, clients, and retry semantics

Build an exact matrix for common envelopes, protocol/request headers, request-id invalid/absent
behavior, AUTH/TQ errors, expected race outcomes, H-09 sizes, explicit nulls, inactive fields,
direction-aware extras, and response status. Challenge FastAPI's default validation paths: no native
422 body, traceback, SQLSTATE, or dependency exception may escape the Protocol envelope.

Trace the attempt fence from SQL claim composite through the dedicated HTTP claim response and back
through heartbeat/settlement requests. Confirm it is available where required but absent from URLs,
read models, OpenAPI examples, repr, validation errors, logs, metrics, traces, and every other
serialization path. Look for a conflict with the current core `ClaimedJob` field exclusion.

For both clients, audit owned/borrowed HTTPX lifecycle, credential redaction, sync thread/process
safety, timeout composition, `/meta` compatibility, protocol-header echo, request-id propagation,
and exact result normalization. Challenge every automatic retry against Protocol §5, especially
non-keyed/mixed bulk, unknown claim response, committed settlement response loss, TQ429/500/503,
and cancellation. No retry may mint a new idempotency key, switch settle verb, or rerun a handler.

### 4. Authorization and credential separation

Independently derive the five-action route matrix and queue source. Verify authentication precedes
detailed validation/lookup; request-id handling must still produce an envelope without letting an
invalid correlation header bypass auth ordering.

For each job-id route, prove the only authorization queue comes from
`get_authorization_projection(job_id)`, missing is handled before authorization, caller queue/type
can only be an assertion on aliases, and the final mutation rechecks its fence. Test default 403 and
optional existence-hiding 404 without leaking projection or fence data.

Audit bulk and worker-presence all-queue preflight, global reads/controls, actor derivation,
advisory worker label, request-local caching, and queue-blind simple adapters. Determine whether the
ordinary facade pool can remain producer+runner+observer+housekeeper while every operator route uses
only the separate observer+operator pool. No missing operator configuration may widen or fall back.

### 5. Long-poll and disconnect state machine

Model claim long polling under notification-before-subscribe, notification-after-empty,
coalescing, missed hints, future-due work, listener loss/reconnect, timeout, client disconnect, and
runtime shutdown. Confirm one dedicated LISTEN connection per process and zero request-held
connections/transactions during waits.

The check/subscribe generation protocol must close lost wakeups without trusting notification
payloads. A disconnect before a claim result may be unknown; neither server nor client may invent a
fence or settle. A committed claim response that is lost must recover by lease. Shutdown must reject
new waits, wake/drain all subscribers, and close listener/pools only after waiter ownership is clear.
Require deterministic barriers for both winner orders and resource-ledger evidence.

### 6. Runtime, housekeeper, embedded worker, and R2-11

Audit construction/start/stop/context-manager ownership as a state machine. Check host startup →
taskq startup → taskq shutdown → host shutdown ordering, both startup-failure directions, app-state
restore, concurrent stop, outer cancellation with re-raise, and borrowed-resource non-closure.

Verify the housekeeper uses only the housekeeper credential, approximately five-second monotonic
jitter, no request transaction, no public HTTP tick, and honest degraded/fatal recovery. Verify
readiness fails on incompatible schema, required listener loss, housekeeper failure, or embedded
presence loss, but not backlog.

For embedded mode, prove default-off plus explicit process-multiplication acknowledgement, separate
worker pool/listener, ordinary WorkerService semantics, and no new handler/settlement state machine.
Derive deployment-wide connection and concurrency arithmetic including every dedicated listener,
unknown inputs, reserves, ceiling refusal, and ASGI grace warning.

R2-11 remains mandatory: a live synchronous handler can never be released or reported stopped while
its thread may side-effect. Confirm Stage-3 lifespan/ASGI cancellation and HTTP transport do not
weaken the Stage-2 external-cancellation, settlement-shield, heartbeat-through-settlement, or
process-exit contracts.

### 7. OutLabs adapter and provisioning reality

Validate the exact real-package API needed for lazy checker creation, explicit any-of semantics,
session dependency, principal result, permission validator, service-token embedded scopes,
API-key prefix policies, system-record seeding, and role services. Flag any design that relies on a
private API, obsolete signature, optional backing service, or alpha-version behavior outside the
pinned range.

Test the permission grammar and counts: five global plus five per distinct queue, no configurable
namespace, real validator, global fallback, per-queue denial, global routes, legacy candidates, and
concurrent cache creation. Include an `emails` run principal denied on `tools`, lied-queue settle,
shared fleet label, service-token no-row path, system-key policy guidance, and personal-worker
denial.

Audit provisioning report/apply/reconcile semantics, role grant exactness, second-run idempotency,
conflict handling, transaction ownership/rollback, CLI lazy import, and secret-free output. The
ensure-plus-provision composition crosses SQL and IAM boundaries; require an honest partial-failure
report rather than a false atomicity claim.

### 8. Test validity, packaging, CI, and scope

Determine whether the S3-01..04/AUDIT matrix is sufficient and executable. Require T6 vectors to
derive independently from Tier 0, run through real SQL and a live ASGI facade, and compare normalized
outcomes plus durable state—not just call mocks or shared adapters that can repeat one bug.

Audit source and wheel/sdist isolation across Python 3.12/3.13 and core/HTTP/OutLabs combinations.
The installed artifact must contain generated metadata and clients without importing optional
modules through core. Review proposed resource ledgers for tasks, exceptions, threads, pools,
HTTP clients, listeners, subscribers, app state, and subprocesses.

Assess B9 as the actual client→ASGI→SQL path and B11 as embedded request-latency/resource overhead.
They are report-only until reviewed baselines; reject environmental noise presented as improvement.
Confirm the planned CI collects PG16/PG18 parity, Stage-3 race families, generation drift, artifact
matrix, and the existing million-row plan gate.

Finally, confirm exact scope: no SQL/migration/function/grant change, no active deferred read model,
no 0.2/0.3 capability, no Tier-4 edit, and no Stage-4 host adoption. Identify any acceptance row that
cannot prove its corresponding broad claim.

## Evidence available, not assumptions

- PostgreSQL 18.3: 366 regular tests green; one opt-in plan test skipped in the regular run; the
  two-test million-row plan gate was previously green.
- PostgreSQL 16.14: the identical 366 regular tests were reported green at Stage-2 completion; its
  permanent CI lane remains required.
- Clean Python 3.13 no-database lane: 219 regular tests reported green at Stage-2 completion.
- Wheel/sdist × core/HTTP/OutLabs artifact smokes and source import-isolation lanes are present in CI.
- Ruff is clean after each S3-00 docs commit.
- No Stage-3 implementation is claimed; these results prove the unchanged baseline, not the future
  design.

Re-run current tests and devise read-only/catalog counterexamples where useful. The standard scratch
DSN is `postgresql://postgres:postgres@localhost:5432/taskq_stage1_test`; migrations create
cluster-wide `taskq_*` roles, so use only an isolated development PostgreSQL cluster.

## Required response shape

Return one self-contained `docs/design-review-5/RESPONSE.md` with:

1. **Verdict:** `PASS`, `PASS WITH FINDINGS`, or `BLOCKED`, plus whether S3-01 may open.
2. **Findings:** severity-ordered (`BLOCKER`, `HIGH`, `MEDIUM`, `LOW`) as `R5-01`, `R5-02`, and so
   on. Each includes exact file/line evidence, violated authority/invariant, impact, a falsifiable
   counterexample or construction trace, and the smallest contract-correct remediation plus test.
3. **Contract questions:** separate from design defects; cite the irreconcilable Tier-0/ADR passages
   and do not recommend a code workaround.
4. **Final Protocol-v1.0.3 matrix:** amendment sequence, active/gated/deferred/internal commands,
   backing, action/queue source, outcomes/status/errors/replay, request-id behavior, queue ensure,
   worker presence/list, and H-13 generation disposition.
5. **Authorization and credential matrix:** every route family, authoritative lookup, actor/label,
   bulk/presence preflight, simple/OutLabs adapters, ordinary/operator/housekeeper capability split,
   and denial behavior.
6. **Client/long-poll/runtime acceptance matrix:** sync/async ownership and retries, fence flow,
   long-poll races/disconnect, lifespan failures/cancellation, housekeeper, embedded worker, process
   budgets, readiness, R2-11, and resource cleanup—each with authority/current feasibility or gap.
7. **OutLabs reality assessment:** locally verified package version/APIs versus unverified
   assumptions, permission/catalog behavior, provisioning/CLI semantics, and import isolation.
8. **Boundary and release-gate assessment:** test-oracle independence, PG/packaging/CI plan,
   benchmark honesty, absence of implementation/scope creep, residual risks, and exact preconditions
   for opening S3-01.

If a category has no findings, say so explicitly. Prefer a minimal executable/design
counterexample over a general concern. Do not approve implementation merely because each component
is individually plausible; the gate is whether the whole design is mutually consistent,
implementable, and falsifiable.
