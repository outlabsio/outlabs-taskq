# Stage-0 transport protocol draft

**Status:** proposed input to ADR-005; not canonical until accepted and moved into the main docs.  
**Scope:** protocol version 1 / package contract 0.1 unless a row is marked deferred.  
**Goal:** direct SQL and HTTP expose the same commands, typed outcomes, errors, authorization inputs, and retry behavior. URLs are frozen here as a proposal; host strangler aliases may differ but must adapt to these semantics.

## 1. Protocol decisions to accept

1. HTTP base path is `/taskq/v1`. Major URL version freezes request/response shapes.
2. Official clients send `Taskq-Protocol-Version: 1`; the server always echoes it. A missing header is accepted for curl/browser simplicity because `/v1` already selects the major. An unsupported supplied version returns `426`/`TQ426`.
3. `GET /taskq/v1/meta` requires no version header and returns protocol min/max, SQL contract version, package version, database server version, and activated capabilities (`followups`, `dependencies`, `workflows`, `schedules`, `archive`, `sse`, uniqueness modes).
4. Every JSON command response contains `protocol_version`, `request_id`, `outcome`, and `data`. Every error uses the one envelope in §4. Expected race outcomes remain typed even when their HTTP status is non-2xx.
5. A claim always has a JSON body and returns 200 for ordinary queue states. Do not use 204: it cannot carry `empty|paused|timeout|unavailable`.
6. Long-poll timeout means “successful wait, no job” (`200 outcome=timeout`), never HTTP 408. Client disconnect cancels the wait but not a claim transaction already committed.
7. Bulk enqueue is atomic in 0.1. It returns one ordered result per input. One invalid item rejects the entire command; no HTTP 207 and no partial success.
8. Job-id mutations authorize from `get_authorization_projection(job_id)` before invoking SQL. Request-supplied queue/job type are not authorization inputs and are omitted from canonical job-id routes.
9. HTTP actor identity is server-derived from `AuthContext`; clients cannot submit `actor`. A worker label may be supplied, but it is distinct from authenticated principal identity and is advisory under a shared fleet token.
10. SQL clients inspect SQLSTATE and typed composites. HTTP clients inspect `outcome`/`error.code`. Neither parses human messages.

## 2. Common wire shapes

### Command result

```json
{
  "protocol_version": 1,
  "request_id": "01J...",
  "outcome": "created",
  "data": {}
}
```

`request_id` comes from a validated inbound correlation header when present or is server-generated. It is safe to log; attempt ids are not.

### Error

```json
{
  "protocol_version": 1,
  "request_id": "01J...",
  "error": {
    "code": "TQ001",
    "message": "queue not found",
    "retryable": false,
    "details": {"resource": "queue", "identifier": "emails"}
  }
}
```

Human text is not stable. `code`, `retryable`, and documented `details` keys are stable within protocol v1. The facade never returns raw SQL, constraint names, payloads, DSNs, stack traces, or exception text.

### Pagination

```json
{
  "protocol_version": 1,
  "request_id": "01J...",
  "outcome": "ok",
  "data": {"items": [], "next_cursor": null, "as_of": "2026-07-18T12:00:00Z"}
}
```

Cursors are opaque, signed/base64url protocol values. They encode the full deterministic keyset, query shape, and direction; clients must not construct them.

## 3. Command × outcome × HTTP status

### 3.1 Meta and queue configuration

| Command | Proposed HTTP | SQL function/read | IAM action | Outcome | HTTP | Response data / note |
|---|---|---|---|---|---:|---|
| Get capabilities | `GET /taskq/v1/meta` | `taskq.meta` safe projection | authenticated read or deployment policy | `ok` | 200 | protocol min/max, contract, package, PG, capabilities |
| Ensure/update queue profile | `PUT /taskq/v1/queues/{queue}` | `taskq.ensure_queue` | `admin` on queue | `created` | 201 | canonical profile + version |
| Ensure/update queue profile | same | same | same | `updated` / `unchanged` | 200 | canonical profile + version |
| Ensure/update queue profile | same | same | same | invalid name/profile | 422 | `TQ422` |
| Read queue profile | `GET /taskq/v1/queues/{queue}` | safe queue projection | `read` on queue | `ok` | 200 | no secrets |
| Read queue profile | same | same | same | missing | 404 | `TQ001`, resource=queue |

Queue profile optimistic concurrency is an **open detail**: recommended `If-Match`/profile version with 409 on stale update before admin configuration is implemented. It is not needed for the first pilot if queues are migration/bootstrap-owned.

