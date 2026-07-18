# taskq — Peer Patterns Research

> **Status:** Research notes — 2026-07-18 (second slice added same day, §7). **Peers are described generically, never named** — this repo's convention for all references to third-party projects: patterns and lessons are recorded; identities are not. Descriptors are stable across the doc family (e.g. "the Go/Postgres job queue" always means the same system).
> **Peers inspected (source clones):** four established queue systems — a mature Python/Postgres task library, a Go/Postgres job queue, a Node/Postgres SQL-first worker, a Node/Postgres queue library
> **Second slice (knowledge-pass, §7):** six more — an Elixir/Postgres job framework, a Postgres message-queue extension, two Rails-world Postgres queues, and two Redis/asyncio task libraries
> **Companion:** [`Task Queue Library Extraction Design Brief.md`](./Task%20Queue%20Library%20Extraction%20Design%20Brief.md), [`Task Queue — Unified Design Spec.md`](./Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md)
> **Normative feature specs (build from these, not from peer repos):** [`taskq-borrowed-features/`](./taskq-borrowed-features/README.md)
> **Goal:** Borrow DX and packaging patterns that keep taskq *configurable but simple* — without reopening the SQL-first correctness core.

**Note:** This file is provenance + ranking only. Implementation detail for every borrowed feature lives in `docs/taskq-borrowed-features/01`–`13`. Do not re-clone peer repos to implement.

---

## 0. Headline

Your protocol already beats these peers on the hard parts (CAS fencing, typed settle races, poison quarantine, concurrency admission, settle-txn followups). What they beat you on is **product surface**:

1. Enqueue results that never look like silent success
2. Small, declarative uniqueness / job-key modes
3. Handler control flow (`Snooze` / `Cancel`) as settle variants
4. Insert-only vs worker client by config omission
5. Tiny worker knobs; rich defaults on the **queue row**
6. Test helpers that make adoption one-liners
7. NOTIFY as a nudge; poll as truth

Steal those. Reject kitchen-sink policy matrices, multi-backend drivers, and `send() → null`.

---

## 1. Peer snapshots

| System | Stack | Strength | Weakness relative to taskq |
|---|---|---|---|
| **the mature Python/Postgres task library** | Python + SQL functions | Closest peer; Blueprint namespaces; retry UX; notify+poll; test connector swap | Unfenced finish-by-`job_id`; attrs; dual Django/SQL migration tracks |
| **the Go/Postgres job queue** | Go + Postgres | Best DX: insert-only clients, declarative unique-options, snooze/cancel, the Go peer's test harness, soft-stop | Logic in app more than PL/pgSQL; in-memory cron leader; driver abstraction tax |
| **the Node/Postgres SQL-first worker** | Node + versioned `.sql` | Job-key replace/throttle; tiny worker surface; migrate-break NOTIFY | Job-key race can still return null; thinner orchestration |
| **the Node/Postgres queue library** | Node + SQL-in-TS | Queue-as-config-unit; DLQ lineage + redrive; clear “NOTIFY is a hint” docs | Policy soup; `send()` still returns `null` on conflict; mega option surface |

---

## 2. Steal list (ranked for a 2-person team)

### S1 — Typed enqueue results (kill silent null) — **must**

the Node/Postgres queue library still resolves `null` on unique/throttle conflict (documented, still a footgun). the Node/Postgres SQL-first worker documents a high-contention race where `add_job` can return `null` even in replace mode.

**Borrow:** every enqueue path returns a structured outcome:

    created | existed | replaced | skipped_locked | conflict

Never `None` meaning “maybe fine.” Your unified spec already wants truthful `created` reporting — make it typed and mandatory in the Python client too.

### S2 — Job-key / uniqueness modes (the Node/Postgres SQL-first worker + the Go/Postgres job queue) — **must**

You already have idempotency via partial unique index. Steal the **named modes** so producers don’t invent semantics:

| Mode | Meaning | Source |
|---|---|---|
| `reject` / exist | Active key → return `existed` (your current default) | taskq today |
| `replace` | Overwrite unlocked job (debounce / reschedule) | the Node/Postgres SQL-first worker default |
| `preserve_run_at` | Overwrite payload but keep schedule (throttle window) | the Node/Postgres SQL-first worker |
| `by_args` (+ optional period) | Declarative uniqueness on payload subset | the Go/Postgres job queue declarative unique-options |

