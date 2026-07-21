# taskq — Stage 5 QDarte contact-verify isolated-local implementation specification

> **Status:** Tier-3 implementation specification. It sequences the accepted
> direct-contact convergence proposal through isolated local evidence. It does
> not authorize a production change, direct-queue retirement, a cloud target,
> a broad worker, or a non-contact lane.
>
> **Authority:** subordinate to the Transport Protocol v1, Function Manifest
> 0.1.4, ADR-006, ADR-007, ADR-010, ADR-011, ADR-020, the Stage 5 QDarte
> Contact Verify Consolidation Specification, and the Round-12 delta response.
> The consolidation specification owns the destination and C1–C7; this
> document owns only the local implementation order and its acceptance proof.

## 1. Objective and non-goals

Prove that one QDarte `contact_verify_scope` job can run through the package
queue in an isolated local environment while preserving a single, independently
idempotent QDarte domain result. The incumbent direct queue remains the only
authoritative lane until later C6/C7 evidence accepts a separately specified
cutover.

This work does not change `qdarteapi_dev.taskq`, its copied routes, the
`qdarte_ops` ledger, a public producer, or any production database. It does
not run a broad worker; its one package worker is permanently allowlisted to
the package contact-verify type. It does not use dual publication, row import,
or automatic fallback after an ambiguous enqueue, result, or settlement.

## 2. Fixed local topology

The only mutable queue database is `qdarte_contact_verify_dev`, a new
disposable package-owned database on the guarded local PostgreSQL cluster. It
has the immutable package `taskq` schema and is distinct from both
`qdarteapi_dev` and `qdarte_pilot_dev`.

The one package queue is `qdarte_contact_verify`; its one permitted type is
`qdarte.contact_verify.scope`. These names are package-only and must never be
registered in QDarte's incumbent direct client, direct worker map, or copied
`/worker/taskq/*` surface.

```text
isolated QDarte API
  ├─ normal QDarte domain/auth database access
  └─ dedicated package facade runtime ──> disposable package contact database
                                      ^
closed package contact worker ── HTTP ──┘
```

The facade pool is a dedicated non-superuser package runtime identity with
only producer, runner, observer, and housekeeper capability memberships. The
worker holds an ephemeral `run` credential and no package database password.
The result service uses only server-owned runner and observer transports. An
owner/admin and an operator credential exist only for explicit local migration,
verification, and profile provisioning commands; neither joins an API or
worker pool.

The package Protocol facade is available only inside the checked-in local
contact harness. The normal QDarte application mounts neither `/taskq` nor a
generic package producer route. The harness has one unlisted local result
adapter, `POST /internal/taskq/contact-verify/jobs/{job_id}/results`, and its
authorizer admits only the exact contact queue scopes. Its reporter-only body
has exactly two operations: `inspect` first validates the current package
attempt and authoritative planned entity, then returns either the already
committed stable domain effect or `pending`; `apply` repeats that validation
and performs the existing single stable effect application. The reporter, not
the handler, supplies the active attempt identity; the handler may supply only
the bounded entity/effect request through ADR-022's fence-free capability. No
operation settles a package job, and neither mounts a generic worker-fence or
package-producer endpoint. Before CV-04, no
enqueue credential is issued at all; CV-04 alone may issue a process-local,
short-lived harness enqueue credential for its one controlled canary. This is
not a public producer or a replacement for the incumbent direct worker API.

## 3. Ordered slices and stop conditions

### CV-01 — C1 compatibility inventory and direct URL contract

Before any QDarte source change, record the current caller, direct producer,
direct result route, worker model, direct catalog, direct data high-water, and
source-versus-live role/grant posture. Freeze the effective base URL matrix:

| Setting form | Claim request | Result request |
| --- | --- | --- |
| Direct API origin | `/worker/taskq/jobs/claim` | `/worker/taskq/jobs/{job_id}/contact-verify-results` |
| Admin proxy ending `/content-api` | `/content-api/worker/taskq/jobs/claim` | `/content-api/worker/taskq/jobs/{job_id}/contact-verify-results` |

An authenticated claim plus result vector is required for each supported
topology. The source or its stale tests are changed only after the vector
identifies the canonical joined path. Stop if the public caller contract,
planned-payload model, or deployed grant posture differs from the consolidation
specification; record a docs-first question rather than adapting it silently.

### CV-02 — host result idempotency before package admission

Implement the stable application-effect identity as package job id plus the
authoritative planned entity identity. It must be independent of attempt id,
worker id, provider retry, and response delivery. Within one QDarte domain
transaction it protects the place/contact writes and the monthly usage update.

The package result endpoint performs this fixed sequence:

1. authenticate the caller as `run` for the one package queue;
2. use the server-owned runner transport to heartbeat the supplied job,
   attempt, and worker identities;
