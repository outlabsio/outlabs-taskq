# Decisions for Growth §3–§5 and 0.1 fitness

These are independent accept/reject recommendations. None changes the accepted 0.1 kernel boundary.

## 1. Dedicated queue database

### Recommendation: accept with amendments for 0.2

The proposal's central statement is honest: once taskq and the domain transaction live in different databases, transactional enqueue is gone and no client retry can close the commit→enqueue crash gap (`Task Queue Growth, Topology & Live Visibility.md`, lines 45–64). Keep co-resident as the default.

Accept two named durability modes under the dedicated topology:

| Mode | Durable promise | Required mechanism | Appropriate use |
|---|---|---|---|
| `best_effort_after_commit` | A crash after domain commit but before taskq acceptance can permanently lose intent | Mandatory idempotency key + bounded retry while the producer is alive; explicit loss accepted | Rebuildable/cache-like work with a separate reconciliation source |
| `host_outbox` | Domain intent survives producer crash | In the domain transaction insert an outbox intent; relay enqueues using outbox id as taskq idempotency key; mark delivered after acceptance; crash between accept/mark converges to `existed` | Emails, billing, notifications, and any work whose intent must not vanish |

An “idempotent re-trigger path” is useful reconciliation but is not durable unless something durable records that a trigger is still owed. Taskq should document a host outbox recipe and test adapter, but not own host tables or cross-database relay lifecycle.

### `session=` guard

Refusing `session=` in dedicated mode is the right public guard because that parameter's documented meaning is “join the host's domain transaction” (borrowed feature 14, lines 62–84), which the topology cannot provide. The guard should inspect the actual session bind/engine identity as well as configuration; a mislabeled mode must not silently accept a session bound to a different database. If advanced callers need a queue-database transaction, expose a distinctly named low-level connection/transaction API rather than overloading `session=` with weaker semantics.

### What the split additionally loses

- Atomic domain write + enqueue.
- Atomic domain write + direct-DB settlement when a handler deliberately uses one co-resident transaction. The general at-least-once contract remains, but the duplicate side-effect window grows.
- SQL joins between domain rows and job/read/archive state.
- Database-enforced cross-domain references (already prohibited) and snapshot-consistent domain/task diagnostics.
- One backup/restore timeline. Queue and app can be restored to mutually inconsistent points.

Taskq-internal dependencies, archived-parent resolution, workflow membership, lineage, attempts, and events do **not** break: those objects still share the queue database. Domain tables holding plain job UUIDs may temporarily or permanently reference a job absent after queue-only restore; that is why the restore contract matters.

### Required operational contract

Before accepting a dedicated topology ADR, define:

1. Queue RPO/RTO. “Rows are usually re-derivable” must be removed; accepted work is durable data unless a host explicitly classifies a lane as rebuildable.
2. Independent backup and restore ownership for the queue database.
3. A restore reconciliation runbook: replay a retained host-outbox window, compare stored job ids/keys, and accept that restoring queue state backward can cause at-least-once re-execution of work the lost queue snapshot had already completed.
4. Health semantics for app DB healthy / queue DB unavailable and the inverse.
5. Separate pool/connection budgets, migration target, maintenance credential, and alerts.
6. No claim that idempotency prevents all post-restore duplicates: the active-only key index intentionally permits a new job after terminal completion, and a restored-away terminal record cannot prove prior execution.

### Exact amendments if accepted

- Growth §3 and §6: add durability modes, bind check, RPO/restore contract, and lost join/settle atomicity.
- New topology ADR: co-resident default; dedicated 0.2 opt-in.
- Feature 14 §2.4: define the bind-aware refusal and host-outbox recipe.
- Extraction Brief topology/runbook: add split-brain health and restore evidence.

## 2. Read-model API

### Recommendation: accept a minimal 0.1 slice; defer broad browsing until query shapes pass B9

The proposal is directionally right: pilots need a safe job/result read and queue health. The broad `queue/status/job_type/cursor` endpoint is not yet an executable performance or authorization contract (`Growth` lines 68–82).

### 0.1 minimum

