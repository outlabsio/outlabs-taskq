# taskq — Transport Protocol v1 (canonical)

> **Status:** CANONICAL — accepted 2026-07-18, satisfying ADR-005's Stage-0 exit requirement; amended by ADR-012 for SQL contract 0.1.1, ADR-013 for SQL contract 0.1.2, ADR-014 as additive protocol document revision 1.0.1, ADR-015 as additive protocol document revision 1.0.2, ADR-016 as additive protocol document revision 1.0.3, and ADR-017 as additive protocol document revision **1.0.4**. The wire-major remains `1`. This document + its adopted base define protocol v1 for the 0.1.x contract; every route sketch elsewhere in the doc family is illustrative and yields to this.
> **Adopted base:** [`design-review-2/03-protocol-draft.md`](./design-review-2/03-protocol-draft.md) §2–§6 (wire shapes, command × outcome × HTTP tables, TQ registry, retry/idempotency matrix, version negotiation) are adopted **verbatim** as protocol v1 content, as amended by §2 below. The draft's §1 decisions 1–10 are all **accepted**.
> **Companions:** the exact SQL signatures/composites live in [`Task Queue 0.1 Function Manifest.md`](./Task%20Queue%200.1%20Function%20Manifest.md); authorization semantics in the Authorization doc (ADR-006/011).

## 1. Hole closures (the draft's §7, decided)

| Hole | Decision |
|---|---|
| H-01 typed claim state | **Closed — contract change:** `taskq.claim_jobs` returns the composite `taskq.claim_batch (state text, jobs taskq.claimed_job[])` with `state ∈ claimed | empty | paused | unknown_queue | unavailable` (targeted claim). One atomic call; the facade maps state 1:1 to the draft's outcomes and never infers from an empty set. Batch ≤50 keeps the array cheap. |
| H-02 frozen composites | **Closed by the 0.1 Function Manifest** — named composite types with additive-only evolution (new fields append; removal/rename = contract major). |
| H-03 cross-verb replay | **Closed — the draft's refinement is accepted:** a replay of the SAME verb for an already-settled attempt returns `already_settled`; a DIFFERENT verb for that attempt returns typed `settle_conflict` (HTTP 409, prior verb + terminal status in data, never the fence). This amends the v1.1 any-verb acknowledgement: acknowledging `complete` after `fail` hid client bugs. Applied in the manifest bodies (the attempt-ledger check compares the settled verb). |
| H-04 worker label binding | Accepted as drafted: per-worker credential → server binds label to subject; shared fleet token → label validated, stored, explicitly advisory; principal actor is always the token subject. |
| H-05 bulk convergence | Closed by spec v1.6 §5.2 (one-result-per-input, later-snapshot resolution, `TQ500` atomic rollback) + manifest body. |
| H-06 error envelope + native normalization | Accepted: §4 registry is closed; the manifest enumerates every public raise; facade normalizes any unregistered SQLSTATE to `TQ500` and logs the original privately. |
| H-07 job-detail projection | **Closed — minimal safe projection frozen for 0.1:** `id, queue, job_type, status, outcome, priority, attempt_count, failure_count, max_attempts, created_at, scheduled_at, started_at, finished_at, updated_at` always; `error` (≤2KB), `result` (≤8KB), `progress` (≤2KB) only via explicit `include=` flags gated by queue `read`; `payload` (≤64KB) via `include=payload`, same gate; **never** headers, fences, or worker internals. Redaction hook point reserved per field. |
| H-08 list cursors/indexes | **Deferred out by ADR-017:** 0.1 read surface is get-by-id + per-queue stats; no general list function or successful HTTP list operation exists. Reactivation requires Growth §4 / R2-16 to freeze the observer projection, redaction, authorization, keyset cursor, indexes, and bounded EXPLAIN evidence. |
| H-09 size ceilings | **Closed — published limits (also in `/meta.limits`):** payload ≤64KB, progress ≤2KB, result ≤8KB, stored error ≤2KB, bulk ≤1000 items and ≤4MB body, claim batch ≤50, `wait_seconds` ≤30, job-type filter ≤20 entries, headers ≤8KB. Oversize → `TQ422`. |
| H-10 long-poll lifecycle | Accepted as drafted: disconnect cancels the waiter, never a committed claim; shutdown drains hub subscribers before LISTEN/pool close (tested in T6). |
| H-11 profile read + If-Match | Deferred by ADR-015 (queues are bootstrap/migration-owned in 0.1). Reactivation requires the Growth §4 / R2-16 exact observer projection, redaction, authorization, bounded-plan, and optimistic-concurrency design. |
| H-12 0.2/0.3 commands | Deferred; inactive fields rejected with `TQ501`, never ignored. |
| H-13 single generation source | **Closed:** one Python protocol manifest (models + command table) generates the OpenAPI schema, sync + async HTTP clients, and the SQL/HTTP parity test vectors. Hand-copied route tables are banned — this document and the manifest are the only human-maintained sources. |

## 2. Amendments to the adopted base

