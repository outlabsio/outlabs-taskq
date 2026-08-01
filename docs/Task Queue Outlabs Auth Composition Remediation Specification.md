# Task Queue × Outlabs Auth Composition Remediation Specification

> **Tier 3 — implementation remediation, 2026-07-28.** This document owns the
> package-side correction required before a high-volume API-key fleet adopts the
> packaged Outlabs authorizer. It changes no SQL function, migration, wire
> command, permission grammar, or queue state-machine rule.
>
> **Status:** closed. Auth a26 and TaskQ a13 are immutably published and the
> TaskQ lock resolves the exact public Auth artifacts. The paired auth-library plan is
> `../../outlabsAuth/docs/TASKQ_AUTHENTICATED_CONTEXT_PLAN.md`.
> Exact identities are published `outlabs-auth==0.1.0a26` and
> `outlabs-taskq==0.1.0a13`.

## 1. Confirmed defect

The Stage 3 contract requires one authentication followed by one or more queue
checks against the same `AuthContext`. The shipped `OutlabsQueueAuthorizer`
correctly creates that context, but `authorize_context(...)` then enters
`auth.deps.require_permission(...)` for every `(action, queue)` pair.

For a system-integration API key:

1. `authenticate(...)` verifies the key through the database-backed API-key
   strategy and records one usage/rate-limit event.
2. The first cold permission check reuses the request-state authentication
   result and may create an auth snapshot.
3. On later warm requests, every permission check uses the snapshot path, which
   records another usage/rate-limit event.

A request authorizing `N` queues can therefore record `1 + N` uses. Worker
presence, workflows and cross-queue follow-ups intentionally authorize several
queues, so this is a real fleet-path multiplier. A sustained 429 is not merely a
latency problem: it can deny heartbeat or settlement long enough for lease loss
and re-execution.

## 2. Corrections to the initial review

- The adapter creates a second SQLAlchemy session scope, but a warm
  snapshot-only or service-token check does not necessarily check out a
  database connection. Session construction is lazy. Pool impact must be
  measured, not inferred from scope count.
- A cold snapshot miss does not necessarily re-run the authentication backend:
  Outlabs Auth may reuse `request.state._outlabs_auth_result`. It can still
  perform database-backed permission resolution for principal types that need
  it.
- TaskQ runtime pools are separate asyncpg pools. A host's SQLAlchemy auth/domain
  pool and TaskQ's request/housekeeper/listener capacity must be budgeted
  separately, then checked together against the PostgreSQL cluster ceiling.
- The released `outlabs-taskq==0.1.0a12` retains its exact
  `outlabs-auth==0.1.0a24` extra pin. The candidate moves that pin exactly to
  `outlabs-auth==0.1.0a26`; it does not introduce a range.

## 3. Ownership boundary

### Outlabs Auth owns

- authorizing an already-authenticated result without recording authentication
  usage again;
- user-owner, integration-principal, key-scope, entity-tree, ABAC, revocation
  and snapshot-version semantics;
- the definition of once-per-request API-key usage and rate-limit accounting;
- typed 401/403/429/503 behavior.

### TaskQ owns

- mapping `(TaskqAction, canonical queue)` to the fixed candidate permission
  names;
- passing the opaque authenticated principal to the supported Outlabs Auth
  context-authorization surface;
- preventing a second generic credential-authentication pass;
- retaining authoritative queue lookup, denial hiding and actor/fingerprint
  behavior.

### TaskQ must not

- inspect `metadata.scopes` and treat those strings as sufficient authority;
- reimplement Outlabs Auth owner, principal, entity, ABAC, wildcard, revocation
  or cache-invalidation rules;
- change queue permission grammar to work around a host policy;
- weaken API-key rate-limit failure behavior.

## 4. Required package behavior

The implementation may choose the final public API name, but the behavior is
fixed:

1. `authenticate(request)` returns one opaque context and records no more than
   one successful API-key usage event for that HTTP request.
2. `authorize_context(request, context, action, queue)` authorizes the supplied
   context. It never authenticates credentials again and never records another
   usage event.
3. Repeated checks for the same candidate tuple may be request-memoized.
4. Service-token and non-ABAC integration-principal checks remain in-process
   when their authenticated result contains everything Outlabs Auth requires.
5. User-owned keys, entity checks and ABAC may still use a host session; the
   auth library, not TaskQ, decides that.
