# taskq — Durable admission reservation specification

> **Status:** Tier 3; accepted by ADR-023 for Protocol document revision
> 1.0.8 / SQL contract 0.1.5. Tier-0 identities and wire behavior live in the
> Transport Protocol and Function Manifest and win every conflict.

## 1. Purpose

Ordinary enqueue answers a task question: “is there already active work under
this key?” Durable admission answers an integration question: “has this
business request already been accepted, even if planning inputs or job state
have changed since?” They are deliberately different authorities.

The primitive supports integrations that must perform volatile or expensive
planning between recognizing a request and constructing a job. It provides:

- one durable owner of a key while planning is in progress;
- a stable admitted job id and small response receipt after commit;
- replay after process or response loss without replanning;
- no read-then-insert race with job settlement;
- bounded expiry, cancellation, retention, and cleanup; and
- the same semantics over direct SQL and Protocol-v1 HTTP.

It is not a workflow engine, distributed lock service, payload cache, result
store, or replacement for worker fencing. It never grants a producer the right
to claim, settle, read payloads, or cancel an admitted job.

## 2. Terms and identities

| Term | Meaning |
|---|---|
| admission key | Required 1–255 character producer key, scoped by canonical queue. |
| intent hash | Required 64-character lowercase hexadecimal SHA-256 of the host's canonical pre-plan business request. |
| handle | Non-nil UUID generated once per logical reserve attempt and reused across transport retries. It is an ownership nonce, not a secret or fence. |
| reservation TTL | Database-clock planning window, 15–3600 seconds; default 300. |
| receipt TTL | Database-clock replay-retention floor, 3600–31,536,000 seconds; default 2,592,000 (30 days). |
| receipt | Immutable non-sensitive JSON object, at most 2,048 UTF-8 bytes, stored atomically with admission. |

The application owns canonical request serialization before SHA-256. The queue
does not receive or reconstruct the source request. A host must version its
canonicalization inside the bytes it hashes when changing that algorithm.

The same key and same intent identify the same admission. Reusing a retained
key with another intent is an error, not an alias. After an admitted receipt is
eligible for cleanup and its hot job row no longer exists, the key may be used
for a new admission; callers requiring a longer replay horizon select a longer
receipt TTL.

## 3. Durable state

Migration 0007 adds a private `taskq.admissions` table. Application roles have
no table grants. Its logical columns are:

| Column | Contract purpose |
|---|---|
| `id` | Internal UUID identity; never an HTTP authority. |
| `queue`, `idempotency_key` | Unique admission identity. |
| `intent_hash` | Stable business-intent binding. |
| `handle` | Current planning owner. |
| `state` | `reserved | admitted | cancelled`. Expiry is derived from the database timestamp. |
| `reservation_expires_at` | Pending-owner deadline stamped from database `now()`. |
| `receipt_ttl_seconds` | Immutable retention policy selected by the successful reservation. |
| `finish_hash` | Database-computed SHA-256 of canonical JSON `{job, receipt}`; null before finish. |
| `job_id` | Stable admitted job id; null before finish. |
| `receipt`, `receipt_expires_at` | Immutable admitted response data and retention floor. |
| lifecycle timestamps | Created, updated, admitted, and cancelled database instants. |

`taskq.jobs` gains nullable internal `admission_id`, unique and foreign-keyed to
the private ledger. Ordinary enqueue leaves it null. The admission key is not
copied into `jobs.idempotency_key`; this prevents ordinary active-key reuse
from becoming a second authority for admission.

## 4. State machine

### 4.1 Reserve

`reserve_admission(queue, key, intent_hash, handle, reservation_ttl,
receipt_ttl)` locks the unique key row and returns:

| Existing state | Request | Outcome | Rule |
|---|---|---|---|
| none | valid | `reserved` | Insert one row owned by this handle. |
| unexpired reserved | same intent + same handle | `reserved` | Exact replay; expiry is not extended. |
| unexpired reserved | same intent + different handle | `pending` | No handle or payload data returned; database-derived retry delay only. |
| unexpired reserved or retained admitted | different intent | `TQ409 idempotency_mismatch` | No request echo or stored intent returned. |
| expired reserved | same or new intent | `reserved` | New generation/handle owns planning; stale handle is invalid. |
| cancelled | same or new intent | `reserved` | Explicit cancellation releases the unadmitted key. |
| admitted and retained | same intent | `admitted` | Return durable job id, receipt, and receipt expiry; do not plan. |
| admitted and cleanup-eligible with no hot job | valid | `reserved` | Recycle only after the retention floor and job-row removal. |

Queue existence is checked before key state. A reservation does not consume
queue depth, emit an event, notify workers, or create a job.

### 4.2 Finish

`finish_admission(queue, key, handle, job, receipt)` locks the same row.

- Current, unexpired `reserved` + matching handle validates the strict job
  command and receipt, invokes ordinary enqueue semantics with no ordinary
  idempotency key, links the created job to the admission, and changes the row
  to `admitted` in one transaction. Outcome: `created`.
- An already admitted row with the same handle and the same database-computed
  canonical job+receipt SHA-256 returns `existed` plus the exact stored job
  id/receipt. Changed finish content is `TQ409 finish_mismatch`; no job or
  receipt field is recomputed.
- Missing, cancelled, expired, or differently owned reservations return the
  typed error fixed in Tier 0. They create no job.
- `TQ429`, `TQ500`, cancellation of the database transaction, or connection
  loss before commit leaves the row reserved and creates no visible job.
- Connection loss after commit is recovered by replaying the same finish with
  the same handle; it returns `existed`.

The strict job object mirrors single enqueue except that it has no
`idempotency_key`, dependency, workflow, or parent authority. In 0.1.5 its
fields are `job_type`, `payload`, `priority`, `scheduled_at`,
`concurrency_key`, `affinity_key`, `max_attempts`, `lease_seconds`,
`backoff_mode`, `backoff_base`, `backoff_cap`, and `headers`. Existing H-09
bounds and queue defaults apply exactly.

### 4.3 Cancel

`cancel_admission(queue, key, handle)` returns:

- `cancelled` when the current unexpired reservation is cancelled;
- `already_cancelled` for an exact cancellation replay;
- `expired` when its planning window already elapsed; or
- `already_admitted` with the stable job id/receipt after finish won.

A different handle is `TQ409 reservation_conflict`. Cancellation never calls
`cancel_job`, releases no job, and consumes no retry/failure budget. A later
reserve may reacquire a cancelled or expired key.

## 5. Wire contract and authorization

All routes are under a queue path and require `taskq_{queue}:enqueue`:

| Command | Route | Success outcomes |
|---|---|---|
| reserve | `POST /taskq/v1/queues/{queue}/admissions/reserve` | `reserved` 200, `pending` 202, `admitted` 200 |
| finish | `POST /taskq/v1/queues/{queue}/admissions/finish` | `created` 201, `existed` 200 |
| cancel | `POST /taskq/v1/queues/{queue}/admissions/cancel` | `cancelled | already_cancelled | expired | already_admitted` 200 |

Authentication and queue authorization happen before request-id normalization,
body decoding, intent comparison, or admission lookup. Denied and absent queue
postures retain the facade's hiding policy. No route accepts queue, actor,
principal, job id, or capability role from the body as authority.

Reserve data is a discriminated projection:

- `reserved`: authoritative handle and `reservation_expires_at`;
- `pending`: only `retry_after_seconds` and `reservation_expires_at`;
- `admitted`: `job_id`, receipt, and `receipt_expires_at`.

Finish always returns `job_id`, receipt, and `receipt_expires_at`. Cancel returns
those fields only for `already_admitted`. Optional response fields are omitted,
not null-filled. Receipt is stored server state, but callers must still treat it
as integration metadata rather than a job-detail projection.

## 6. Retry and race rules

The official clients generate a handle once outside their transport retry loop.
Every retry uses the same queue, key, intent hash, handle, TTLs, job command,
and receipt bytes.