1. Wherever the base says claim outcomes are inferred, H-01's typed `claim_batch` is authoritative.
2. The settlement tables gain the `settle_conflict` outcome per H-03 (409, `retryable=false`).
3. The base's §3.6 list-jobs row was initially described as "operator-minimal, pre-H-08" for
   0.1. **Historical note:** ADR-017 / amendment 11 supersedes that phrase; it grants no surface.
4. `GET /taskq/v1/meta` additionally reports the H-09 limits verbatim.
5. **ADR-012 diagnostic exception to H-09:** payload, headers, progress, result, and request/body oversize remain `TQ422`. Persisted job/attempt errors and cancel reasons are instead byte-safely truncated to 2,048 UTF-8 bytes, and event messages to 500 UTF-8 bytes, because diagnostic text must not prevent settlement.
6. **ADR-012 null boundary:** omission alone invokes a SQL default. Explicit `NULL` for an argument whose documented domain is non-null is invalid and raises `TQ422`; optional nullable fields are unchanged.
7. **ADR-013 effective lease projection:** `taskq.claimed_job` appends non-null `lease_seconds` after `step_key`. It is the exact duration selected by `claim_jobs` and used for the job lease and attempt row. Workers schedule `min(lease_seconds/3, 30s)` heartbeats from this duration on a monotonic timer and never derive it by subtracting local wall time from `lease_expires_at`. This is additive under H-02; protocol major remains v1.
8. **ADR-014 HTTP worker presence:** protocol document revision 1.0.1 adds the canonical worker-presence command in §2.1. It is additive, changes no SQL identity or migration, and leaves the wire-major header at `1`.
9. **ADR-015 queue-profile read deferral:** protocol document revision 1.0.2 moves the adopted base's `GET /taskq/v1/queues/{queue}` row from the active 0.1 surface to §2.2. The Function Manifest wins for 0.1 SQL specifics: no safe observer projection exists, so the route cannot be implemented honestly. This changes no SQL identity or migration and leaves the wire-major header at `1`.
10. **ADR-016 final HTTP wire normalization:** protocol document revision 1.0.3 defines `Taskq-Request-Id`, removes the nonexistent 0.1 queue-profile version, and keeps worker list declared behind a typed capability gate as specified in §2.3. This changes no SQL identity or migration and leaves the wire-major header at `1`.
11. **ADR-017 manifest-backed wire corrections:** protocol document revision 1.0.4 defers the
    unbacked general job-list command, removes the unproducible enqueue `created_at`, pins the exact
    enqueue response fields, and completes invalid-request-id behavior as specified in §2.4. This
    changes no SQL identity or migration and leaves the wire-major header at `1`.

### 2.1 Worker presence (document revision 1.0.1)

Canonical route: `POST /taskq/v1/workers/heartbeat`.

The inbound body is strict (`extra=forbid`) and contains:

| Field | Contract |
|---|---|
| `worker_id` | required advisory label, 1–200 characters |
| `queues` | required non-empty list of distinct canonical queue names (`[a-z0-9_]{1,57}`) |
| `hostname` | optional string, at most 200 characters |
| `pid` | optional positive signed 32-bit integer |
| `version` | optional string, at most 200 characters |
| `meta` | optional JSON object, at most 8KB serialized; bounded operational scalars only |

`meta` may describe process mode, concurrency, batch, or listener-effective state. It must never
contain a DSN, credential, payload, headers, progress, result, error, job id, attempt id, or fence.
The whole request remains subject to H-09's 4MB request-body ceiling.

Processing order is normative:

1. authenticate the request and establish its subject;
2. validate the complete strict body and distinct queue list;
3. authorize action `run` against every declared queue, without changing state if any check fails;
4. invoke `taskq.worker_heartbeat(worker_id, queues, hostname, pid, version, meta)` exactly once.

`worker_id` never supplies the actor. The authenticated subject remains authoritative for
authorization, audit, rate limiting, and logs. Under a shared fleet credential, one caller can use
another advisory label and observe that label's targeted shutdown signal. That is benign by design:
draining is always safe and releases are budget-free. Per-worker credentials use H-04 binding when
stronger attribution is required.

| SQL value | Envelope `outcome` | HTTP | `data` |
|---|---|---:|---|
| `shutdown_requested=false` | `continue` | 200 | `{"shutdown_requested": false}` |
| `shutdown_requested=true` | `shutdown_requested` | 200 | `{"shutdown_requested": true}` |

This is **worker presence**, not the per-job heartbeat. It extends no lease, carries no job or
attempt fence, is advisory observability plus drain signalling only, and is never a reclaim input.
The two commands must not be combined.

H-13 includes this route in the single generated command source, OpenAPI, sync and async clients,
conformance vectors, and SQL-versus-HTTP parity vectors. The SQL contract remains 0.1.2.

### 2.2 Deferred routes (document revision 1.0.2)

| Reserved route | 0.1 status | Reactivation gate |
|---|---|---|
| `GET /taskq/v1/queues/{queue}` | deferred; `TQ501` capability inactive; no successful response model | H-11 through Growth §4 / R2-16: exact observer-granted projection, field/redaction contract, queue authorization, bounded query plan, optimistic concurrency where applicable, and a new contract amendment |