### 3.2 Enqueue

| Command | Proposed HTTP | SQL | IAM action | Outcome | HTTP | Response data / retry rule |
|---|---|---|---|---|---:|---|
| Enqueue one | `POST /taskq/v1/queues/{queue}/jobs` | `taskq.enqueue` | `enqueue` on queue | `created` | 201 | `job_id`, queue, job_type, created_at; retry with same idempotency key |
| Enqueue one | same | same | same | `existed` | 200 | existing active `job_id`; success, not error |
| Enqueue one | same | same | same | queue missing | 404 | `TQ001` |
| Enqueue one | same | same | same | invalid value/type/range | 422 | `TQ422`; no row |
| Enqueue one | same | same | same | advisory depth gate | 429 | `TQ429`; `Retry-After` integer seconds + queue details |
| Enqueue one | same | same | same | convergence failure | 500 | `TQ500`, retryable=true; whole transaction rolled back |
| Enqueue batch | `POST /taskq/v1/queues/{queue}/jobs/batch` | `taskq.enqueue_many` | `enqueue` on queue | `ok` | 200 | ordered `{input_index, job_id, outcome=created|existed}` for all inputs |
| Enqueue batch | same | same | same | any invalid item | 422 | `TQ422` with input index; no rows from batch |
| Enqueue batch | same | same | same | queue missing/depth/convergence | 404/429/500 | same code as single; atomic rollback |

0.1 request rules: one queue per bulk call; 1–1000 items; no dependencies; uniqueness mode fixed to `reject`; `idempotency_key` optional but strongly recommended for retryable producers. When two input items carry the same key, input order determines one `created` and subsequent `existed` results pointing to the same job.

Deferred outcomes (`replaced`, `skipped_locked`) do not appear in the v1/0.1 schema until the corresponding capability is active. A client must not send an inactive mode; server returns `TQ501`, not silently downgrade.

### 3.3 Claim and heartbeat

| Command | Proposed HTTP | SQL | IAM action | Outcome | HTTP | Response data / retry rule |
|---|---|---|---|---|---:|---|
| Claim | `POST /taskq/v1/queues/{queue}/claims` | `taskq.claim_jobs` plus facade wait loop | `run` on queue | `claimed` | 200 | non-empty `jobs`; includes attempt fences only here |
| Claim immediate | same, `wait_seconds=0` | same | same | `empty` | 200 | `jobs=[]`; normal poll |
| Claim long poll | same, `wait_seconds>0` | same | same | `timeout` | 200 | `jobs=[]`, elapsed/deadline; normal poll |
| Claim | same | same | same | `paused` | 200 | `jobs=[]`; do not long-poll; client backs off |
| Targeted claim | same with `job_id` | same | same | `unavailable` | 200 | target missing/not-ready/already-owned for this queue; no existence detail |
| Claim | same | same | same | queue missing | 404 | `TQ001`; distinct from paused |
| Claim | same | same | same | invalid batch/lease/wait/job type | 422 | `TQ422` |
| Heartbeat | `POST /taskq/v1/jobs/{job_id}/heartbeat` | `taskq.heartbeat` | job projection → `run` | `ok` | 200 | `cancel_requested`, new lease expiry |
| Heartbeat | same | same | same | `lost` | 409 | typed result, no TQ error; handler must stop/avoid settlement |
| Heartbeat | same | same | same | assertion mismatch, if legacy alias sends queue/type | 409 | `assertion_mismatch` typed result; authoritative row wins |
| Heartbeat | same | same | same | job missing | 404 | projection returns `TQ001` without exposing fence |

Claim request bounds: batch 1–50; `wait_seconds` 0–25 by default with server maximum 30; lease 15–86400; worker label non-empty/length-bounded; optional job-type list bounded and deduplicated. The facade performs repeated short claim transactions and waits on its notification hub/poll timer between attempts. It never holds a database transaction or pooled request connection for the full wait.

`saturated` is deliberately not a public outcome in 0.1. Proving that every due candidate is blocked only by a concurrency key would add a second expensive admission scan. It is represented as empty/timeout and diagnosed through queue stats.

### 3.4 Fenced settlement

