# taskq — Growth, Topology & Live Visibility

> **Status:** Design proposal — 2026-07-18 (post-ADR). §1–§2 mostly *restate and extend* decided material (spec §13, ADR-009/010); §3 (dedicated queue database) and §4–§5 (read-model API, SSE bridge) are **new proposals, pending acceptance** — none of them changes the 0.1 kernel contract, so they can be accepted independently of Stage 1.
> **Prompted by:** owner review questions — queue health at millions of rows, optional second database, PG18/19 exploitation, stats endpoints for a frontend, server-sent events.

---

## 1. Data growth: the two-home model, made explicit

The design's standing answer to "durable data that could grow into the millions" is that job data has **two homes with different physics**, and retention choice only ever moves the boundary between them:

| Home | Physics | Size rule |
|---|---|---|
| **Hot table** (`taskq.jobs`) | Claim-path indexes, HOT heartbeats, autovacuum-tuned churn | **Never holds history.** Steady state ≈ active jobs + terminal rows inside the hot-retention window. Millions of *active* rows is a backlog incident, not a storage mode |
| **Archive** (`taskq.jobs_archive`, 0.3) | Append-only, monthly partitions, frozen pages, retention = partition `DROP` (no dead tuples, O(1)) | Millions to hundreds of millions is fine — partition pruning keeps queries bounded; this is where "people want to keep everything" lives |

Everything already decided that protects this: dead letters stay hot 14d for redrive (§13.1); events prune in tiers with a partition recipe one migration away (§20.4); bulky outputs belong in app tables, `result` stays compact (§5); the maintenance CLI owns concurrent reindexing (ADR-010); `taskq_archive_default_rows` and `taskq_index_bytes` alert on rot (§12.2). In 0.1 (no archive yet, ADR-009) retention is bounded deletes — acceptable at outlabsAPI/pilot scale; the archive lands in 0.3 **before** the full Diverse cutover.

### 1.1 Proposed extension: named retention profiles

"Sometimes people want millions kept, sometimes they won't" becomes three named presets on the queue row (sugar over the existing `retention_hours` / `failed_retention_hours` + a new `archive_policy`), settable per queue:

| Profile | Succeeded/cancelled | Failed | Archive |
|---|---|---|---|
| `ephemeral` | delete at 1h | delete at 72h | never — queue-as-transport |
| `standard` (default) | 48h hot → archive (0.3) or delete (0.1) | 14d hot → archive | `keep_months = 6` |
| `archival` | 48h hot → archive | 14d hot → archive | `keep_months = null` (partitions never dropped; capacity is a disk decision, alerted via partition-size metric) |

Rules: profiles are **per queue**, resolved at janitor time (not stamped — retention is an ops dial, unlike retry policy); `archival` never exempts the hot table — history accumulates only in partitions; a `taskq stats storage` CLI/read-model reports per-queue hot rows, archive rows, and bytes per partition so "are we keeping too much" is a query, not a guess. Staging: `ephemeral`/`standard` semantics exist in 0.1 via the two retention columns; the named profiles + `archival` land with the archive in 0.3.

---

## 2. PG18/19 — already mapped (summary, no new work)

Spec §15 is the authority and is comprehensive; restated for this discussion:

- **Baseline PG16/17:** the correctness core needs nothing newer; `uuid7()` has a pure-SQL fallback.
- **PG18 (deploy target), exploited now:** native `uuidv7()` PKs; per-table autovacuum + `vacuum_truncate=off` in the DDL; global churn backstops (`autovacuum_vacuum_max_threshold`, `track_cost_delay_timing`); B-tree **skip scan** so type-filtered monitoring rides the claim index; `NOT NULL ... NOT VALID` for zero-scan hot-table evolution; eager freezing for the archive; **AIO** (`io_method=worker`) for vacuum/archive sweeps; stats-preserving `pg_upgrade`.
- **PG19 (GA ~Q4 2026), capability-gated and never load-bearing:** `INSERT ... ON CONFLICT DO SELECT` collapses the enqueue dedup tail; `REPACK CONCURRENTLY` for bloat recovery via the maintenance CLI; targeted NOTIFY wakeups (hint semantics unchanged); scored/parallel autovacuum; `pg_stat_lock` forensics; 64-bit multixact for `FOR SHARE` dep locks.