| Surface | Decision | Why |
|---|---|---|
| Get job by id | Ship | Required by outlabsAPI's 202+job-id UX and worker/producer diagnosis |
| Get terminal result by id / bounded wait helper | Ship | Makes an enqueued tool run useful without SSE |
| Per-queue stats snapshot | Ship | Pilot operations and readiness; already ADR-009 scope |
| Dead/running safe views | Ship for operator/diagnostics | Redrive and lease investigation |
| Arbitrary all-status job browser | Defer or operator-preview only | Needs a write-amplification/index decision and B9 evidence |
| Payload/result/error/event-data inclusion | Exclude by default | Sensitive/large; policy and redaction are unresolved |
| Storage/archive stats | 0.3 | Archive does not exist earlier |

### Finite query shapes

Do not promise arbitrary filter combinations backed by “partial indexes.” Freeze a small set whose cursor matches an index:

- Ready jobs: queue + `(priority, scheduled_at, id)` using the claim index, read-only and capped.
- Running jobs: queue + `(started_at, id)` with a small running-only index if B9 proves needed.
- Dead jobs: queue + `(finished_at, id)` with a queue-leading failed-only index if operator volume needs it.
- Terminal history: queue + bounded finished-at window; add a queue-leading terminal index only if measured. It writes on every terminal transition.
- Job type is not an arbitrary extra predicate in 0.1; add a specific index/query only for a measured diagnostic need.

Every response has `as_of`. Snapshot reads expose their staleness; live detail reads do not imply snapshot consistency with stats.

### Authorization and redaction

- A queue-scoped principal may list exactly one authorized queue. Unfiltered/all-queue reads require global `taskq:read`.
- A job-id read follows ADR-006's authoritative projection before detail lookup.
- 0.1 detail never exposes current or historical attempt ids, worker credential material, raw headers, payload, event data, attempt stats, or unbounded error/result text.
- The existing closed `read` action is not enough to make `?include=payload` safe by itself. Recommended 0.1 decision: do not expose payload. A later sensitive-field policy may be a host authorizer capability or an ADR-006 action amendment, with byte caps and redaction hooks.
- Result/progress fields used by outlabsAPI receive explicit size caps and host redaction. Error messages are truncated and treated as potentially sensitive.
- Global stats are filtered before serialization; never fetch all and filter in Python after unauthorized queue names have entered logs/cache.

### Exact amendments if accepted

- Growth §4: split minimum 0.1 from benchmark-gated browser.
- ADR-005 protocol: exact projections, cursor tuples, field caps, authorization source, errors.
- Unified Spec §12: replace raw-table operator examples with safe functions/views.
- Harness T6/B9: auth matrix, EXPLAIN structure, deep-backlog latency, pagination stability under concurrent inserts.

## 3. Server-sent events

### Recommendation: accept for 0.2 only with reset and handshake semantics

SSE remains a read-model hint/UX channel, never delivery correctness. The table is truth. The proposal can be safe if it stops claiming replay is always lossless after retention.

### Prune watermark

The pruner must persist `max_pruned_event_id` in the same transaction as each delete batch. Because tiered pruning can be non-contiguous, an “earliest available id” is insufficient. The rule is conservative and deterministic:

- no resume cursor: send a fresh snapshot/current stream according to endpoint semantics;
- `Last-Event-ID < max_pruned_event_id`: at least one event after the client's cursor has been deleted, so replay is incomplete—reset;
- cursor at/above the watermark: bounded replay may proceed; identity gaps alone are ignored.

A reset frame contains:

```text
event: reset
data: {"reason":"retention","max_pruned_event_id":123,"snapshot_url":"..."}
```

The browser client closes its EventSource, fetches the authorized snapshot (which returns a fresh `event_cursor`), and opens a new stream from that cursor. Slow-consumer buffer overflow and catch-up limit exhaustion use the same reset event with reasons `slow_consumer` or `catchup_limit`. Never loop forever trying to replay an unbounded ledger range.

### Race-free initialization

PostgreSQL's required order is LISTEN commit, inspect database in a new transaction, then rely on notifications; early notifications can duplicate inspected rows ([PostgreSQL 18 LISTEN](https://www.postgresql.org/docs/18/sql-listen.html)). Apply it at two levels:

1. Facade process establishes and commits its one dedicated LISTEN connection before SSE readiness.
2. An SSE handler authorizes and subscribes to the bounded in-process hub first.
3. It reads prune watermark and a database high-water id, then replays `(last_id, high_water]` in bounded pages.
4. It drains buffered live events with id `> high_water` and continues live, deduplicating by id.
5. If the subscriber buffer overflowed at any point, send reset instead of pretending continuity.