Keep `concurrency_key` separate: **enqueue identity ≠ runtime admission**.

the Go/Postgres job queue’s best DX detail: a unique-skipped-as-duplicate flag / `unique_skipped` on the insert result so callers can branch without treating duplicates as exceptions.

### S3 — `Snooze` / `Cancel` as handler settle results — **must**

the Go/Postgres job queue:

- a snooze-for-duration job-return value — does **not** burn attempts; `0` = immediately reworkable (great for soft interrupt)
- a cancel-with-error job-return value — terminal cancel from inside the handler

You already have snooze/release/cancel in SQL. Steal the **Python control-flow shape**: handlers return typed results (or raise only for real failures), runtime maps to settle functions. Prefer the Go/Postgres job queue’s semantics with taskq’s typed settle (`ok | already_settled | lost | …`), not Go’s error-as-control-flow.

### S4 — Insert-only client by omission — **should**

the Go/Postgres job queue: omit `Queues` / don’t `Start` → enqueue-only client. Same package, same models, no worker loop.

For taskq:

    TaskQ(dsn, handlers=...)                 # insert-only OK
    TaskQ(dsn, queues=["courts"], ...).run() # worker

Optional: validate known `job_type`s on enqueue (a skip-unknown-job check flag equivalent) so API processes fail fast on typos.

### S5 — Queue profiles as the config unit — **should**

the Node/Postgres queue library puts retry/retention/expire/DLQ on the **queue**, not every `send()`:

    createQueue('order-processing', {
      retryLimit: 5,
      retryDelay: 60,
      retryBackoff: true,
      deadLetter: 'order-processing-dlq',
    })

Maps cleanly onto your `taskq.queues` row + stamped-at-enqueue policy. Worker CLI stays tiny:

    concurrency | poll_interval | listen | soft_stop_timeout

Everything else is data on the queue / job row.

### S6 — NOTIFY nudge + poll truth — **should** (already in your spec; copy their clarity)

the Node/Postgres SQL-first worker: always-on `jobs:insert` → `nudge(n)`.  
the Node/Postgres queue library docs: notify is opt-in latency help; **polling always remains**.

Steal the Node/Postgres SQL-first worker’s simplicity + the Node/Postgres queue library’s wording. Only notify when a row is **immediately runnable** (not future-dated). Dedicated non-pooled LISTEN connection (the mature Python/Postgres task library / your spec).

### S7 — DLQ source lineage + `redrive()` — **should**

You have poison/failed as the dead-letter set. Steal the Node/Postgres queue library’s ops fields:

    source_queue, source_job_id, source_created_at, source_failure_count

so `redrive()` is a first-class function, not hand SQL. Fits §11.5 runbook.

### S8 — Blueprint / namespaced task registration — **should**

the mature Python/Postgres task library `Blueprint` → `app.add_tasks_from(bp, namespace="billing")` yields `billing:send_invoice`. Perfect for Diverse (courts/lists/…) and QDarte (discovery/content/…) without one mega registry.

Also steal `aliases` for rename-without-breaking in-flight `job_type`s.

### S9 — Retry UX: `bool | int | Strategy` — **should**

the mature Python/Postgres task library:

    retry=False | True | 5 | RetryStrategy(...)

Simple cases stay one keyword; power users get exponential/exception filters. Decisions still stamp onto the row — no live registry KeyError path (your F7 kill stays intact).

### S10 — Test helpers — **should**

| Steal | From |
|---|---|
| `require_enqueued(conn, JobArgs)` | the Go/Postgres job queue the Go peer's insert-assertion helper |
| `work(conn, handler, args)` in a tx | the Go/Postgres job queue the Go peer's in-test worker helper |
| a connector-swap hook / in-memory fake of *contract* | the mature Python/Postgres task library (fake claim/settle/enqueue, not full SQL reimplementation) |

### S11 — Soft-stop timeout + `stopped` awaitable — **should**