| Command | Proposed HTTP | SQL | IAM action | Outcomes | HTTP | Notes |
|---|---|---|---|---|---:|---|
| Complete | `POST /taskq/v1/jobs/{id}/complete` | `taskq.complete_job` | projection → `run` | `ok`, `already_settled` | 200 | `job_status=succeeded`; replay never revalidates follow-ups |
| Complete | same | same | same | `lost` | 409 | stale fence; do not retry as success |
| Complete | same | same | same | invalid follow-up (0.2+) | 422 | `TQ422`; worker terminal-fail escape |
| Complete | same | same | same | non-empty follow-up while inactive (0.1) | 501 | `TQ501`; worker terminal-fails + soft-stops for skew |
| Fail | `POST /taskq/v1/jobs/{id}/fail` | `taskq.fail_job` | projection → `run` | `retry_scheduled`, `dead`, `already_settled` | 200 | next schedule for retry; terminal status/outcome for dead |
| Fail | same | same | same | `lost` | 409 | stale fence |
| Release | `POST /taskq/v1/jobs/{id}/release` | `taskq.release_job` | projection → `run` | `ok`, `already_settled` | 200 | queued or cancelled; budget untouched |
| Release | same | same | same | `lost` | 409 | stale fence |
| Snooze | `POST /taskq/v1/jobs/{id}/snooze` | `taskq.snooze_job` | projection → `run` | `ok`, `already_settled` | 200 | scheduled_at or cancelled; budget untouched |
| Snooze | same | same | same | `lost` | 409 | stale fence |
| Handler cancel | `POST /taskq/v1/jobs/{id}/cancel-running` | `taskq.cancel_running_job` | projection → `run` | `ok`, `already_settled` | 200 | cancelled/canceled; budget untouched |
| Handler cancel | same | same | same | `lost` | 409 | stale fence |
| Any settle | same family | same | same | job projection missing | 404 | `TQ001` before fence supplied to SQL |
| Any settle | same family | same | same | invalid range/body | 422 | `TQ422` |

All fenced requests carry `attempt_id`. It is a capability token: never in URL, read model, metrics, OpenAPI example, ordinary log, or error detail. Official clients redact it.

**Open replay decision (must close before migration):** the current attempt ledger treats a retry of *any* settle command after any explicit settle as `already_settled`. Recommended refinement: same-command replay returns `already_settled`; a different command for the same already-settled attempt returns `settle_conflict`/409 with the prior terminal verb but no fence. This catches client bugs such as `complete` after `fail` instead of silently acknowledging them. If the maintainer keeps cross-verb acknowledgement, document it explicitly and test it across both transports.

### 3.5 Operator and coordination commands

| Command | Proposed HTTP | SQL | IAM action | Outcome | HTTP | Notes |
|---|---|---|---|---|---:|---|
| Pause queue | `POST /taskq/v1/queues/{queue}/pause` | `taskq.pause_queue` | `control` on queue | `paused`, `already_paused` | 200 | actor derived; enqueue still accepted |
| Resume queue | `POST /taskq/v1/queues/{queue}/resume` | `taskq.resume_queue` | `control` on queue | `resumed`, `already_resumed` | 200 | idempotent |
| Operator cancel | `POST /taskq/v1/jobs/{id}/cancel` | `taskq.cancel_job` | projection → `control` | `cancelled` | 200 | queued/blocked terminalized immediately |
| Operator cancel | same | same | same | `cancel_requested` | 202 | running; cooperative completion pending |
| Operator cancel | same | same | same | `already_terminal` | 200 | current terminal status |
| Redrive | `POST /taskq/v1/jobs/{id}/redrive` | `taskq.redrive_job` | projection → `control` | `redriven` | 200 | same id; new attempt only after claim |
| Redrive | same | same | same | not failed | 409 | `TQ409`, reason=not_redrivable |
| Redrive | same | same | same | active-key collision | 409 | `TQ409`, reason=idempotency_collision |
| Expire job | `POST /taskq/v1/jobs/{id}/expire` | `taskq.expire_job` | projection → `control` | `expired_and_reaped` | 200 | synchronous; handler heartbeat loses |
| Expire job | same | same | same | not running | 409 | typed `not_running` |
| Expire worker | `POST /taskq/v1/workers/{worker_id}/expire-leases` | `taskq.expire_worker_leases` | global `control` | `ok` | 200 | `{matched,reaped,skipped}` per R2-02 |
| Purge queued | `POST /taskq/v1/queues/{queue}/purge` | `taskq.purge_queued` | `control` on queue | `ok` | 200 | bounded count; cancels, never deletes |
| Run now | `POST /taskq/v1/jobs/{id}/run-now` | `taskq.run_now` | projection → `control` | `ok` | 200 | queued only; otherwise 409 |
| Reprioritize | `POST /taskq/v1/jobs/{id}/reprioritize` | `taskq.reprioritize` | projection → `control` | `ok` | 200 | queued/blocked only; otherwise 409 |
| Set concurrency cap | `PUT /taskq/v1/concurrency-limits/{key}` | `taskq.set_concurrency_limit` | global `admin` | `created`, `updated`, `unchanged` | 201/200 | non-negative cap; 0 pauses resource |
| Request worker shutdown | `POST /taskq/v1/workers/shutdown-requests` | `taskq.request_worker_shutdown` | global `control` | `accepted` | 202 | exact worker, queue, or fleet filter |