| Event | Required behavior |
|---|---|
| reserve response lost | Replay with the same handle; receive the same `reserved` or later `admitted` truth. |
| two reserve callers, same intent | One handle owns; the other receives `pending`. |
| two reserve callers, different intent | One owns; the other receives TQ409. |
| finish races finish, same handle/content | One commits `created`; the replay returns `existed`. |
| finish replay changes job or receipt | TQ409 `finish_mismatch`; stored job/receipt remain unchanged. |
| finish races cancellation | Row lock orders them; either job admission wins or cancellation wins, never both. |
| finish races expiry/takeover | Only the current unexpired handle can create the job. |
| finish hits max depth | TQ429; reservation remains current until its original expiry. |
| finish response lost after commit | Same-handle replay returns the stored admitted result. |
| job settles before reserve replay | Replay still returns admitted while the receipt is retained. |
| process crashes while planning | Another handle waits until database expiry, then may acquire; stale finish is rejected. |

`pending` clients honor its database-derived retry delay with jitter. They do
not spin, steal a live reservation, invoke the planner, or mint jobs through
ordinary enqueue.

## 7. Retention and housekeeping

Receipt expiry is a cleanup eligibility time, not permission to delete an
active job or forget a still-hot job mapping. The existing housekeeper performs
bounded admission cleanup:

1. expired/cancelled unadmitted rows may be removed after a bounded diagnostic
   grace period;
2. admitted rows may be removed only after `receipt_expires_at` and after their
   referenced hot job row is absent; and
3. one pass has a fixed row limit and index-backed candidate selection.

Manual deletion, manual state updates, and capability edits are forbidden.
Future job archival must move or preserve admission receipts before deleting a
mapping whose replay window remains open.

## 8. Compatibility and release sequence

The additive SQL change follows ADR-020:

1. publish a route-free bridge whose supported set is
   `{0.1.2, 0.1.3, 0.1.4, 0.1.5}`;
2. make that bridge the deployed and rollback floor for any target database;
3. apply immutable 0007, which reports 0.1.5 and activates exactly
   `admission_reservations` plus the already-active `read_model_list_ready`;
4. deploy the feature runtime, which mounts admission commands only after
   startup verifies both 0.1.5 and the capability; and
5. retain the bridge as the zero-DML rollback while 0007 remains applied.

This library slice authorizes no production migration. A host adoption plan
must repeat the floor, backup/restore, capability, and rollback analysis for its
own database.

## 9. Acceptance program

Implementation is incomplete until all of the following pass:

- fresh 0001→0007 and full upgrade 0001→…→0007 on PostgreSQL 16 and 18;
- exact catalog, ownership, pinned path, PUBLIC revoke, grants, columns,
  indexes, composites, metadata, and `verify()` parity;
- reserve/finish/cancel state-table vectors including every outcome/error;
- real concurrent transactions for same/different intent, finish/finish,
  finish/cancel, expiry/takeover, and stale-handle rejection;
- response-loss tests proving one job and byte-identical receipt replay;
- backpressure/validation rollback proving no partial admission;
- SQL/HTTP parity, authentication ordering, queue hiding, strict request
  models, safe errors, and producer-only privilege negatives;
- bridge acceptance for 0.1.4/0.1.5 plus preserved pre-bridge rejection; and
- resource/cleanup and bounded-plan evidence for the housekeeping query.

## 10. First integration: QDarte

QDarte computes the intent hash from its canonical contact scope request before
candidate planning. Its package path then:

1. reserves the canonical key;
2. returns the stored `admitted` result as caller `existed` without invoking the
   planner;
3. invokes the planner only when its handle owns `reserved`;
4. finishes with the package job command and receipt
   `{ "planned_entities": N }`; and
5. maps `created | existed` to the already-frozen backend-neutral response.

`pending` is a bounded retryable host refusal. It never invokes the direct
producer, a second planner, a broad worker, or a cross-backend fallback. The
adapter is a temporary retirement seam; the reservation primitive and normal
package producer remain after the direct QDarte queue is removed.