the Go/Postgres job queue: a soft-stop timeout option then cancel contexts; an awaitable stopped signal. Pair with your `release_job(p_cause='worker_shutdown')` and cooperative `ctx.raise_if_cancelled()` / hard-cancel after grace (already in §14).

### S12 — Migrate-break channel — **nice**

the Node/Postgres SQL-first worker NOTIFY `worker:migrate` so workers exit on breaking schema. Cheap safety for staged rollouts.

### S13 — SQL packaging conventions — **nice**

Steal the mature Python/Postgres task library/the Node/Postgres SQL-first worker organization:

    taskq/sql/
      schema.sql
      queries.sql          # -- claim_jobs -- named sections
      migrations/
        0.2.0_01_pre_....sql
        0.2.0_50_post_....sql

Versioned function names (`claim_jobs_v2`) during rollout. Prefer the Node/Postgres SQL-first worker’s numbered `.sql` over the Node/Postgres queue library’s SQL-in-TypeScript monolith.

---

## 3. Reject list (do not borrow)

| Pattern | Why |
|---|---|
| `send() → null` / undocumented silent skip | Highest footgun across peers |
| Full the Node/Postgres queue library policy matrix (`stately`, `short`, `exclusive`, …) | Overlaps `concurrency_key` + job_key; explodes simplicity |
| Multi-DB driver abstraction (the Go/Postgres job queue) | Postgres-only is a feature |
| In-memory leader-only cron (the Go/Postgres job queue) | Weak durability; you already designed SQL schedules |
| Unfenced settle by `job_id` (the mature Python/Postgres task library) | You already won this |
| Mega `work()` option surfaces (the Node/Postgres queue library) | Violates “configurable but simple” |
| Prefetch local queues (the Node/Postgres SQL-first worker) | Stuck-job risk; premature |
| Middleware/plugin/hook stacks early | One adapter surface (`http`, `outlabs`) is enough |
| Dual Django + raw SQL migration tracks | One installer path |

---

## 4. Conflicts — which peer wins

| Topic | Prefer | Reason |
|---|---|---|
| Enqueue identity | the Node/Postgres SQL-first worker job_key modes + the Go/Postgres job queue `unique_skipped` | Clear producer semantics |
| Runtime admission | taskq `concurrency_key` (keep) | Already stronger than pool-row locks |
| Wake path | the Node/Postgres SQL-first worker simplicity + the Node/Postgres queue library “hint only” docs | Best of both |
| Config home | the Node/Postgres queue library queue profiles + the Node/Postgres SQL-first worker-tiny workers | Simple ops model |
| Handler control flow | the Go/Postgres job queue snooze/cancel semantics in typed results | Matches your settle model |
| Schema ownership | the Node/Postgres SQL-first worker/the mature Python/Postgres task library `.sql` files | Matches SQL-first brief |
| Cron durability | taskq SQL schedules (not the Go/Postgres job queue in-memory) | Already specified |
| Auth / HTTP | Your extraction brief (outlabs optional) | Peers don’t solve this |

---

## 5. Concrete API sketch (borrowed DX, taskq guts)

    from datetime import timedelta
    from taskq import TaskQ, Blueprint, EnqueueOpts, declarative unique-options, Snooze, Cancel, Complete

    courts = Blueprint()

    @courts.task(
        queue="courts",
        job_type="missouri_casenet",
        retry=5,  # or RetryStrategy(max_attempts=5, exponential_wait=30)
        aliases=["missouri_casenet_v1"],
    )
    async def scrape_missouri(ctx, payload: dict):
        if quota_hit:
            return Snooze(timedelta(hours=1))      # no attempt burn
        if payload.get("abandoned"):
            return Cancel(reason="case_dismissed")
        records = await do_scrape(payload)
        return Complete(
            {"n": len(records)},
            followups=[Enqueue("enrich_county", {"county": payload["county"]}, step="enrich")],
        )

    app = TaskQ(dsn=..., worker_defaults=WorkerOptions(concurrency=2, listen=True))
    app.add_tasks_from(courts, namespace="courts")

    # API process — insert-only
    result = await app.enqueue(
        scrape_missouri,
        {"county": "Boone"},
        idempotency_key="courts:missouri:Boone:2026-07-18",
        unique=declarative unique-options(mode="reject"),  # or replace / preserve_run_at
    )
    # result.status in {created, existed, replaced, ...}  — never None

    # Worker process
    await app.run(queues=["courts"], soft_stop_timeout=timedelta(seconds=30))

