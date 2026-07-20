# taskq — Read Model Specification

> **Status:** Tier-3 proposal — 2026-07-20. It prepares the H-08/H-11
> reactivation decision; it changes no 0.1 contract, SQL function, migration,
> generated client, facade route, or deployed host behavior. The current
> `TQ501` dispositions remain authoritative until an ADR and the docs-first
> contract amendments described in §9 are accepted.
>
> **Authority:** [Transport Protocol v1](./Task%20Queue%20Transport%20Protocol%20v1.md)
> H-07/H-08/H-11, the [0.1 Function Manifest](./Task%20Queue%200.1%20Function%20Manifest.md),
> ADR-005/006/010/011/015/017, and R2-16. This document yields to each of
> them. It is a design for a future additive contract revision, not permission
> to expose the observer views or base tables.

## 1. Purpose and boundary

TaskQ 0.1 already has two safe read surfaces:

- `get_job(id, include=...)`: one authoritative job projection, with bounded
  optional fields and no fences or headers; and
- `get_queue_stats(queue)`: a tick snapshot, not a live hot-table aggregate.

The remaining visibility gap is deliberately deferred: a bounded job browser
(H-08) and a non-mutating queue-profile read with an honest update-precondition
story (H-11). This proposal defines the smallest useful activation slice.

It does **not** design a frontend, storage/archive statistics, worker listing,
attempt/event timelines, payload search, a general reporting query language,
SSE, or a direct-SQL dashboard. Those require their own exact projections and
evidence. An operator UI remains a future HTTP client of this surface, per
ADR-018; it is not part of this slice.

## 2. Invariants

1. The facade authenticates before parsing a cursor, disclosing validation
   details, looking up a job, or querying queue data. It authorizes `read` for
   the one requested queue before executing either read function.
2. A queue-scoped credential can inspect exactly one named, authorized queue.
   There is no unfiltered or multi-queue list endpoint. A global `taskq:read`
   grant may use the same one-queue route; it does not activate an all-queue
   aggregate.
3. New observer functions are `SECURITY DEFINER`, owned by `taskq_owner`, use
   the pinned search path, have PUBLIC execute revoked, and are granted only to
   `taskq_observer`. The facade never reads `taskq.jobs`, `taskq.queues`,
   `dead_jobs`, or `worker_status` directly.
4. The list projection contains no payload, headers, worker identity,
   current/historical attempt id, fence, cancellation reason, error, result,
   progress, event data, or arbitrary JSON. Existing `get_job` remains the
   only bounded opt-in detail projection.
5. Cursor values are opaque API tokens, but their decoded form is bound to the
   queue and finite view. A cursor cannot change scope, select a different
   order, or turn into an SQL expression.
6. No added index may degrade the claim/reap/heartbeat path without measured
   PG16 and PG18 evidence. A proposal that needs an index is not accepted on an
   appealing route sketch alone.

## 3. H-08: queue-scoped job pages

### 3.1 Canonical command

The proposed reactivation keeps the reserved identity and adds no alias:

```text
GET /taskq/v1/jobs?queue={queue}&view={ready|running|finished}&limit={1..100}&cursor={opaque?}
```

`queue` and `view` are required. `limit` defaults to 50 and is capped at 100.
Unknown query keys, repeated scalar keys, an invalid queue, invalid view,
malformed cursor, cursor/view mismatch, or cursor/queue mismatch are `TQ422`.
The route is a non-replayable GET; it neither claims nor mutates work.

The finite views are intentional:

| View | Membership at `as_of` | Order | Initial index decision |
|---|---|---|---|
| `ready` | `status='queued'`, no pending cancellation, `scheduled_at <= as_of` | `priority ASC, scheduled_at ASC, id ASC` | Reuse `jobs_claim_idx`; prove the bounded heap fetch is acceptable before considering an include index. |
| `running` | `status='running'` | `started_at DESC, id DESC` | Add a queue-leading partial index only if the B9 gate proves it necessary. |
| `finished` | `status IN ('succeeded','failed','cancelled')` | `finished_at DESC, id DESC` | Add a queue-leading terminal partial index only if the B9 gate proves it necessary. |

There is deliberately no arbitrary `status`, `job_type`, time-range, text, or
payload predicate. `scheduled`, cancellation-pending, `blocked`, and
failed-only browsing remain future explicit views; adding any one requires its
own cursor/index/plan amendment. This prevents a friendly query parameter from
quietly becoming an unbounded reporting API.