`emit_event` should notify one fixed channel with the inserted event id (or a payload-free nudge if fetching the high water is cheaper). The notification commits with the event; duplicate/missed hints are harmless because replay/poll is authoritative. One LISTEN connection per process feeds all clients; never one DB connection per SSE stream.

### Stream contract

- Job stream authorization comes from the job projection; queue stream requires queue `read`; no global stream without global read.
- Event fields are id, job id, queue when authorized, type, typed outcome, timestamp, and bounded sanitized message. Never payload, event data by default, attempt id, or fence.
- Per-process client cap and per-subscriber buffer cap are mandatory. Capacity rejection is `503/TQ503` with `Retry-After`.
- Heartbeat comments do not consume job-event ids.
- Maximum catch-up rows/time and stream lifetime are server-advertised limits.
- Multi-process duplication is fine: each process has its own listener/hub; ledger ids converge.

### Exact amendments if accepted

- Growth §5/§6: replace “catches up losslessly” with bounded replay/reset semantics.
- Unified Spec `emit_event` and §13 pruning: add nudge and atomic prune watermark.
- ADR-005 0.2 extension: SSE content type, event schemas, reset behavior, auth, limits.
- Harness: prune-while-disconnected, listen/replay race, duplicate nudge, process restart, slow consumer, multi-process, authorization, and redaction cases.

## 4. 0.1 fitness by first host

### outlabsAPI dogfood

| Need | Covered by accepted 0.1? | Remaining acceptance item |
|---|---|---|
| One FastAPI process, Postgres, embedded worker | Yes—ADR-008/feature 14 | Compose with existing lifespan; separate small worker/LISTEN pools |
| Immediate `tools` and `notifications` lanes | Yes | Define queue profiles/handlers and idempotency keys; no schedule needed |
| Durable 202 response and run status/result | Mostly | Freeze minimal `get_job`/result projection and bounded wait/poll helper |
| Retry/dead letter/redrive | Yes | Apply SQL blockers R2-01–R2-08 |
| Auth | Yes after prerequisite | outlabsAPI currently pins a20; upgrade to verified a24+ or start with static adapter |
| Graceful deploy | Yes in direction | Apply safe sync/thread contract; verify uvicorn/Coolify grace budget |
| Retire RabbitMQ lane by lane | Yes | Keep broker until each lane evidence packet passes; no big-bang |

Schedules, workflows, follow-ups, archive, SSE, and a bundled dashboard are unnecessary for the first tools/notifications slice.

### QDarte non-chaining pilot

| Need | Covered by accepted 0.1? | Remaining acceptance item |
|---|---|---|
| HTTP-only worker with OutLabsAuth service token | Yes | Service wildcard verified; authoritative job lookup and protocol T6 tests |
| Synchronous httpx client/worker integration | Intended but not named in ADR-009 | Make sync HTTP client and adapter an explicit 0.1 release gate |
| Claim/heartbeat/complete/fail/release/cancel | Yes | Complete missing bodies/protocol outcomes and safe sync cancellation |
| Concurrency caps | Yes, correctly retained | Keep B7/T3 gate; do not cut |
| Chains/dependencies | Deliberately no | Choose a lane that never returns follow-ups; TQ501 catches mistakes |
| Operational diagnosis | Yes in principle | Minimal job detail/stats and migrate-break readiness |

The same assessment applies to the current Diverse court pilot, with the additional source-confirmed gap that its `TaskqHeartbeatLoop` does not currently propagate `cancel_requested` to the blocking handler (`diverse-data-workers/.../worker.py`, lines 747–791).

## 5. Can anything accepted for 0.1 be cut?

**Recommendation: no further cut.** The apparently optional pieces each have a named first-host reason:

- concurrency caps are QDarte's load-bearing pilot requirement;
- migrate-break is recovery for SQL-as-contract upgrades;
- janitor/tick is required because schedules are deferred;
- FastAPI/outlabs integration is the two fleet entry path;
- embedded runtime is outlabsAPI's entry path;
- sync HTTP/runtime is required by both existing worker fleets;
- safe reads/metrics are how a pilot is debugged;
- T1–T8/B1–B4 are the evidence needed to trust a queue whose correctness lives in races and migrations.

The release can sequence delivery internally, but Stage 0 should not call 0.1 complete until every ADR-009 item has its exact function/protocol/test contract. The right simplification is strict release gating of 0.2/0.3 objects, not another cut to the accepted kernel.