There is **no public HTTP tick or janitor command**. The facade's internal housekeeper calls SQL as `taskq_housekeeper`; `taskq tick`/`taskq janitor` are authenticated local/admin CLI operations using a DB credential. A host may expose its own emergency endpoint, but it is outside protocol v1.

Workflow create/cancel, dependency-bearing enqueue, schedules, and schedule administration are capability-gated 0.2 commands. Archive inspection/rotation is 0.3. Their fields must not appear as accepted-but-ignored 0.1 inputs.

### 3.6 Read models

| Command | Proposed HTTP | Backing | IAM action | Outcome | HTTP | 0.1 contract |
|---|---|---|---|---|---:|---|
| Get job | `GET /taskq/v1/jobs/{id}` | safe detail function | projection → `read` | `ok` | 200 | status, typed outcome, timestamps, compact result/progress under policy; never fence |
| Get job | same | same | same | missing/hidden | 404 | `TQ001` |
| List jobs | `GET /taskq/v1/jobs?queue=...` | keyset projection | `read` on exactly one queue, or global read | `ok` | 200 | limit 1–200; stable cursor; no payload by default |
| Queue stats | `GET /taskq/v1/stats/queues/{queue}` | tick snapshot + bounded counts | `read` on queue | `ok` | 200 | `as_of` required |
| Global queue stats | `GET /taskq/v1/stats/queues` | filtered snapshot | global `taskq:read` | `ok` | 200 | never returns unauthorized queues |
| Worker list | `GET /taskq/v1/workers` | safe presence view | global read/control | `ok` | 200 | no credential/network secrets |
| Metrics | `GET /taskq/metrics` | `taskq.metrics()` | deployment scrape auth | n/a | 200 | Prometheus content type, not JSON envelope |

The exact detail/list projections, payload/result redaction, cursor tuple, and indexes remain holes H-07/H-08 below and must be frozen before these routes leave “minimal pilot” status. The minimum 0.1 producer surface is get-by-id plus terminal result; arbitrary list filters can remain operator-only until benchmarked.

## 4. TQ registry

Existing codes keep their names but gain one meaning family and exactly one HTTP mapping. Resource subtype or conflict reason belongs in `details`, never in a second status mapping.

| SQLSTATE / body code | Stable category | HTTP | Retryable default | Required details |
|---|---|---:|---|---|
| `TQ001` | referenced taskq resource not found | 404 | false | `resource=queue|job|dependency|workflow|schedule`, identifier when safe |
| `TQ409` | command conflicts with current durable state | 409 | false | `reason`, current status when safe |
| `TQ422` | invalid command argument/semantic value | 422 | false | field/path and bounded reason; bulk input index if applicable |
| `TQ426` | unsupported protocol/contract version | 426 | false | supported min/max, received version |
| `TQ429` | queue admission/capacity backpressure | 429 | true | queue, reason; HTTP `Retry-After` |
| `TQ500` | internal convergence/invariant failure | 500 | true | opaque incident/reference only; internal logs hold diagnostics |
| `TQ501` | known capability not active in this contract | 501 | false | capability, active contract version, first supported version if known |
| `TQ503` | facade/database temporarily unavailable | 503 | true | dependency class only; HTTP `Retry-After` when known |

Authentication failures use the same outer envelope with `AUTH401`→401 and `AUTH403`→403; they are not PostgreSQL SQLSTATEs. Request JSON syntax/model failures normalize to `TQ422`. Unhandled native PostgreSQL errors normalize to `TQ500` at the facade and retain the original SQLSTATE only in protected logs.

`lost`, `already_settled`, `paused`, `timeout`, and `unavailable` are typed command outcomes, not TQ exceptions. This distinction is important for direct SQL parity.

## 5. Retry and idempotency matrix

