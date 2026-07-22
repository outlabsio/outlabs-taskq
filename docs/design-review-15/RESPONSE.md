# Internal targeted review response — QDarte C6 local cutover completion

## Verdict

**READY.** The complete C6-01..04 local contact-verify cutover range is
accepted. This is an **internal, non-independent review** performed by the
implementation session with the owner's explicit authorization because the
usual separate review session was unavailable. The evidence below was still
re-derived from source and live read-only database observations rather than
accepted from the implementation records.

READY opens only **C7-00 environment planning**. It authorizes no production
mutation, package cohort, worker or provider run, deployment, direct-queue
retirement, non-contact lane, C7-01 or later work, or Stage 6.

## Exact identities

- taskq review/request tip: `42138bcea0b7414106c303b242dbeec535e2aa2d`
- immutable taskq release: `v0.1.0a6`, peeling to
  `c2f6827`; wheel SHA-256
  `a731a6dcf4cd80b94742fca1d2203e09fab2b96c4e002273d90ded29e50d5419`
- taskq SQL metadata in `qdarte_contact_verify_dev`: contract `0.1.5`, exact
  active capabilities `admission_reservations` and
  `read_model_list_ready`
- QDarte API branch tip:
  `7a744582b0d824a559aa29dfaf03ef1081058064`
- QDarte workers branch tip:
  `21bd880d5f2688f04cf323326512e6b630073d70`

The release asset was downloaded again in the preceding admission review;
its version, migration ledger `0001` through `0007`, tag identity, and wheel
hash matched the QDarte pins exactly. The C6-04 evidence commit changes only
the new evidence document; the accepted C6 source remains at the reviewed
implementation tips.

## Derived source findings

### Closed dispatch and lifecycle

The contact lane has its own exact `QDARTE_CONTACT_VERIFY_MODE` selector,
independent of the incumbent `QDARTE_TASKQ_*` selector. Its only accepted
values are `legacy`, `draining`, and `package`; invalid values and a mixed
non-legacy/incumbent-contact configuration fail startup.

The retained cutover URL samples one effective mode and selects exactly one
behavior. `draining` returns the fixed refusal before either producer is
constructed. `legacy` calls only the direct admission service. `package`
requires the same-process controller and calls only the package adapter. A
controller, transport, package, or typed admission failure maps to the fixed
sanitized 503 and never enters the direct branch.

A process requested as `package` begins internally as `draining`. Before the
application serves requests, its lifespan constructs the controller, performs
the drain proof in that process, and changes the effective mode to `package`
only after success. Failure leaves no callable package producer.

### Direct-drain interlock

The interlock reads only the incumbent `qdarte_ops` contact job, attempt, and
event ledgers. It reads no taskq package table and no job payload. It requires
two observations between 1 and 60 seconds apart, the same development
database instance, no queued/blocked/running work, no running attempt, and an
equal status/count/high-water continuity key.

The resulting handle is random, process-local, held only in the controller's
private in-memory registry, and cannot be serialized or supplied through a
route or setting. Its TTL is positive and capped at five minutes. Immediately
before every package reservation, the controller re-observes the direct
ledger and rejects plus evicts authorization on expiry, identity/mode/source
change, active work, a new direct row, or continuity drift.

### Admission semantics

The package adapter derives its canonical idempotency key and versioned
pre-plan intent SHA-256 before invoking the planner. It mints one handle, then
reserves before planning. An already-admitted result returns the stored job id
and the bounded immutable `planned_entities` receipt without calling the
planner. `pending` refuses; only `reserved` invokes the planner and then the
single finish operation. The official client omits null request fields in
accordance with the frozen literal-JSONB identity rule.

The retained URL exposes one backend-neutral response containing job id,
`created | existed`, canonical key, and planned count. There is no host map,
active-row import, package-row copy, lookup/enqueue race, copied package route,
or cross-backend fallback.

## Rollback and raw-state findings

### Before package publish

