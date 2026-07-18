# taskq — Unified Postgres Task Queue (Final Synthesized Design)

> **Provenance:** Produced 2026-07-06 by a cross-repo audit of the Postgres task queues in `diverse-data-api` (`scrape_jobs` queue domain) and `qdarteAPI` (`qdarte_ops.worker_jobs` platform). The input reports cited by code (DC, DCP, QC, QO, PA, gap_analysis) were working artifacts of that analysis session; all `file:line` citations refer to the two repos as of this date. **Canonical home since 2026-07-18: the `outlabs-taskq` repo (this copy).** In-repo copies at diverse-data-api may lag (see its `Task Queue Docs Canonical Home.md`); qdarteAPI carries no copy. Vault index note: `lifeOS/Projects/Business/QDarte/Postgres Task Queue Cross-Repo Audit & Unified Design.md`.

**Status:** Design v1.6, 2026-07-18 — **Tier 3 (destination design)**: authoritative for semantics/rationale; the Tier-0 [0.1 Function Manifest](./Task%20Queue%200.1%20Function%20Manifest.md) + [Transport Protocol v1](./Task%20Queue%20Transport%20Protocol%20v1.md) win for 0.1 specifics — v1.1-final (adversarial review) + v1.2/v1.3 Codex addenda + v1.4 extraction fixes + v1.5 ADR fold-in + **v1.6 round-2 fold-in** (R2-01..R2-13 applied; ADR-011 accepted; see revision notes in §19). Synthesized from three competing designs (`design_simplicity.md`, `design_robustness.md`, `design_pg_native.md`) under a three-judge panel (votes: pg_native 2, simplicity 1). Skeleton = **pg_native**; every judge-mandated graft applied; conflicts reconciled explicitly in section 19.
**Replaces:** the `scrape_jobs` queue in diverse-data-api AND the `qdarte_ops.worker_jobs` platform in qdarteAPI.
**Baseline:** PostgreSQL 18 (both systems run it). Degrades to PG16/17; PG19 features are optional accelerators (section 15).
**Inputs:** gap_analysis.md, diverse_core.md (DC), diverse_control.md (DCP), qdarte_core.md (QC), qdarte_orchestration.md (QO), pg18.md, pg19.md, prior_art.md (PA).
**Cross-reference convention:** citations like `§18.10` or `§16.3.4` mean "numbered item 10 of §18" / "gate 4 of §16.3" — several late sections are numbered lists, not sub-headed subsections.

---

## 0. Executive summary

One schema (`taskq`), one hot table (`taskq.jobs`), six statuses, and a set of PL/pgSQL functions that ARE the contract. Any client — the Python library, the optional HTTP facade, or an operator in psql — drives the queue through the same functions; dedicated capability roles (`taskq_producer`/`taskq_runner`/`taskq_observer`/`taskq_operator`, ADR-010) have EXECUTE on the functions and **no direct DML on the tables**, so the fencing and budget invariants cannot be bypassed by a raw UPDATE.

Claiming is `FOR UPDATE SKIP LOCKED` over a purpose-built partial index; ownership fencing is a server-generated per-attempt UUID (`current_attempt_id`) CAS-checked on every mutation, backstopped by a partial unique index on the attempts ledger so a double-claim is a hard database error even for a rogue SQL writer. All time is database `now()` — client clocks never participate in lease, retry, or schedule math. Delay, retry backoff, and cron children are all `queued` rows with a future `scheduled_at`: there is no `scheduled` status and no promoter. Retries are exponential-with-jitter, computed from policy **stamped onto the row at enqueue** (no registry lookup can KeyError a retry). Lease expiry flows through the identical budget+backoff engine as handler failure, with a **poison quarantine**: three consecutive deaths-by-lease-expiry terminalize the job as `failed/poison` regardless of remaining budget, so one worker-killing payload cannot chew through the fleet.

Idempotency is a partial unique index plus `ON CONFLICT DO NOTHING` — the only enqueue mechanism, single and bulk. Settle calls (`complete`, `fail`, `release`, `snooze`) return **typed results** (`ok | already_settled | lost | retry_scheduled | dead`) instead of raising for expected races, and network-retried settles of any kind resolve to `already_settled` via the attempt ledger — a lost HTTP response is never again indistinguishable from theft. Releases and snoozes never consume retry budget; only failures and expiries do.

Per-resource concurrency caps (`concurrency_key` stamped at enqueue, `taskq.concurrency_limits` rows) are enforced at claim with a **try-lock admission protocol that is provably deadlock-free and never overshoots**. Orchestration is one layer: dependency edges + workflows for DAG/fan-in, and **followup enqueues executed inside the settle transaction** (with derived dedup keys) as the default chain mechanism — exactly-once chaining without a second state machine. Batch-run expansion is itself an ordinary claimed job with a cursor checkpoint, deleting Diverse's second lease system.

Housekeeping never rides a *successful* claim: a 5-second advisory-lock-deduped tick (savepoint-per-pass, so one failing pass cannot kill the rest) reaps leases and fires cron; a bounded **idle-claim micro-reap** (an *empty* claim runs one reap pass, limit 5) guarantees lease recovery even if every ticker dies; the daily janitor is structurally triggered — in 0.1 by a due-gated pass hardwired into the housekeeper tick, from 0.2 by a **seeded `taskq.schedules` row** — so "nothing ever calls maintenance" is impossible. Terminal rows move to a partitioned archive whose retention is partition DROP — deletion without dead tuples. Pure lease-bump heartbeats are HOT updates that touch zero indexes (the lease column is deliberately unindexed — the single highest-leverage bloat decision for the per-job hot path; lifecycle status flips are structurally non-HOT and get scheduled index maintenance instead, §13).

The whole 2am incident surface is one page of psql (section 11.5), and the design ships with a mandatory pre-cutover validation-gate test suite and an adversarial failure-mode audit (section 17). Migration is strangler-style, **qdarte first** (personal blast radius), **Diverse second** (protected income realm), per-lane, no big bang.

---

## Current shortcomings (what taskq replaces, and why)

This section is the self-contained problem statement — the defects of the two production systems that every mechanism below exists to kill. Citation codes used throughout the document resolve here and to the input reports: **DC** = diverse_core.md, **DCP** = diverse_control.md, **QC** = qdarte_core.md, **QO** = qdarte_orchestration.md, **PA** = prior_art.md; numbered items are the findings of gap_analysis.md §3.

**Correctness holes (jobs lost, duplicated, or wedged):**
- **Dedup by SELECT-then-INSERT** in both systems (QC F1, DC 11.4): two concurrent enqueues of the same key both pass the check — duplicate active jobs under exactly the concurrent-producer load dedup exists for. qdarte's variant is its top severity-1 finding.
- **Lost settle responses are indistinguishable from theft** (DCP 7.2, QC F5): a worker whose `complete` HTTP response is dropped retries, gets a conflict, and *discards the finished batch* — real completed work thrown away. There is no fencing token and no attempt ledger to distinguish "my settle already landed" from "another worker took this job".
- **No ownership fencing at all**: reclaim + slow original worker = two live executions with both allowed to settle (the primary double-execution window in both systems).
- **Lease expiry with zero backoff** (QC F6): an expired job requeues immediately — a crash-looping payload spins the fleet at full speed for its whole budget. Diverse's inverse (DC 11.6): compose-driven worker drains *consume* retry budget, so N innocent restarts + 1 real failure terminal-fails a healthy job.
- **`partial` status broke the terminal-set predicate** (DC §1.1): the dedup/idempotency predicate enumerated statuses, someone added `partial`, and settled jobs stopped counting as settled. Diverse's `uq_scrape_jobs_running` "safety net" is a no-op on the PK.
- **App-clock lease math** (QC F4): client timestamps participate in lease decisions, so clock skew moves correctness.
- **State-machine-bypassing manual resets** (DC 11.10): operators UPDATE status by hand (the rendering reset), silently corrupting budget/audit invariants — nothing prevents it.
- **Registry KeyError in the retry path** (QC F7): retry policy lives in the client registry; retire a job type and its in-flight rows 500 on settle and stick forever.

**Reliability-of-maintenance holes (silent stalls):**
- **Nothing structurally calls maintenance** (DC 11.3): housekeeping depends on a container someone must remember to run; when it silently dies, leases stop reaping and nobody notices until a queue freezes.
- **Reaper-in-read-path** (QC F2/F3): qdarte's list/detail *reads* mutate state (reclaim expired attempts) — monitoring load changes system behavior.
- **FK-wedged maintenance** (DC 11.2): archival hits an FK from a domain table into the job table and retries the same poisoned batch forever; the archiver also silently un-gates dependents (QC F15).
- **Catch-up loops O(missed intervals)** (QC F16): a schedule paused for months iterates millions of cron instants on resume. Diverse silently loses past-midnight windows (DCP 7.9).
- **`ps`-based orphan reclaim / presence-driven reclaim** (QO 7.1/7.3): process-table inspection as a correctness input — kills jobs on the wrong box, misses them across hosts.

**Throughput and bloat holes:**
- **Per-claim housekeeping** (DCP 7.5, QC F19): every claim call also runs reaping/promotion — the hot path pays for the cold path.
- **Pool-row `FOR UPDATE` convoy** (QC F9): qdarte serializes *every* claim across job types on one pool row.
- **No deterministic claim tiebreaker** (QC F10) and **priority-direction drift** (QO 7.8): call sites "boost" by adding in a lower-wins scheme.
- **Hot-table churn unmanaged** (PA §2.1): no fillfactor/autovacuum tuning, no archive, terminal rows accrete; stats endpoints do N+1 raw-error GROUP BYs (DC 11.13); pg_duckdb's analytics executor caught queue OLTP queries (DC 11.12).

**Contract and operability holes:**
- **Hand-mirrored transport models** (DCP 7.14): three separately-maintained copies of the job contract (SQL, API models, worker models) that drift.
- **Two lease systems** (DCP §1.2): Diverse's `queue_runs` dispatchers carry their own bespoke lease/recovery machinery beside the job lease.
- **No dead-letter redrive** in either system; no typed settle results (expected races surface as 500s); heartbeat loops die on first transport error (DCP 7.2); releasing workers immediately re-claim their own releases (QC F12, with a dead `skip_own_releases` flag); attempt/capability ids leak into read models (DCP 7.11); CSV imports enqueue with no idempotency keys (DCP 7.7); model/migration index drift (QC §1.1); business side effects run inside completion transactions (QC F13, QO 7.9), making settles slow, unretryable, and entangled with domain failures.

Every one of these is cited inline below at the mechanism that eliminates it.

---

## 1. Goals and non-goals

**Goals (priority order):**

1. **No interleaving of worker crashes, network partitions, duplicate deliveries, clock skew, or operator actions can (a) lose a job, (b) run its effects twice without detection, or (c) wedge a queue.** Every invariant that matters is enforced by the database — constraints, partial unique indexes, CAS updates, DB clock, role grants — never by application memory, `ps` output, client clocks, or runbook discipline alone.
2. **One canonical contract, SQL-first.** The protocol is a set of PL/pgSQL functions in schema `taskq`. The Python library, the HTTP facade, and psql are equal clients; no hand-mirrored transport models (kills DCP 7.14).
3. **Cover the real workloads:** scraping fleets (long leases, resume checkpoints, proxy pools, politeness caps), render pipelines (chaining, fan-out/fan-in), ingestion (transactional enqueue, bulk), cron with explicit bounded catch-up, per-resource caps (LM Studio = 1, proxy pool = N), and control operations (pause, drain, cancel, redrive, expire-worker).
4. **Runnable by a two-person team.** No leader election, no broker, no mandatory sidecars, no babysitting. Maintenance is self-scheduling; the system degrades gracefully with every optional component off; the incident surface is psql one-liners.

**Non-goals:** multi-tenant fairness engines (a multi-tenant task-orchestration platform territory, >20k tasks/min); exactly-once *execution* (impossible — we provide exactly-once state transitions plus the primitives for exactly-once effects); rate limiting beyond concurrency caps and retry-after hints; the queue as a permanent analytics store (archive + retention instead).

**Delivery semantics, stated honestly:** at-least-once execution; exactly-once state transitions (CAS-fenced, index-backstopped); exactly-once active-enqueue per idempotency key (index-enforced); exactly-once chain-step enqueue (settle-transaction followups); duplicate *effect* detection is the handler's job, and the design hands every handler the tokens it needs (stable `job_id` across attempts, unique `attempt_id` per attempt, `progress` checkpoint).

---

## 2. Naming and concept reconciliation

One canonical vocabulary, replacing both dialects (adoption glue between the two codebases — adopted verbatim from the pg_native design per judge mandate):

| Concept | Diverse today | qdarte today | taskq canonical |
|---|---|---|---|
| Worker subscription lane | `platform` | implicit (`supported_job_types`) | `queue` |
| Handler selector | `job_kind` | `job_type` | `job_type` |
| Job table | `scrape_jobs` | `worker_jobs` | `taskq.jobs` |
| Retry budget | `max_retries` (really attempts) | `max_attempts` | `max_attempts`, counted against `failure_count` |
| Delay column | `scheduled_for` | `scheduled_at` | `scheduled_at` |
| Sticky-key preference | `affinity_key` | — | `affinity_key` |
| Per-resource cap | — (fleet size only) | `concurrency_pool` (worker-declared) | `concurrency_key` (job-declared) + `taskq.concurrency_limits` |
| Batch/run grouping | `queue_runs` | `workflow_runs` | `taskq.workflows` (`kind = 'batch' \| 'dag'`) |
| Machine fleet | `worker_pools` | `worker_specs` | out of queue scope (per-project fleet tooling; reads taskq views) |
| Last error | `error` | `error_summary` | `error` |
| Priority | int, default 2, 0–10 | int, default 100, 0–1000 | smallint, default 100, 0–1000, **lower wins** |

The word **"pool" is banned** in taskq (it meant machines in Diverse and admission caps in qdarte — the most dangerous collision in gap §4.4). Machines are "fleet" (out of scope); admission caps are "concurrency limits".

---

## 3. State machine

### 3.1 Job statuses (table form)

| Status | Meaning | Claimable | Terminal |
|---|---|---|---|
| `blocked` | Has unsatisfied dependency edges (`pending_deps > 0`) | no | no |
| `queued` | Eligible when `scheduled_at <= now()`. Covers fresh, delayed, retry-backoff, snoozed, and cron children — retry is **data on `scheduled_at`**, not a status | yes | no |
| `running` | Leased to exactly one attempt (`current_attempt_id` set, `lease_expires_at` set) | no | no |
| `succeeded` | Completed | — | yes |
| `failed` | Retries exhausted, non-retryable failure, or **poison quarantine**. This IS the dead-letter set: rows stay for inspection; `taskq.redrive_job` is the redrive. Retained hot for `queues.failed_retention_hours` (default 14 days — deliberately longer than the 48h terminal default, so a weekend incident never falls off the redrive path; §13.1) | — | yes (redrivable) |
| `cancelled` | Operator or dependency cancellation. Never conflated with failure; never pollutes failure stats | — | yes |