---

## 6. What this changes in the extraction brief

These are now fully specified under [`taskq-borrowed-features/`](./taskq-borrowed-features/README.md):

| Steal | Spec |
|---|---|
| S1 Typed enqueue results | [01](./taskq-borrowed-features/01-typed-enqueue-results.md) |
| S2 Job-key modes | [02](./taskq-borrowed-features/02-job-key-and-uniqueness-modes.md) |
| S3 Handler settle results | [03](./taskq-borrowed-features/03-handler-settle-results.md) |
| S4 Insert-only client | [04](./taskq-borrowed-features/04-insert-only-client.md) |
| S5 Queue profiles | [05](./taskq-borrowed-features/05-queue-profiles.md) |
| S6 NOTIFY nudge + poll | [06](./taskq-borrowed-features/06-notify-nudge-and-poll.md) |
| S7 DLQ lineage + redrive | [07](./taskq-borrowed-features/07-dead-letter-lineage-and-redrive.md) |
| S8 Blueprints | [08](./taskq-borrowed-features/08-blueprints-and-namespaces.md) |
| S9 Retry value surface | [09](./taskq-borrowed-features/09-retry-value-surface.md) |
| S10 Test helpers | [10](./taskq-borrowed-features/10-test-helpers.md) |
| S11 Soft stop | [11](./taskq-borrowed-features/11-soft-stop-and-shutdown.md) |
| S12 Migrate break | [12](./taskq-borrowed-features/12-migrate-break-channel.md) |
| S13 SQL packaging | [13](./taskq-borrowed-features/13-sql-packaging-conventions.md) |
| S14 Telemetry hooks (2nd slice) | instrumentation contract rides [`Test & Benchmark Harness`](./Task%20Queue%20Test%20%26%20Benchmark%20Harness.md); feature doc when implemented |
| S15 Inline/drain testing modes (2nd slice) | [10 §2.5](./taskq-borrowed-features/10-test-helpers.md) |
| S16 Declarative schedule sync (2nd slice) | NICE — future feature doc |
| S17 Embedded worker (2nd slice) | [14](./taskq-borrowed-features/14-embedded-worker-and-fastapi-lifespan.md) |

Do **not** expand the worker CLI beyond a handful of knobs. Do **not** add a policy matrix.

---

## 7. Second slice — the Elixir/Postgres job framework / the Postgres message-queue extension / the Rails-native Postgres queue / the Rails in-process Postgres queue / a lean Redis/asyncio job library / a Redis/asyncio task library (completed 2026-07-18)