### 3.2 Projection and pagination

Every item has exactly these fields:

```text
job_id, job_type, status, outcome, priority,
attempt_count, failure_count, max_attempts,
created_at, scheduled_at, started_at, finished_at, updated_at
```

`queue` is implied by the required query parameter and is not duplicated. The
response data shape is:

```json
{
  "as_of": "database timestamp",
  "items": ["the fixed projection above"],
  "next_cursor": "opaque token or null"
}
```

`as_of` is captured from the database once before membership is evaluated. It
means “the server's membership boundary for this page,” not a cross-page
snapshot guarantee. A job may move between views during paging; keyset paging
prevents offset drift and duplicate rows within a stable view/order, but it
does not promise a historical export.

The cursor is unpadded base64url of a canonical JSON object, bounded to 1 KiB.
It includes `v=1`, `queue`, `view`, and the full final sort tuple. The server
validates all fields and types before calling SQL. Cursors are not secrets and
are not authorization credentials; authorization always precedes cursor use.

The future SQL function receives typed cursor components, never a fragment of
user SQL, and obtains one extra row (`limit + 1`) to decide `next_cursor`.

### 3.3 Outcomes and hiding

After authentication, the facade authorizes `read(queue)` before the list
query. An unauthorized queue follows the host's established `403`/hiding-mode
behavior. An authorized queue that does not exist returns the normal missing
queue outcome (`TQ001`/404); an existing empty view returns 200 with an empty
`items` array and `next_cursor: null`. The query does not leak other queue
names, counts, or cursor positions in either case.

## 4. H-11: queue-profile read and conditional update

### 4.1 Read projection

The proposed active route is the reserved identity:

```text
GET /taskq/v1/queues/{queue}
```

It requires `read(queue)` and returns only the canonical configuration fields
already accepted by `ensure_queue`, plus an explicit version:

```text
name, profile_version,
default_priority, default_lease_seconds, default_max_attempts,
default_backoff_mode, default_backoff_base, default_backoff_cap,
retention_hours, failed_retention_hours, max_depth, notify_enabled,
paused
```

It never returns pause reason, workers, connection data, IAM data, raw queue
rows, or any host-specific metadata. `paused` is current operational state;
it is not a promise that a claim will remain paused after the response.

An observer-granted `taskq.get_queue_profile(text)` function owns this
projection. It returns no row for an unknown queue. The facade performs queue
authorization first, then maps that absence to the normal missing queue
outcome. No call to the operator-only mutating `ensure_queue` may serve a GET.

### 4.2 Version and `If-Match`

H-11 cannot honestly reactivate a profile read while leaving interactive
profile edits with an unspecified lost-update story. The recommended design is:

1. Add `profile_version bigint NOT NULL DEFAULT 1` to `taskq.queues` in an
   additive migration. It increments exactly when one of the canonical profile
   fields changes; pause/resume do not increment it.
2. `GET` returns `profile_version` and `ETag: "taskq-profile-{version}"`.
3. Existing three-argument `ensure_queue(name, profile, actor)` remains the
   backwards-compatible bootstrap/desired-state primitive. It may create or
   update without a precondition and increments the version only on an actual
   profile change.
4. A new, separately named operator function
   `taskq.update_queue_profile(name, profile, actor, expected_version)` performs
   the compare-and-update under one row lock. It returns the canonical profile
   and new version, or a typed `profile_version_conflict` without mutation.
   A new name avoids ambiguous PostgreSQL default-argument overloads and keeps
   existing direct-SQL clients valid.
5. Canonical `PUT /taskq/v1/queues/{queue}` accepts no `If-Match` for
   bootstrap creation. For an existing queue, an exact ETag routes to the
   conditional function; absent `If-Match` preserves the current idempotent
   bootstrap behavior. A malformed tag is `TQ422`; a stale version is the
   protocol's typed conflict response (HTTP 409, `retryable=false`) with the
   current profile version only—not a request echo or a hidden profile.

This retains deployment automation while giving an interactive client a real
compare-and-set operation. The protocol amendment must define the exact ETag
grammar, profile response shape, conflict code/data, and whether a later
strict-update route is warranted. Nothing in this proposal authorizes an
implementation to make `If-Match` silently optional or silently ignored.

## 5. SQL and migration shape

The activation package must be docs-first and append-only:

1. ADR decision and Protocol document revision activate H-08/H-11 and replace
   their current negative-capability entries.
2. Function Manifest minor revision defines exact input/output composites,
   grants, pinned search paths, typed SQLSTATEs, cursor validation, and the
   two new functions above before a migration is written.
3. The next immutable migration adds `profile_version`, the new functions, and
   only the indexes that pass the benchmark gate. Existing function identities
   and the three-argument `ensure_queue` remain intact.
4. `verify()` and the catalog-parity matrix assert the new column, functions,
   grants, indexes, views, and protocol command vectors on fresh install and
   the complete upgrade chain.

`list_jobs` should return a typed page composite with database `as_of`, the
fixed item projection, and typed next-sort components. HTTP encodes those
components into the opaque cursor; SQL callers receive the same bounded page,
not an observer SELECT grant on a base table. A direct SQL client must not get
a wider projection merely because it bypasses HTTP.

## 6. Required evidence before acceptance

### Security and parity

- Hand-derived Tier-0 catalog oracle proves generated clients/OpenAPI expose
  both active routes only after activation and retain no hidden success path.
- SQL and HTTP execute the same scenario set and independently compare each
  fixed projection against raw owner-only rows. A deliberate projection or
  route mutation must fail the oracle.
- Queue-scoped allow, wrong-queue deny, global-read allow, hiding equality,
  unknown queue, malformed cursor, cross-queue cursor, and field-redaction
  vectors run against the real authorizer adapter.
- Fences, attempt ids, headers, payloads, error/result/progress, worker data,
  and raw profile/control fields are asserted absent from every list/profile
  success and error response.

### Pagination and concurrency

- Page-boundary tests cover each view, tie timestamps, UUID tie-breakers, empty
  pages, exact limit, `limit + 1`, malformed/forged cursors, and cursor/view or
  cursor/queue mismatch.
- A concurrent enqueue/claim/settle stress test proves no duplicate item within
  a stable view order and documents allowed movement between views as state
  changes.
- Conditional queue-update tests prove created, unchanged, successful
  exact-version update, stale conflict without mutation, and concurrent
  competing updates. They also prove legacy `ensure_queue` remains available.

### B9 plan gate

On PG16 and PG18, with a million-row fixture distributed across queues and
views, each accepted query shape must use its named index family, scan at most
`limit + 1` candidate rows after the queue/view predicate, and avoid a
sequential scan or sort of `taskq.jobs`. The report includes `EXPLAIN
(ANALYZE, BUFFERS, FORMAT JSON)`, p95 latency under the documented fixture,
and write-path comparison against the current claim/heartbeat benchmarks.

If `running` or `finished` cannot meet that gate without unacceptable write
amplification, that view stays `TQ501`; it is not activated with an unbounded
fallback. `ready` is separately measured against the existing claim index.

## 7. Explicit deferrals

The following are not implied by this work:

- worker list activation (its safe projection remains its own gated command);
- attempt history, event timeline, failure-only search, payload/full-text
  search, and arbitrary job-type filters;
- all-queue job browsing, global storage/accounting views, archive data, SSE,
  or a dashboard;
- retention-profile changes, dedicated-DB topology, dependencies/workflows,
  schedules, or completion handles.

Each needs its own bounded projection, authorization decision, SQL support,
plan evidence, and contract amendment.

## 8. Implementation sequence after approval

1. Land the ADR, Protocol revision, Manifest revision, this specification's
   accepted status, and a board task in a docs-only commit.
2. Land one immutable migration with fresh-install and 0001→current upgrade
   proof on PG16 and PG18.
3. Add typed protocol models, one generated command source, SQL transport,
   facade, and sync/async client support with conformance/parity tests.
4. Add the B9 plan gate and CI evidence, then conduct a targeted independent
   review before activating either deferred capability in a host.

No Stage-4 retirement observation, host producer/consumer behavior, UI work,
or side-effecting lane is authorized by these preparation steps.

## 9. Decision request

The next decision should be a new ADR (provisionally **ADR-019**) that either:

- accepts this minimum H-08/H-11 package, with the named finite views and
  conditional profile update; or
- narrows/reorders a view based on measured write cost while keeping every
  rejected capability visibly `TQ501`.

On acceptance, it must authorize an additive Protocol v1 document revision and
Function Manifest revision before implementation. Until then, the existing
Protocol v1 revision 1.0.4 and Function Manifest 0.1.2 are unchanged.