The explicit legacy posture prepared with zero package observations. It
created no package row and left the package database at its retained 12-job
history. No direct diagnostic row was invented merely to prove routing.

### After publish, before external work

No package worker or broad QDarte worker was running. The operator used the
typed SQL transport—not manual table DML—to pause `qdarte_contact_verify` and
cancel the two unclaimed zero-attempt jobs:

- `019f8781-0a42-7630-b7fb-a469886eabd6`
- `019f89f4-b4d0-760c-a513-1c76ed6fbf9a`

Live read-only reinspection found the queue paused with reason `package
admission stopped before external work`, zero queued/running contact jobs,
and exactly two cancelled jobs. Each job has a `cancelled` event from actor
`c6-04-local-rollback` with reason `local rollback before external work`.
The repeat typed-control results recorded by the exercise were
`already_paused` and `already_terminal/cancelled`.

The durable admission for `contact_verify_scope:country:AR` remains
`admitted`, links the same cancelled job
`019f89f4-b4d0-760c-a513-1c76ed6fbf9a`, and retains exactly
`{"planned_entities": 1}`. No row was deleted, rewritten, copied, or recreated
in the direct ledger.

### After external work

The retained hard-kill history for
`019f8701-e698-7c80-8671-578971bd6f76` is still `succeeded` with two attempts,
one failure, and zero releases. Its raw attempts are exactly one
`expired/lease_expired` followed by one `succeeded/success`; its event counts
are two claims, one enqueue, one lease expiry, and one success.

The authorized canonical observer read recorded by the exercise omitted
payload, result, and error while returning the safe status/counter projection.
The stable QDarte effect ledger has exactly one application for this job and
entity. The corresponding contact-method effect remains singular, and the
bounded usage counter is still 3. No provider call or usage unit was consumed
by the rollback inspection.

### Conservation oracles

The live databases reproduced the recorded counts and canonical in-database
full-row SHA-256 values:

| Oracle | Count | SHA-256 |
| --- | ---: | --- |
| direct contact jobs | 5 | `4b4d918a5b3a309b8782b8e38105d3c8b8719b253f1be46ab378f82bdf407664` |
| direct contact attempts | 5 | `70f9849783c79e401cb7cf3088460b993c94ef9d0b016df6b4dadad5a7d3b553` |
| direct contact events | 20 | `9b8963c6a790b7211737aa030e9ee381a339734fda4ec44566504e5333b470ee` |
| stable result applications | 3 | `dc63c884f1680dfdc0fd07eb4f99abf7fc11aa7834e281c9e7d8fda0a4772916` |
| place contact methods | 484 | `f1ea52841455311f167a931f63d92f67023033423a39a437dd46aa3c3127e197` |
| discovery usage counters | 1 | `83dc6ca18d925156ce26143a7038fc1e7213954bec79cd992f44d33e8136e14d` |

The container inventory has no package/contact worker or broad worker, and
`taskq.workers` has no worker heartbeat within the last two minutes.

## Regression evidence

Reproduced at the exact QDarte tips:

- QDarte API focused C6 boundary suite: **62 passed**
- QDarte API Ruff: clean
- QDarte API format check: clean
- QDarte API targeted MyPy: clean
- QDarte workers focused package/pilot suite: **73 passed**
- QDarte workers Ruff: clean

No C7 implementation, production configuration, retirement, non-contact
producer, provider expansion, or Stage-6 source was added by the reviewed C6
range.

## Contract questions

None. The reviewed implementation conforms to Protocol v1 revision 1.0.8,
Function Manifest / SQL contract 0.1.5, ADR-020, ADR-022, ADR-023, and the
frozen C6 specification.

## Scope opened

READY opens only a docs-first **C7-00 environment plan** naming the target,
topology, credential ownership, connection arithmetic, backup/restore target,
and direct-lane baseline. Any production mutation, preflight, package publish,
worker/provider run, direct retirement, non-contact lane, C7-01+, or Stage 6
still requires its later explicit gate.