> **Provenance caveat:** knowledge-pass, not source-clone inspection (unlike §1's four peers). Verdicts are directional; verify any load-bearing detail against upstream docs before implementing.

### 7.1 Snapshots

| System | Stack | Strength | Verdict for taskq |
|---|---|---|---|
| **the Elixir/Postgres job framework** | Elixir + Postgres | Telemetry-first instrumentation; testing modes (inline/manual/drain); aggressive pruning defaults; unique-jobs periods | Steal telemetry hooks + testing modes; uniqueness already covered (feature 02 `by_period`) |
| **the Postgres message-queue extension** | Postgres extension | Pure message semantics (send/read/visibility timeout); archive-instead-of-delete with partition retention | Confirms two existing choices: partitioned archive + “taskq is a *job* queue, not a message bus”. Nothing new to borrow |
| **the Rails-native Postgres queue** | Rails + Postgres | First-class `pauses`; declarative recurring tasks reconciled from config at boot | Pause already exists; steal **declarative schedule sync** |
| **the Rails in-process Postgres queue** | Rails + Postgres | **Async mode: worker runs inside the web process** (thread pool in Puma); cron with jitter; batches with callbacks | Steal embedded-worker mode for FastAPI hosts (feature 14) — the single best borrow of this slice |
| **a lean Redis/asyncio job library** | Python + Redis | Minimal before/after hooks; tiny web UI; `group_key` anti-join caps | Hooks fold into telemetry protocol; admission already stronger (`concurrency_key` try-lock) |
| **a Redis/asyncio task library** | Python + Redis | asyncio-native worker shape; `defer_until`/`keep_result` TTLs | Confirm-only: `scheduled_at` + queue retention already cover both; Redis disqualifies the rest |

### 7.2 New steals (numbered to continue §2)

**S14 — Worker telemetry hooks (the Elixir/Postgres job framework Telemetry + a lean Redis/asyncio job library hooks) — should.**
A tiny instrumentation protocol on the worker runtime and client — `on_claim`, `on_job_start`, `on_job_settle(result, duration)`, `on_settle_race(kind)`, `on_heartbeat_error`, `on_tick(pass_timings)` — with a no-op default and a `contrib` Prometheus adapter. No middleware stack, no plugin registry (§3 rejection stands): one protocol, host passes one object. This is also what the bench harness (Test & Benchmark Harness doc) hangs measurements off, so instrumentation is exercised by CI rather than rotting.

**S15 — Inline + drain testing modes (its inline/manual testing modes) — should.**
Extends feature 10: (a) **inline mode** — enqueue executes the handler immediately in-process and records the settle (unit tests without a worker loop); (b) **`drain(queue)`** — synchronously claim+run queued jobs until empty (integration tests assert end state, not sleeps). Both explicitly test-only (`taskq.testing`), never a production execution mode.

**S16 — Declarative schedule sync (the Rails-native Postgres queue recurring config / the Elixir/Postgres job framework cron) — nice.**
`@bp.schedule(name, cron, payload, ...)` alongside task declarations + `sync_schedules(app, *, prune=…)` that upserts `taskq.schedules` rows at deploy and pauses (never deletes) rows that vanished from code. DB rows stay the runtime truth (Unified Spec §6); code becomes the *source* of them. Kills “the janitor schedule exists but nobody remembers why” drift.

**S17 — Embedded worker mode (the Rails in-process Postgres queue async) — should.**
Run claim loop + housekeeper inside the FastAPI process via lifespan for small hosts (outlabsAPI-scale: one container, no worker fleet). Full spec in feature 14 (`14-embedded-worker-and-fastapi-lifespan.md`) — includes the honest failure-mode table (event-loop starvation, deploy kills) and the graduation path to separate workers.

### 7.3 Confirmed non-borrows (second slice)

| Pattern | Why not |
|---|---|
| the Elixir/Postgres job framework leader election / Pro plugins (workflow engine, dynamic scaling) | Leaderless by design; advisory-lock tick already dedupes; workflows exist in core |
| the Postgres message-queue extension `pop()` / visibility-timeout message API | Different product. If a raw message bus is ever needed, run the message-queue extension beside taskq — do not blur job semantics into it |
| the Rails-native Postgres queue table-per-state layout | Deliberately rejected: one hot table + partial indexes (Unified Spec §4); archive handles the terminal set |
| the Rails-native Postgres queue semaphore rows | `concurrency_limits` + try-lock admission is stronger (no orphaned semaphores) |
| the Rails in-process Postgres queue advisory-lock-per-job execution | CAS + attempt ledger already fence; advisory locks don't survive connection loss cleanly |
| broker-based task frameworks (brokers) | Stack rule: Postgres-native, no brokers. Canvas primitives map: chain → settle followups, group → `workflow(kind='batch')`, chord → dep edges + `pending_deps` fan-in. Nothing missing |
| a multi-tenant task-orchestration platform / durable-execution platforms / a Postgres-backed durable-workflow platform | Durable-execution category; explicit non-goal (Unified Spec §1). Revisit only if DAG needs outgrow dep edges |

Slice closed. Any future peer pass should start from a live source clone, per §1 methodology.

---

## 8. Bottom line

| Keep (yours) | Steal (theirs) |
|---|---|
| PL/pgSQL contract, CAS, typed settle, poison, concurrency_key, followups | Typed enqueue outcomes, job-key modes, Snooze/Cancel DX, insert-only client, queue profiles, tiny workers, Blueprints, peer-style helpers, NOTIFY-as-nudge clarity |

The peers validate the extraction direction: **SQL owns correctness; the Python package owns delight.** Borrow delight aggressively; leave their correctness shortcuts on the floor.