There is **no `partial` status** (it caused Diverse's terminal-set idempotency-predicate bug, DC §1.1) — partial progress is data: the `progress` checkpoint column plus `outcome` text. There is **no `scheduled`/`waiting` status** — no promoter pass, no stuck-in-waiting bug class.

`outcome` (text, on terminal rows and attempts) carries the fine taxonomy: `success`, `retry_scheduled`, `retry_exhausted`, `non_retryable`, `poison`, `lease_expired`, `canceled`, `canceled_after_expiry`, `dep_failed`, `worker_shutdown`, `no_handler`, `snoozed`, `released`. Every value has exactly one assigner: `worker_shutdown`/`no_handler`/`released` are stamped by `release_job` from its typed `p_cause` parameter (§5.7); free-text reasons go in `error`/`stats`, **never** into `outcome` (the typed taxonomy is what monitoring GROUP BYs — DC 11.13's raw-text stats class stays dead). Redrive is recorded as a `redriven` *event* and resets the row's `outcome` to NULL; it is not an outcome value.

### 3.2 Legal transitions (complete list — anything else is a bug)

| From | To | Trigger | Budget (`failure_count`) |
|---|---|---|---|
| — | `queued` | `enqueue` (no unsatisfied deps) | — |
| — | `blocked` | `enqueue` (deps present) | — |
| — | `cancelled` | `enqueue` with an already-dead dependency (fail-closed) | — |
| `blocked` | `queued` | last dep satisfied inside the parent's `complete_job` | — |
| `blocked` | `cancelled` | a dependency failed/cancelled (cascade at settle, or the tick's dep-straggler sweep `finalize_dep_stragglers` — §5.9, §11.4), or operator cancel | — |
| `queued` | `running` | `claim_jobs` (`attempt_count += 1`; audit only) | not consumed |
| `queued` | `cancelled` | operator cancel | — |
| `queued`/`blocked` | `cancelled` | tick `finalize_cancel_stragglers`: a stale `cancel_requested_at` on a non-running row (cancel raced a requeue transition); actor `system` (§5.9, §11.4) | — |
| `running` | `succeeded` | `complete_job` (CAS) | — |
| `running` | `queued` | `fail_job` retryable, budget remains (CAS; `scheduled_at` = backoff) | **+1** |
| `running` | `queued` | lease expiry, budget remains (reaper; backoff; `expiry_streak += 1`) | **+1** |
| `running` | `queued` | `release_job` / `snooze_job` with no cancel pending (CAS; optional/caller delay) | **0 — never consumed** |
| `running` | `failed` | `fail_job` non-retryable, or budget exhausted (fail or expiry), or `expiry_streak` hits 3 (**poison**) | +1 / terminal |
| `running` | `cancelled` | worker acks cancel via settle — `fail_job`, `release_job`, **and `snooze_job`** all honor a pending `cancel_requested_at` (§5.7); handler-initiated `Cancel` settles through the fenced `cancel_running_job` (ADR-007, §4); or lease expires with `cancel_requested_at` set | — |
| `failed` | `queued` | operator `redrive_job` (`failure_count` reset; TQ409 if a new active job holds the same idempotency key) | reset |

### 3.3 Budget semantics (the table both old systems needed)

| Event | `attempt_count` (audit) | `failure_count` (budget) | Backoff applied | `expiry_streak` |
|---|---|---|---|---|
| Claim | +1 | — | — | — |
| `fail(retryable=true)` | — | +1 | yes (policy + jitter, or client hint) | reset to 0 |
| `fail(retryable=false)` | — | +1 → terminal `failed` | — | reset to 0 |
| Lease expiry | — | +1 | yes (same engine) | **+1**; at 3 → terminal `failed/poison` |
| Release (shutdown/drain/no-handler) | — | **0** | optional caller delay | reset to 0 |
| Snooze | — | **0** | caller-specified delay | reset to 0 |
| Complete | — | — | — | reset to 0 |
| Redrive | — | reset to 0 | — | reset to 0 |

`max_attempts` is compared against `failure_count`. N compose-driven drains + 1 real failure no longer terminal-fails a healthy job (DC 11.6); a crash-looping job cannot requeue forever with zero backoff (QC F6); a segfault payload cannot burn `max_attempts` worker crashes (poison quarantine — robustness graft).

---

## 4. Schema DDL

Everything lives in schema `taskq`. All timestamps are `timestamptz` (naive timestamps banned — DC's `TIMESTAMP WITHOUT TIME ZONE` mistake). All IDs are UUIDv7 (server-side `uuidv7()` on PG18+, `taskq.uuid7()` SQL fallback on PG16/17 — Appendix B of the pg_native draft, carried forward). Every index is declared in exactly one place (the installer), and a test asserts live schema == installer output (kills qdarte's model/migration index drift, QC §1.1).

**Status columns are `text` + CHECK, not enums** (reconciliation: judge 3 mandate; see §19). Same DB enforcement; adding/renaming a status is a plain constraint migration instead of `ALTER TYPE` surgery — which matters because the dedup index predicate enumerates statuses and must be revisited together with any status change.

```sql
CREATE SCHEMA IF NOT EXISTS taskq;

-- Belt-and-braces: no table outside taskq may ever FK into taskq tables.
-- Domain tables store job uuids as PLAIN columns (see §14 and the migration FK checklist).
-- ENFORCEMENT IS THE OWNERSHIP SPLIT, stated honestly: REFERENCES requires a grant on the
-- referenced table, and taskq_owner owns the schema while the app/migration role is granted
-- NOTHING on it (a bare `REVOKE REFERENCES ... FROM PUBLIC` would be a no-op — REFERENCES is
-- never in PUBLIC's default table privileges). Where migrations run as a superuser/owner role
-- (single-role app setups, Neon default), the rail is convention + the migration-time
-- information_schema sweep in §16.2.5 — that limitation is documented, not papered over.

-- ---------------------------------------------------------------------------
-- taskq.uuid7() — PG18 native, PG16/17 pure-SQL fallback
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION taskq.uuid7() RETURNS uuid
LANGUAGE sql VOLATILE PARALLEL SAFE AS $$ SELECT uuidv7() $$;
-- Installer swaps in the RFC-9562 pure-SQL body when server_version_num < 180000.

-- ---------------------------------------------------------------------------
-- Composite return types (the contract's response shapes)
-- ---------------------------------------------------------------------------
CREATE TYPE taskq.claimed_job AS (
    job_id            uuid,
    queue             text,
    job_type          text,
    priority          smallint,
    payload           jsonb,
    headers           jsonb,
    progress          jsonb,          -- checkpoint from prior attempts (resume support)
    attempt_id        uuid,           -- fencing token for every later call
    attempt_number    int,
    failure_count     smallint,
    max_attempts      smallint,
    lease_expires_at  timestamptz,
    workflow_id       uuid,
    step_key          text
);

CREATE TYPE taskq.settle_result AS (
    result       text,               -- 'ok' | 'already_settled' | 'lost' | 'retry_scheduled' | 'dead'
    job_status   text,
    scheduled_at timestamptz         -- next run time when retry_scheduled
);

-- ---------------------------------------------------------------------------
-- Queue registry: pause switch, per-queue defaults, optional depth guard
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.queues (
    name                  text PRIMARY KEY CHECK (name ~ '^[a-z0-9_]{1,57}$'),
                          -- 57, not 63 (v1.6, R2-07): the per-queue NOTIFY channel is
                          -- 'taskq_' || name and PostgreSQL identifiers truncate at 63
                          -- bytes — a 63-byte queue name would produce a silently
                          -- truncated 69-byte channel.
    paused_at             timestamptz,                 -- pauses CLAIMS; intake continues
    pause_reason          text,
    default_priority      smallint    NOT NULL DEFAULT 100 CHECK (default_priority BETWEEN 0 AND 1000),
    default_lease_seconds int         NOT NULL DEFAULT 300 CHECK (default_lease_seconds BETWEEN 15 AND 86400),
    default_max_attempts  smallint    NOT NULL DEFAULT 5   CHECK (default_max_attempts BETWEEN 1 AND 100),
    default_backoff_mode  text        NOT NULL DEFAULT 'exponential'
                                      CHECK (default_backoff_mode IN ('fixed','exponential')),
    default_backoff_base  int         NOT NULL DEFAULT 30   CHECK (default_backoff_base BETWEEN 1 AND 86400),
    default_backoff_cap   int         NOT NULL DEFAULT 3600 CHECK (default_backoff_cap >= default_backoff_base),
    retention_hours       int         NOT NULL DEFAULT 48,  -- terminal rows stay hot this long
    failed_retention_hours int        NOT NULL DEFAULT 336, -- dead letters stay hot LONGER (14d):
                                                            -- redrive targets the hot table only (§13.1)
    max_depth             int CHECK (max_depth IS NULL OR max_depth > 0),
                                      -- NULL = unlimited; ADVISORY producer backpressure (TQ429)
    notify_enabled        boolean     NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Workflows: the single grouping/orchestration layer (batch-of-N and DAG)
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.workflows (
    id            uuid PRIMARY KEY DEFAULT taskq.uuid7(),
    workflow_key  text UNIQUE,                          -- idempotent creation (run keys)
    kind          text NOT NULL DEFAULT 'dag' CHECK (kind IN ('dag','batch')),
    status        text NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed','cancelled')),
    params        jsonb,
    stats         jsonb,
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);
CREATE INDEX workflows_open_idx ON taskq.workflows (created_at) WHERE status = 'running';

-- ---------------------------------------------------------------------------
-- Per-resource concurrency caps. max_running = 0 is a pause valve.
-- A key with NO row here defaults to max_running = 1 at claim (fail-closed mutex).
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.concurrency_limits (
    key         text PRIMARY KEY CHECK (key ~ '^[a-z0-9_.:-]{1,120}$'),
    max_running int  NOT NULL CHECK (max_running >= 0),
    note        text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- THE hot table
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.jobs (
    id                     uuid PRIMARY KEY DEFAULT taskq.uuid7(),
    queue                  text  NOT NULL REFERENCES taskq.queues(name),
    job_type               text  NOT NULL CHECK (char_length(job_type) <= 120),
    status                 text  NOT NULL DEFAULT 'queued'
        CONSTRAINT jobs_status_ck CHECK (status IN
            ('blocked','queued','running','succeeded','failed','cancelled')),
    priority               smallint NOT NULL DEFAULT 100 CHECK (priority BETWEEN 0 AND 1000),
    payload                jsonb NOT NULL DEFAULT '{}'::jsonb,   -- IMMUTABLE original request
    headers                jsonb,                                -- trace ids, payload schema version
    idempotency_key        text CHECK (char_length(idempotency_key) <= 255),
    concurrency_key        text CHECK (char_length(concurrency_key) <= 120),
    affinity_key           text CHECK (char_length(affinity_key) <= 120),
    workflow_id            uuid REFERENCES taskq.workflows(id) ON DELETE SET NULL,
    step_key               text,
    parent_job_id          uuid,                                 -- lineage only; NO FK (parent may archive first)
    pending_deps           smallint NOT NULL DEFAULT 0 CHECK (pending_deps >= 0),
    -- scheduling & lease (the lease lives HERE; attempts are pure history)
    scheduled_at           timestamptz NOT NULL DEFAULT now(),
    lease_seconds          int NOT NULL CHECK (lease_seconds BETWEEN 15 AND 86400),
    lease_expires_at       timestamptz,                          -- DELIBERATELY UNINDEXED (see notes)
    worker_id              text,
    current_attempt_id     uuid,
    -- budget (section 3.3)
    attempt_count          smallint NOT NULL DEFAULT 0,          -- claims; audit only
    failure_count          smallint NOT NULL DEFAULT 0,          -- consumed budget
    release_count          smallint NOT NULL DEFAULT 0,          -- release-loop observability
    expiry_streak          smallint NOT NULL DEFAULT 0,          -- consecutive lease-expiry deaths (poison)
    max_attempts           smallint NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
    backoff_mode           text NOT NULL CHECK (backoff_mode IN ('fixed','exponential')),
    backoff_base_seconds   int NOT NULL,
    backoff_cap_seconds    int NOT NULL,
    -- control
    cancel_requested_at    timestamptz,
    cancel_reason          text,
    -- outputs
    progress               jsonb,                                -- resume checkpoint; survives retries
    result                 jsonb,                                -- compact; bulky output -> app tables
    error                  text,                                 -- truncated to 2000 by the functions
    outcome                text,
    finished_by_attempt_id uuid,                                 -- idempotent-settle support
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    started_at             timestamptz,
    finished_at            timestamptz,
    CONSTRAINT jobs_running_shape CHECK (
        (status = 'running') = (current_attempt_id IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CONSTRAINT jobs_terminal_shape CHECK (
        (status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL))
) WITH (
    fillfactor = 85,                        -- HOT-update headroom for HEARTBEATS ONLY: status flips
                                            -- and requeues touch partial-index predicate columns and
                                            -- are structurally non-HOT (see §13.6 for the honest model)
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold    = 500,
    autovacuum_vacuum_cost_delay   = 0,
    autovacuum_analyze_scale_factor = 0.02,
    vacuum_truncate = off,                  -- no ACCESS EXCLUSIVE truncation stalls on the hot table
    toast.autovacuum_vacuum_scale_factor = 0.02,  -- progress jsonb rewrites dead-chunk TOAST;
    toast.autovacuum_vacuum_threshold    = 1000   -- default thresholds are too lazy for it (§5.4)
);

-- Claim path: predicate matches the claim WHERE exactly; order matches the claim ORDER BY.
CREATE INDEX jobs_claim_idx ON taskq.jobs (queue, priority, scheduled_at, id)
    WHERE status = 'queued' AND cancel_requested_at IS NULL;

-- Affinity variant (claim preference only, never exclusivity).
CREATE INDEX jobs_affinity_idx ON taskq.jobs (queue, affinity_key, priority, scheduled_at)
    WHERE status = 'queued' AND cancel_requested_at IS NULL AND affinity_key IS NOT NULL;

-- THE dedup authority. Predicate enumerates ACTIVE statuses (never "NOT IN terminal" —
-- adding a status later must force revisiting this predicate; DC §1.1 made structural).
CREATE UNIQUE INDEX jobs_idem_uq ON taskq.jobs (queue, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running');

-- Running set: small (<= worker count). Serves BOTH the reaper scan AND concurrency
-- counting. lease_expires_at is DELIBERATELY UNINDEXED so every heartbeat is a HOT
-- update touching zero indexes — the reaper scans this small partial index and filters
-- lease expiry in the heap. The single highest-leverage bloat decision in the design
-- (simplicity graft, judge 2 mandate; judge 3: benchmark before finalizing — see §18.10).
CREATE INDEX jobs_running_idx ON taskq.jobs (concurrency_key)
    WHERE status = 'running';

-- Archival sweep + stats windows.
CREATE INDEX jobs_finished_idx ON taskq.jobs (finished_at)
    WHERE status IN ('succeeded','failed','cancelled');

-- Workflow membership.
CREATE INDEX jobs_workflow_idx ON taskq.jobs (workflow_id) WHERE workflow_id IS NOT NULL;

-- Deliberately absent: a job_type column in jobs_claim_idx (workers subscribe per queue;
-- on PG18 type-filtered monitoring rides B-tree skip scan over the low-cardinality
-- prefix — every extra index is write amplification on the hottest table). Also
-- deliberately absent: any index on lease_expires_at (above).

-- ---------------------------------------------------------------------------
-- Attempts: durable per-claim ledger + the DB-level one-running-attempt guard.
-- The attempt row id IS the fencing token handed to the worker.
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.job_attempts (
    id            uuid PRIMARY KEY,                       -- == jobs.current_attempt_id while running
    job_id        uuid NOT NULL REFERENCES taskq.jobs(id) ON DELETE CASCADE,
    worker_id     text NOT NULL,
    status        text NOT NULL DEFAULT 'running'
        CONSTRAINT attempts_status_ck CHECK (status IN
            ('running','succeeded','failed','released','snoozed','expired','cancelled')),
    outcome       text,
    claimed_at    timestamptz NOT NULL DEFAULT now(),
    lease_seconds int NOT NULL,
    finished_at   timestamptz,
    error         text,
    stats         jsonb
) WITH (
    fillfactor = 90,
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold    = 1000
);
-- The REAL safety net (Diverse's uq_scrape_jobs_running was a no-op on the PK):
-- a double-claim is a hard DB error even for direct-SQL writers.
CREATE UNIQUE INDEX uq_job_attempts_running ON taskq.job_attempts (job_id) WHERE status = 'running';
CREATE INDEX job_attempts_job_idx ON taskq.job_attempts (job_id, claimed_at);

-- ---------------------------------------------------------------------------
-- Events: append-only audit. No FK (pruning decoupled from job archival;
-- highest-churn table). Identity PK (no timestamp ties); BRIN for time pruning.
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.job_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id      uuid NOT NULL,
    attempt_id  uuid,
    event_type  text NOT NULL CHECK (char_length(event_type) <= 64),
    actor       text,                    -- worker_id | 'operator:<who>' | 'system'
    message     text,                    -- truncated by taskq.emit_event (load-bearing: an
    data        jsonb,                   --   oversized error must never 500 a settle — DC lesson)
    created_at  timestamptz NOT NULL DEFAULT now()
) WITH (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold    = 2000
);
CREATE INDEX job_events_job_idx   ON taskq.job_events (job_id, id);
CREATE INDEX job_events_time_brin ON taskq.job_events USING brin (created_at);

CREATE OR REPLACE FUNCTION taskq.emit_event(
    p_job_id uuid, p_attempt_id uuid, p_event_type text,
    p_actor text, p_message text, p_data jsonb DEFAULT NULL
) RETURNS void LANGUAGE sql AS $$
    INSERT INTO taskq.job_events (job_id, attempt_id, event_type, actor, message, data)
    VALUES (p_job_id, p_attempt_id, p_event_type, p_actor, left(p_message, 500), p_data);
$$;

-- ---------------------------------------------------------------------------
-- Dependency edges. Satisfied edges are DELETED at unlock, so a surviving edge
-- always means "still gating". depends_on has NO ON DELETE action (RESTRICT
-- semantics): the archiver cannot remove a job that still gates someone (kills
-- qdarte F15's silent un-gating) and its selection SKIPS such parents instead of
-- wedging the batch (kills DC 11.2). A dependent's own edges cascade away with it.
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.job_deps (
    job_id      uuid NOT NULL REFERENCES taskq.jobs(id) ON DELETE CASCADE,
    depends_on  uuid NOT NULL REFERENCES taskq.jobs(id),   -- NO ACTION = restrict-on-delete
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, depends_on)
);
CREATE INDEX job_deps_reverse_idx ON taskq.job_deps (depends_on);

-- ---------------------------------------------------------------------------
-- Cron schedules. Cron parsing is a client concern (croniter); the ROW is the
-- coordination point. Per-schedule catch-up policy (robustness + pg_native grafts).
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.schedules (
    name           text PRIMARY KEY CHECK (name ~ '^[a-z0-9_.-]{1,120}$'),
    queue          text NOT NULL REFERENCES taskq.queues(name),
    job_type       text NOT NULL,
    cron           text NOT NULL,                        -- 5-field cron, or '@every Ns'
    timezone       text NOT NULL DEFAULT 'UTC',
    payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
    priority       smallint,
    lease_seconds  int,
    max_attempts   smallint,
    concurrency_key text,
    catchup_policy text NOT NULL DEFAULT 'fire_once'
                   CHECK (catchup_policy IN ('skip','fire_once','fire_all')),
    max_catchup    smallint NOT NULL DEFAULT 10 CHECK (max_catchup BETWEEN 1 AND 1000),
    paused_at      timestamptz,
    next_fire_at   timestamptz NOT NULL,
    last_fired_at  timestamptz,
    last_error     text,                                 -- bad cron string surfaces HERE, never hot-loops
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX schedules_due_idx ON taskq.schedules (next_fire_at) WHERE paused_at IS NULL;

-- ---------------------------------------------------------------------------
-- Worker presence — observability + drain signalling ONLY. Never an input to
-- reclaim (lease expiry is the only recovery authority; QO 7.3's ps-yanking is dead).
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.workers (
    worker_id             text PRIMARY KEY,
    queues                text[] NOT NULL,
    hostname              text,
    pid                   int,
    version               text,
    meta                  jsonb,
    started_at            timestamptz NOT NULL DEFAULT now(),
    last_seen_at          timestamptz NOT NULL DEFAULT now(),
    shutdown_requested_at timestamptz
);
CREATE INDEX workers_seen_idx ON taskq.workers (last_seen_at);

-- ---------------------------------------------------------------------------
-- Cold archive: partitioned monthly on finished_at; retention = partition DROP
-- (deletion without dead tuples). A DEFAULT partition guarantees a missed
-- rotation can never block archival (answers the "babysitting" critique — §19).
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.jobs_archive (
    LIKE taskq.jobs INCLUDING DEFAULTS,
    attempts     jsonb,                                  -- attempt ledger aggregated into the row
    archived_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (finished_at, id)
) PARTITION BY RANGE (finished_at);
CREATE TABLE taskq.jobs_archive_default PARTITION OF taskq.jobs_archive DEFAULT;
CREATE INDEX jobs_archive_queue_idx ON taskq.jobs_archive (queue, job_type, finished_at);
CREATE INDEX jobs_archive_idem_idx  ON taskq.jobs_archive (queue, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
-- Bare-id lookups (enqueue's archived-dependency resolution §5.2, lineage forensics)
-- would otherwise scan every partition — the partitioned PK leads on finished_at:
CREATE INDEX jobs_archive_id_idx ON taskq.jobs_archive (id);

-- ---------------------------------------------------------------------------
-- Tick/janitor coordination state (timings + last error per pass; feeds the
-- taskq_tick_age_seconds alert — robustness graft).
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.control_state (
    key              text PRIMARY KEY,
    last_started_at  timestamptz,
    last_finished_at timestamptz,
    last_error       text,
    data             jsonb          -- pass-specific state; the tick's queue_stats
                                    -- snapshot row (§12.1) lives here, key 'stats_snapshot'
);

-- ---------------------------------------------------------------------------
-- Meta: installed contract version + detected capabilities
-- ---------------------------------------------------------------------------
CREATE TABLE taskq.meta (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- rows: ('contract_version','"1.0"'),
--       ('capabilities','{"uuidv7":true,"returning_old_new":true,"on_conflict_do_select":false}')
-- contract_version tracks the SQL contract, NOT this document's revision (v1.x) and NOT the
-- Python package version. It bumps only on contract-visible SQL changes; clients assert
-- compatibility against it at startup and stop on skew (borrowed-feature 12's matrix +
-- min_client_version). The library maps package version -> supported contract range.
```

**Installer seeding — staged by release (v1.6, R2-09/ADR-009):** the **0.1** migration seeds only real queues/profiles and the `control_state` rows (`tick`, `janitor_daily` due marker) — the 0.1 janitor trigger is the housekeeper tick's due-gated pass (§11.4), and neither `_system` nor `taskq.schedules` exists yet. The block below is the **0.2** migration's seeding, restoring the structural schedule-row trigger:

```sql
-- 0.2: the janitor becomes a schedule row, so "nothing ever calls maintenance"
-- (DC 11.3) is impossible even if the worker-tick convention erodes (robustness graft):
INSERT INTO taskq.queues (name) VALUES ('_system') ON CONFLICT DO NOTHING;
INSERT INTO taskq.schedules (name, queue, job_type, cron, next_fire_at, catchup_policy, max_catchup)
VALUES ('taskq-janitor', '_system', 'taskq.janitor', '30 6 * * *', now(), 'fire_once', 1)
ON CONFLICT (name) DO NOTHING;
```

**Role model (v1.5, ADR-010 — capability roles replace the original two-role model).** Five roles:

| Role | Capability |
|---|---|
| `taskq_owner` | `NOLOGIN`; owns the schema and every object; runs the installer; never an application login |
| `taskq_producer` | EXECUTE on enqueue (+ its typed results) |
| `taskq_runner` | EXECUTE on claim, heartbeat, and every fenced settlement verb |
| `taskq_observer` | EXECUTE on safe read functions + SELECT on views; no mutation |
| `taskq_operator` | EXECUTE on pause/resume, cancel, redrive, expire, and the transactional maintenance functions |

Deployment credentials are memberships: a facade DB user typically holds producer+runner+observer; ops CLIs add operator. **No role except `taskq_owner` has DML on any taskq table** — the functions are `SECURITY DEFINER`, turning "never UPDATE taskq.jobs by hand" from a runbook rule into a DB-enforced invariant. Runtime DDL (archive partition rotation) stays packaged as owner-owned `SECURITY DEFINER` functions (`taskq.rotate_archive_partitions`) EXECUTE-granted to `taskq_operator` (§13.3). Where older text in this document says `taskq_worker`, read "the capability role granted that function family" (the umbrella name may persist as a legacy grant during host migration only).

**SECURITY DEFINER hardening contract (ADR-010 — normative for every taskq function):**
1. Owned by `taskq_owner` (`NOLOGIN`).
2. `SET search_path = pg_catalog, taskq, pg_temp` on the function — safe to pin literally because the schema is fixed (ADR-002).
3. Fully schema-qualified references in bodies anyway (belt to the pinned path's braces).
4. `REVOKE EXECUTE ... FROM PUBLIC` **in the same migration that creates the function** — PostgreSQL grants EXECUTE to PUBLIC by default, so a created-but-not-revoked definer function is callable by any database user.
5. `GRANT EXECUTE` to the smallest capability role that needs it.
6. No schema/table/function identifier is ever interpolated from caller input.
7. Privilege-regression tests run as untrusted roles, including shadow-object attempts (harness T2).

**Because raw DML is denied, every documented flow has a function.** The contract therefore includes — beyond the lifecycle functions of §5 — the coordination and operator functions that earlier drafts left as raw SQL: `taskq.claim_due_schedules(limit)` / `fire_schedule(name, fired_at, next_fire_at)` / `schedule_error(name, error, retry_at)` (the cron protocol, §6); `taskq.create_workflow(workflow_key, kind, params, actor)` (idempotent on `workflow_key`; required by §14's `workflow=` and §16.2.3's batch runs); `taskq.set_concurrency_limit(key, max_running, actor)`; `taskq.request_worker_shutdown(worker_id => NULL, queue => NULL, actor)` (NULL worker_id = fleet-wide; `queue` filters one lane); and **`taskq.cancel_running_job(job_id, attempt_id, worker_id, reason) -> taskq.settle_result`** (ADR-007 — the fenced worker-side cancel: accepts only the matching running attempt, replays resolve to `already_settled`, stale fences get `lost`, lands `cancelled`/outcome `canceled` with budget untouched; the handler `Cancel(...)` result maps here and only here — `cancel_job` stays operator-only). There is **no** flow in this document that requires an application role to touch a table directly — any such flow is a design bug by definition.

```sql
-- Applied to every application capability role (producer/runner/observer/operator):
ALTER ROLE taskq_runner SET statement_timeout = '30s';
ALTER ROLE taskq_runner SET idle_in_transaction_session_timeout = '10s';
-- Diverse pg_duckdb substrate only: pin the OLTP queue off the analytics executor (DC 11.12):
ALTER ROLE taskq_runner SET duckdb.execution = off;
```

Human operators use a personal role granted `taskq_operator`; superuser DML on taskq tables remains possible but is outside the paved path (runbook rule §11.5 backs the grant).

---

## 5. Core operations — the exact SQL

All lifecycle functions run in one short transaction, use DB time exclusively, and return **typed results** for expected races (`'lost'`, `'already_settled'`) so clients and the HTTP facade map them deterministically (409, not 500). Exceptions are reserved for caller errors (unknown queue `TQ001`, depth `TQ429`, redrive collision `TQ409`). Row locks are never held while a job executes — ownership during execution is the lease.

**Status of the SQL below:** these bodies are the *normative reference implementation* — the load-bearing lines (the CAS WHERE clauses, the ON CONFLICT predicate, the try-lock admission, the lock-ordering discipline) are contract, not illustration. The shipping source of truth is the `taskq` package installer; this document is re-synced only when contract-visible behavior changes. **Lock-ordering discipline (global, deadlock-freedom — v1.6, R2-06, replaces the uuidv7 time-ordering argument):** the proof is **graph-based**, never time-based. Invariants: (1) a dependency edge is only ever created from a newly inserted dependent to already-existing parents, and edges are immutable until deletion — so the public contract cannot introduce cycles; (2) every multi-row operation acquires **ancestor/parent rows before dependent rows**; (3) rows at the **same graph frontier** (a dep list, a dependent set, an arbitrary operator batch) are locked in ascending-id order as a deterministic *tie-break only*; (4) no function may use creation time, uuid version, or generator behavior as a correctness premise — uuidv7 buys index locality and FIFO tie-breaking, nothing more (two sessions in the same millisecond can produce ids whose sort order reverses causality); (5) `SKIP LOCKED` passes stay convergent via bounded tick sweeps; waiting operations all share the parent-frontier-then-id order. Caller-supplied job ids are **not accepted in 0.x** (no host needs them; if ever added, any UUID version must be safe because the proof no longer depends on id order). **PL/pgSQL rowcount discipline:** zero-row `UPDATE ... RETURNING ... INTO` leaves its target NULL and `IF NOT <null>` never fires — every fence check below therefore uses `IF NOT FOUND` (or `GET DIAGNOSTICS`), never a `RETURNING true INTO`-style flag. **Public-boundary validation (v1.6, R2-07):** every application-callable function validates its inputs at entry with registered TQ SQLSTATEs (`USING ERRCODE`) — worker/actor ids non-empty, claim batch 1–50, lease override 15–86400s, retry/snooze/release delays 0–30d, priority 0–1000, JSON arguments type/size-checked — so direct SQL callers get the same failure shapes the facade models, never raw cast/check errors. These are normative rules for the implementation, and the validation gates assert the fenced paths (§16.3). **PL/pgSQL rowcount discipline:** zero-row `UPDATE ... RETURNING ... INTO` leaves its target NULL and `IF NOT <null>` never fires — every fence check below therefore uses `IF NOT FOUND` (or `GET DIAGNOSTICS`), never a `RETURNING true INTO`-style flag. This is a normative rule for the implementation, and the validation gates assert the fenced paths (§16.3).

### 5.1 Backoff helper

```sql
CREATE OR REPLACE FUNCTION taskq.backoff_seconds(
    p_mode text, p_base int, p_cap int, p_failures int
) RETURNS int LANGUAGE sql VOLATILE AS $$
    -- Exponential with cap and +/-15% jitter (neither old system had jitter).
    SELECT greatest(1, round(
        least(p_cap,
              CASE p_mode
                  WHEN 'exponential' THEN p_base::numeric * pow(2, least(greatest(p_failures - 1, 0), 16))
                  ELSE p_base::numeric
              END)
        * (0.85 + random() * 0.30)
    ))::int;
$$;
```

### 5.2 Enqueue — index-enforced idempotency, dep-safe, transactional

```sql
CREATE OR REPLACE FUNCTION taskq.enqueue(
    p_queue            text,
    p_job_type         text,
    p_payload          jsonb        DEFAULT '{}'::jsonb,
    p_priority         smallint     DEFAULT NULL,
    p_scheduled_at     timestamptz  DEFAULT NULL,
    p_idempotency_key  text         DEFAULT NULL,
    p_concurrency_key  text         DEFAULT NULL,
    p_affinity_key     text         DEFAULT NULL,
    p_max_attempts     smallint     DEFAULT NULL,
    p_lease_seconds    int          DEFAULT NULL,
    p_backoff_mode     text         DEFAULT NULL,
    p_backoff_base     int          DEFAULT NULL,
    p_backoff_cap      int          DEFAULT NULL,
    p_depends_on       uuid[]       DEFAULT NULL,
    p_workflow_id      uuid         DEFAULT NULL,
    p_step_key         text         DEFAULT NULL,
    p_parent_job_id    uuid         DEFAULT NULL,
    p_headers          jsonb        DEFAULT NULL
    -- v1.6 (R2-07): p_internal is GONE from the public signature — a producer
    -- could pass it to bypass the depth gate. The depth exemption for settle-path
    -- followups lives in the OWNER-ONLY taskq._enqueue_followup (0.2), which
    -- producers cannot execute.
) RETURNS TABLE (job_id uuid, created boolean)
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    q          taskq.queues%ROWTYPE;
    v_id       uuid;
    v_deps     uuid[] := '{}';
    v_dep      record;
    v_status   text;
    v_dead_dep uuid;
    v_arch     text;
    v_existing uuid;
    v_created  boolean := false;
    v_try      int;
BEGIN
    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN RAISE EXCEPTION 'taskq: unknown queue %', p_queue USING ERRCODE = 'TQ001'; END IF;

    -- Advisory producer backpressure, off by default (robustness graft, judge 3 mandate).
    -- Bounded EXISTENCE probe, never count(*): walks at most max_depth index entries and
    -- stops — an exact count would be O(backlog) precisely when the flood it guards
    -- against is happening. Skipped for settle-path followups (p_internal): chain steps
    -- are continuations of already-admitted work; a deep child queue must never fail a
    -- parent's settle (§5.5, §8).
    -- v1.6 (R2-07): probe at max_depth - 1 — the v1.5 OFFSET max_depth accepted
    -- row N+1 before rejecting. Still explicitly ADVISORY under concurrency/bulk.
    IF q.max_depth IS NOT NULL AND EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE queue = p_queue AND status IN ('blocked','queued')
        OFFSET greatest(q.max_depth - 1, 0) LIMIT 1) THEN
        RAISE EXCEPTION 'queue % at max_depth %', p_queue, q.max_depth USING ERRCODE = 'TQ429';
    END IF;

    -- Dependencies: lock dep rows FOR SHARE so a dependency cannot complete between
    -- our status check and our edge insert (complete_job's unlock takes FOR UPDATE on
    -- the same rows — ordering is serialized by the row locks; this provably closes
    -- the enqueue-vs-complete race the robustness design left ambiguous).
    -- ORDER BY id = the global ascending-id lock order (§5 preamble): the same diamond
    -- that would deadlock an unordered dep pass against complete_job's promotion pass
    -- now acquires locks in one global order — no cycle can form.
    IF p_depends_on IS NOT NULL THEN
        FOR v_dep IN
            SELECT id, status FROM taskq.jobs
            WHERE id = ANY (SELECT DISTINCT unnest(p_depends_on))
            ORDER BY id
            FOR SHARE
        LOOP
            IF v_dep.status IN ('failed','cancelled') THEN
                v_dead_dep := v_dep.id;                 -- fail-closed: dead dep => cancelled child
            ELSIF v_dep.status <> 'succeeded' THEN
                v_deps := v_deps || v_dep.id;           -- still live => gate on it
            END IF;                                      -- succeeded => already satisfied, no edge
        END LOOP;
        -- Dep ids with NO hot row: consult the archive before declaring a caller bug.
        -- A parent that succeeded and was archived days ago is a SATISFIED dependency,
        -- not a typo (late chain children, redrive re-runs, lineage backfills).
        FOR v_dep IN
            SELECT d.d AS id FROM (SELECT DISTINCT unnest(p_depends_on) AS d) d
            WHERE NOT EXISTS (SELECT 1 FROM taskq.jobs j WHERE j.id = d.d)
        LOOP
            -- [0.3 contract only — R2-13/ADR-009: the archive does not exist before
            -- 0.3. In 0.1 (no dependencies) this path is absent; in 0.2 a missing
            -- parent is a typed TQ001 error — dependency resolution is hot-table-only
            -- until the archive capability activates.]
            SELECT a.status INTO v_arch FROM taskq.jobs_archive a WHERE a.id = v_dep.id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'taskq: unknown dependency id % ', v_dep.id USING ERRCODE = 'TQ001';
            ELSIF v_arch IN ('failed','cancelled') THEN
                v_dead_dep := v_dep.id;                 -- archived-dead dep: same fail-closed path
            END IF;                                      -- archived-succeeded: satisfied, no edge
        END LOOP;
    END IF;

    v_status := CASE WHEN cardinality(v_deps) > 0 THEN 'blocked' ELSE 'queued' END;

    -- Insert with a bounded convergence loop. The loser path re-selects the active
    -- holder in a LATER statement snapshot; if the holder settled in that gap the key
    -- is free again and the honest answer is to RETRY THE INSERT — never (NULL, false),
    -- which would silently drop an enqueue that should have succeeded.
    FOR v_try IN 1..3 LOOP
        v_id := taskq.uuid7();
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, concurrency_key, affinity_key,
            workflow_id, step_key, parent_job_id, pending_deps,
            scheduled_at, lease_seconds, max_attempts,
            backoff_mode, backoff_base_seconds, backoff_cap_seconds
        ) VALUES (
            v_id, p_queue, p_job_type, v_status,
            COALESCE(p_priority, q.default_priority),
            COALESCE(p_payload, '{}'::jsonb), p_headers,
            p_idempotency_key, p_concurrency_key, p_affinity_key,
            p_workflow_id, p_step_key, p_parent_job_id, cardinality(v_deps),
            COALESCE(p_scheduled_at, now()),
            COALESCE(p_lease_seconds, q.default_lease_seconds),
            COALESCE(p_max_attempts, q.default_max_attempts),
            COALESCE(p_backoff_mode, q.default_backoff_mode),
            COALESCE(p_backoff_base, q.default_backoff_base),
            COALESCE(p_backoff_cap,  q.default_backoff_cap)
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING;

        IF FOUND THEN v_created := true; EXIT; END IF;

        -- Loser of the idempotency race / pre-existing active job: return it.
        -- Race-free BY THE INDEX, not by this select (qdarte F1, Diverse 11.4 closed).
        -- Never an exception => transactional callers need no rollback handling
        -- (Diverse 11.5's mid-txn rollback bug has nothing to trigger it).
        SELECT j.id INTO v_existing FROM taskq.jobs j
        WHERE j.queue = p_queue AND j.idempotency_key = p_idempotency_key
          AND j.status IN ('blocked','queued','running')
        ORDER BY j.created_at DESC LIMIT 1;
        IF v_existing IS NOT NULL THEN
            RETURN QUERY SELECT v_existing, false;       -- created is ALWAYS truthfully reported
            RETURN;
        END IF;
        -- Holder settled between the two statements: loop retries the INSERT.
        -- PG19: this whole loop collapses into ON CONFLICT DO SELECT RETURNING (section 15).
    END LOOP;

    IF NOT v_created THEN
        RAISE EXCEPTION 'taskq: idempotency insert did not converge for key % on queue %',
            p_idempotency_key, p_queue USING ERRCODE = 'TQ500';   -- 3 flaps in one call: pathological
    END IF;

    -- Dead-dep fail-closed path FIRST — before any edge insert, so a cancelled-at-birth
    -- job NEVER leaves job_deps edges behind ("a surviving edge always means still
    -- gating" stays true; live parents stay archivable).
    IF v_dead_dep IS NOT NULL THEN
        UPDATE taskq.jobs SET status = 'cancelled', outcome = 'dep_failed',
               error = format('dependency %s already terminal-failed', v_dead_dep),
               finished_at = now(), updated_at = now()
        WHERE id = v_id;
        PERFORM taskq.emit_event(v_id, NULL, 'cancelled', 'system', 'dead dependency at enqueue', NULL);
        RETURN QUERY SELECT v_id, true; RETURN;
    END IF;

    INSERT INTO taskq.job_deps (job_id, depends_on)
        SELECT v_id, d FROM unnest(v_deps) AS d;

    PERFORM taskq.emit_event(v_id, NULL, 'enqueued', 'system', NULL,
        jsonb_build_object('status', v_status, 'scheduled_at', COALESCE(p_scheduled_at, now())));

    IF v_status = 'queued' AND COALESCE(p_scheduled_at, now()) <= now() AND q.notify_enabled THEN
        PERFORM pg_notify('taskq_' || p_queue, '');      -- payload-free; commit-gated; Postgres
    END IF;                                              -- dedups identical NOTIFYs per txn

    RETURN QUERY SELECT v_id, true;
END $$;
```

**Dep-declaring enqueues and transaction length:** the `FOR SHARE` locks on dependency rows are held until the *producer's* transaction commits — a slow ingest transaction that declares deps blocks `complete_job` on every listed parent for its full duration. Rule: enqueue dep-bearing joins from short transactions, or from a planner job; bulk ingest paths never declare deps (`enqueue_many` forbids them below).

**Transactional enqueue is the point:** callers invoke `taskq.enqueue()` inside their own domain transaction — the queue is the outbox (the Go/Postgres job queue's core argument, PA §1.2).

**Bulk enqueue** — `taskq.enqueue_many(p_queue text, p_jobs jsonb)` (v1.6, R2-12 — the single `RETURNING` cannot by itself satisfy the one-result-per-input contract: `DO NOTHING RETURNING` reports inserted rows only, never the existing holder ids): one transaction, **one queue per call**, ≤1000 specs, dependencies forbidden, all validation before insert. Result is **one typed row per input in input order** — `(input_index, job_id, outcome created|existed)` — duplicates within the same request resolved deterministically (first occurrence may create; later ones report `existed` of it). Conflict holders are resolved by follow-up snapshot statements with the same convergence rule as single enqueue (a holder that settles mid-call is retried; exhaustion raises `TQ500` and rolls back the whole batch — no partial batches, no per-item errors, no HTTP 207 in 0.1). Still: **one** NOTIFY per queue per call and **one depth probe per call** (never per spec).

### 5.3 Claim — batch, cap-aware, deadlock-free, idle micro-reap

```sql
CREATE OR REPLACE FUNCTION taskq.claim_jobs(
    p_queue         text,
    p_worker_id     text,
    p_batch         int    DEFAULT 1,
    p_job_types     text[] DEFAULT NULL,
    p_lease_seconds int    DEFAULT NULL,
    p_affinity_key  text   DEFAULT NULL,
    p_job_id        uuid   DEFAULT NULL    -- targeted claim: exactly this job or nothing
) RETURNS SETOF taskq.claimed_job
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_job        taskq.jobs%ROWTYPE;
    v_attempt_id uuid;
    v_lease      int;
    v_skip       uuid[] := '{}';
    v_claimed    int := 0;
    v_scans      int := 0;
    v_cap        int;
    v_running    int;
    v_affinity   text := p_affinity_key;
    v_batch      int := p_batch;
    v_saturated  text[] := '{}';
BEGIN
    IF v_batch < 1 OR v_batch > 50 THEN RAISE EXCEPTION 'taskq: batch out of range'; END IF;
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;   -- targeted claim is singular by definition
    PERFORM 1 FROM taskq.queues WHERE name = p_queue AND paused_at IS NULL;
    IF NOT FOUND THEN RETURN; END IF;                    -- unknown or paused: claim nothing

    -- Saturated-key set, computed ONCE per call (a lean Redis/asyncio job library anti-join shape, PA §1.8) — never a
    -- per-candidate correlated count. The running set is small (<= worker count), so this
    -- is one grouped pass over jobs_running_idx. Candidates under a saturated key are then
    -- excluded by a cheap array test. The alternative (a correlated count(*) per visited
    -- row) re-creates the a multi-tenant task-orchestration platform head-of-line pathology: every poll from every worker
    -- walking an entire backlog parked behind a saturated key.
    SELECT COALESCE(array_agg(k.key), '{}') INTO v_saturated
    FROM (SELECT r.concurrency_key AS key, count(*) AS c
            FROM taskq.jobs r
           WHERE r.status = 'running' AND r.concurrency_key IS NOT NULL
           GROUP BY r.concurrency_key) k
    WHERE k.c >= COALESCE((SELECT l.max_running FROM taskq.concurrency_limits l
                            WHERE l.key = k.key), 1);

    WHILE v_claimed < v_batch AND v_scans < v_batch + 20 LOOP
        v_scans := v_scans + 1;
        v_job := NULL;

        -- Pass 1 (optional): affinity-preferred candidate. Pass 2: general FIFO.
        IF v_affinity IS NOT NULL AND p_job_id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND j.affinity_key = v_affinity
              AND (p_job_types IS NULL OR j.job_type = ANY (p_job_types))
              AND NOT (j.id = ANY (v_skip))
              AND (j.concurrency_key IS NULL OR NOT (j.concurrency_key = ANY (v_saturated)))
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
            IF v_job.id IS NULL THEN v_affinity := NULL; END IF;   -- preference, not exclusivity
        END IF;

        IF v_job.id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND (p_job_id IS NULL OR j.id = p_job_id)  -- targeted claim (Diverse tier-1, §16.2)
              AND (p_job_types IS NULL OR j.job_type = ANY (p_job_types))
              AND NOT (j.id = ANY (v_skip))
              -- cheap racy pre-filter: array test against the per-call saturated set
              AND (j.concurrency_key IS NULL OR NOT (j.concurrency_key = ANY (v_saturated)))
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
        END IF;

        EXIT WHEN v_job.id IS NULL;

        -- Strict admission: serialize same-key admission with a TRY advisory xact
        -- lock (simplicity graft, judge 2 mandate — the loser SKIPS this key this
        -- round, never waits => provably deadlock-free). Unknown key = mutex(1),
        -- fail-closed. max_running = 0 = paused resource.
        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                       hashtextextended('taskq.ck:' || v_job.concurrency_key, 0)) THEN
                v_skip := v_skip || v_job.id; CONTINUE;   -- brief under-admission, never over
            END IF;
            SELECT COALESCE((SELECT l.max_running FROM taskq.concurrency_limits l
                              WHERE l.key = v_job.concurrency_key), 1) INTO v_cap;
            SELECT count(*) INTO v_running FROM taskq.jobs r
             WHERE r.status = 'running' AND r.concurrency_key = v_job.concurrency_key;
            IF v_running >= v_cap THEN
                v_skip := v_skip || v_job.id; CONTINUE;   -- cap full: row untouched, next poll retries
            END IF;
        END IF;

        v_attempt_id := taskq.uuid7();
        v_lease := COALESCE(p_lease_seconds, v_job.lease_seconds);

        UPDATE taskq.jobs j SET
            status             = 'running',
            worker_id          = p_worker_id,
            current_attempt_id = v_attempt_id,
            attempt_count      = j.attempt_count + 1,
            lease_expires_at   = now() + make_interval(secs => v_lease),
            started_at         = COALESCE(j.started_at, now()),
            updated_at         = now()
        WHERE j.id = v_job.id;                            -- row already locked; status verified above

        INSERT INTO taskq.job_attempts (id, job_id, worker_id, lease_seconds)
        VALUES (v_attempt_id, v_job.id, p_worker_id, v_lease);
        -- uq_job_attempts_running: a double-claim is a hard DB error, not a data race.

        PERFORM taskq.emit_event(v_job.id, v_attempt_id, 'claimed', p_worker_id, NULL,
            jsonb_build_object('attempt', v_job.attempt_count + 1));

        v_claimed := v_claimed + 1;
        RETURN NEXT (v_job.id, v_job.queue, v_job.job_type, v_job.priority, v_job.payload,
                     v_job.headers, v_job.progress, v_attempt_id, v_job.attempt_count + 1,
                     v_job.failure_count, v_job.max_attempts,
                     now() + make_interval(secs => v_lease),
                     v_job.workflow_id, v_job.step_key)::taskq.claimed_job;
    END LOOP;

    -- Idle-claim micro-reap (robustness graft, judges 2+3 mandate): ONLY when nothing
    -- was claimable, run a tiny bounded reap so lease recovery never depends solely on
    -- the tick process being alive — at zero cost when work exists.
    IF v_claimed = 0 THEN PERFORM taskq.reap_expired(5); END IF;
END $$;
```

**No-overshoot proof for the concurrency cap** (adopted verbatim per judge 2 mandate):
1. Two concurrent claimers select different candidate rows (SKIP LOCKED) for the same key K.
2. Admission for K is serialized by `pg_try_advisory_xact_lock('taskq.ck:K')`, held until commit. The loser does not block — it skips K-jobs this round (seconds of under-admission at worst, never over-admission).
3. The winner's recount of `running` rows for K happens while holding the K lock; any previously admitted K-job is either committed (visible to the count) or admitted by this same transaction (visible to its own snapshot). Therefore `count(running with K) < max_running` is exact at admission time. **No overshoot is possible.**
4. try-lock (not blocking lock) means no lock-wait ordering exists → **no deadlock, ever** (rejects pg_native's original blocking `pg_advisory_xact_lock` and robustness's `run_at`-nudge — see §19).
5. When a K-job completes/fails/reaps, its slot frees by definition — the count is derived from rows, never a counter that can drift.

What is deliberately **not** in claim: no promotion pass, no dependency counting, no schedule firing, no unbounded housekeeping beyond the bounded micro-reap (fixes DCP 7.5 / QC F19).

**Claim cost, stated honestly:**
- A *successful* claim, or a claim against an empty queue, costs one partial-index probe (plus the one grouped saturated-set pass, which is O(running set), i.e. O(worker count)). the Rails-native Postgres queue's ~110µs figure applies only to this probe shape — it polls a dedicated tiny ready-table; our number holds **only when the queue head is claimable or empty**.
- An *empty* claim costs **two** partial-index probes: the claim probe plus the bounded (limit 5) micro-reap. "Housekeeping never rides the hot path" means never rides a *successful* claim.
- When the queue head is a deep backlog behind a saturated `concurrency_key`, the scan walks past those rows inside the index with a cheap array filter — no per-row subquery — but it still visits them. If one key can realistically back up thousands of due rows at the head (LM Studio-style single-slot lanes), **give that lane its own queue**: the queue is the isolation unit and its claim scan is its own index prefix. The soak gate includes a claim-p99-under-saturated-cap profile (§16.3) so this is measured, not assumed. On PG16/17 (no B-tree skip scan) a large future-scheduled population in higher-priority bands is walked similarly — same mitigation.
- SKIP LOCKED skips still touch rows (the Ringer/PlanetScale cost); the batch loop's `v_skip` list re-visits at most `batch + 20` candidates per call by construction — the scan bound is the loop bound, not the backlog.

### 5.4 Heartbeat — lease extension, cancel channel, checkpoint carrier, typed loss

```sql
CREATE OR REPLACE FUNCTION taskq.heartbeat(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_lease_seconds int DEFAULT NULL, p_progress jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL
) RETURNS TABLE (ok boolean, cancel_requested boolean, lease_expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_row taskq.jobs%ROWTYPE;
BEGIN
    UPDATE taskq.jobs j SET
        lease_expires_at = now() + make_interval(secs => COALESCE(p_lease_seconds, j.lease_seconds)),
        progress         = COALESCE(p_progress, j.progress),   -- checkpoint persists across retries
        updated_at       = now()
    WHERE j.id = p_job_id AND j.status = 'running'
      AND j.current_attempt_id = p_attempt_id AND j.worker_id = p_worker_id
    RETURNING j.* INTO v_row;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, false, NULL::timestamptz;   -- typed loss, not an exception:
        RETURN;                                                -- the worker MUST abort the handler
    END IF;

    IF p_stats IS NOT NULL THEN
        UPDATE taskq.job_attempts SET stats = p_stats WHERE id = p_attempt_id;
    END IF;

    RETURN QUERY SELECT true, v_row.cancel_requested_at IS NOT NULL, v_row.lease_expires_at;
END $$;
```

- **HOT update — for the pure lease bump:** no indexed column is touched (`lease_expires_at`, `progress`, `updated_at` are all unindexed), so with `fillfactor = 85` a plain heartbeat generates zero index writes. **This claim is scoped to heartbeats that do not grow the row.** A heartbeat carrying a `p_progress` payload stays HOT only while the new row version fits on the same page; a monotonically growing checkpoint eventually migrates pages (non-HOT: fresh entries in the PK and every matching partial index) and, once `progress` TOASTs (>~2KB), every rewrite copies the whole value — a checkpoint-per-unit loop over N units writes O(N²) bytes. Hence the checkpoint rules below and the `toast.*` reloptions in §4.
- **Heartbeat and checkpoint are two concerns, deliberately split in the library:** the heartbeat task sends pure lease bumps (`p_progress` NULL — HOT by construction); `ctx.checkpoint()` is **batched** onto at most one heartbeat per interval (default: at most every 30s or every K units, whichever is later), not per unit of work. Normative size guidance: keep `progress` **under 2KB pre-TOAST** — it is a *cursor*, not a result set. Large resumable state goes in an app table (or `job_attempts.stats`) keyed by `job_id`, with a compact pointer in `progress`. The soak gate includes a growing-checkpoint profile asserting heartbeat p99, HOT ratio, and TOAST size stay bounded (§16.3.4).
- The heartbeat response is the cancellation channel (both systems' good idea, kept) and the lease-loss channel (`ok = false` — the worker learns it was fenced out and stops burning external side effects; closes DCP 7.2 / QC F5, the primary double-execution window).
- `p_progress` is the **single resume mechanism**: `ctx.checkpoint()` rides a heartbeat; the Diverse counties-remainder and dispatcher-cursor patterns resume from it. `payload` is never rewritten (see §19 for the rejected `remaining_payload` alternative).

### 5.5 Complete — CAS-fenced, idempotent replay, exactly-once chain followups

```sql
CREATE OR REPLACE FUNCTION taskq.complete_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_result jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL,
    p_followups jsonb DEFAULT NULL      -- array of enqueue specs; exactly-once chaining
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_notify_queues text[];
    v_spec jsonb;
    v_i int := 0;
    v_job record;
BEGIN
    -- v1.6 (R2-01/ADR-007): LOCK-AND-READ FIRST — no mutation until replay
    -- recognition, the fence, and every followup gate have all passed. The row
    -- lock (own job = the graph parent, acquired before any dependent — R2-06)
    -- makes the later plain UPDATE safe; replays of an already-settled complete
    -- return here without ever reaching the followup gates, so a queue dropped
    -- after the original success can never turn a harmless network retry into
    -- an error.
    SELECT j.status, j.current_attempt_id, j.finished_by_attempt_id, j.queue
      INTO v_job
      FROM taskq.jobs j WHERE j.id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN ('lost', NULL, NULL)::taskq.settle_result;          -- archived/unknown id
    END IF;
    IF v_job.status = 'succeeded' AND v_job.finished_by_attempt_id = p_attempt_id THEN
        RETURN ('already_settled', 'succeeded', NULL)::taskq.settle_result;
    END IF;
    IF v_job.status <> 'running' OR v_job.current_attempt_id IS DISTINCT FROM p_attempt_id THEN
        RETURN ('lost', NULL, NULL)::taskq.settle_result;          -- genuinely fenced out
    END IF;

    -- FOLLOWUP GATES — before any state change (ADR-007's order, executable).
    -- 0.1 capability gate: the 0.1 contract ships without followups; a non-empty
    -- array is client/contract skew. Registered SQLSTATE 'TQ501' (never message-
    -- text-only — a bare RAISE would be P0001, invisible to SQLSTATE dispatch,
    -- R2-01). The worker's response: terminal-fail the parent as
    -- 'unsupported_followup', then soft-stop (version skew is fatal, feature 12).
    IF p_followups IS NOT NULL AND jsonb_typeof(p_followups) = 'array'
       AND jsonb_array_length(p_followups) > 0
       AND NOT taskq.has_capability('followups') THEN
        RAISE EXCEPTION 'followups are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;
    -- 0.2 validation (active once the followups capability exists): every raise
    -- carries USING ERRCODE = 'TQ422'. Deterministic invalids fail the settle
    -- atomically; the worker terminal-fails the parent 'invalid_followup'
    -- (dead-lettered, redrivable after the code fix). Nothing is truncated.
    IF p_followups IS NOT NULL THEN
        IF jsonb_typeof(p_followups) <> 'array' THEN
            RAISE EXCEPTION 'p_followups must be a jsonb array, got %',
                jsonb_typeof(p_followups) USING ERRCODE = 'TQ422';
        END IF;
        IF jsonb_array_length(p_followups) > 20 THEN
            RAISE EXCEPTION
                'followup cap is 20/settle, got % — wide fan-out goes through a planner job (§10)',
                jsonb_array_length(p_followups) USING ERRCODE = 'TQ422';
        END IF;
        FOR v_spec IN SELECT * FROM jsonb_array_elements(p_followups) LOOP
            v_i := v_i + 1;
            IF COALESCE(v_spec->>'job_type', '') = '' THEN
                RAISE EXCEPTION 'followup spec % has no job_type', v_i
                    USING ERRCODE = 'TQ422';
            END IF;
            IF v_spec ? 'queue' AND NOT EXISTS
               (SELECT 1 FROM taskq.queues q WHERE q.name = v_spec->>'queue') THEN
                RAISE EXCEPTION 'followup spec % names unknown queue "%"',
                    v_i, v_spec->>'queue' USING ERRCODE = 'TQ422';
            END IF;
        END LOOP;
        v_i := 0;
    END IF;

    -- GRAPH-ORDER LOCKS (R2-06): the parent (own row) is locked above; dependents
    -- are the next frontier — lock them now, id order as the deterministic
    -- SAME-FRONTIER tie-break only (never a causality claim), before any mutation
    -- of parent or children.
    PERFORM 1 FROM taskq.jobs d
     WHERE d.id IN (SELECT e.job_id FROM taskq.job_deps e WHERE e.depends_on = p_job_id)
     ORDER BY d.id
     FOR UPDATE;

    -- All gates passed — mutate. Parent completion, attempt settle, children,
    -- dependency unlock, and events commit in ONE transaction (lossless-atomic).
    UPDATE taskq.jobs j SET
        status = 'succeeded', outcome = 'success',
        worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
        result = COALESCE(p_result, j.result), error = NULL, expiry_streak = 0,
        finished_at = now(), finished_by_attempt_id = p_attempt_id, updated_at = now()
    WHERE j.id = p_job_id;

    UPDATE taskq.job_attempts SET status = 'succeeded', outcome = 'success',
           finished_at = now(), stats = COALESCE(p_stats, stats)
    WHERE id = p_attempt_id AND status = 'running';

    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'succeeded', p_worker_id, NULL, NULL);

    -- Chain followups INSIDE the settle transaction — validated above, inserted
    -- here, atomic with the parent (ADR-007). Each spec gets a derived idempotency
    -- key, so a chain step is enqueued exactly once no matter how many times
    -- complete is retried after a lost response; 'existed' from the derived key is
    -- success. Casts inside the helper surface as TQ422, never native 22P02
    -- (R2-07). Inserts go through the OWNER-ONLY taskq._enqueue_followup — it
    -- holds the depth exemption (child-queue backpressure must not fail a parent
    -- settle, §8) that the public enqueue no longer exposes (R2-07: p_internal is
    -- gone from the producer surface). A failed parent has NO committed children —
    -- a rejected complete rolls all of this back, so redrive cannot duplicate
    -- chain steps (R2-01 redrive note).
    IF p_followups IS NOT NULL THEN
        FOR v_spec IN SELECT * FROM jsonb_array_elements(p_followups) LOOP
            v_i := v_i + 1;
            PERFORM taskq._enqueue_followup(
                    p_parent_job_id   => p_job_id,
                    p_parent_queue    => v_job.queue,
                    p_spec            => v_spec,
                    p_spec_index      => v_i);
        END LOOP;
    END IF;

    -- Dependents were already locked above (graph order, before mutation); the
    -- cascade below therefore cannot deadlock a sibling settle or a dep-declaring
    -- enqueue that follows the same parent-frontier-then-id discipline (R2-06).

    -- Dependency unlock: delete satisfied edges, decrement, promote at zero.
    -- O(direct dependents); no scan, no starvation window (kills qdarte F14).
    WITH satisfied AS (
        DELETE FROM taskq.job_deps WHERE depends_on = p_job_id RETURNING job_id
    ), promoted AS (
        UPDATE taskq.jobs d SET
            pending_deps = d.pending_deps - 1,
            status = CASE WHEN d.pending_deps - 1 = 0 AND d.status = 'blocked'
                          THEN 'queued' ELSE d.status END,
            updated_at = now()
        FROM satisfied s
        WHERE d.id = s.job_id
        RETURNING d.queue, (d.pending_deps = 0 AND d.status = 'queued') AS woke
    )
    SELECT array_agg(DISTINCT queue) INTO v_notify_queues FROM promoted WHERE woke;

    IF v_notify_queues IS NOT NULL THEN
        PERFORM pg_notify('taskq_' || q, '') FROM unnest(v_notify_queues) AS q;
    END IF;

    RETURN ('ok', 'succeeded', NULL)::taskq.settle_result;
END $$;
```

**Hard rule:** `complete_job` performs **no business side effects** — no publishes, no proxy bookkeeping, no domain writes (qdarte F13/QO 7.9 structurally impossible). Followup *enqueues* are queue work, not business work — cheap inserts through `taskq.enqueue` with bounded count (cap 20/settle, enforced in the SQL loop and mirrored by the library). Handlers do their business side effects before settling, idempotently.

### 5.6 Fail — one retry engine; replay-aware for every non-terminal path

```sql
CREATE OR REPLACE FUNCTION taskq.fail_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_error text,
    p_retryable boolean DEFAULT true,
    p_retry_after_seconds int DEFAULT NULL,     -- hint normalization ("1h30m", ISO) is client-side
    p_progress jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    v_job taskq.jobs%ROWTYPE;
    v_att text;
    v_delay int;
    v_next timestamptz;
BEGIN
    SELECT * INTO v_job FROM taskq.jobs
    WHERE id = p_job_id AND status = 'running' AND current_attempt_id = p_attempt_id
    FOR UPDATE;

    IF NOT FOUND THEN
        -- Replay ack for ANY settle kind (robustness graft, judge 2 mandate): if OUR
        -- attempt row is already settled by an explicit worker call, this is a
        -- network-retried duplicate — 'already_settled', never a spurious 'lost'.
        -- 'expired' means the reaper took it: genuinely lost.
        SELECT a.status INTO v_att FROM taskq.job_attempts a
        WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att IN ('failed','succeeded','released','snoozed','cancelled') THEN
            RETURN ('already_settled',
                    (SELECT status FROM taskq.jobs WHERE id = p_job_id),
                    NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;
    END IF;

    -- v1.6 (R2-03): PENDING CANCEL BRANCHES BEFORE FAILURE ACCOUNTING. A worker
    -- failing a job whose cancellation an operator already requested lands
    -- cancelled with the budget UNTOUCHED and the attempt marked cancelled — the
    -- v1.5 body marked the attempt failed and charged failure_count on this path,
    -- contradicting §3.3 and the snooze/release cancel branches. Only the
    -- non-cancel paths below may touch failure accounting. (complete_job
    -- deliberately does NOT check pending cancel: a valid completion wins until
    -- the worker observes cancellation — §3.2.)
    IF v_job.cancel_requested_at IS NOT NULL THEN
        UPDATE taskq.jobs SET
            status = 'cancelled', outcome = 'canceled',
            worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
            progress = COALESCE(p_progress, progress),
            error = left(p_error, 2000),
            finished_at = now(), finished_by_attempt_id = p_attempt_id, updated_at = now()
        WHERE id = p_job_id;
        UPDATE taskq.job_attempts SET status = 'cancelled', outcome = 'canceled',
               finished_at = now(), error = left(p_error, 2000), stats = COALESCE(p_stats, stats)
        WHERE id = p_attempt_id AND status = 'running';
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id,
            left(p_error, 500), NULL);
        PERFORM taskq.cancel_dependents(p_job_id, 'dependency cancelled');
        RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
    END IF;

    -- v1.6 (R2-07): validate the caller-supplied retry hint at the public
    -- boundary — negative or absurd delays are caller errors, not data.
    IF p_retry_after_seconds IS NOT NULL
       AND (p_retry_after_seconds < 0 OR p_retry_after_seconds > 2592000) THEN
        RAISE EXCEPTION 'retry_after_seconds must be 0..2592000, got %',
            p_retry_after_seconds USING ERRCODE = 'TQ422';
    END IF;

    UPDATE taskq.job_attempts SET status = 'failed',
           finished_at = now(), error = left(p_error, 2000), stats = COALESCE(p_stats, stats)
    WHERE id = p_attempt_id AND status = 'running';

    IF p_retryable
       AND v_job.failure_count + 1 < v_job.max_attempts
    THEN
        v_delay := COALESCE(p_retry_after_seconds,
                            taskq.backoff_seconds(v_job.backoff_mode, v_job.backoff_base_seconds,
                                                  v_job.backoff_cap_seconds, v_job.failure_count + 1));
        v_next := now() + make_interval(secs => v_delay);
        UPDATE taskq.jobs SET
            status = 'queued', scheduled_at = v_next,
            failure_count = failure_count + 1, expiry_streak = 0,
            worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
            progress = COALESCE(p_progress, progress),
            error = left(p_error, 2000), updated_at = now()
        WHERE id = p_job_id;
        UPDATE taskq.job_attempts SET outcome = 'retry_scheduled' WHERE id = p_attempt_id;
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'retry_scheduled', p_worker_id,
            left(p_error, 500), jsonb_build_object('delay_seconds', v_delay, 'next_at', v_next,
                                                   'failure', v_job.failure_count + 1));
        RETURN ('retry_scheduled', 'queued', v_next)::taskq.settle_result;
    END IF;

    -- Terminal failure = the dead-letter state. (Pending cancel already branched
    -- above — v1.6; no cancel CASEs remain here.)
    UPDATE taskq.jobs SET
        status = 'failed',
        outcome = CASE WHEN NOT p_retryable THEN 'non_retryable' ELSE 'retry_exhausted' END,
        failure_count = failure_count + 1, expiry_streak = 0,
        worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
        progress = COALESCE(p_progress, progress),
        error = left(p_error, 2000),
        finished_at = now(), finished_by_attempt_id = p_attempt_id, updated_at = now()
    WHERE id = p_job_id;
    UPDATE taskq.job_attempts
        SET outcome = CASE WHEN p_retryable THEN 'retry_exhausted' ELSE 'non_retryable' END
        WHERE id = p_attempt_id;
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'failed',
        p_worker_id, left(p_error, 500), NULL);
    PERFORM taskq.cancel_dependents(p_job_id, 'dependency failed');
    RETURN ('dead', 'failed', NULL)::taskq.settle_result;
END $$;
```

Retry policy lives **on the row** (stamped at enqueue), so retry works even when the job type was retired from the client registry — no KeyError-500-stuck-job path (qdarte F7). Retry-after hints in worker results (`"1h30m"`, ISO datetimes, seconds) are normalized to an int by the client library — the SQL contract only ever sees seconds (ends DC 11.7's broken parser vs qdarte's grammar drift). An unparseable hint is a warning event + policy backoff, never silent.

### 5.7 Snooze and release — budget-free give-backs, replay-aware

```sql
CREATE OR REPLACE FUNCTION taskq.snooze_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_delay_seconds int, p_reason text DEFAULT NULL, p_progress jsonb DEFAULT NULL
) RETURNS taskq.settle_result LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_att text; v_cancelled boolean;
        v_next timestamptz := now() + make_interval(secs => greatest(p_delay_seconds, 0));
BEGIN
    -- A snooze HONORS a pending cancel (like fail_job and release_job): a running job
    -- that was operator-cancelled and then snoozed by its worker must terminalize as
    -- 'cancelled', never park as an unclaimable queued row (cancel_requested_at set
    -- makes it invisible to the claim predicate) waiting for the straggler sweep.
    UPDATE taskq.jobs j SET
        status       = CASE WHEN j.cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE 'queued' END,
        outcome      = CASE WHEN j.cancel_requested_at IS NOT NULL THEN 'canceled' ELSE j.outcome END,
        finished_at  = CASE WHEN j.cancel_requested_at IS NOT NULL THEN now() ELSE NULL END,
        scheduled_at = v_next, expiry_streak = 0,
        worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
        progress = COALESCE(p_progress, j.progress), updated_at = now()
    WHERE j.id = p_job_id AND j.status = 'running' AND j.current_attempt_id = p_attempt_id
    RETURNING (j.status = 'cancelled') INTO v_cancelled;
    IF NOT FOUND THEN                                    -- FOUND, never a NULLable flag (§5 preamble)
        SELECT a.status INTO v_att FROM taskq.job_attempts a
        WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att IN ('snoozed','failed','succeeded','released','cancelled') THEN
            RETURN ('already_settled', NULL, NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;
    END IF;
    UPDATE taskq.job_attempts SET
           status  = CASE WHEN v_cancelled THEN 'cancelled' ELSE 'snoozed' END,
           outcome = CASE WHEN v_cancelled THEN 'canceled'  ELSE 'snoozed' END,
           error   = p_reason,          -- caller text lives in error/stats, NEVER in outcome (§3.1)
           finished_at = now()
    WHERE id = p_attempt_id AND status = 'running';
    IF v_cancelled THEN
        PERFORM taskq.cancel_dependents(p_job_id, 'dependency cancelled');
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id, p_reason, NULL);
        RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
    END IF;
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'snoozed', p_worker_id, p_reason,
        jsonb_build_object('next_at', v_next));
    RETURN ('ok', 'queued', v_next)::taskq.settle_result;
END $$;
```

`taskq.release_job(p_job_id, p_attempt_id, p_worker_id, p_cause DEFAULT 'released', p_reason DEFAULT NULL, p_requeue_delay_seconds DEFAULT 15, p_progress DEFAULT NULL)` is identical in shape — including the `IF NOT FOUND` fence check and the honor-pending-cancel transition to `cancelled` (attempt status `cancelled`, outcome `canceled`) — with attempt status `released`, `release_count = release_count + 1`, `expiry_streak = 0`, and a default 15-second `scheduled_at` push (prevents the releasing worker's own immediate re-claim spin — kills qdarte F12 and the dead `skip_own_releases` flag). `p_cause` is **typed** — one of `'released' | 'worker_shutdown' | 'no_handler'` (CHECK-validated) — and is what lands in `outcome`; free-text `p_reason` goes to `error`/events. This is where the taxonomy's `worker_shutdown`/`no_handler` values are assigned (§3.1). Neither snooze nor release touches `failure_count`. Snooze is the "provider quota latched, come back in an hour" primitive both systems faked with retries; release is the shutdown/no-handler give-back.

### 5.8 Reaper — the ONLY reclaim authority, with poison quarantine

```sql
CREATE OR REPLACE FUNCTION taskq.reap_expired(p_limit int DEFAULT 100) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_job taskq.jobs%ROWTYPE; v_n int := 0; v_delay int; v_next timestamptz;
BEGIN
    FOR v_job IN
        -- Scans the SMALL running partial index (jobs_running_idx covers all running
        -- rows) and filters lease expiry in the heap — the price of HOT heartbeats.
        SELECT * FROM taskq.jobs
        WHERE status = 'running' AND lease_expires_at < now()
        ORDER BY lease_expires_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE taskq.job_attempts SET status = 'expired', outcome = 'lease_expired',
               finished_at = now()
        WHERE id = v_job.current_attempt_id AND status = 'running';

        IF v_job.cancel_requested_at IS NOT NULL THEN
            UPDATE taskq.jobs SET status = 'cancelled', outcome = 'canceled_after_expiry',
                   worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
                   finished_at = now(), updated_at = now() WHERE id = v_job.id;
            PERFORM taskq.cancel_dependents(v_job.id, 'dependency cancelled');
        ELSIF v_job.expiry_streak + 1 >= 3 THEN
            -- POISON QUARANTINE (robustness graft, all three judges): three consecutive
            -- deaths-by-expiry with no complete/fail/release ever called is a
            -- crash-the-worker payload. Terminalize even with budget remaining, so one
            -- poison pill cannot chew through the fleet for its whole backoff budget.
            UPDATE taskq.jobs SET status = 'failed', outcome = 'poison',
                   failure_count = failure_count + 1,
                   error = 'quarantined after 3 consecutive lease expiries',
                   worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
                   finished_at = now(), updated_at = now() WHERE id = v_job.id;
            PERFORM taskq.cancel_dependents(v_job.id, 'dependency failed');
        ELSIF v_job.failure_count + 1 >= v_job.max_attempts THEN
            UPDATE taskq.jobs SET status = 'failed', outcome = 'retry_exhausted',
                   failure_count = failure_count + 1,
                   error = 'lease expired; retry budget exhausted',
                   worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
                   finished_at = now(), updated_at = now() WHERE id = v_job.id;
            PERFORM taskq.cancel_dependents(v_job.id, 'dependency failed');
        ELSE
            -- Expiry consumes budget AND backs off — the SAME engine as fail_job
            -- (fixes qdarte F6 in both directions).
            v_delay := taskq.backoff_seconds(v_job.backoff_mode, v_job.backoff_base_seconds,
                                             v_job.backoff_cap_seconds, v_job.failure_count + 1);
            v_next  := now() + make_interval(secs => v_delay);
            UPDATE taskq.jobs SET status = 'queued', scheduled_at = v_next,
                   failure_count = failure_count + 1,
                   expiry_streak = expiry_streak + 1,
                   worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
                   updated_at = now() WHERE id = v_job.id;
        END IF;

        PERFORM taskq.emit_event(v_job.id, v_job.current_attempt_id, 'lease_expired',
            'system', NULL, jsonb_build_object('failure', v_job.failure_count + 1,
                                               'expiry_streak', v_job.expiry_streak + 1));
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
```

Invoked by `taskq.tick()` (section 11.4) and by the idle-claim micro-reap — never by list/detail reads (qdarte F2/F3 banned by construction: **no read function in taskq mutates state**). The reaper does not NOTIFY (no stampede); requeued jobs are found by normal polling within one interval. The per-row branch body (attempt-expire + cancel/poison/exhaust/requeue decision) is factored as `taskq.reap_job(p_job_id uuid)`, which `reap_expired` loops over and `expire_job` (§5.9) calls directly — one reclaim code path for batch and targeted reaping.

### 5.9 Cancel, dependents cascade, redrive, expire

```sql
-- Operator cancel: immediate for blocked/queued, cooperative for running.
CREATE OR REPLACE FUNCTION taskq.cancel_job(p_job_id uuid, p_actor text, p_reason text DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_job taskq.jobs%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM taskq.jobs WHERE id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001'; END IF;
    IF v_job.status IN ('succeeded','failed','cancelled') THEN RETURN v_job.status; END IF;

    IF v_job.status IN ('blocked','queued') THEN
        UPDATE taskq.jobs SET status = 'cancelled', outcome = 'canceled',
               error = COALESCE(p_reason, 'cancelled by operator'),
               cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = p_reason,
               finished_at = now(), updated_at = now() WHERE id = p_job_id;
        DELETE FROM taskq.job_deps WHERE job_id = p_job_id;         -- drop its own gates
        PERFORM taskq.cancel_dependents(p_job_id, 'dependency cancelled');
        PERFORM taskq.emit_event(p_job_id, NULL, 'cancelled', p_actor, p_reason, NULL);
        RETURN 'cancelled';
    END IF;

    UPDATE taskq.jobs SET cancel_requested_at = now(), cancel_reason = p_reason,
           updated_at = now() WHERE id = p_job_id;
    PERFORM taskq.emit_event(p_job_id, v_job.current_attempt_id, 'cancel_requested',
                             p_actor, p_reason, NULL);
    RETURN 'running';               -- worker sees cancel_requested on next heartbeat;
END $$;                             -- reaper terminalizes as cancelled if it never responds.

-- Transitive dependent cancellation over surviving edges.
-- ONE-SHOT, CONVERGENT-VIA-TICK: SKIP LOCKED means a dependent concurrently locked by
-- a sibling parent's complete_job (P1 succeeds while P2 fails — the promotion CTE holds
-- the child's row lock) is SKIPPED here. That child would otherwise be stranded
-- 'blocked' forever with a surviving edge to a terminal parent — which also blocks the
-- parent's archival. The tick's finalize_dep_stragglers pass (below) is the convergence
-- guarantee: any blocked/queued row gated by a terminal dep is cancelled within ~one
-- tick. This race is a named case in the §16.3.1 harness.
CREATE OR REPLACE FUNCTION taskq.cancel_dependents(p_job_id uuid, p_reason text)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_n int := 0; r record;
BEGIN
    FOR r IN
        SELECT d.job_id FROM taskq.job_deps d
        JOIN taskq.jobs j ON j.id = d.job_id AND j.status IN ('blocked','queued')
        WHERE d.depends_on = p_job_id
        ORDER BY d.job_id                                 -- global ascending-id lock order (§5)
        FOR UPDATE OF j SKIP LOCKED
    LOOP
        UPDATE taskq.jobs SET status = 'cancelled', outcome = 'dep_failed',
               error = p_reason, finished_at = now(), updated_at = now() WHERE id = r.job_id;
        DELETE FROM taskq.job_deps WHERE job_id = r.job_id;
        PERFORM taskq.emit_event(r.job_id, NULL, 'cancelled', 'system', p_reason, NULL);
        v_n := v_n + 1 + taskq.cancel_dependents(r.job_id, p_reason);
    END LOOP;
    RETURN v_n;
END $$;

-- Tick straggler passes — they make BOTH cascades convergent instead of one-shot
-- (fully specified here because correctness depends on them; transitions in §3.2):
--
-- taskq.finalize_cancel_stragglers(p_limit int) RETURNS int
--   Predicate: status IN ('blocked','queued') AND cancel_requested_at IS NOT NULL.
--   (A cancel raced a running->queued requeue: the requeue won, and the row is now
--   invisible to claims — the claim predicate requires cancel_requested_at IS NULL.)
--   Action: batched (LIMIT p_limit, FOR UPDATE SKIP LOCKED, ascending id): status =
--   'cancelled', outcome = 'canceled', finished_at = now(), delete own job_deps edges,
--   cancel_dependents(), emit 'cancelled' with actor 'system'.
--
-- taskq.finalize_dep_stragglers(p_limit int) RETURNS int
--   Predicate: status IN ('blocked','queued') AND EXISTS a job_deps edge whose
--   depends_on is terminal 'failed'/'cancelled' (join on the hot table; archived
--   parents cannot occur — the archiver skips parents with surviving edges, §13.2).
--   Action: same batched cancel shape, outcome = 'dep_failed', actor 'system'.
--   This is the convergence backstop for cancel_dependents' SKIP LOCKED (above) and
--   for any cascade interrupted mid-recursion.

-- Dead-letter redrive: the operation neither old system had.
CREATE OR REPLACE FUNCTION taskq.redrive_job(p_job_id uuid, p_actor text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_queue text;
BEGIN
    UPDATE taskq.jobs SET
        status = 'queued', scheduled_at = now(),
        failure_count = 0, expiry_streak = 0,
        outcome = NULL, finished_at = NULL, finished_by_attempt_id = NULL,
        cancel_requested_at = NULL, cancel_reason = NULL, updated_at = now()
    WHERE id = p_job_id AND status = 'failed'
    RETURNING queue INTO v_queue;
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM taskq.emit_event(p_job_id, NULL, 'redriven', p_actor, 'operator redrive from failed', NULL);
    PERFORM pg_notify('taskq_' || v_queue, '');
    RETURN true;
EXCEPTION WHEN unique_violation THEN
    -- Redrive-vs-dedup collision, made explicit (judge 2 mandate): a NEW active job
    -- now holds the same idempotency key. The operator chooses: cancel the new job
    -- and redrive, or leave the dead row. Never a raw unique-violation leak.
    RAISE EXCEPTION 'taskq: redrive blocked — a new active job holds idempotency key of %',
        p_job_id USING ERRCODE = 'TQ409';
END $$;
-- Bulk: taskq.redrive_failed(p_queue text, p_job_type text DEFAULT NULL, p_limit int DEFAULT 100).

-- Force-expire ONE wedged running job. Backdates the lease AND reaps that job in the
-- SAME transaction while holding the row lock — identical airtightness to
-- expire_worker_leases below. A backdate-only version is flaky by construction: a live
-- per-job heartbeat (interval up to 30s) firing inside the backdate->next-tick window
-- (~5s) would silently push the lease forward again after the operator was told
-- "true". Synchronous reap closes it: a racing heartbeat blocks on the row lock, then
-- fails its WHERE (status no longer 'running') and returns ok=false — the worker
-- aborts the handler. This is THE paved path for a hung-but-heartbeating handler.
-- Still no status force-writes, ever (bans Diverse 11.10; the role grants make it stick).
CREATE OR REPLACE FUNCTION taskq.expire_job(p_job_id uuid, p_actor text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    UPDATE taskq.jobs SET lease_expires_at = now() - interval '1 second', updated_at = now()
    WHERE id = p_job_id AND status = 'running';
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM taskq.reap_job(p_job_id);   -- reap_expired's per-row body, factored as
    RETURN true;                        -- taskq.reap_job(id) so targeted + batch reaping
END $$;                                 -- share one code path (one reclaim authority).

-- Operator "this WORKER is dead" — the 2am bulk verb (robustness graft, all judges):
-- sugar over the one reclaim authority; budget/backoff/poison all apply normally.
-- v1.6 (R2-02): capture the TARGET ids and reap exactly those. The v1.5 body called
-- the generic reap_expired(N), whose global oldest-first selection could spend its
-- limit on OTHER workers' older expired rows — reporting success while the named
-- worker's jobs stayed running. Typed result: matched (backdated), reaped
-- (reclaimed here), skipped (state changed between backdate and lock — e.g. the
-- worker settled mid-call; reap_job re-checks under lock and declines).
CREATE OR REPLACE FUNCTION taskq.expire_worker_leases(p_worker_id text, p_actor text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_ids uuid[]; v_id uuid; v_reaped int := 0;
BEGIN
    WITH backdated AS (
        UPDATE taskq.jobs SET lease_expires_at = now() - interval '1 second', updated_at = now()
        WHERE status = 'running' AND worker_id = p_worker_id
        RETURNING id
    ) SELECT array_agg(id ORDER BY id) INTO v_ids FROM backdated;

    IF v_ids IS NOT NULL THEN
        FOREACH v_id IN ARRAY v_ids LOOP
            IF taskq.reap_job(v_id) THEN v_reaped := v_reaped + 1; END IF;
        END LOOP;
    END IF;
    -- Per-job audit events come from reap_job itself (actor 'system', cause
    -- lease_expired); the operator invocation is audited by the caller (CLI log /
    -- facade actor) — job_events.job_id is NOT NULL, so no summary row here.
    RETURN jsonb_build_object(
        'matched', COALESCE(array_length(v_ids, 1), 0),
        'reaped',  v_reaped,
        'skipped', COALESCE(array_length(v_ids, 1), 0) - v_reaped);
END $$;
```

Plus small operator/coordination functions (signatures only): `taskq.pause_queue(name, actor, reason)` / `resume_queue(name, actor)` (sets/clears `queues.paused_at`; enqueue continues — blocking intake corrupts caller transactions); `taskq.purge_queued(queue, job_type, actor, limit)` (cancels, never deletes); `taskq.reprioritize(job_id, priority, actor)` (queued/blocked only); `taskq.run_now(job_id)` (`scheduled_at = now()`); `taskq.cancel_workflow(workflow_id, actor)` (bulk `cancel_requested_at` stamp on non-terminal members); `taskq.create_workflow(workflow_key, kind, params, actor)` (idempotent upsert on `workflow_key`, returns id); `taskq.set_concurrency_limit(key, max_running, actor)`; `taskq.request_worker_shutdown(worker_id, queue, actor)` (NULL worker_id = fleet-wide, `queue` filters one lane); the schedule protocol trio `claim_due_schedules` / `fire_schedule` / `schedule_error` (§6). Every operator mutation records an event with `actor = 'operator:<who>'` — "who requeued this" always has an answer.

---

## 6. Scheduling (cron)

**Model.** One `taskq.schedules` row per recurring definition: real 5-field cron + IANA timezone. Cron *parsing* is a client concern (croniter in the library); the SQL contract stores and coordinates `next_fire_at`.

**Leaderless firing protocol** (runs inside the housekeeper coroutine — a worker's, or the API process's in HTTP-facade deployments, §11.4; no scheduler daemon, no leader election). Because `taskq_worker` has **no direct DML on any taskq table** (§4), the protocol's row mutations live in three `SECURITY DEFINER` functions; only the cron *math* (croniter) stays client-side. The housekeeper wraps one firing round in **one transaction**, so the row locks taken inside `claim_due_schedules` are held until commit:

```sql
BEGIN;
-- 1. Claim due schedules (any number of tickers may race; row locks arbitrate;
--    locks held to COMMIT because the function runs in the caller's transaction):
SELECT * FROM taskq.claim_due_schedules(20);
--    == SELECT ... FROM taskq.schedules WHERE paused_at IS NULL AND next_fire_at <= now()
--       ORDER BY next_fire_at LIMIT p_limit FOR UPDATE SKIP LOCKED

-- 2. Per claimed schedule, the CLIENT computes the fire instant(s) and the next fire
--    time (croniter), then ONE function performs the enqueue AND the clock advance
--    atomically — the enqueue+advance atomicity is never client-optional:
SELECT taskq.fire_schedule(s.name, :fire_at, :computed_next);
--    fire_schedule: taskq.enqueue(queue, job_type, payload, priority, lease_seconds,
--        max_attempts, concurrency_key,
--        p_idempotency_key => format('cron:%s:%s', s.name, fire_at_utc_iso));
--    then UPDATE taskq.schedules SET next_fire_at = :computed_next,
--        last_fired_at = :fire_at, last_error = NULL, updated_at = now().

-- 3. Bad cron string (client-side parse failure) — record and defuse, never hot-loop:
SELECT taskq.schedule_error(s.name, :error_text, now() + interval '5 minutes');
COMMIT;
```

Three mechanisms stack to make this exactly-once-per-fire without a leader:
1. `FOR UPDATE SKIP LOCKED` on the schedule row (inside `claim_due_schedules`, held to commit) — one ticker advances a given schedule at a time.
2. `next_fire_at` advanced in the same transaction as the enqueue (both inside `fire_schedule`) — a crash rolls both back; a later claimer sees the advanced clock.
3. The per-fire idempotency key `cron:{name}:{fire_at}` rides `jobs_idem_uq` — even a pathological double-fire collapses to one job (the mature Python/Postgres task library's pattern = Diverse's `scheduler_windows` generalized; the jobs idempotency index IS the window table).

**Static vs fire-time-dynamic payloads:** `fire_schedule` enqueues the row's *stored* payload. Schedules whose real payload must be computed at fire time (date-range resolution, entity expansion) store a **spec** as the payload and fire a small **planner job** whose handler expands and bulk-enqueues the real work — the convention §16.2.4 applies to the Diverse scheduler families.

**Catch-up: per-schedule policy, bounded** (robustness `catchup_policy` + pg_native backwards-from-now, judge 1 mandate). When a ticker finds `next_fire_at` far in the past, the client computes missed fires by iterating the cron expression **backwards from now** — O(max_catchup), never O(missed intervals) (kills qdarte F16's million-iteration loop):
- `skip` — drop all missed fires, log them, resume from the next future instant (audience syncs).
- `fire_once` (default) — one job for the most recent missed instant (a daily job that missed 3 days runs once, not three times) (court scrapes).
- `fire_all` — one job per missed instant, capped at `max_catchup`.

Diverse's silent past-midnight window loss (DCP 7.9) becomes a per-schedule policy choice instead of an accident. A bad cron string writes `schedules.last_error` and pushes `next_fire_at` 5 minutes so the row cannot hot-loop; the "did it fire?" audit is the jobs table itself (`idempotency_key LIKE 'cron:{name}:%'`, hot + archive). Interval schedules (`'@every 90s'`) use the identical protocol with client-interpreted arithmetic.

---

## 7. Dedup and idempotency

Three distinct layers, one mechanism each:

1. **Enqueue dedup (at most one active job per key).** `jobs_idem_uq` is the *only* authority; every enqueue path (single, bulk, cron, chain followups, redrive) inserts against it with `ON CONFLICT DO NOTHING` and always truthfully reports `created` (the Node/Postgres queue library's silent-null lesson). Semantics: a new job with the same key is allowed once the previous one is terminal (intentional — daily re-runs, redrives); the archive keeps key history queryable. SELECT-then-INSERT is banned; there is no second mechanism (closes qdarte F1 — the top severity-1 finding — and Diverse 11.4 with one line of DDL).
   Key conventions (documented, not enforced): `cron:{schedule}:{fire_at}`, `chain:{parent_job_id}:{step}` (the followup default), `entity:{job_type}:{entity_id}:v{n}`, content-hash keys (Diverse's FSBO digest pattern, kept).
2. **Execution mutual exclusion.** `concurrency_key` with `max_running = 1` serializes execution per key. Distinct from enqueue dedup: two jobs for one entity may *exist*; only one *runs*.
3. **Side-effect idempotency (handler contract).** At-least-once execution means handlers upsert by business key and pass idempotency keys to external APIs. Tokens provided: `job_id` stable across attempts (natural effect key), `attempt_id` unique per execution (log/provider-request correlation), `progress` checkpoint (resume without redoing). Chain followups via `p_followups` are exactly-once by construction.

**Settle idempotency for network retries:** `finished_by_attempt_id` (terminal settles) plus the attempt-ledger status check (non-terminal settles: retryable fail, release, snooze) mean a retried settle of **any** kind returns `already_settled`, never a spurious `lost` and never a 409-as-500. The client library retries settles on 5xx/timeout with `already_settled` as success — no more discarded finished batches (DCP 7.2).

---

## 8. Priorities, ordering, fairness, backpressure

- **Priority:** smallint 0–1000, **lower wins**, default 100, CHECK-enforced. Named bands in the library: `URGENT = 0`, `HIGH = 25`, `NORMAL = 100`, `LOW = 200`, `BACKGROUND = 500`. Boost = subtract. One convention, one direction — the qdarte call sites that "boost" by adding (QO 7.8) are migration-time bugs fixed against this table; Diverse's 0–10 values are remapped **×50 at the enqueue shim** (judge 3 mandate — concrete, testable, prevents silent priority inversion during cutover).
- **Ordering:** `(priority ASC, scheduled_at ASC, id ASC)` — priority bands, then due-time FIFO, then uuidv7 id as the deterministic tiebreaker both systems lacked. `jobs_claim_idx` serves this exact sort per queue (fixes qdarte F10).
- **Fairness policy for these codebases:** big batches enqueue at `BACKGROUND`; interactive/chained work at `NORMAL`+. A 500k agent-refresh cannot starve a render because renders outrank it; two same-priority batches interleave FIFO. Queues are the isolation unit (each queue's claim scan is its own index prefix). Per-tenant round-robin engines are out of scope until sustained load approaches ~20k jobs/min in one queue (a multi-tenant task-orchestration platform territory — documented reopen boundary, not built).
- **Backpressure:** producer side — optional `queues.max_depth` (advisory admission, `TQ429`, off by default); consumer side — bounded claim batch (≤50), `concurrency_limits` caps, `max_running = 0` resource pause valve, snooze/retry-after for provider quota latches (quota exhaustion becomes a scheduled retry, not budget burn).
- **max_depth cost and scope (normative):** the gate is a bounded *existence probe* (`OFFSET max_depth LIMIT 1` — walks at most `max_depth` index entries, no aggregation), checked **once per `enqueue_many` call**, never per spec — an exact `count(*)` would be most expensive exactly when the flood it guards against is happening. And it applies to **producer intake only**: settle-path followups (the owner-only followup inserter, §5.5) are exempt — chain steps are continuations of already-admitted work, and backpressure on a child queue must never fail a parent's settle (it would convert a finished job into lease-expiry re-execution). Enabling `max_depth` on a chain-target queue is therefore safe by construction. The exemption lives in `taskq._enqueue_followup` (owner-only, 0.2) — the public enqueue has no bypass parameter (v1.6, R2-07).

---

## 9. Per-resource concurrency caps

The qdarte mechanism (DB-enforced, derived count, drift-free) with all three flaws removed:

| Property | qdarte `concurrency_pool` | taskq `concurrency_key` |
|---|---|---|
| Bound to | the **claiming worker** (env var) | the **job row**, stamped at enqueue |
| Bypass | any worker without the env claims uncapped | impossible — claim enforces for every claimer |
| Serialization | `FOR UPDATE` on pool row across whole claim (convoy across job types, F9) | **try**-advisory-lock scoped to the single key, only when a keyed candidate is selected; loser skips, never waits |
| Unknown key | pool missing → claim nothing | `max_running = 1` (fail-closed mutex — a typo throttles, never uncaps) |
| Cap change | UPDATE pool row | `taskq.set_concurrency_limit(key, n, actor)`; next claim; `0` = pause valve |

Typical seeded keys: `proxy:webshare-residential = 8`, `llm:lmstudio-host = 1`, `llm:claude-api = 6`, `site:remine = 1`, `agent:{id} = 1`. The count is always derived from `status = 'running'` rows via `jobs_running_idx` — a reaped lease frees its slot automatically; `complete_job`'s NOTIFY wakes waiting workers promptly. Singleton platforms (remine, prospect-intake) stop being "run exactly one container" fleet rules and become `max_running = 1` keys — the cap holds even if someone accidentally starts two workers. Fleet sizing (`worker_pool_targets`) remains a deployment concern; the queue-level cap is the correctness backstop. No-overshoot proof: §5.3.

**Binding rule — `concurrency_key` can only express resources determinable at enqueue.** It is stamped on the job row (that is what makes it bypass-proof), so it *cannot* express a resource that is a property of whichever worker claims the job — qdarte's LLM engine binding is exactly that case: the same `content_synthesis_scope` job can execute against LM Studio under one fleet spec and Claude under another. The rule: **split such job lanes into engine-specific queues** (e.g. `content_lmstudio` / `content_claude` / `content_codex`), so the enqueue-time key is determinate and each fleet spec subscribes to its engine's queue (§16.1.1 applies this). A claim-time key parameter on `claim_jobs` was rejected: a worker-declared cap key is precisely the "bound to the claiming worker, bypassable by any worker without it" hole this table's first row exists to close — an enqueue with a NULL key claimed by six workers is six concurrent LM Studio calls, the melt `max_in_flight = 1` was built to prevent.

---

## 10. Pipelines and chaining

**One orchestration layer at the queue: dependency edges + workflows. One chain surface: settle-transaction followups.** Everything else is application code that enqueues.

- **Chains (the dominant case: scrape → enrich → render):** the handler returns followups; the library passes them as `p_followups` to `complete_job`, which enqueues them **inside the settle transaction** with `chain:{job_id}:{step}` dedup keys — a chain step is enqueued exactly once no matter how many times complete is retried or the job re-runs after a lost response (robustness decision #9, adopted as the library default per judges 1+3; strictly stronger than enqueue-before-complete, which is retained as a documented fallback for handlers that must enqueue mid-execution).
- **DAG / fan-in:** enqueue with `p_depends_on` + shared `workflow_id` (+ `step_key`). `blocked → queued` promotion is transactional inside the parent's `complete_job` — no promotion scans, no starvation, no fan-in crash window (rejects simplicity's post-commit group-done check; see §19). Parent failure/cancel cascades transitively (`dep_failed`). The enqueue-vs-complete race is closed by `FOR SHARE` on dep rows (§5.2).
- **Fan-out:** a planner handler bulk-enqueues N children (idempotency-keyed `chain:{parent}:{i}`) plus one join job depending on all N. `pending_deps` makes the join O(1) per child completion.
- **Batch runs (Diverse's `queue_runs`):** a workflow with `kind = 'batch'`, `workflow_key` = old run key. **Dispatcher expansion becomes an ordinary claimed job** (judges 1+3): "expand this run into 50k children" is a job whose handler pages through candidates, bulk-enqueues idempotency-keyed children, and checkpoints its cursor in `progress` via heartbeat. Crash/expiry resumes from the checkpoint; the second lease system (`queue_runs` dispatch leases, DCP §1.2) is deleted.
- **Workflow status is derived, never stored-and-drifting:** the `workflow_status` view rolls up member counts; the janitor stamps `workflows.status/finished_at` once all members are terminal. `complete_job` stays small (no mega-transaction, qdarte F13) at the cost of an eventually-consistent rollup (seconds).
- **Explicitly banned:** completion-time handoff registries inside the queue engine (QO §3.2), domain state machines writing job rows directly (QO §3.3), and business side effects inside settles. Domain orchestrators (qdarte's launch pipeline) live in application code and interact with taskq only through idempotency-keyed `enqueue` and read views. qdarte's `publish_scope` becomes a real `publish` job (`depends_on` the content job).
- **Terminal-failure domain hooks (the corollary of the ban):** because settles are side-effect-free and reaper-terminalized jobs (poison, expiry exhaustion) execute *no worker code at all*, the paved mechanism for "the domain must learn a job died" is a **domain reconciler job** — a `taskq.schedules` row whose handler sweeps `taskq.dead_jobs` / `workflow_status` for its job types and writes the domain markers (qdarte's `blocked_exhausted` lane is the worked example, §16.1.3). `fail_job` deliberately has no `p_followups`: a followup-on-failure would fire only on worker-observed failures and silently miss reaper deaths — the reconciler covers every death path uniformly.

---

## 11. Control plane

### 11.1 Operator operations (SQL functions; the HTTP facade wraps 1:1)

| Operation | Verb |
|---|---|
| Pause / resume claims | `taskq.pause_queue(name, actor, reason)` / `resume_queue(name, actor)` |
| Drain queue | pause + wait for `running = 0` in the stats view (drain is a read loop, not a state) |
| Cancel job / workflow | `taskq.cancel_job(id, actor, reason)` / `cancel_workflow(id, actor)` |
| Redrive dead letters | `taskq.redrive_job(id, actor)` / `redrive_failed(queue, type, limit)` |
| Take back a wedged running job | `taskq.expire_job(id, actor)` |
| Declare a worker dead | `taskq.expire_worker_leases(worker_id, actor)` |
| Purge queued backlog | `taskq.purge_queued(queue, type, actor, limit)` — cancels, never deletes |
| Rush / reprioritize | `taskq.run_now(id)` / `reprioritize(id, priority, actor)` |
| Create a workflow | `taskq.create_workflow(key, kind, params, actor)` — idempotent on `workflow_key` |
| Close a resource | `taskq.set_concurrency_limit('llm:lmstudio-host', 0, actor)` — `0` = pause valve |
| Drain a worker / fleet / lane | `taskq.request_worker_shutdown(worker_id, queue, actor)` |
| Force maintenance | `SELECT taskq.tick();` / `SELECT taskq.janitor();` |

All operator verbs are functions — the operator role holds EXECUTE, not DML (§4); there is no paved operation expressed as a raw UPDATE.

### 11.2 Worker presence

Workers upsert `taskq.workers` every ~60s (`taskq.worker_heartbeat(...)`), reading back `shutdown_requested_at` as the drain signal — Diverse's proven heartbeat-as-drain-channel, minus the pool-identity flapping (whole-row upsert from one call site, DCP 7.3). **Strictly advisory:** no correctness path reads this table; `ps`-based discovery and presence-driven reclaim are dead (QO 7.3).

### 11.3 Environment interlocks

Kept from Diverse verbatim in client and facade: refuse `production` targets without `TASKQ_ALLOW_PRODUCTION=true`; refuse env-name mismatches against `TASKQ_EXPECTED_ENV`. Cheap, effective, standardized.

### 11.4 The tick — savepoint-per-pass, observable, leaderless

```sql
CREATE OR REPLACE FUNCTION taskq.tick(p_reap_limit int DEFAULT 200)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_out jsonb := '{}'; v_n int;
BEGIN
    -- Fleet-wide dedup: at most one concurrent ticker, zero waiting.
    IF NOT pg_try_advisory_xact_lock(hashtextextended('taskq:tick', 0)) THEN
        RETURN jsonb_build_object('skipped', true);
    END IF;
    UPDATE taskq.control_state SET last_started_at = now() WHERE key = 'tick';
    INSERT INTO taskq.control_state (key, last_started_at) VALUES ('tick', now())
        ON CONFLICT (key) DO UPDATE SET last_started_at = now();

    -- Savepoint per pass (robustness graft, judge 2): one failing pass logs to
    -- control_state.last_error; the remaining passes still run.
    BEGIN
        v_n := taskq.reap_expired(p_reap_limit);
        v_out := v_out || jsonb_build_object('reaped', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'reap: ' || SQLERRM WHERE key = 'tick';
    END;

    BEGIN
        -- Finalize cancel-requested blocked/queued stragglers (cancel raced a transition; §5.9).
        v_n := taskq.finalize_cancel_stragglers(50);
        v_out := v_out || jsonb_build_object('cancel_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'cancel: ' || SQLERRM WHERE key = 'tick';
    END;

    -- [0.2 contract only — absent from the 0.1 migration, ADR-009]
    BEGIN
        -- Cancel blocked/queued rows gated by a terminal dep — the convergence backstop
        -- for cancel_dependents' SKIP LOCKED skips (§5.9).
        v_n := taskq.finalize_dep_stragglers(50);
        v_out := v_out || jsonb_build_object('dep_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'deps: ' || SQLERRM WHERE key = 'tick';
    END;

    -- [0.2 contract only — absent from the 0.1 migration, ADR-009]
    BEGIN
        v_n := taskq.finalize_workflows(50);   -- stamp workflows whose members are all terminal
        v_out := v_out || jsonb_build_object('workflows_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'workflows: ' || SQLERRM WHERE key = 'tick';
    END;

    BEGIN
        -- Stats snapshot for exporters/dashboards (§12.1): bounded, index-backed,
        -- written to control_state key 'stats_snapshot' with as_of.
        PERFORM taskq.refresh_stats_snapshot();
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'stats: ' || SQLERRM WHERE key = 'tick';
    END;

    -- v1.6 (R2-09, ADR-009 carve-out): the 0.1 DUE-GATED DAILY JANITOR. Schedules
    -- are a 0.2 capability, so the tick itself carries the trigger: atomically
    -- claim the 'janitor_daily' due marker (control_state.data holds next_due);
    -- if due, run the janitor's independently bounded passes. Ordering is
    -- load-bearing: reaping ALWAYS ran first (above) — a slow janitor can degrade
    -- retention, never lease recovery. Row/time budgets inside taskq.janitor keep
    -- the pass smaller than the tick cadence's overlap tolerance; an exception
    -- block is a subtransaction, not an independent commit, so the janitor's own
    -- per-pass error records (not this savepoint) are the observability surface.
    -- Marker policy: advance next_due on successful claim; a failed pass records
    -- last_error and stays due on the next tick. In 0.2 the seeded schedule row
    -- replaces this trigger; the janitor function and its bounds are unchanged.
    BEGIN
        IF taskq.claim_janitor_due() THEN
            v_out := v_out || jsonb_build_object('janitor', taskq.janitor());
        END IF;
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'janitor: ' || SQLERRM WHERE key = 'tick';
    END;

    UPDATE taskq.control_state SET last_finished_at = now() WHERE key = 'tick';
    RETURN v_out;
END $$;
-- EXECUTE: taskq_housekeeper + taskq_operator (ADR-011). No public HTTP route.
```

**Who runs it — per deployment topology (normative):**
- **DB-connected workers (qdarte):** every worker's housekeeper coroutine calls `SELECT taskq.tick()` every ~5s (jittered) and runs the schedule-firing protocol (§6, client-side croniter) on the same cadence — the advisory lock makes N workers cost one ticker.
- **HTTP-facade deployments (the no-DB-credential Diverse fleet):** HTTP workers *cannot* tick — they have no DB connection, and the facade deliberately exposes no tick route. **The API process hosting the facade (diverse-data-api) runs the housekeeper coroutine** — it holds the DB connection and is always running. Without this, reaping in the Diverse topology would degrade to the bounded idle-claim micro-reap alone and cron (court scrapes, the janitor) would never fire; §16.2.1 makes it a cutover requirement.
- A `taskq tick` CLI subcommand exists for real cron as belt-and-braces on quiet deployments (and as the profile for autosuspending databases, §20.3).

Recovery latency is bounded by `lease_expires_at + ~5s`; if every ticker dies, the idle-claim micro-reap still recovers leases as long as any worker polls, and if no worker polls, nothing is consuming anyway (frozen queue, visible in `worker_status` and the `taskq_tick_age_seconds` alert).

### 11.5 The 2am psql runbook (part of the contract — simplicity graft, all judges)

```sql
-- WHAT IS GOING ON?
SELECT * FROM taskq.queue_stats;                                        -- depth/oldest-age per queue
SELECT * FROM taskq.jobs WHERE status = 'running' ORDER BY started_at;  -- who is doing what
SELECT * FROM taskq.worker_status;                                      -- fleet liveness
SELECT * FROM taskq.job_events WHERE job_id = $1 ORDER BY id;           -- one job's full timeline
SELECT key, last_started_at, last_finished_at, last_error FROM taskq.control_state;

-- PAUSE / RESUME a queue (claims stop; enqueues still land)
SELECT taskq.pause_queue('zillow_fsbo', 'operator:andi', 'incident #42');
SELECT taskq.resume_queue('zillow_fsbo', 'operator:andi');

-- PAUSE a shared resource (LLM host melting)
SELECT taskq.set_concurrency_limit('llm:lmstudio-host', 0, 'operator:andi');

-- DRAIN the fleet (workers finish current job, then exit)
SELECT taskq.request_worker_shutdown(NULL, NULL, 'operator:andi');       -- everything
SELECT taskq.request_worker_shutdown(NULL, 'render', 'operator:andi');   -- one lane

-- A WORKER BOX DIED
SELECT taskq.expire_worker_leases('mini:courts:1', 'operator:andi');

-- CANCEL
SELECT taskq.cancel_job('018f3f...', 'operator:andi', 'wrong params');
SELECT taskq.cancel_workflow('018f40...', 'operator:andi');

-- INSPECT + REDRIVE the dead letters
SELECT * FROM taskq.dead_jobs LIMIT 50;
SELECT taskq.redrive_job(id, 'operator:andi') FROM taskq.jobs
WHERE status = 'failed' AND queue = 'remine' AND error LIKE '%quota%';

-- RUSH a job
SELECT taskq.run_now($1); SELECT taskq.reprioritize($1, 0, 'operator:andi');

-- PURGE pending work you never want to run
SELECT taskq.purge_queued('old_lane', NULL, 'operator:andi', 10000);

-- TAKE BACK a wedged running job / FORCE maintenance now
SELECT taskq.expire_job($1, 'operator:andi');
SELECT taskq.tick(); SELECT taskq.janitor();
```

**Read surface (v1.6, ADR-011):** the psql one-liners below are illustrative of the *questions*; the granted answers are the safe views/functions (`queue_stats`, `dead_jobs`, `worker_status`, `get_job`, `taskq.metrics()`) — `taskq_observer`/`taskq_operator` hold no base-table SELECT, and raw `SELECT ... FROM taskq.jobs` is an audited owner break-glass path, not the runbook.

**Hard operator rules:** (1) **never `UPDATE taskq.jobs SET status = ...` by hand** — always the functions, so events, budget, and invariants stay true (the Diverse rendering-reset bug, DC 11.10, now has a paved alternative AND a role grant blocking the unpaved one); (2) **no table outside `taskq` may FK into `taskq` tables** — enforced by the ownership split (`taskq_owner` owns the schema; the app/migration role holds no `REFERENCES` grant on it) plus the migration-time information_schema sweep (§16.2.5). Where migrations run as an owner/superuser role, the rail is convention + that sweep — stated honestly in §4; it is not a `REVOKE ... FROM PUBLIC` (which would be a no-op).

---

## 12. Observability

### 12.1 Views (SQL-first; dashboards and exporters read these)

```sql
CREATE VIEW taskq.queue_stats AS
SELECT q.name AS queue, q.paused_at IS NOT NULL AS paused,
       count(*) FILTER (WHERE j.status = 'queued' AND j.scheduled_at <= now()) AS ready,
       count(*) FILTER (WHERE j.status = 'queued' AND j.scheduled_at >  now()) AS scheduled,
       count(*) FILTER (WHERE j.status = 'blocked')                            AS blocked,
       count(*) FILTER (WHERE j.status = 'running')                            AS running,
       count(*) FILTER (WHERE j.status = 'running' AND j.lease_expires_at < now()) AS expired_running,
       count(*) FILTER (WHERE j.status = 'failed')                             AS dead,
       extract(epoch FROM now() - min(j.scheduled_at) FILTER
           (WHERE j.status = 'queued' AND j.scheduled_at <= now()))::bigint    AS oldest_ready_seconds,
       min(j.lease_expires_at) FILTER (WHERE j.status = 'running')             AS next_lease_expiry
FROM taskq.queues q LEFT JOIN taskq.jobs j ON j.queue = q.name
GROUP BY q.name, q.paused_at;

CREATE VIEW taskq.dead_jobs AS
SELECT id, queue, job_type, outcome, error, failure_count, expiry_streak, finished_at,
       workflow_id, payload
FROM taskq.jobs WHERE status = 'failed'
ORDER BY finished_at DESC;

CREATE VIEW taskq.workflow_status AS       -- derived, never stored-and-drifting
SELECT w.id, w.workflow_key, w.kind, w.status AS stored_status,
       count(j.id) AS total,
       count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
       count(*) FILTER (WHERE j.status = 'failed')    AS failed,
       count(*) FILTER (WHERE j.status = 'cancelled') AS cancelled,
       count(*) FILTER (WHERE j.status IN ('blocked','queued','running')) AS active
FROM taskq.workflows w LEFT JOIN taskq.jobs j ON j.workflow_id = w.id
GROUP BY w.id;

CREATE VIEW taskq.worker_status AS
SELECT w.*, (now() - w.last_seen_at) < interval '180 seconds' AS online,
       (SELECT count(*) FROM taskq.jobs j
         WHERE j.worker_id = w.worker_id AND j.status = 'running') AS running_jobs
FROM taskq.workers w;

CREATE VIEW taskq.rates_15m AS             -- grouped by typed events, never raw error text
SELECT j.queue, e.event_type, count(*) AS events
FROM taskq.job_events e JOIN taskq.jobs j ON j.id = e.job_id
WHERE e.created_at > now() - interval '15 minutes'
  AND e.event_type IN ('claimed','succeeded','failed','retry_scheduled','lease_expired','released','snoozed')
GROUP BY 1, 2;
```

**Cost note (normative):** `queue_stats` as written is a grouped aggregate over the whole hot table (including up to 48h of terminal rows and 14d of dead letters) — it is the *ad-hoc psql* surface, not the scrape path. `taskq.metrics()` must **not** execute it per call: it composes targeted per-status counts that each ride an existing partial index (ready/scheduled split on `jobs_claim_idx`, running on `jobs_running_idx`, dead on `jobs_finished_idx`) plus `control_state`, and the tick additionally refreshes a `queue_stats` snapshot into `taskq.control_state` (key `stats_snapshot`, `data` jsonb — §4) so exporters on high-volume deployments read the snapshot (staleness ≤ one tick, fine for dashboards). Scrape-interval floor: 15s. `queue_stats`/metrics latency under a deep backlog is asserted in the soak gate (§16.3.4) — a repeated full-table aggregate against the table the claim path contends on is the DC 11.13 stats mistake in miniature, and it is designed out here, not just discouraged.

### 12.2 Metrics contract

`taskq.metrics()` returns `(name text, labels jsonb, value numeric)` — one call feeds a Prometheus exporter route (`/taskq/metrics`) in each API. Gauges/counters: `taskq_ready{queue}`, `taskq_scheduled`, `taskq_blocked`, `taskq_running`, `taskq_expired_running`, `taskq_dead_total`, `taskq_oldest_ready_seconds`, `taskq_release_count_sum`, `taskq_tick_age_seconds` (from `control_state`), `taskq_workers_online`, `taskq_archive_default_rows` (rows sitting in the archive DEFAULT partition — should be 0), `taskq_index_bytes{index}` (janitor-recorded sizes of `jobs_claim_idx`, `jobs_idem_uq`, and the PK). From the standard postgres exporter: `pg_stat_user_tables.n_dead_tup` on `taskq.jobs` (PG18 adds `total_autovacuum_time`), and `n_tup_hot_upd / n_tup_upd` as the HOT-ratio check for the heartbeat path.

**Day-one alert set:**
- `taskq_oldest_ready_seconds > 15m` for 10m per queue → queue not draining.
- `taskq_expired_running > 0` for 10m → workers dying or maintenance not running.
- `increase(taskq_dead_total, 1h) > threshold` → failure spike.
- `taskq_tick_age_seconds > 120` → no ticker alive (robustness graft).
- `taskq_release_count_sum` rising without deploys → handler-missing spin (ex-F12).
- `n_dead_tup(taskq.jobs) > 50k` → vacuum falling behind.
- `taskq_archive_default_rows > 0` → archive partition rotation missed or failing — an incident, not a log line (§13.3).
- `taskq_index_bytes` sustained growth with flat row count → B-tree bloat outrunning the janitor's REINDEX cadence (§13.5).

### 12.3 Forensics

Per-job timeline = `job_events` ordered by identity `id` (no timestamp ties) + `job_attempts` ("who consumed which budget"). Every event carries actor identity. **Reads never write** — no reaper-in-GET is possible because reclaim exists only in `reap_expired`, called only by tick and the idle micro-reap. `current_attempt_id` never appears in read models (attempt ids are capability tokens; DCP 7.11 mitigation).

---

## 13. Retention, archival, bloat engineering

Layered so the hot table stays small **by construction** (PA §2.1: hot-table churn is the #1 killer):

1. **In-table terminal retention:** terminal rows stay in `taskq.jobs` for `queues.retention_hours` (default 48h) for the ops UI and workflow rollups. **Exception — `failed` rows (the dead-letter set) use `queues.failed_retention_hours` (default 336h = 14 days):** `redrive_job` targets the hot table only, and there is no unarchive path, so archiving dead letters at 48h would silently amputate redrive after a weekend (batch poisons Friday night, inspected Monday-after-next — still redrivable). Dead letters are few by definition; 14 days of them is cheap. Redriven or purge-cancelled rows age out on the normal clock. Partial indexes already exclude terminal rows from every hot path.
2. **Archive move (janitor, 0.3):** normative ordering (v1.6, R2-13 — never rely on cascade/`RETURNING` evaluation order against `job_attempts`' `ON DELETE CASCADE`): **select-and-lock** bounded candidates (`FOR UPDATE SKIP LOCKED`, guarded by `NOT EXISTS (job_deps.depends_on = id)`) → **aggregate attempts while the rows still exist** → **insert** complete archive rows → **delete** the hot jobs, all one transaction with a row-count conservation assertion (inserted == deleted) — the archiver **skips** parents still gating a dependent instead of wedging (kills DC 11.2's retry-the-poisoned-batch-forever) and can never un-gate anything (kills qdarte F15). Events are pruned, not archived.
3. **Archive retention = partition DROP.** `taskq.rotate_archive_partitions(keep_months => 6)` runs inside the janitor — deletion without dead tuples, `n_dead_tup = 0` on the archive. Because a partition CREATE is DDL and the janitor runs under an application capability role, the function is **`SECURITY DEFINER` owned by `taskq_owner`** and EXECUTE-granted to `taskq_operator` (§4, ADR-010) — a permissions mismatch cannot silently disable retention. Rotation is engineered around a Postgres fact the naive design ignored: **you cannot CREATE/ATTACH a partition whose range overlaps rows already in the DEFAULT partition** — the DDL fails after scanning the default under ACCESS EXCLUSIVE, and there is no automatic re-split. Therefore:
   - **Rotate ahead:** every run ensures partitions exist for the current month **and the next two** — one missed janitor run (fleet down over a month boundary) can never strand rows in the default.
   - **Self-healing default:** before each CREATE, rotation checks the default partition for rows in the target range and **re-homes them first** (batched move: `DELETE ... RETURNING` from the default, `INSERT` into the freshly created partition — create under a temporary name, move, attach), so a default landing is repaired on the next run, not permanent.
   - **Loud, not silent:** `taskq_archive_default_rows > 0` is an alerting metric (§12.2). A missed rotation degrades to: rows land in default → alert fires → next janitor run re-homes and resumes partition-DROP retention. *Not* "self-managing with no failure mode" — self-healing with a visible one (§17, §19 re-worded to match).
   PG18 eager freezing amortizes the append-only archive's freeze work.
4. **Event pruning (tiered, qdarte's discipline kept):** verbose types at 7 days, everything at 30 days, batched deletes ordered by the BRIN.
5. **Janitor** = archive + prune events + finalize workflows + prune stale `workers` rows (>7 days) + rotate partitions + recording index sizes for §12.2. Savepoint per pass. **Transactional work only (v1.5, ADR-010):** `REINDEX ... CONCURRENTLY` cannot run inside a transaction block and a PL/pgSQL function always is one — the v1.1–v1.4 design that scheduled it from the janitor could never execute. Index maintenance moves to the external **`taskq maintenance`** CLI/daemon: admin credentials, autocommit, per-run advisory lock, bloat/age thresholds, logged plan, `--dry-run`, safe under double invocation — weekly cadence to start (the Go/Postgres job queue ships daily — the proven ceiling; tighten if the retry-storm soak or the `taskq_index_bytes` alert says so). Janitor trigger: the seeded `taskq-janitor` cron row once schedules exist; **in the 0.1 contract (schedules deferred, ADR-009) the housekeeper tick hardwires a daily janitor pass**, plus the `taskq janitor` CLI either way. qdarte's daily-ops ordering is retained: **backup before janitor**.
6. **Heap/index hygiene — the honest model:** `fillfactor = 85` + the unindexed lease column keep **pure lease-bump heartbeats** HOT (the dominant per-job write). Lifecycle writes are **structurally non-HOT** — `status` appears in the predicates of five partial indexes and `scheduled_at`/`finished_at` are key columns — so every claim, settle, and retry requeue inserts fresh entries into the PK and each matching partial index; a job that retries 4 times leaves ~10 versions churning the left edge of `jobs_claim_idx` and the UNIQUE `jobs_idem_uq`. That churn is why scheduled concurrent reindexing exists (via the external `taskq maintenance` command — ADR-010, above) and why the soak includes a retry-storm profile (§16.3.4) — B-tree bloat from lifecycle writes is budgeted, not wished away. `uuidv7` keeps the PK append-mostly; per-table autovacuum reacts at 500 dead tuples; `toast.*` reloptions cover progress-checkpoint churn (§4, §5.4); global backstops on PG18 (`autovacuum_vacuum_max_threshold = 500000`, `track_cost_delay_timing = on`). Escape hatch: PG19 `REPACK CONCURRENTLY taskq.jobs` in a low-traffic window. Hot-table partitioning is explicitly rejected at this scale.

---

## 14. Client library (Python) and HTTP facade

One package (`taskq`, published like outlabs-auth), consumed by qdarteAPI, qdarte-workers, diverse-data-api, diverse-data-workers. It contains the SQL installer/migrations, the asyncio client, the worker runtime, the **only** pydantic contract models (no hand-mirroring — DCP 7.14 dead), the FastAPI facade router, and the CLI.

```python
from taskq import TaskQ, RetryPolicy, Retry, NonRetryable, Snooze, Enqueue, JobContext, PRIORITY

tq = TaskQ(dsn=settings.dsn)      # SQLAlchemy async / asyncpg; schema is FIXED at `taskq` (ADR-002)

# --- Task registry: policies are DEFAULTS stamped onto rows at enqueue --------
@tq.task(
    queue="zillow_fsbo", max_attempts=5, lease_seconds=900,
    retry=RetryPolicy(mode="exponential", base=30, cap=3600),
    concurrency_key=lambda p: f"proxy:{p['proxy_pool']}",     # derived per payload
)
async def scrape_fsbo_counties(ctx: JobContext, payload: dict) -> dict:
    done = set((ctx.progress or {}).get("done", []))          # resume from checkpoint
    for county in payload["counties"]:
        if county in done: continue
        ctx.raise_if_cancelled()                              # cooperative cancel point
        await scrape(county)
        done.add(county)
        await ctx.checkpoint({"done": sorted(done)})          # BATCHED: flushed onto at most one
    return {"counties": len(done)}                            # heartbeat per ~30s, not per unit;
                                                              # keep progress <2KB — it is a cursor,
                                                              # not a result set (§5.4)

# --- Chaining: followups execute inside the settle transaction (exactly-once) --
@tq.task(queue="courts")
async def scrape_county(ctx, payload):
    data = await do_scrape(payload)
    await store_results(data)                                 # app tables, idempotent
    return Result.success(
        {"records": len(data)},
        followups=[Enqueue("enrich_county", {"county": payload["county"]}, step="enrich")],
    )                                                         # -> complete_job(p_followups=...)

# --- Producers (inside the caller's transaction => the outbox property) -------
async with session.begin():
    ...domain writes...
    job = await tq.enqueue(scrape_fsbo_counties, payload, session=session,
                           idempotency_key=f"fsbo:{digest}", priority=PRIORITY.HIGH,
                           workflow=run_wf, depends_on=[plan_job.id])   # (id, created)
    await tq.enqueue_many(items, session=session)             # <=1000/call, created/existing split

# --- Worker --------------------------------------------------------------------
worker = tq.worker(worker_id=stable_worker_id(), queues=["zillow_fsbo"],
                   concurrency=2, batch=2, poll_interval=5.0, listen=True)
await worker.run()
```

Handler control flow: `return dict`/`Result.success` → `complete_job`; `raise Retry(after="1h30m")` → `fail_job(retryable=True, retry_after_seconds=...)` (hints normalized client-side); `raise NonRetryable(...)` → `fail_job(retryable=False)`; `raise Snooze(3600)` → budget-free snooze; unhandled exception → `fail_job(retryable=True)`.

**Worker-loop guarantees (normative — where both systems' worker bugs are fixed):**
1. **Heartbeat task per job** at `min(lease/3, 30s)`, retry+backoff on transport errors — never exits on one failure (kills DCP 7.2's one-strike death). The duration is the effective `claimed_job.lease_seconds` returned under ADR-013 and is scheduled on a monotonic timer; workers **never** derive it from `lease_expires_at - local_now()`. On `ok = false` or 3 consecutive transport failures: **stop the handler** — for async handlers, cancel the task immediately; for thread-offloaded sync handlers (which Python cannot kill — a running call ignores `Future.cancel()`, v1.6/R2-11) signal the cooperative token, **suppress any later settlement from this attempt, and never release/snooze while the thread may still run** — keep the lease-loss loud (process exit if the thread never yields) and let lease expiry reclaim. On `cancel_requested = true`: signal cooperative cancel (`ctx.raise_if_cancelled` fires at the next checkpoint) and **hard-cancel the handler asyncio task after a grace period (default 30s)** even if the handler never reaches a cooperative checkpoint — operator cancel of asyncio-cancellable code must not depend on handler cooperation (a handler hung in a non-cooperative section while its heartbeat coroutine stays alive would otherwise be uncancellable; the DB-side backstop for that wedge is `taskq.expire_job`'s synchronous reap, §5.9).
2. **Settle retries** on 5xx/timeout; `already_settled` = success; `lost` = ERROR log with the attempts-ledger reference, never report results elsewhere. No discarded finished batches.
3. **Graceful shutdown:** SIGTERM / `shutdown_requested_at` → stop claiming → cooperative cancel, then after grace: async handlers are hard-cancelled and released (`release_job(p_cause='worker_shutdown')`, budget-free, checkpoint attached); **sync/thread handlers are never released while the thread lives** (R2-11) — the runtime either keeps the lease alive and waits, or exits the whole process and lets lease expiry reclaim → presence bye.
4. **Unknown `job_type`** → `release_job(p_cause='no_handler', delay=60)` + loud log + startup assertion that subscribed queues' types are all handled (no budget burn, no spin, typed outcome for the `release_count` alert).
5. **Housekeeper coroutine:** `taskq.tick()` + schedule firing (croniter) every ~5s, advisory-lock-deduped fleet-wide.
6. **LISTEN** on `taskq_{queue}` via one dedicated non-pooled connection; degrade silently to polling; polling is always the correctness mechanism; skip LISTEN behind transaction-pooling pgbouncer.

**HTTP facade** (`taskq.http`, for the no-DB-credential Diverse fleet): FastAPI router — claim with **long-poll `?wait=25s`** (the NOTIFY bridge for HTTP workers, closing DCP 7.13's latency asymmetry without giving them DB connections — judge 1 mandate; accepts an optional `job_id` for targeted claims, §5.3), the settle verbs (including the fenced `cancel-running`, ADR-007), enqueue single/bulk, reads, and operator routes 1:1 with §11.1 (including `create_workflow` — the `workflow=` producer parameter above calls it under the hood). **Route shapes in this document are illustrative — the versioned transport protocol (ADR-005) is canonical once published**; direction is command-oriented `/taskq/v1/...` with the queue in the path for queue-addressed commands. **The facade host process runs the housekeeper coroutine (tick + schedule firing, §11.4) — HTTP workers never tick.** `'lost'` → 409, `'already_settled'` → 200. Auth: queue-scoped authorization per ADR-006 — job-ID routes authorize from the `get_authorization_projection(job_id)` read, never from caller-supplied queue/job_type (those are assertions, rejected on mismatch); credentials per the Authorization doc (service tokens default; a single shared fleet key remains acceptable single-tenant, `worker_id` attribution then advisory). Environment interlocks included.

**CLI:** `taskq migrate`, `taskq verify` (ADR-004), `taskq tick`, `taskq janitor`, `taskq maintenance` (out-of-transaction index work, ADR-010), `taskq stats`, `taskq redrive`, `taskq pause|resume`, `taskq expire-worker` — the cron/ops escape hatch.

---

## 15. Postgres version strategy

Installer detects `server_version_num`, records capabilities in `taskq.meta`, and swaps function bodies where profitable. Nothing above PG16 is load-bearing.

### 15.1 Baseline (PG16/17) — everything works
`FOR UPDATE SKIP LOCKED`, partial unique indexes, `ON CONFLICT DO NOTHING` with index predicate, advisory locks, LISTEN/NOTIFY, per-table autovacuum/fillfactor, range partitioning: the correctness core is PG9.5–13 era. `taskq.uuid7()` uses the pure-SQL RFC-9562 fallback. (Caller-supplied job ids are **not accepted** in 0.x — v1.6/R2-06; ids are server-generated, and the deadlock proof no longer depends on id ordering anyway.) Global vacuum backstops unavailable — per-table settings carry the load.

### 15.2 PG18 — the deploy target (both systems already run it)

| Feature | Use |
|---|---|
| `uuidv7()` | server-side id default — append-mostly PK, right-edge-hot claim index, FIFO tiebreaker |
| Per-table autovacuum + `vacuum_truncate = off` | in the DDL, not a TODO |
| `autovacuum_vacuum_max_threshold`, `autovacuum_worker_slots`, `track_cost_delay_timing` | global churn backstops |
| B-tree skip scan | type-filtered monitoring rides `jobs_claim_idx` without a third index (verify with `EXPLAIN` `Index Searches: N`) |
| `NOT NULL ... NOT VALID` | zero-scan schema evolution on the hot table |
| Eager freezing | append-only archive freeze amortization |
| AIO (`io_method = worker`) | faster vacuum/archive sweeps; claim path unaffected; keep `worker` under Docker/OrbStack (seccomp blocks io_uring) |
| pg_upgrade keeps stats | no post-upgrade claim-plan regressions |

Not used: virtual generated columns (not indexable), temporal `WITHOUT OVERLAPS` (GiST write cost wrong for the hot path — the attempts partial unique already enforces the invariant), `RETURNING OLD/NEW` (not load-bearing; may simplify functions later).
Diverse pg_duckdb substrate: `duckdb.execution = off` on the taskq role (§4); keep long-snapshot analytics off the queue's MVCC horizon — first thing to check if `n_dead_tup` climbs.

### 15.3 PG19 (GA ~Q4 2026) — optional accelerators, never load-bearing

| Feature | Adoption |
|---|---|
| `INSERT ... ON CONFLICT DO SELECT RETURNING` | enqueue's DO-NOTHING + re-select tail collapses to one statement (installer swap) |
| `REPACK CONCURRENTLY` | scheduled bloat recovery on `taskq.jobs` if ever needed (one at a time; budget a replication slot) |
| Targeted NOTIFY wakeups | channel-per-queue layout wins ~10–40x at high channel counts; commit-path serialization is NOT fixed — the once-per-transaction rule stays; NOTIFY remains a hint |
| Parallel + scored autovacuum | prioritize the queue table over cold analytics tables |
| `pg_stat_lock`, `log_lock_waits` default on | claim/cap contention forensics |
| 64-bit multixact members | removes the wraparound cliff for `FOR SHARE` dep locks under load |

Re-verify against final release notes at GA.

---

## 16. Migration plan (phased, no big-bang)

**Sequencing rule (explicit, judge-mandated): qdarte cuts over first — personal blast radius. Diverse second, after weeks of observed production behavior — Diverse is the protected income realm.** taskq installs alongside the existing tables (new schema, zero contact); traffic moves lane-by-lane; old tables drain to zero and are dropped. No in-flight row translation — **drain-in-place** (cheaper and safer than translating live leases). The validation gates (§16.3) pass before any production lane moves.

### 16.0 Phase 0 — package + schema (both projects)
Ship the `taskq` package (installer, library, facade, CLI, test suite). Alembic migration creates schema + functions + views + roles + seeds (`_system` queue, `taskq-janitor` schedule, `taskq.queues` rows, `taskq.concurrency_limits` rows). Vocabulary shims per §2, including the **priority ×50 remap for Diverse values** at the enqueue shim, and the **retry-budget shim**: Diverse `max_retries = 0` → `p_max_attempts = 1`, `max_retries = N` → `p_max_attempts = N`. (`max_attempts` is CHECK-constrained to ≥1; a naive passthrough of Diverse's production `max_retries=0` call sites — renders, prospect_intake, which use 0 to mean fail-terminally-on-first-failure — would raise inside `taskq.enqueue` and, because chain enqueues run inside the caller's ingest transaction, abort the whole ingest write. Semantic note, stated so nobody is surprised: Diverse counted *claims*; taskq counts *failures* — releases and expiries now count per §3.3, which makes migrated budgets slightly more generous, never less.) A validation-gate test asserts every existing Diverse and qdarte enqueue call site's literal values pass taskq's CHECK constraints (§16.3.2).

### 16.1 Phase 1 — qdarteAPI / qdarte-workers / qdarte-runtime

**Staging-first gate (Codex review 2026-07-09):** before writing any production-routable enqueue path, qdarte gets explicit cutover rails:

- `QDARTE_TASKQ_ENABLED=false` by default everywhere; production remains false until the staging evidence packet is complete.
- `QDARTE_TASKQ_ENV` must match the app environment before taskq claim/enqueue routes accept traffic; an environment mismatch is a hard refusal, not a warning.
- `QDARTE_TASKQ_JOB_TYPES` / `QDARTE_TASKQ_QUEUES` are allowlists. Any unlisted job type keeps using legacy `qdarte_ops.worker_jobs`.
- `QDARTE_TASKQ_ROLLBACK=true` (or clearing the allowlist) immediately returns new producers to legacy enqueue; existing taskq rows drain or are operator-cancelled/redriven, never live-translated.
- A staging smoke proves production config cannot enqueue/claim taskq jobs by default, then proves one allowlisted staging lane can enqueue, claim, heartbeat, settle, pause/resume, redrive, and fall back to legacy. The evidence packet includes a sanitized cutover-status read showing `cutover_active`, `rollback`, environment match, and the effective queue/job-type allowlists; the full staging packet must show exactly `queues=["comms"]` and `job_types=["contact_verify_scope"]` before the first production flip.

The first staging lane must be deliberately boring: **no `publish`, no launch-pipeline completion followups, no engine-specific LLM content queue, and no job type whose app writes are not mapped.** Preferred first candidates are bounded `media` or `research` jobs with minimal followups. Before enabling any lane, write a side-effect disposition row for every job type in that lane: producer, handler, result/app-table writes, artifacts, proxy/domain signals, followups, dependencies/workflows, schedules/recovery hooks, and rollback path. A lane is not eligible until that row says exactly where each legacy side effect lands under taskq.

The worker-runtime port must also choose one compatibility mode before staging tests: either provide a sync HTTP client wrapper that preserves today's synchronous handler loop while mapping typed taskq results (`already_settled`, `lost`, heartbeat `ok=false`, cancel) to explicit worker behavior, or port the qdarte worker loop to the async taskq runtime in one slice. **Decision for first staging lane:** use the sync taskq HTTP wrapper so the existing qdarte-workers handler loop stays intact while the queue contract is proven. A later async port is allowed only after the first lane is quiet. A half-port that keeps status-code exceptions as the settle contract is rejected, because it preserves the lost-settle ambiguity taskq exists to kill.

1. Create queues: `media`, `research`, `publish`, `discovery`, `comms`, `_system`, and — because the LLM engine is a property of the *claiming fleet spec*, not the payload (§9 binding rule) — **engine-specific content queues**: `content_lmstudio`, `content_claude`, `content_codex`. Each fleet spec's `supported_job_types` repoints at its engine's queue, so `llm:lmstudio-host = 1` is stamped determinately at enqueue and a Claude-spec worker can never burn (or bypass) the LM Studio slot. Limits: `llm:lmstudio-host = 1`, `llm:claude-api = 6`, `llm:codex-api = 6`, per-proxy keys.
2. Registry port: `JobExecutionPolicy`/`JobRetryPolicy` map 1:1 onto `@tq.task` (same fields). Job types keep their names. Dependency edges **port as edges** (no re-architecture).
3. Structural fixes during the port: `publish_scope` side effects move out of `complete_job` into a `publish` worker job; the three orchestration idioms collapse to edges + settle-followups; the launch-pipeline state machine stays in domain code but only enqueues (idempotency-keyed); recurring templates become `taskq.schedules` rows; the two worker frameworks become one (`qdarte-runtime` local_worker retired; qdarte-workers adopts the taskq runtime). Plus the **domain-hook and sweep dispositions**, item by item so nothing silently loses its driver:
   - **On-terminal-failure domain hooks** (the `blocked_exhausted` markers `fail_job` used to write for translations and content scopes — what the recovery lane keys off): settles stay side-effect-free (§5.5 hard rule) and reaper deaths run no worker code at all, so the *only* complete mechanism is a **domain reconciler job** — a seeded `taskq.schedules` row (every 5 minutes) whose handler sweeps `taskq.dead_jobs` + `workflow_status` for its job types and writes the `blocked_exhausted` markers. One mechanism covers every death path: handler-observed terminal fails, poison quarantine, and expiry exhaustion alike. A translation job that dies by poison is picked up by the recovery lane within one reconciler interval — no silent mid-pipeline stall.
   - **`run_queue_tick`'s seven sub-passes**, each with an explicit owner after the opportunistic self-tick dies: expired-attempt reclaim / cancel finalize / workflow finalize → absorbed by `taskq.tick()`; **launch orchestration ticking** (`_tick_due_launch_orchestrations`, ~300s cadence) → a seeded `@every 300s` schedule enqueueing a `launch_orchestration_tick` domain job with `concurrency_key qdarte:launch-tick = 1`; **region-rescue backlog** (`_advance_region_rescue_backlog`) → same pattern, `qdarte:region-rescue = 1`; **exhausted-backlog recovery** → driven by the reconciler above; **artifact TTL expiry** → a seeded schedule enqueueing a qdarte maintenance job (`worker_artifacts` survives as an app table; taskq's janitor never touches app tables — the queue correctly rejects artifact stores, §19, but the table keeps a maintenance owner).
   - **LLM lane suppression** (`llm_provider_state.suppressed_until` claim filter): maps to **handler-side snooze** — the LLM handlers (content synthesis, translation, research-summarization types) check suppression state before spending and `raise Snooze(until - now)`; budget-free, and the lane re-arms automatically when `suppressed_until` passes. The lane-wide claim filter is not ported; `taskq.set_concurrency_limit(key, 0)` remains the manual override valve.
4. Delete on cutover: `_reclaim_expired_running_attempts` in reads (F2/F3), `ps`-based orphan reclaim (runtime "kill" becomes `expire_worker_leases`), `skip_own_releases`, module-global reconciler tick state, `worker_job_schedules` (absorbed by `scheduled_at` + schedules), app-clock lease math (F4).
5. Drain-in-place: old enqueue paths flip to taskq behind the explicit staging/prod/job-type allowlists above; existing `worker_jobs` rows finish under the old engine; freeze, snapshot to archive, drop after 30 quiet days.

### 16.2 Phase 2 — diverse-data-api / diverse-data-workers (after qdarte production observation)

**Staging-first gate (Codex review 2026-07-09):** Diverse may start staging
implementation once qdarte has an accepted final-gate packet. Production
cutover stays blocked until qdarte has completed a clean 24-hour production
taskq observation period and the Diverse staging packet is clean. The protected
income-realm rule is therefore split deliberately: implementation moves in
staging; production enablement does not. The qdarte production start timestamp
must be recorded explicitly; do not infer it from the final-gate packet alone.

- `DIVERSE_TASKQ_ENABLED=false` by default everywhere; production remains false
  until staging evidence and the qdarte 24-hour production observation gate are
  both complete.
- `DIVERSE_TASKQ_ENV` must match `APP_ENV` before any taskq mutation route
  accepts traffic. Environment mismatch is a hard refusal.
- `DIVERSE_TASKQ_QUEUES` / `DIVERSE_TASKQ_JOB_TYPES` are allowlists. Any
  unlisted platform/job kind keeps using the legacy `scrape_jobs` path.
- `DIVERSE_TASKQ_ROLLBACK=true` (or clearing the allowlists) immediately returns
  new producers to legacy enqueue; existing taskq rows drain or are
  operator-cancelled/redriven, never live-translated.
- The first staging lane must be a single court-scraper platform. Agent-profile,
  rendering, delivery, prospect, and audience-sync lanes remain legacy until the
  court lane proves taskq under staging gates.
- Before enabling a lane, write a side-effect disposition row naming producer,
  handler, app-table writes, result/progress mapping, proxy/domain signals,
  schedule/planner ownership, retry/cancel behavior, and rollback path.
- The first facade must preserve targeted claim-by-jobId. Affinity is
  preference-only and is not accepted as a replacement for this capability.
- The API host owns the taskq housekeeper/tick loop in Diverse because workers
  are HTTP-only. The housekeeper must be gated by the same taskq env/allowlist
  rails and use leaderless/advisory-lock behavior so multiple API workers do not
  double-drive tick effects.

1. Mount the HTTP facade under `/api/v1/taskq/` in diverse-data-api. Queues = today's 13 platform lanes (`zillow_agent_profile`, `zillow_fsbo`, `remine_property_details`, `prospect_intake`, `list_delivery_send`, courts, ...); `job_kind` → `job_type`. **diverse-data-api also hosts the housekeeper coroutine (tick + schedule firing, §11.4)** — the HTTP fleet has no DB access, so in this topology "ticking rides the workers" does not apply; it rides the API process. This is a cutover requirement, not an optimization: without it, no cron fires and reaping degrades to the idle micro-reap.
2. Workers repo swaps `DiverseQueueClient` + `run_worker_loop` for the taskq runtime in HTTP mode (same cadences to start; long-poll claim; heartbeat loop no longer dies on first failure **by contract**). **Targeted claim-by-jobId — Diverse's tier-1 claim, used by the HTTP-driven rendering flows — maps to `claim_jobs(p_job_id => ...)` (§5.3): same predicate gate, claims exactly that job or nothing.** (Affinity is preference-only and is *not* a substitute; the capability is carried over explicitly, not silently dropped.) `counties`-remainder logic becomes `progress` checkpoints. Per-county outcome rows move to an app-owned table keyed by `job_id` (plain uuid, written through ingest routes — results are app data, not queue data).
3. `queue_runs` → workflows (`kind = 'batch'`, `workflow_key` = old run key, created via `taskq.create_workflow`); **dispatchers become expansion jobs** with cursor checkpoints; CSV-import enqueues finally get idempotency keys (`csv:{run}:{row_hash}` — fixes DCP 7.7). Admin UI repoints at `workflow_status` + `queue_stats`.
4. `scheduler_windows` + `schedules.yaml` → `taskq.schedules` rows (cron grammar upgrade, per-schedule timezone, **`catchup_policy`/`max_catchup` set explicitly per family — court scrapes `fire_once`/1, audience syncs `skip`**). The scheduler container is deleted; ticking rides the facade host (item 1). **Payload dispositions per family — the Diverse scheduler builds payloads at fire time, which a static schedule row cannot express (§6):**
   - **Court scrape families** (date_range resolution → concrete `date_from/date_to`, county × case_type expansion into vendor ScrapeRequest arrays, content-fingerprint idempotency keys): the schedule row stores the *spec* and fires a **planner job**; the planner handler resolves dates, expands, and bulk-enqueues the real scrape jobs with the existing fingerprint keys — platform executors consume unchanged payload shapes. `fire_once`/1.
   - **Audience syncs**: planner job, `skip` catch-up.
   - **`court_case_rechecks`**: not an enqueue at all today (it POSTs `/api/v1/cases/rechecks/enqueue-due`) — becomes a taskq job whose handler calls that endpoint. `fire_once`/1.
   - **Fixed-payload families** (janitor-style): direct schedule rows, no planner.
5. **FK landmine checklist (verbatim, judge 3 mandate)** — these columns become **plain uuid columns with NO FK**, documented as "resolves against `taskq.jobs` UNION `taskq.jobs_archive`":
   - `prospects.last_job_id`
   - `render_jobs.*_job_id` (every job-reference column on render_jobs)
   - `letter_batches.render_queue_job_id`
   - plus a migration-time sweep: `SELECT ... FROM information_schema.constraint_column_usage WHERE table_name = 'scrape_jobs'` to catch the remaining ~4 referencing columns (worker_presence et al.) — every hit converts to a plain column.
6. Cutover per lane: court platforms first (lowest volume, singleton workers), agent-profile fleet last. The worker-pool control plane (`worker_platforms`, `worker_pool_targets`, compose reconciler) is **untouched** — it manages containers, not jobs, and now reads taskq views. **Drain bridge (one decision, made here):** the pool-target-driven per-instance graceful drain (presence heartbeat comparing `instance_index` against `desired_count`) no longer flows automatically once workers heartbeat `taskq.workers` — so the compose reconciler, which already reads `worker_pool_targets`, calls `taskq.request_worker_shutdown` for instances whose `instance_index >= desired_count`. Per-instance drain inside multi-thread worker containers (zillow-agent-worker `--concurrency 8`) keeps working; the container-level SIGTERM path is unchanged.
7. Retired on completion: the no-op `uq_scrape_jobs_running`, the FK-wedged `run_maintenance`, per-claim housekeeping, the 5m/30m/2h ladder, the broken retry-hint parser, rendering's state-machine-bypassing reset (→ `expire_job` + `cancel_job`), naive timestamps, hand-mirrored contracts.

### 16.3 Validation gates (shipped as package tests; ALL pass before any production lane moves — pg_native §17, judge 1 mandate)

1. **Race harness:** N concurrent claimers, **0 duplicate claims at ≥2x expected peak** (qdarte's 80 jobs/s bar is the floor); concurrent same-key enqueues → exactly 1 active job; **enqueue(key K) concurrent with settle of the active K-holder → always the old id or a new `created=true` id, never `(NULL, false)`** (§5.2 convergence loop); concurrent settle+reap never double-finishes an attempt (`uq_job_attempts_running` violations = 0; event-log consistency); **fenced-out settle with a stale `attempt_id` (after reclaim) returns `'lost'` AND leaves `job_deps` edges intact — no dependent is promoted, no false `succeeded` event, no followup fires** (the `IF NOT FOUND` fence, §5.5/§5.7); **P1-succeeds-while-P2-fails on a shared dependent → the dependent is cancelled within one tick** (`finalize_dep_stragglers` convergence, §5.9); **diamond-shaped dep enqueue concurrent with parent completes → no deadlock (40P01 = 0)** (ascending-id lock order, §5); cap adherence under 20 concurrent claimers (never exceeds `max_running`); **claim p99 bounded with a deep single-key backlog behind a saturated cap** (§5.3 cost model).
2. **Budget-semantics property tests:** release/snooze never consume budget; expiry backs off and consumes; `max_attempts` failures → `failed/retry_exhausted`; 3 consecutive expiries → `failed/poison` with budget remaining; redrive resets; redrive-vs-dedup raises TQ409; snooze/release with a pending cancel → `cancelled`, never a parked queued row; **every existing Diverse and qdarte enqueue call site's literal values pass the CHECK constraints through the shims (§16.0)**.
3. **Crash tests:** `kill -9` a worker mid-job → recovered within lease + tick; lost complete/fail/release/snooze response → retried settle returns `already_settled`, results intact; crash between followup-bearing complete retries → exactly one chain child; **a followup spec naming an unknown queue → `TQ422` unwinds the whole settle (CAS included), nothing commits, and the worker terminal-fails the parent `invalid_followup`** (§5.5, ADR-007 — no partial success, no dropped children); **`expire_job` racing a live heartbeat → the heartbeat gets `ok=false`, the expire is never silently reverted** (§5.9 synchronous reap).
4. **Bloat soak:** 24h at target rate; `n_dead_tup` stabilizes, claim p99 flat, hot-table row count bounded by janitor. Profiles: a heartbeat-heavy profile validating the unindexed-lease HOT decision (§18.10, HOT ratio via `n_tup_hot_upd/n_tup_upd`); a **retry-storm profile** (high failure rate, deep backoff churn) asserting claim p99 against index size over 24h — B-tree bloat from non-HOT lifecycle writes is what this catches (§13.6); a **growing-checkpoint profile** (3000-unit job, checkpoint per unit through the batching layer) asserting heartbeat p99, HOT ratio, and TOAST size stay bounded (§5.4); plus `queue_stats`/`taskq.metrics()` latency under the deep-backlog state (§12.1).
5. **Schema drift guard:** live schema == installer output (kills the qdarte QC §1.1 model/migration index drift class).
6. **Chaos drills via the runbook:** pause queue mid-run; drain fleet mid-run; `expire_worker_leases` a live box; redrive a dead batch; **skip one janitor run over a month boundary → default-partition alert fires and the next run re-homes** (§13.3) — all from psql.

---

## 17. Failure-mode audit (adversarial walkthrough — kept as the acceptance-test checklist)

| Scenario | Outcome under this design |
|---|---|
| Worker crashes mid-job | Lease expires → reap: attempt `expired` (budget consumed), job requeued with backoff, or `failed` at budget/poison limits. No lost job, bounded retries. |
| Worker network-partitioned, keeps working; job reclaimed and re-run | First worker's heartbeat returns `ok=false` → library aborts the handler (duplicate effects bounded to ≤ one heartbeat interval). If it still finishes, settle returns `lost` — loud log; ledger shows both executions; effects deduped by `job_id` keys where handlers implement them. |
| Complete/fail/release/snooze committed, response lost, worker retries | `already_settled` → success. No 409-as-500, no re-run, no discarded results, no false duplicate-effects alarm. |
| Two clients enqueue the same idempotency key concurrently | Unique index: exactly one row; both callers get the id and a truthful `created`. Holds for N API replicas and direct-SQL admins alike. |
| Two workers race to claim one job | SKIP LOCKED arbitrates; if any writer bypasses the functions anyway, `uq_job_attempts_running` makes the double-claim a hard DB error. |
| Payload segfaults every worker that touches it | 3 consecutive expiries → `failed/poison` even with budget remaining. Fleet protected; redrive available after a fix. |
| Compose restarts drain workers 5 times, then one real failure | Releases consume no budget; the job retains its full retry budget. |
| Worker with no handler for a claimed type | Release with 60s delay + loud log + `release_count` alert. No budget burn, no spin. |
| Parent completes while a child is being enqueued against it | `FOR SHARE` on dep rows serializes against `complete_job`'s unlock — the edge is either created-then-satisfied or never created (dep already `succeeded`). No permanently-blocked child. |
| Operator cancels a running job that never heartbeats again | Reaper terminalizes as `cancelled/canceled_after_expiry` at lease expiry. Never counted as failure. |
| Every ticker process is down | Idle claims micro-reap (bounded 5); leases still recover as long as any worker polls. Cron fires late, not never (bounded per-policy catch-up on ticker return). `taskq_tick_age_seconds` alerts. |
| One tick pass throws (e.g. a poisoned workflow finalize) | Savepoint per pass: the error lands in `control_state.last_error`; reap and the other passes still run. |
| Janitor meets a parent still referenced by edges | Skipped this pass (predicate) + RESTRICT FK backstop; archived after its dependent. No FK violation, no wedged batch, no un-gated dependent. |
| Archive partition rotation missed | Rotate-ahead means one missed run strands nothing; if rows do land in the DEFAULT partition, `taskq_archive_default_rows > 0` alerts and the next janitor run **re-homes them before creating the overlapping partition** (Postgres refuses the CREATE otherwise — §13.3). Archival never blocks; recovery is automatic and loud. |
| A followup spec is invalid at complete (unknown queue, malformed, cap exceeded) | Validation raises `TQ422` and the whole settle unwinds atomically — CAS included, nothing commits. The worker terminal-fails the parent (`fail_job` retryable=false, `invalid_followup`), which lands in dead letters and is redrivable after the code fix. Success-minus-children can never commit; the wedge (a finished job re-executing via lease expiry) is prevented by the terminal-fail escape, not by dropping children (§5.5, ADR-007). |
| Downstream chain-target queue hits `max_depth` while a parent settles | Settle-path followups are exempt from the depth gate (owner-only inserter) — backpressure is producer-intake-only; a deep child queue never fails a parent settle (§8). |
| Two parents of one dependent settle simultaneously, one succeeds and one fails | Promotion and cascade lock children in ascending-id order (no deadlock); if the fail-side cascade SKIP-LOCKED-skips the child, `finalize_dep_stragglers` cancels it within ~one tick. No permanently-blocked dependent (§5.9). |
| Operator `expire_job` races a live heartbeat | Backdate + reap happen in one transaction under the row lock; the racing heartbeat blocks, then returns `ok=false` and the worker aborts the handler. The expire cannot be silently reverted (§5.9). |
| Fenced-out worker calls complete with a stale attempt_id after reclaim | CAS matches zero rows; `IF NOT FOUND` (never a NULLable flag) routes to `already_settled` (its own prior settle) or `'lost'`. No false success, no dependent promotion, no followups (§5.5; gate §16.3.1). |
| Clock skew between workers and DB | Irrelevant: no client timestamp participates in any decision. |
| Long transaction pins the MVCC horizon | `taskq_worker` role carries `statement_timeout = 30s` and `idle_in_transaction_session_timeout = 10s`; analytics roles are the remaining watch item (§15.2). |
| Operator pauses a queue during an incident | Claims stop immediately (at most one in-flight claim slips — eventually consistent, harmless); running jobs finish or reap normally; enqueues keep landing; resume picks up FIFO. |
| Unparseable retry hint / retired job type at settle | Hint: warning event + policy backoff, never silent (DC 11.7). Type: retry math reads the row's stamped policy — no registry KeyError in the settle path (F7). |
| Dead-letter redrive collides with a new active job on the same key | TQ409 with an explanatory message; the operator chooses. Never a raw unique-violation leak. |
| DB failover / restart | All state is in Postgres; in-flight leases either get heartbeated (extend) or expire (reclaim). No app-memory coordination exists to lose. |

---

## 18. Tradeoffs, stated honestly

1. **PL/pgSQL functions are the contract.** Pro: one implementation, fencing not trusted to clients, role grants deny raw DML, any-language clients (including psql). Con: logic changes are migrations; plpgsql debugging is worse than Python; ORMs see opaque calls. Judged worth it — hand-mirrored client logic is precisely what rotted both current systems. Functions are unit-tested against ephemeral Postgres (both repos already do this).
2. **At-least-once, full stop.** We invest in making duplicates rare (abort-on-lost-lease), detectable (typed `lost`, attempts ledger), and harmless (idempotency primitives) — not in pretending exactly-once execution. Industry consensus position (the Go/Postgres job queue, the Elixir/Postgres job framework, the message-queue extension).
3. **Lease expiry consumes budget.** A healthy-but-slow job whose heartbeats persistently fail can die as `retry_exhausted`. Accepted: the alternative (free expiries) is qdarte's infinite crash loop. Mitigations: robust heartbeat loop, generous per-type leases, `expiry_streak` distinguishing poison from bad luck, redrive.
4. **Poison threshold is a constant (3).** A job legitimately slower than its lease three times in a row is quarantined. Mitigation: leases are per-type and generous; heartbeats extend them; quarantine is redrivable. A per-type threshold is a one-column migration if ever needed.
5. **Concurrency caps may briefly under-admit** (try-lock loser skips a round; capped keys serialize admission for milliseconds). The cheap side of the tradeoff: strict no-overshoot with provable deadlock-freedom.
6. **Release loops are possible** (a worker that always releases never kills the job). Deliberate: releases are observable (`release_count`, events, alert); the alternative — releases burning budget — killed healthy jobs in production (DC 11.6). Monitoring, not termination.
7. **Single hot table + archive**, not Solid-Queue table-per-state or hot-table partitioning. At both systems' real volume (jobs/minute, not jobs/second), partial indexes + 48h retention + partition-dropped archive is comfortably sufficient. Documented reopen threshold: sustained ~1k jobs/s or ~100 concurrent workers.
8. **No separate `scheduled` status:** future-dated rows sit in the claim index. A pathological far-future backlog (millions of rows scheduled months out) would bloat it — the escape hatch (add a status + promotion tick) is noted, deliberately not built.
9. **Workflow rollups are eventually consistent** (view + janitor finalization). An ops UI may briefly show a finished run as running. Accepted against qdarte's mega-transaction alternative.
10. **Unindexed `lease_expires_at`:** the reaper scans the running partial index (≤ worker count rows) and filters in the heap; in exchange every heartbeat is a HOT update touching zero indexes. If the running set ever grows to thousands of concurrent jobs, revisit — the bloat soak (§16.3.4) includes a heartbeat-heavy profile to validate this before cutover (judge 3's benchmark caveat honored).
11. **Cron parsing lives in clients** (croniter). A buggy client could mis-compute `next_fire_at`; row lock + idempotency keys bound the damage to wrong timing, never duplicates. Bad grammar surfaces in `schedules.last_error`, not at write time. Chosen over pg_cron/extension dependencies.
12. **Polling latency** (default ~5s, LISTEN/long-poll to shave it) instead of guaranteed sub-second dispatch. Irrelevant for scrapers, renders, and LLM pipelines; buys the removal of a notification-correctness problem class. NOTIFY is a hint on all PG versions (commit-path serialization unfixed even in 19).
13. **Not adopting the mature Python/Postgres task library/the message-queue extension** (PA §4.11 demands justification): the mature Python/Postgres task library lacks the HTTP no-DB-creds fleet mode, fencing-token settlement, workflows/batch runs, cap semantics, and the control-plane surface both projects depend on; the message-queue extension is storage-only. Owning ~1.5k lines of SQL + a small runtime buys exact fit for two systems that already operate 90% of these semantics.
14. **Coarse trust model within the role:** any holder of `taskq_worker` can settle any job given `(id, attempt_id)`. Attempt ids are capability tokens (never in read models); per-worker API keys are available on the facade but a shared fleet key is acceptable single-tenant. Unchanged from today, now documented.

---

## 19. Synthesis decisions — what was grafted, what was rejected, and why

**Revision note (v1.1):** an adversarial review pass produced 35 findings; all substantive ones are folded into the sections above. The load-bearing corrections: `IF NOT FOUND` fencing discipline (a `RETURNING true INTO flag` fence was dead code on the zero-row path); the schedule/operator/workflow raw-DML flows moved behind `SECURITY DEFINER` functions to honor the role model; the claim path's per-candidate correlated cap count replaced with a per-call saturated-key set; ascending-id lock ordering across the dependency graph; savepoint-isolated, depth-exempt settle followups; synchronous-reap `expire_job`; snooze honoring pending cancels; the archive rotation re-written around Postgres's actual default-partition semantics (rotate-ahead + re-home + alert); dead letters retained 14 days; the fillfactor/HOT story corrected to heartbeats-only with scheduled REINDEX; heartbeat/checkpoint split with TOAST budgeting; the enqueue idempotency-loser convergence loop; archived-parent dependency resolution; and the full set of Diverse/qdarte migration dispositions (targeted claim, planner-job schedules, retry shim, terminal-failure reconciler, orchestration/rescue/artifact schedule owners, LLM engine queues, drain bridge, facade-hosted housekeeper).

**Revision note (v1.2, Codex confirmation review 2026-07-09):** the qdarte-first migration now has explicit staging/prod/job-type interlocks, a rollback flag, a first-lane eligibility rule, a required per-job-type side-effect disposition matrix, and a worker-runtime compatibility decision before staging tests. This reconciles the external Codex review into the canonical spec without changing the target taskq state.

**Revision note (v1.6, round-2 review fold-in 2026-07-18):** the second external review's 19 findings were accepted and folded in ([ADR-011](./adr/ADR-011-housekeeper-role-credentials.md) + this pass). Contract-visible changes: (1) **§5.5 `complete_job` reordered** — lock-and-read replay/fence recognition first, followup gates (`TQ501` capability gate + `TQ422` validation, both `USING ERRCODE` — a bare RAISE is `P0001` and invisible to SQLSTATE dispatch) before ANY mutation, graph-order dependent locks, then the mutation block; children insert via the owner-only `taskq._enqueue_followup` (R2-01). (2) **`fail_job` branches to budget-free `cancelled` before failure accounting** — the v1.5 body charged `failure_count` on the pending-cancel path (R2-03); retry hints bounds-checked. (3) **`expire_worker_leases` reaps captured target ids** via `reap_job`, returning `{matched, reaped, skipped}` — the generic-reaper form could spend its limit on other workers' rows (R2-02). (4) **Lock-order proof rebased from uuidv7 time-ordering to the dependency graph** (parents-before-dependents; id order = same-frontier tie-break only; caller-supplied ids not accepted in 0.x; §5 preamble, R2-06). (5) **Public-boundary hardening** — `p_internal` removed from `enqueue` (depth exemption moved to the owner-only inserter), input bounds validated with registered SQLSTATEs, `max_depth` probe off-by-one fixed + positive CHECK, queue names capped at 57 bytes for NOTIFY-channel safety (R2-07). (6) **Tick gains the 0.1 due-gated janitor pass + stats-snapshot pass**, reaper-first; dep/workflow finalizers badged 0.2; installer seeding staged by release (R2-09). (7) **Sync/thread handlers get an honest cancellation contract** — never released while the thread may run (§14, features 11/14, R2-11). (8) Bulk enqueue's one-result-per-input convergence contract (R2-12); archive-move ordering + 0.3 staging of archived-dependency resolution (R2-13); observer read surface = safe views/functions only (§11.5, ADR-011). The remaining round-2 deliverables — the canonical transport protocol (from review draft 03) and the complete 0.1 function bodies/manifest (R2-08) — are tracked as Stage-0 exit work, not doc amendments.

**Revision note (v1.5, ADR fold-in 2026-07-18):** the design-review decisions (D-01..D-12) were accepted as [ADR-001..010](./adr/README.md) and folded in. Contract-visible changes: (1) **§4 role model** replaced by the five capability roles (`taskq_owner` NOLOGIN + producer/runner/observer/operator) with the SECURITY DEFINER hardening contract — pinned `search_path = pg_catalog, taskq, pg_temp`, `REVOKE EXECUTE FROM PUBLIC` at creation, qualified references, privilege-regression tests (ADR-010; the missing search_path/PUBLIC-revoke was a verified escalation surface). (2) **§5.5 followups** rewritten lossless-atomic: validate-then-enqueue with `TQ422` rejects, no savepoints, no truncation — supersedes v1.1's savepoint-per-spec and v1.4's truncation guard; §16.3/§17 entries updated; `followup_failed`/`followups_truncated` events removed; 0.1 contract raises `TQ501` on non-empty followups (ADR-007/009). (3) **`taskq.cancel_running_job`** added as the fenced worker-side cancel (ADR-007). (4) **§13.5 janitor** loses REINDEX — concurrent reindexing cannot run inside a function; it moves to the external `taskq maintenance` CLI; 0.1's janitor trigger is a hardwired daily tick pass until schedules land (ADR-009/010). (5) **Fixed `taskq` schema** — `schema=` removed from the public surface (ADR-002). (6) §14 route sketches demoted to illustrative pending the versioned transport protocol (ADR-005); facade authorization now cites ADR-006's authoritative-projection rule. Release staging (what ships in 0.1 vs 0.2/0.3) is ADR-009's, not this document's, concern — this spec remains the destination design.

**Revision note (v1.4, library-extraction review 2026-07-18):** four surgical fixes from the outlabs-taskq extraction review, none touching settle semantics: (1) `jobs_archive_id_idx` — enqueue's archived-dependency resolution and lineage forensics do bare-`id` lookups the partitioned PK (leading on `finished_at`) could not serve; (2) `control_state.data` jsonb — the §12.1 stats snapshot's storage was referenced but undefined; (3) SQL-side followup cap (truncate + `followups_truncated` event at 20) — the cap was library-only, a "DB enforces invariants" break for direct-SQL settlers; (4) `taskq.meta` comment pinning the relationship between `contract_version` (SQL contract), this document's revision, and the package version (compat matrix in borrowed-feature 12). Companion docs added in the extraction repo: Authorization & Queue Permissions (queue-scoped facade authz — supersedes nothing here; §18.14's coarse DB-role trust model stands), Test & Benchmark Harness (implements §16.3/§17 and discharges the §18.10 benchmark caveat), borrowed-feature 14 (embedded worker / FastAPI lifespan).

**Revision note (v1.3, Diverse Codex confirmation review 2026-07-09):** the Diverse phase now explicitly separates staging implementation from production cutover. Staging may begin after qdarte's accepted final-gate packet, but production remains blocked until qdarte's 24-hour production taskq observation gate and a clean Diverse staging evidence packet. The review also made the API-hosted housekeeper a first-slice requirement, preserved targeted claim-by-jobId as mandatory facade behavior, required a side-effect disposition row before any lane flips, and selected a single court-scraper lane as the first Diverse staging lane.

**Skeleton:** pg_native (judges 2+3). Kept: schema shape, attempt-ledger fencing with `uq_job_attempts_running`, dependency edges with `FOR SHARE` race closure and RESTRICT-guarded archival, workflows + expansion-job pattern, naming table + "pool" ban, EXECUTE-only role model with session timeouts and `duckdb.execution = off`, leaderless cron with backwards-from-now catch-up, partitioned archive, HTTP facade with long-poll, qdarte-first migration ordering, validation gates, capability-detecting installer.

**Grafts applied (source → change):**
- robustness §4.7 → `expiry_streak` poison quarantine in the reaper (§5.8). *(all three judges)*
- robustness §4.5/4.6 → replay acks for **non-terminal** settles via the attempt-ledger status check — retried retryable-fail/release/snooze return `already_settled`, never a spurious `lost` (§5.6, §5.7). *(judge 2)*
- robustness §4.2 → idle-claim micro-reap, bounded to 5, only when a claim finds nothing (§5.3). *(judges 2+3)*
- simplicity §4.2 → `pg_try_advisory_xact_lock` admission + the 5-step no-overshoot/no-deadlock proof, replacing pg_native's blocking lock (§5.3). *(judge 2)*
- simplicity §3 → `lease_expires_at` unindexed; `jobs_running_idx` serves reaper + cap counting; heartbeats HOT (§4, §18.10). *(judge 2; judge 3 benchmark caveat encoded in gate 16.3.4)*
- robustness §4.9 → savepoint-per-pass tick + `control_state` timings + `taskq_tick_age_seconds` alert (§11.4, §12.2). *(judge 2)*
- robustness §4.8 → `expire_worker_leases(worker_id)` as sugar over the one reclaim authority (§5.9). *(all three)*
- robustness §6 + pg_native §6 → per-schedule `catchup_policy (skip|fire_once|fire_all)` + `max_catchup`, computed backwards-from-now (§6). *(judge 1)*
- robustness §6 → janitor seeded as a `taskq.schedules` row at install (§4 seeding). *(judge 1)*
- robustness decision #9 → followup enqueues **inside the settle transaction** with `chain:{job_id}:{step}` keys as the library's default chain mechanism (§5.5, §10). *(judges 1+3)*
- robustness §13 → the enumerated Diverse FK landmine checklist, verbatim, + the ×50 priority shim (§16.2). *(judge 3)*
- robustness §4.1 → `queues.max_depth` advisory admission (`TQ429`), off by default (§5.2). *(judge 3)*
- robustness §14 → the adversarial failure-mode audit as a design section and acceptance-test checklist (§17). *(judge 1)*
- simplicity §10 → the 2am psql runbook as part of the contract + "never UPDATE status by hand" + `REVOKE REFERENCES` making no-external-FK schema-enforced (§11.5, §4). *(judges 2+3)*
- simplicity §3 → `text` + CHECK statuses instead of enums (plain-migration evolution; the dedup predicate enumerates statuses and must be co-edited with any status change) (§4). *(judge 3; the ALTER TYPE documentation alternative rejected as strictly worse)*
- redrive-vs-dedup collision → explicit `TQ409` (§5.9). *(judge 2)*

**Rejected, with reasons:**
- **robustness's `remaining_payload` rewrite on fail/release** — would create two resume mechanisms (payload rewrite AND progress checkpoint) and destroy the original request. Rejected in favor of the single `progress` checkpoint riding the heartbeat and settles; `payload` is immutable. Judge 1's graft asked for both; one mechanism is the honest reconciliation, and judge 3's migration text ("counties-remainder becomes progress checkpoints") confirms the checkpoint is sufficient.
- **simplicity's integer-`attempt` fencing without an attempts table** — the fence would be enforceable only through functions + runbook in codebases whose own gap analysis documents direct-SQL writers as the recurring rot vector. The attempts ledger + `uq_job_attempts_running` makes a double-claim a hard DB error from ANY writer (judge 2's decisive point), and the ledger is also what makes replay acks and budget forensics mechanical.
- **simplicity's no-DAG position (completion chaining only, fan-in via post-commit group check)** — the group-done check has an admitted crash window with manual-only recovery (a silent pipeline stall), and it forces qdarte's cutover into a re-architecture. Edges port qdarte 1:1 and the `FOR SHARE`/`pending_deps` design has no scan and no crash window. Chains still exist — as settle-followups — so simple pipelines never need edges.
- **robustness's at-cap `run_at` nudge** — rewrites a claim-index key on every poll against a saturated key (non-HOT index churn + FIFO perturbation). Replaced by the in-call `v_skip` list, which touches nothing.
- **pg_native's blocking `pg_advisory_xact_lock` admission** — detectable ABBA deadlock potential; replaced by try-lock (above).
- **pg_native's `'lost'` on replayed non-terminal settles** — false duplicate-side-effect alarms; replaced by ledger-checked `already_settled` (above).
- **pg_native's heartbeat requiring `worker_id` match only** — kept, but the raised-exception style from simplicity's heartbeat (which also contained a syntax bug) is rejected in favor of typed `ok=false` throughout.
- **Enums for statuses** — `ALTER TYPE` surgery vs plain CHECK migration; rejected (above).
- **simplicity's delete-only retention (no archive)** — judges 2+3 selected the archive; Diverse's render/letter lineage genuinely resolves against archived jobs, and partition DROP is cheaper than bounded deletes at any volume. Judge 1's "babysitting" critique is answered structurally: rotate-ahead partition creation, a self-healing default re-home, and a default-rows alert mean a missed rotation degrades *loudly* and recovers on the next run (§13.3) — zero human calendar entries. (The v1.0 claim that the DEFAULT partition alone made this self-managing was wrong — Postgres refuses to create a partition overlapping default-partition rows; the re-home step is what makes the claim true.)
- **Mandatory per-worker API keys** — kept optional; a shared fleet key remains acceptable for the current single-tenant posture (judge 1's operational-honesty point), with attribution documented as advisory in that mode.
- **`waiting`/`scheduled` statuses, promoter passes, `partial` status, ps-based reclaim, presence-driven scaling in the queue, fleet reconcilers in the queue, artifact TTL stores, SSE from the queue, tenant-fair scheduling engines** — rejected by all three designs for the same production-evidence reasons (QO 7.1/7.3/7.7, DC §1.1); the rejections carry forward unchanged.

---

## 20. Open questions

1. **Poison threshold tuning:** is 3 consecutive expiries right for very long scrape jobs on flaky proxies? Consider a per-queue `poison_streak` default column if the soak test shows false quarantines (one-column migration; start with the constant).
2. **`_system` queue worker placement:** the janitor job needs at least one worker subscribed to `_system` in each deployment. Housekeeper placement is now decided (§11.4: workers in qdarte, the facade host in Diverse), so the remaining question is narrower: in qdarte, does every worker subscribe to `_system` (simplest) or one designated process; in Diverse, the facade host process claims `_system` jobs itself (it has the DB connection) or one designated worker's queue list includes `_system`. Belt-and-braces cron CLI exists either way. **Recommended resolution (2026-07-18):** borrowed-feature 14's embedded worker makes "facade host claims `_system`" a one-liner (`worker=WorkerOptions(queues=["_system"], concurrency=1)`) — the process that already runs the housekeeper also claims janitor jobs, in every topology. Note the honest scoping either way: install-time seeding makes *reaping* structurally unavoidable (housekeeper + idle micro-reap); archival/REINDEX/rotation still require a `_system` claimer, which this resolves per-deployment rather than structurally.
3. **Neon deployment (qdarte-intake):** PG16/17 degradation is designed but untested against Neon's pooler specifics (LISTEN through the pooler, autosuspend vs 5s ticks — the tick will keep the compute awake; the cron-CLI-only mode may be the right profile for autosuspending databases). Validate during Phase 1 if any taskq consumer lands on Neon.
4. **Event volume:** if `job_events` growth ever dominates janitor time (heartbeat-stats-heavy fleets), the partition recipe for events is one migration away — decide after the first month of production metrics.
5. **Followup cap — resolved (ADR-007, 2026-07-18):** the cap is 20/settle, enforced in SQL as a `TQ422` reject (never truncate); wide fan-outs go through a planner job. Whether 20 fits qdarte's render-pipeline shapes gets confirmed against real data before the 0.2 contract freezes, but the *mechanism* is decided.
6. **Archive query surface:** does the Diverse admin UI need indexed access to archived jobs beyond `(queue, job_type, finished_at)` and idempotency-key lookups? Add BRIN/btree on demand — the archive is cold and cheap to index later.
7. **HTTP long-poll concurrency:** 25s holds per waiting HTTP worker occupy facade connections; size uvicorn workers/timeouts accordingly for the Diverse fleet (~tens of workers — fine, but confirm against the agent-profile fleet's worst case).
8. **Grafana ownership:** the alert rules are specified; decide which existing dashboard stack hosts them per project (Diverse Grafana exists; qdarte's Prometheus is wired but queue-metric-empty today).