The installer records capabilities in `taskq.meta` and swaps function bodies where profitable — future-support is structural, not aspirational. Re-verify PG19 items at GA (§15.3 note stands).

---

## 3. Dedicated queue database (PROPOSAL — support as a named topology)

Not a silly idea — and the schema is **already split-ready by construction**: the no-external-FK rule (domain tables store job uuids as plain columns, nothing may `REFERENCES` into `taskq`) means nothing breaks when `taskq` lives in a different database. What changes is one property:

**What you gain (dedicated DB):**
- Bloat/vacuum isolation — queue churn can't pressure app tables, and app analytics can't pin the queue's MVCC horizon (the §17 watch item, solved structurally).
- Independent tuning, connection ceilings, backup/restore cadence (queue rows are usually re-derivable; app data isn't — different RPO), and disk blast radius.
- On Neon: a separate project isolates compute/autosuspend billing — the same split already applied to qdarte-intake for exactly this reason.

**What you lose:**
- **Transactional enqueue.** The outbox property (task commits atomically with domain writes — ADR-001's central DX advantage) requires sharing the caller's database. There is no 2PC path and we will not build one.

**Decision shape (proposed):** two named topologies, chosen per host:

| Topology | Enqueue semantics | When |
|---|---|---|
| `co-resident` (default) | Transactional (`session=` joins the host txn) | Any producer that pairs enqueues with domain writes — keep the default |
| `dedicated` | **Enqueue-after-commit** with mandatory idempotency keys: producer commits domain work, then enqueues (client retries on failure; a crash in the gap loses *intent*, which the idempotent re-trigger path or a host outbox covers) | Queue churn measurably hurting the app DB, separate scaling/billing, ops isolation |

Rules if accepted: the client surface is identical (one DSN points elsewhere); `dedicated` mode **refuses `session=`** loudly (a silent non-transactional enqueue masquerading as transactional is the one unacceptable failure); hosts needing atomic intent under `dedicated` keep a small app-side outbox relay — host-owned, not taskq machinery; migrations/verify/maintenance run against the queue DSN. Per-host recommendation today: everyone starts `co-resident`; QDarte is the first candidate to split if `worker_jobs`-era churn history repeats. Not an 0.1 work item — it needs only documentation + the `session=` refusal guard, so target 0.2.

---

## 4. Read-model API for a frontend (PROPOSAL — the P-21 sequence, concretized)

Versioned, read-only, **`read`-scoped** (queue-scoped per ADR-006) diagnostics endpoints in the facade — JSON models frozen in the ADR-005 protocol doc so a dashboard is just a client:

| Endpoint | Returns | Backing |
|---|---|---|
| `GET /taskq/v1/stats/queues` | per queue: depth by status, oldest-ready age, 15m rates, paused, worker count | tick snapshot (`control_state`, ≤1 tick stale) — never a hot-table aggregate (the DC 11.13 rule) |
| `GET /taskq/v1/stats/queues/{queue}` | detail + failures grouped by **typed outcome** | snapshot + partial-index counts |
| `GET /taskq/v1/stats/storage` | per-queue hot rows; archive partitions with rows/bytes | catalog + janitor-recorded sizes (§1.1) |
| `GET /taskq/v1/jobs?queue=&status=&job_type=&cursor=` | keyset-paginated projections (hard cap ≤200/page; **no payloads unless `?include=payload`**, which is `read` on that queue + explicitly requested) | partial indexes |
| `GET /taskq/v1/jobs/{id}` | job + attempts + events timeline (forensics view; **never fences**) | `job_events (job_id, id)` index |
| `GET /taskq/v1/workers` / `workflows/{id}` | presence / rollup | existing views |
| `GET /taskq/metrics` | Prometheus text | already specified (§12.2) |

`taskq inspect --json` shares the same read models (one contract, three consumers: CLI, dashboard, MCP-later). The operator UI is **not** part of the Python package: it is a separate Protocol-v1 client. **Stack and packaging are locked by [ADR-018](./adr/ADR-018-operator-ui-tech-stack.md)** (React + Vite + TanStack + Base UI; standalone first, embeddable mount later; AuthUI/qdarte-admin family). This section’s endpoint table remains a **proposal** until accepted via its own ADR / H-11. Staging: stats/jobs/detail endpoints are cheap and belong in **0.1** (they are how the pilot gets debugged); storage stats with the archive in 0.3; do not build the console ahead of those read models.

**ADR-019 reactivation:** the table above is historical direction, not the active
H-08/H-11 contract. The [Read Model Specification](./Task%20Queue%20Read%20Model%20Specification.md)
and Protocol revision 1.0.5 now own the exact queue-profile GET, finite
queue-scoped job-page views, profile ETag/conditional-update behavior, field
redaction, cursor, and B9 plan gates. The old broad `jobs?queue=&status=&job_type=`
sketch must not be implemented. Unproven views remain typed `TQ501`; the UI
waits for the accepted implementation, not merely this design decision.

---

## 5. Server-sent events (PROPOSAL — an SSE bridge over the events ledger, 0.2)

The spec's §19 rejection of "SSE from the queue" stands for the **core** — SSE must never become a delivery or correctness mechanism. But live dashboards and "follow this job" UX are legitimate, and the architecture already contains the right substrate: `taskq.job_events` is an append-only ledger with a monotonic bigint id.

**Design: SSE as a read-model bridge, resumable from the ledger.**

1. **One LISTEN connection per facade process** (the long-poll bridge already holds one) feeds an in-process broadcast hub; SSE handlers subscribe to filtered streams. Never a LISTEN or DB connection per SSE client — an open stream costs ASGI concurrency only.
2. **`Last-Event-ID` = `job_events.id`.** On (re)connect the handler replays `WHERE id > last_id` (bounded batch) from the table, then continues live from the hub. The browser `EventSource` reconnect contract maps 1:1 onto the ledger — NOTIFY stays a hint, the table stays the truth, and a dashboard that slept through a deploy catches up losslessly.
3. **Streams:** `GET /taskq/v1/events?job_id=` (follow one job — authorized via the ADR-006 projection) and `?queue=` (queue activity, `read`-scoped). Event payloads: event type, ids, typed outcome, timestamps — never fences, never job payloads.
4. **Budgets:** heartbeat comment ~15s; per-process max SSE clients (default 100, then 503 with `Retry-After`); optional max stream lifetime so proxies recycle. Multi-process ASGI is fine — each process has its own LISTEN + hub, and the ledger makes their views converge.
5. **Why SSE, not WebSockets:** one-way, proxy-friendly, native browser reconnect+resume — exactly the dashboard shape. Polling the §4 endpoints remains the fallback and the 0.1 answer.
6. This also gives **completion handles** (peer-research P-19) their transport ladder for free: `await handle.wait()` (SQL/long-poll) → SSE follow → plain polling.

Staging: not 0.1 (dashboards poll). Target 0.2 alongside the read models it streams; the only 0.1 obligation is *not foreclosing it* — which the events ledger already guarantees.

---

## 6. Acceptance path

Each section independently: §1.1 retention profiles and §3 dedicated topology need an ADR each (or a joint "operations topology" ADR) before implementation; §4's 0.1 endpoints should ride the ADR-005 protocol doc directly; §5 gets decided with the 0.2 composition release. Nothing here touches the frozen 0.1 kernel verbs.