This is an explicit correction to the adopted base's §3.1 active row. The Tier-0 Function Manifest
is senior for 0.1 SQL specifics and contains no safe queue-profile projection; the Protocol row was
a drafting error, not evidence that the Manifest should grow a one-off function.

H-13 excludes this deferred command from the active generated command table, OpenAPI operation
set, sync/async client methods, and SQL/HTTP conformance vectors. One negative capability vector
pins `TQ501`; no official consumer can bind to an accidental response shape.

The interim posture is honest: `get_queue_stats` supplies observer-safe operational state, while an
administrator declares a canonical profile through idempotent `PUT /taskq/v1/queues/{queue}` /
`taskq.ensure_queue` and receives that canonical profile in the command response. Neither the
observer credential nor the ordinary facade pool gains base-table or operator access. SQL contract
0.1.2 and migrations 0001–0003 remain unchanged; there is no migration 0004.

### 2.3 Final HTTP wire normalization (document revision 1.0.3)

#### Request correlation

The optional inbound correlation header is `Taskq-Request-Id`. It must be 1–128 ASCII characters
matching `[A-Za-z0-9._:-]+`. When absent, the server generates a lowercase UUID string. An invalid
supplied value is treated as absent for correlation selection: the server mints a lowercase UUID,
authenticates the request, then returns `TQ422`; bad credentials still return the authentication
error. The minted value appears in the envelope and response header, and the invalid supplied bytes
appear nowhere in the response or diagnostics. The selected value is returned both as the JSON
envelope's `request_id` and the response `Taskq-Request-Id` header. It may enter only bounded
diagnostic fields and structured logs and is never persisted unbounded.

#### Queue ensure correction

The adopted base's `PUT /taskq/v1/queues/{queue}` response contains the exact canonical profile
returned by `taskq.ensure_queue` and **no version or ETag** in 0.1. `created` returns 201;
`updated | unchanged` return 200. An `If-Match` header while H-11 is inactive returns `TQ501`; it is
never ignored. H-11's future Growth §4/R2-16 read-model amendment owns version/ETag design.

#### Declared, gated worker list

`GET /taskq/v1/workers` remains in H-13's generated route, OpenAPI, sync/async client, and
conformance surfaces, but 0.1 exposes only the typed `TQ501` capability-inactive response and no
success schema. The command identity and global `read`/`control` authorization are settled; the
Growth §4/R2-16 slice must still freeze a bounded observer projection, redaction, cursor, and query
plan before activation. The facade never forwards raw `worker_status` fields such as hostname, pid,
or arbitrary `meta`. Operators may query that view directly with an observer SQL credential.

This differs deliberately from ADR-015 queue detail: queue detail has neither a designed public
model nor observer SQL backing and is therefore excluded from H-13. Worker list has a settled
command and backing view but lacks a public-safe projection, so it stays declared behind the typed
gate. The reusable rule is “undesigned command: defer out; settled command awaiting safe
projection: declare and gate.” SQL contract 0.1.2 and migrations 0001–0003 remain unchanged.

### 2.4 Final manifest-backed corrections (document revision 1.0.4)

#### Deferred general job list

| Reserved route | 0.1 status | Reactivation gate |
|---|---|---|
| `GET /taskq/v1/jobs` | deferred; `TQ501` capability inactive; no successful response model | H-08 through Growth §4 / R2-16: exact observer-granted projection, redaction, queue authorization, keyset cursor, supporting indexes, and bounded EXPLAIN evidence |

The adopted base's §3.6 row and amendment 3's historical “operator-minimal” phrase do not define an
active or gated success surface. The 0.1 Function Manifest contains no `list_jobs`; no facade may
assemble a list from base tables, unsafe views, repeated detail calls, or operator credentials.
H-13 excludes the route from its generated command table, OpenAPI operation set, official clients,
and success parity vectors. A hidden reserved-path responder supplies the negative `TQ501` vector.

#### Exact single-enqueue response

For `POST /taskq/v1/queues/{queue}/jobs`, the response field set is exact:

- envelope `outcome`: `created | existed`;
- envelope `data`: exactly `{"job_id": <uuid>}`; and
- queue identity: implied by the canonical path, not repeated in `data`.

There is no `created_at` and there are no request echoes. Payload, headers, schedule, priority,
job type, idempotency key, and other requested values cannot be presented as durable truth,
especially when the outcome is `existed`. Authorized job detail is the sole HTTP source for the
stored timestamp and current row projection. H-13's independent catalog oracle asserts this exact
field set.

## 3. Stage-0 exit status (ADR-005 checklist)

- Draft §1 decisions: accepted (10/10). Holes: H-01..H-07, H-09, H-10, H-13 closed above; H-08/H-11/H-12 explicitly deferred with typed negative capabilities.
- Exact SQL signatures/composites/grants/SQLSTATEs per command: the 0.1 Function Manifest (same pass).
- Parity suite, OpenAPI fence-redaction, and compatibility-window tests: harness T6/T8 obligations, pre-wired in the manifest's per-function test ids.
- Legacy Diverse/QDarte paths remain host adapters with a removal milestone; they define no second protocol.