| Situation | Client action |
|---|---|
| Network timeout before enqueue response | Retry with the same queue/idempotency key; created or existed is success |
| Network timeout before bulk response | Retry the identical atomic batch; every idempotent item converges. Non-keyed items cannot be safely retried and clients must warn/refuse automatic retry |
| Network timeout after fenced settle | Retry the same settle command with the same attempt id; expect `already_settled` or original-equivalent terminal data |
| `lost` | Do not retry settlement as if owned; stop handler/side effects and log loudly |
| `TQ429` | Retry after header with jitter; do not mint a new idempotency key |
| `TQ500`/`TQ503` | Retry only commands documented idempotent/replay-safe; bounded exponential backoff |
| `TQ501` | Do not loop; client/server capability skew. Worker follows R2-01 fatal-skew behavior |
| Claim `empty|paused|timeout|unavailable` | Normal poll/backoff; not an error budget event |

## 6. Version negotiation and capabilities

`GET /taskq/v1/meta` proposed body fields:

- `protocol_version`, `protocol_min`, `protocol_max`
- `contract_version`, `package_version`
- `postgres_version_num`
- `capabilities` map with boolean or enum values, including `uniqueness_modes`
- `limits`: max batch, max claim batch, max wait, payload/progress/result sizes

Official clients declare a supported protocol major and SQL contract range. Startup fails before claiming when no overlap exists. A breaking migration emits the accepted migrate-break notification; workers soft-stop. Capabilities control field acceptance: the server rejects inactive fields with TQ501 and never ignores them.

The protocol major and package semver are independent. Additive optional response fields may appear within v1; clients ignore unknown fields. Removing/renaming a field, changing a status/outcome, or changing retry semantics requires a new protocol major or an explicitly negotiated capability.

## 7. Holes encountered while drafting

| Hole | Status | Why it blocks / proposed closure |
|---|---|---|
| H-01 SQL claim result cannot distinguish unknown, paused, empty, targeted unavailable | **BLOCKS Stage 0** | Introduce a typed claim result/header or companion atomic function result; do not make the facade infer from an empty SETOF |
| H-02 Exact SQL composite schemas for enqueue/bulk/operator results are not frozen | **BLOCKS Stage 0** | Define named composite types and compatibility rules before migration 0001 |
| H-03 Cross-verb settle replay semantics | **BLOCKS Stage 0** | Accept recommended same-verb replay/different-verb conflict, or explicitly keep any-verb acknowledgement and test it |
| H-04 Worker label binding to authenticated subject | **BLOCKS auth acceptance** | Per-worker token: server derives/binds label. Shared token: label is validated but marked advisory; principal actor remains token subject |
| H-05 Bulk set-based convergence algorithm | **BLOCKS Stage 0** | Specify later-snapshot existing-id resolution and intra-request duplicate order; T3 race proof |
| H-06 Error detail schema and native-error normalization | **BLOCKS Stage 0** | Accept §4 registry/envelope and enumerate every public SQL raise/cast/check path |
| H-07 Job detail field/redaction/size policy | **BLOCKS outlabsAPI result read** | Freeze minimal safe projection and per-field caps before dogfood |
| H-08 List cursor and supporting indexes | **Can defer beyond minimal 0.1 read** | Accept exact queue-scoped keyset plus B9 EXPLAIN evidence before broad list route |
| H-09 HTTP request/body/response size ceilings | **BLOCKS facade security** | Publish limits for payload, headers, progress, result, error, bulk bytes/items, job-type filters |
| H-10 Long-poll disconnect and shutdown behavior | **BLOCKS T6** | Cancel waiter; never cancel a committed claim; drain hub subscribers before LISTEN/pool close |
| H-11 Queue-profile optimistic concurrency | **Can defer if bootstrap-owned** | Add profile version/If-Match before interactive admin edits |
| H-12 Schedule/workflow/archival HTTP commands | **Correctly deferred** | Do not reserve ambiguous accepted fields; add them with 0.2/0.3 capability specs |
| H-13 Protocol source generation | **BLOCKS parity maintenance** | Generate OpenAPI models, sync/async HTTP clients, and SQL adapter contract tests from one Python model/manifest—not hand-copied route tables |

## 8. Stage-0 exit checklist for ADR-005

- Maintainer accepts/rejects every decision in §1 and closes H-01–H-07, H-09, H-10, H-13.
- Every 0.1 command has an exact SQL signature/composite, HTTP model, action/queue source, outcome set, error set, retry rule, and capability role.
- Direct SQL and HTTP parity tests run the same scenario vectors and compare normalized results.
- OpenAPI contains no attempt-id examples and marks fences write-only/sensitive.
- Compatibility tests cover old client/new schema and new client/old schema inside/outside the supported range.
- Legacy Diverse/QDarte paths are adapters to these commands with an explicit removal milestone; they do not define a second protocol.