6. A principal mismatch or changed authenticated subject remains a 401. A
   permission denial remains a 403. Rate-limit and auth-infrastructure failures
   retain the adapter's bounded 429/503 envelopes.

## 5. Implementation sequence

### OA-CTX-01 — Outlabs Auth contract and tests

Specify and test the supported operation for authorizing an existing
authentication result. Freeze once-per-request usage accounting and the
credential/policy matrix before source changes.

### OA-CTX-02 — Outlabs Auth implementation and release

Refactor `require_permission(...)` and/or expose the supported context operation
without changing ordinary one-dependency route behavior. Publish only after the
full auth suite and installed-artifact checks pass.

**Published in immutable a26:** Auth exposes
`authorize_authenticated(...)` and the auth-owned
`authenticated_authorization_requires_session(...)` decision. The tag resolves
to `0ba4065d50003e6723059dbedeb8f72980187f88`; independently downloaded wheel
and sdist hashes are recorded in the
[G8 evidence](./stage-6/G8%20Authenticated%20Context%20Release%20Evidence.md).

### TQ-OA-01 — Adapter adoption

Replace the adapter's second generic `require_permission(...)` entry with the
supported context operation. Remove the second session scope where the auth API
does not require one; do not add TaskQ-local permission evaluation.

**Published in immutable a13:** the adapter carries the exact auth-owned result inside
its request-bound principal, passes canonical any-of candidates to Auth, and
opens a policy session only when Auth requires it. TaskQ does not inspect raw
scopes.

### TQ-OA-AUDIT — Joint acceptance

Pin TaskQ's `[outlabs]` extra to the accepted auth release, run the joint matrix,
build wheel and sdist artifacts, and record latency/counter evidence. Host
adoption remains blocked until this closes.

**Closed:** the dependency is exactly `0.1.0a26`; the
Git source override is deleted and the lock resolves PyPI wheel
`c47e1100…dd8f` and sdist `43528893…0808`. TaskQ a13 passed exact-head and
exact-merge dual-major CI plus installed-artifact acceptance, and its immutable
GitHub release assets were independently re-downloaded byte-identically. Exact
source, CI and artifact identities are recorded in the
[G8 evidence](./stage-6/G8%20Authenticated%20Context%20Release%20Evidence.md).

## 6. Acceptance matrix

The joint gate must prove all of the following:

1. Cold system API key: one request, one usage increment.
2. Warm system API key: one request, one usage increment.
3. Five-queue worker presence: one authentication, five authorizations, one
   usage increment.
4. Workflow and cross-queue follow-up preflight: all queues authorized before
   SQL, one usage increment.
5. A denied queue changes no TaskQ state and does not add an authentication
   usage event beyond the request's one authentication.
6. Principal/user-owned API keys retain owner permission and scope narrowing.
7. Integration-principal keys retain both key-scope and principal-envelope
   narrowing.
8. Entity-tree and ABAC checks retain their current allow/deny behavior.
9. Service-token workers authorize while Redis is unavailable.
10. With Redis configured fail-closed, API-key workers receive bounded 503
    behavior when distributed quota enforcement is unavailable.
11. Permission/principal/key revocation and snapshot-version invalidation remain
    effective.
12. A warm service-token or integration-principal queue check performs zero
    TaskQ-auth SQL queries; any session/checkout claim is proven by
    instrumentation.
13. Production-shaped load uses measured fleet concurrency and command cadence,
    not an arbitrary worker count. The gate is an end-to-end latency/error
    budget, not “auth p99 must be lower than SQL p99.”
14. Before any API-key fleet adopts native workflow continuations, one
    continuation-specific vector authenticates once, authorizes the
    authoritative parent queue plus every distinct declared child queue,
    records one usage increment, and performs zero TaskQ writes when any child
    queue is denied. This row is dormant until a continuation contract exists;
    it is not permission to pre-implement that contract.

## 7. Host adoption rule

No host using system-integration API keys may place the packaged
`OutlabsQueueAuthorizer` on a production worker hot path until `OA-CTX-02` and
`TQ-OA-AUDIT` are accepted. A host may instead use a reviewed self-contained
service-token authorizer, but that does not waive its credential rotation,
revocation, least-privilege or load-test obligations.

The Native Workflow Continuations design depends on this gate only for
API-key-backed adoption. TaskQ Core continuation semantics remain authentication
provider neutral and may not import or require Outlabs Auth.