3. use a server-owned observer transport to load the authoritative package
   projection and compare queue, type, entity, and place identity;
4. apply or observe the stable idempotent QDarte effect; and
5. return a bounded result to the worker, which settles through its own replay
   policy.

No request echo is an authority source. A failed/lost heartbeat, terminal or
missing job, old reclaimed attempt, or projection mismatch performs no domain
write. A domain commit followed by a lost HTTP response is retried through the
same effect identity. Tests must prove wrong fence, wrong entity, old reclaimed
attempt, same-job retry, and commit-then-lost-response behavior with exact
domain/usage row oracles.

CV-02 delivers this bridge as a server-owned, injectable host component plus
the durable QDarte application ledger. CV-03 alone binds it to the newly
provisioned package runtime and mounts the local-only endpoint; no package
database access, worker process, or public route is implied by CV-02.

### CV-03 — isolated package database and facade preflight

Only after CV-01/CV-02 pass, create the disposable package contact database.
Install the approved immutable package release, migrate and `verify()` twice,
and provision one queue through the operator identity. Prove the runtime
identity cannot assume operator, call queue administration, read package base
tables, create roles/databases, or bypass RLS. Recompute the API/worker/listener
connection budget against the measured local ceiling.

The disabled configuration must mount no package route and open no package
connection. Enabled local configuration may expose only the package contact
adapter and its internal/local harness; it must not expose a generic enqueue
route from the normal QDarte application or copy the incumbent direct worker
API. The harness's package endpoint is private to the local evidence process
and remains enqueue-denied until CV-04 issues its one ephemeral credential.

### CV-04 — closed worker and controlled effect canary

Register exactly one package task type and allow it only on the package contact
queue. The worker uses the package HTTP path, a run-only ephemeral credential,
and a fixed one-item allowlist that no setup or cleanup operation can widen.
It installs only ADR-022's trusted reporter: for each planned entity the
handler asks the private adapter to inspect the durable result identity before
the provider call, skips the provider when it receives the committed result,
and otherwise applies the provider result through the same adapter. The
reporter receives the active attempt internally and retries an ambiguous
adapter response with the identical request while it still owns that attempt;
it never settles the job. The registry handler receives neither attempt nor
fence and must not call the copied direct worker API or use a package database
credential.
The local harness receives a separate short-lived enqueue credential and a
read-only credential for canonical observation; none persists in QDarte auth
storage or reaches a command line or log.

Run one bounded synthetic or controlled real entity whose provider outcome and
domain effect can be observed without exposing credentials. Require a keyed
`created` then `existed` pair, canonical authorized terminal read, package
attempt/event ledger, exact stable domain/usage oracle, zero direct queue
mutation, and a bounded provider observation. Failure leaves the direct queue
authoritative and stops the worker.

### CV-05 — side-effect recovery and local rollback

First run a committed-result-response-loss drill: one observable provider
invocation, one stable domain application, worker-owned settlement replay, and
one terminal package job. Then interrupt a held result boundary past the
worker grace, terminate the worker, allow the same package job id to reclaim,
and prove the effect/usage oracle remains singular across attempts.

Local rollback before the first package publish is a zero-DML return to
`legacy` mode with proof of no package contact job. After external work,
rollback never recreates a direct job; preserve package history and resolve it
with the effect oracle. CV-05 closes only local side-effect evidence. It does
not meet C6/C7, authorize a production canary, or retire the direct catalog.

## 4. Acceptance ledger

| Slice | Required proof | Opens |
| --- | --- | --- |
| CV-01 | C1 inventory, exact URL matrix, both authenticated direct vectors | CV-02 source work |
| CV-02 | stable effect key and all five bridge regressions | CV-03 local package provisioning |
| CV-03 | disposable package DB, two migrate/verify runs, least-privilege negatives, budget | CV-04 controlled canary |
| CV-04 | one closed worker, keyed pair, canonical read, effect/provider/direct-ledger oracles | CV-05 recovery |
| CV-05 | response-loss and hard-kill same-id reclaim with no duplicate effect | later separately reviewed C6/C7 planning |

Each slice has its own task-board row and commit. Tests, Ruff, and the host
gates applicable to changed source must be green before the commit. A conflict
with a Tier-0 contract stops for docs-first adjudication; a conflict with this
specification stops for a Tier-3 amendment. No later slice is implicitly
opened by a successful earlier one.

## 5. Deferred work

The following remain explicitly out of scope: direct queue drain or
retirement, package production migration, cloud or Mac-mini target, broad
worker start, read models/UI, non-contact or side-effecting sibling lanes,
legacy ledger changes, and Stage 6. Those require C6/C7 evidence and their own
approval after isolated local CV-05 succeeds.
