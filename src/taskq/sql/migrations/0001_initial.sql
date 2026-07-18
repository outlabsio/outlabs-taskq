-- ============================================================================
-- outlabs-taskq — migration 0001 (initial): the 0.1 SQL kernel
-- ============================================================================
-- GENERATED FROM: docs/Task Queue 0.1 Function Manifest.md (Tier 0 — canonical
-- for the 0.1 SQL surface), with normative bodies pulled from the Unified
-- Design Spec v1.6 sections it cites (SS4 DDL, SS5 lifecycle bodies, SS11.4
-- tick, SS12.1 views) and the Transport Protocol v1 TQ registry.
--
-- REGENERATION RULE: this file is derived. To change contract-visible SQL,
-- amend the 0.1 Function Manifest (via ADR if Tier-0) and regenerate; never
-- hand-patch drift into this file without updating the manifest first.
-- verify() compares the live catalog against the manifest (ADR-011 SS4).
--
-- EXECUTION CONTRACT (runner responsibilities):
--   * Executed as ONE file in ONE transaction (psql --single-transaction or
--     equivalent), by a superuser or a role with membership in taskq_owner
--     and CREATEROLE.
--   * Idempotent per run: guarded creates / OR REPLACE / ON CONFLICT seeds —
--     safe to re-execute against a database where it already applied.
--   * Assumes PostgreSQL 16+. taskq.uuid7() below ships the pure-SQL RFC-9562
--     fallback; on PostgreSQL 18+ the runner MAY swap in the native body
--     (see the function's comment).
--   * The runner records the ledger row itself (single writer; no self-insert).
--     the file checksum before execution (ADR-004).
--   * Third-party queue projects are never named in this repo (docs/README.md
--     rule 3); comments describe patterns generically.
--
-- ----------------------------------------------------------------------------
-- DERIVATION NOTES — doc conflicts / ambiguities resolved while deriving 0.1
-- (manifest wins per docs/README.md; each item lists the sources involved):
--
--  1. 0.3/0.2 DDL stripped: taskq.jobs_archive (+ its 3 indexes + DEFAULT
--     partition) and taskq.schedules (+ schedules_due_idx) are NOT created
--     (Spec SS4 has them; Manifest SS7 / ADR-009 exclude them from 0.1). No
--     '_system' queue, no seeded janitor schedule row (Spec SS4 seeding note).
--  2. taskq.workflows and taskq.job_deps TABLES are created (Spec SS4: the
--     frozen jobs DDL FKs workflows and the dep model is schema-stable) but
--     every dependency/workflow FUNCTION is absent (Manifest SS7), and the
--     lines calling them are stripped from 0.1 bodies: complete_job loses the
--     dependent-lock pass + dep-unlock CTE + followup loop; fail_job /
--     snooze_job / release_job / cancel_running_job / cancel_job lose their
--     cancel_dependents() calls; tick loses the finalize_dep_stragglers and
--     finalize_workflows passes (Spec SS11.4 marks them 0.2).
--  3. enqueue: non-null p_depends_on / p_workflow_id / p_step_key raise TQ501
--     (Protocol H-12: inactive capability fields are rejected, never ignored).
--     The Manifest's raises row for enqueue (TQ001/TQ422/TQ429/TQ500) predates
--     this gate and does not list TQ501. enqueue_many rejects dependency/
--     workflow spec fields with TQ422 + input index instead (Manifest SS2's
--     explicit wording for bulk). Asymmetry preserved as documented.
--  4. Verb-aware settle replay (Manifest delta b / Protocol H-03) applied to
--     complete_job, fail_job, snooze_job whose Spec SS5.5-5.7 bodies predate
--     H-03 (they acknowledged any-verb replays). 'expired' counts as settled
--     by the reaper verb, so any worker verb against it returns
--     'settle_conflict' (matches the Manifest's release_job body), not 'lost'
--     as Spec SS5.6's comment said.
--  5. cancel_job returns (result, job_status) with result in the Manifest's
--     typed vocabulary cancelled|cancel_requested|already_terminal; Spec SS5.9
--     returned raw statuses ('running', the terminal status). job_status
--     carries the current status the Protocol wants in response data.
--  6. expire_job returns text 'expired_and_reaped' | 'not_running' (Manifest
--     "typed not_running" + Protocol SS3.5); Spec SS5.9 returned boolean.
--     TQ001 raised for an unknown job id (registry category).
--  7. redrive_job: Manifest signature (job_id, actor, reset_progress) with
--     TQ409 raises for not_redrivable / idempotency_collision (reason carried
--     in DETAIL as reason=<token>); Spec SS5.9 had 2 args, returned false for
--     not-failed, and only mapped the unique_violation. TQ001 for unknown id.
--     redrive_failed uses the Manifest signature (queue, limit, actor), not
--     Spec SS5.9's (queue, job_type, limit).
--  8. run_now / reprioritize raise TQ001 for an unknown job id (Manifest rows
--     list only TQ409/TQ422; TQ001 follows the registry + cancel_job's
--     pattern for missing resources).
--  9. Manifest SS5 says pause_queue/resume_queue emit an event, but
--     taskq.job_events.job_id is NOT NULL (Spec SS4) and there is no queue-
--     level event store in 0.1 (Spec SS5.9's expire_worker_leases comment
--     acknowledges the same limit). Queue/worker-level operator verbs
--     (ensure_queue, pause/resume, set_concurrency_limit,
--     request_worker_shutdown) therefore emit no event row; job-targeted
--     verbs do. Reconcile in a future ADR if queue-level audit rows are
--     wanted.
-- 10. reap_expired appears in the Manifest only as a comment under the SS1
--     internal-helper table and in no EXEC row (review doc 04 SS2 lists only
--     reap_job): treated as an internal helper — owner-only, no application-
--     role EXECUTE (tick and claim_jobs reach it running as owner).
-- 11. Views created: queue_stats, dead_jobs, worker_status only (Manifest
--     SS4). Spec SS12.1's rates_15m is not in the Manifest's 0.1 view set and
--     workflow_status is 0.2 — both absent.
-- 12. list_jobs: absent. Review doc 04 said "minimal in 0.1 or gated later";
--     the later, canonical Manifest lists no such function and H-08 defers
--     the list route ("A function not listed here does not exist in 0.1").
-- 13. meta.capabilities seeded as {"active": []} (Manifest SS1
--     has_capability body defines the shape); Spec SS4's example value
--     ({"uuidv7":true,...}) is superseded. contract_version seeds as "0.1"
--     (Spec SS4's "1.0" example is illustrative).
-- 14. snooze_job bounds: Manifest says "reject negative delay"; upper bound
--     taken from Spec SS5 preamble ("retry/snooze/release delays 0-30d") =
--     0..2592000. release_job keeps its Manifest body's explicit 0..86400.
--     release_job's p_delay_seconds DEFAULT 0 follows the Manifest signature;
--     Spec SS5.7 prose had p_requeue_delay_seconds DEFAULT 15 (anti-re-claim
--     spin) — Manifest wins; the library client may pass 15.
-- 15. ALTER ROLE statement_timeout/idle_in_transaction_session_timeout applied
--     to producer/runner/observer/operator (Spec SS4 "every application
--     capability role"); taskq_housekeeper is exempt (ADR-011 postdates that
--     Spec line; a due janitor pass inside tick() may legitimately exceed
--     30s). The Spec's duckdb.execution=off line is a Diverse-substrate-only
--     deployment concern and is omitted from the generic migration. Note
--     these settings bind only when a LOGIN role inherits them via a session;
--     deployments applying them to login roles should mirror them there.
-- 16. Public-boundary validation (Spec SS5 preamble R2-07 + Protocol H-09
--     limits) added to spec-derived bodies: worker ids non-empty <=200 chars;
--     claim batch 1..50 (TQ422 — Spec SS5.3's bare RAISE had no ERRCODE),
--     lease overrides 15..86400, job-type filter <=20 entries; payload <=64KB,
--     headers <=8KB, progress <=2KB, result <=8KB (all TQ422). Manifest-
--     verbatim bodies (release_job, cancel_running_job, worker_heartbeat,
--     redrive_failed, expire_worker_leases) are unchanged.
-- 17. taskq.uuid7() ships the pure-SQL RFC-9562 v7 fallback (48-bit ms
--     timestamp via clock_timestamp() + version/variant bits over
--     gen_random_uuid() randomness). On PG18+ the runner may swap the body
--     for the native generator (Spec SS4).
-- ----------------------------------------------------------------------------

-- ============================================================================
-- 1. Capability roles (ADR-010 + ADR-011): six NOLOGIN roles.
--    CREATE ROLE has no IF NOT EXISTS — DO blocks guard duplicates.
-- ============================================================================

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'taskq_owner') THEN
        CREATE ROLE taskq_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'taskq_producer') THEN
        CREATE ROLE taskq_producer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'taskq_runner') THEN
        CREATE ROLE taskq_runner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'taskq_observer') THEN
        CREATE ROLE taskq_observer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'taskq_operator') THEN
        CREATE ROLE taskq_operator NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'taskq_housekeeper') THEN
        CREATE ROLE taskq_housekeeper NOLOGIN;
    END IF;
END
$do$;

-- Statement-timeout settings per Spec SS4 (see DERIVATION NOTES item 15).
ALTER ROLE taskq_producer SET statement_timeout = '30s';
ALTER ROLE taskq_producer SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE taskq_runner   SET statement_timeout = '30s';
ALTER ROLE taskq_runner   SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE taskq_observer SET statement_timeout = '30s';
ALTER ROLE taskq_observer SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE taskq_operator SET statement_timeout = '30s';
ALTER ROLE taskq_operator SET idle_in_transaction_session_timeout = '10s';

-- ============================================================================
-- 2. Schema
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS taskq;
ALTER SCHEMA taskq OWNER TO taskq_owner;
REVOKE ALL ON SCHEMA taskq FROM PUBLIC;
GRANT USAGE ON SCHEMA taskq
    TO taskq_producer, taskq_runner, taskq_observer, taskq_operator, taskq_housekeeper;

-- No table outside taskq may ever FK into taskq tables. Enforcement is the
-- ownership split: taskq_owner owns the schema and application/migration
-- roles hold no REFERENCES grant on it (REFERENCES is never in PUBLIC's
-- default table privileges). Where host migrations run as superuser/owner,
-- the rail is convention + the migration-time information_schema sweep
-- (Spec SS16.2.5).

-- ============================================================================
-- 3. taskq.uuid7() — id generator (Spec SS4)
--    Pure-SQL RFC-9562 UUIDv7: 48-bit big-endian unix-ms timestamp from
--    clock_timestamp(), version nibble 7, variant 10, remaining 74 bits from
--    gen_random_uuid() randomness. The runner MAY swap this body for the
--    PG18-native generator on server_version_num >= 180000:
--        CREATE OR REPLACE FUNCTION taskq.uuid7() RETURNS uuid
--        LANGUAGE sql VOLATILE PARALLEL SAFE SECURITY DEFINER
--        SET search_path = pg_catalog, taskq, pg_temp
--        AS $u7$ SELECT uuidv7() $u7$;
--    uuidv7 buys index locality and FIFO tie-breaking only — never a
--    correctness premise (Spec SS5 lock-ordering discipline).
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.uuid7() RETURNS uuid
LANGUAGE sql VOLATILE PARALLEL SAFE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT encode(
             set_byte(
                 set_byte(t.b, 6, ((get_byte(t.b, 6) & 15) | 112)),
                 8, ((get_byte(t.b, 8) & 63) | 128)),
             'hex')::uuid
    FROM (SELECT overlay(uuid_send(gen_random_uuid())
                         PLACING substring(int8send((floor(extract(epoch FROM clock_timestamp()) * 1000))::bigint) FROM 3 FOR 6)
                         FROM 1 FOR 6) AS b) t
$$;
ALTER FUNCTION taskq.uuid7() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.uuid7() FROM PUBLIC;
-- Internal helper: owner-only, no application-role EXECUTE (ADR-011 SS4).

-- ============================================================================
-- 4. Composite types — the contract's response shapes (Spec SS4 + Manifest
--    H-02: frozen for 0.1, additive evolution only).
--    CREATE TYPE has no IF NOT EXISTS — DO block guards duplicates.
-- ============================================================================

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_type t
                   JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                   WHERE n.nspname = 'taskq' AND t.typname = 'claimed_job') THEN
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
            workflow_id       uuid,           -- frozen shape; always NULL in 0.1
            step_key          text            -- frozen shape; always NULL in 0.1
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_type t
                   JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                   WHERE n.nspname = 'taskq' AND t.typname = 'settle_result') THEN
        CREATE TYPE taskq.settle_result AS (
            result       text,               -- 'ok' | 'already_settled' | 'settle_conflict'
                                             --   | 'lost' | 'retry_scheduled' | 'dead'
                                             -- (settle_conflict added by Protocol H-03)
            job_status   text,
            scheduled_at timestamptz         -- next run time when retry_scheduled/requeued
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_type t
                   JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                   WHERE n.nspname = 'taskq' AND t.typname = 'claim_batch') THEN
        CREATE TYPE taskq.claim_batch AS (
            state text,                 -- claimed | empty | paused | unknown_queue | unavailable
            jobs  taskq.claimed_job[]   -- non-empty only when state = 'claimed'
        );
    END IF;
END
$do$;

ALTER TYPE taskq.claimed_job   OWNER TO taskq_owner;
ALTER TYPE taskq.settle_result OWNER TO taskq_owner;
ALTER TYPE taskq.claim_batch   OWNER TO taskq_owner;

-- ============================================================================
-- 5. Tables + indexes (Spec SS4 DDL, v1.6 — jobs_archive and schedules are
--    0.3/0.2 and are NOT created here; workflows/job_deps are created for
--    schema stability with no 0.1 functions over them).
-- ============================================================================

-- Queue registry: pause switch, per-queue defaults, optional depth guard.
CREATE TABLE IF NOT EXISTS taskq.queues (
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
    failed_retention_hours int        NOT NULL DEFAULT 336, -- dead letters stay hot LONGER (14d)
    max_depth             int CHECK (max_depth IS NULL OR max_depth > 0),
                                      -- NULL = unlimited; ADVISORY producer backpressure (TQ429)
    notify_enabled        boolean     NOT NULL DEFAULT true,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.queues OWNER TO taskq_owner;

-- Workflows: schema-stable in 0.1 (jobs.workflow_id FKs it); no 0.1 functions.
CREATE TABLE IF NOT EXISTS taskq.workflows (
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
ALTER TABLE taskq.workflows OWNER TO taskq_owner;
CREATE INDEX IF NOT EXISTS workflows_open_idx ON taskq.workflows (created_at) WHERE status = 'running';

-- Per-resource concurrency caps. max_running = 0 is a pause valve.
-- A key with NO row here defaults to max_running = 1 at claim (fail-closed mutex).
CREATE TABLE IF NOT EXISTS taskq.concurrency_limits (
    key         text PRIMARY KEY CHECK (key ~ '^[a-z0-9_.:-]{1,120}$'),
    max_running int  NOT NULL CHECK (max_running >= 0),
    note        text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.concurrency_limits OWNER TO taskq_owner;

-- THE hot table.
CREATE TABLE IF NOT EXISTS taskq.jobs (
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
    lease_expires_at       timestamptz,                          -- DELIBERATELY UNINDEXED (HOT heartbeats)
    worker_id              text,
    current_attempt_id     uuid,
    -- budget (Spec SS3.3)
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
    fillfactor = 85,                        -- HOT-update headroom for HEARTBEATS ONLY
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold    = 500,
    autovacuum_vacuum_cost_delay   = 0,
    autovacuum_analyze_scale_factor = 0.02,
    vacuum_truncate = off,                  -- no ACCESS EXCLUSIVE truncation stalls on the hot table
    toast.autovacuum_vacuum_scale_factor = 0.02,  -- progress jsonb rewrites dead-chunk TOAST
    toast.autovacuum_vacuum_threshold    = 1000
);
ALTER TABLE taskq.jobs OWNER TO taskq_owner;

-- Claim path: predicate matches the claim WHERE exactly; order matches the claim ORDER BY.
CREATE INDEX IF NOT EXISTS jobs_claim_idx ON taskq.jobs (queue, priority, scheduled_at, id)
    WHERE status = 'queued' AND cancel_requested_at IS NULL;

-- Affinity variant (claim preference only, never exclusivity).
CREATE INDEX IF NOT EXISTS jobs_affinity_idx ON taskq.jobs (queue, affinity_key, priority, scheduled_at)
    WHERE status = 'queued' AND cancel_requested_at IS NULL AND affinity_key IS NOT NULL;

-- THE dedup authority. Predicate enumerates ACTIVE statuses (never "NOT IN terminal").
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idem_uq ON taskq.jobs (queue, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running');

-- Running set: small (<= worker count). Serves BOTH the reaper scan AND concurrency
-- counting. lease_expires_at is DELIBERATELY UNINDEXED so every heartbeat is a HOT
-- update touching zero indexes.
CREATE INDEX IF NOT EXISTS jobs_running_idx ON taskq.jobs (concurrency_key)
    WHERE status = 'running';

-- Retention sweep + stats windows.
CREATE INDEX IF NOT EXISTS jobs_finished_idx ON taskq.jobs (finished_at)
    WHERE status IN ('succeeded','failed','cancelled');

-- Workflow membership (schema-stable; unused until 0.2).
CREATE INDEX IF NOT EXISTS jobs_workflow_idx ON taskq.jobs (workflow_id) WHERE workflow_id IS NOT NULL;

-- Deliberately absent: a job_type column in jobs_claim_idx, and any index on
-- lease_expires_at (Spec SS4 notes).

-- Attempts: durable per-claim ledger + the DB-level one-running-attempt guard.
-- The attempt row id IS the fencing token handed to the worker.
CREATE TABLE IF NOT EXISTS taskq.job_attempts (
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
ALTER TABLE taskq.job_attempts OWNER TO taskq_owner;
-- The REAL safety net: a double-claim is a hard DB error even for direct-SQL writers.
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_attempts_running ON taskq.job_attempts (job_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS job_attempts_job_idx ON taskq.job_attempts (job_id, claimed_at);

-- Events: append-only audit. No FK (pruning decoupled from job retention;
-- highest-churn table). Identity PK (no timestamp ties); BRIN for time pruning.
CREATE TABLE IF NOT EXISTS taskq.job_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id      uuid NOT NULL,
    attempt_id  uuid,
    event_type  text NOT NULL CHECK (char_length(event_type) <= 64),
    actor       text,                    -- worker_id | 'operator:<who>' | 'system'
    message     text,                    -- truncated by taskq.emit_event (an oversized
    data        jsonb,                   --   error must never fail a settle)
    created_at  timestamptz NOT NULL DEFAULT now()
) WITH (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold    = 2000
);
ALTER TABLE taskq.job_events OWNER TO taskq_owner;
CREATE INDEX IF NOT EXISTS job_events_job_idx   ON taskq.job_events (job_id, id);
CREATE INDEX IF NOT EXISTS job_events_time_brin ON taskq.job_events USING brin (created_at);

-- Dependency edges: schema-stable in 0.1; no function creates or reads them
-- (enqueue rejects dependencies with TQ501 until the 0.2 capability).
CREATE TABLE IF NOT EXISTS taskq.job_deps (
    job_id      uuid NOT NULL REFERENCES taskq.jobs(id) ON DELETE CASCADE,
    depends_on  uuid NOT NULL REFERENCES taskq.jobs(id),   -- NO ACTION = restrict-on-delete
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, depends_on)
);
ALTER TABLE taskq.job_deps OWNER TO taskq_owner;
CREATE INDEX IF NOT EXISTS job_deps_reverse_idx ON taskq.job_deps (depends_on);

-- Worker presence — observability + drain signalling ONLY. Never an input to
-- reclaim (lease expiry is the only recovery authority).
CREATE TABLE IF NOT EXISTS taskq.workers (
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
ALTER TABLE taskq.workers OWNER TO taskq_owner;
CREATE INDEX IF NOT EXISTS workers_seen_idx ON taskq.workers (last_seen_at);

-- Tick/janitor coordination state (timings + last error per pass; feeds the
-- taskq_tick_age_seconds alert). data: pass-specific state; the tick's
-- queue-stats snapshot lives here under key 'stats_snapshot' (v1.6).
CREATE TABLE IF NOT EXISTS taskq.control_state (
    key              text PRIMARY KEY,
    last_started_at  timestamptz,
    last_finished_at timestamptz,
    last_error       text,
    data             jsonb
);
ALTER TABLE taskq.control_state OWNER TO taskq_owner;

-- Meta: installed contract version + activated capabilities.
-- contract_version tracks the SQL contract, NOT the package version.
CREATE TABLE IF NOT EXISTS taskq.meta (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.meta OWNER TO taskq_owner;

-- Migration ledger (ADR-004): ordered, immutable package migrations are the
-- single source of truth; taskq migrate applies missing ids under an advisory
-- lock; taskq verify compares without changing state.
CREATE TABLE IF NOT EXISTS taskq.schema_migrations (
    id              text PRIMARY KEY,
    package_version text,
    checksum        text,
    applied_at      timestamptz DEFAULT now()
);
ALTER TABLE taskq.schema_migrations OWNER TO taskq_owner;

-- ============================================================================
-- 6. Internal helpers (Manifest SS1) — owner-only: PUBLIC EXECUTE revoked and
--    NO application-role grant. Nested calls work because the outer hardened
--    functions run as taskq_owner (ADR-011 SS4).
-- ============================================================================

-- Backoff: exponential with cap and +/-15% jitter, fixed jitter in 0.1 (Spec SS5.1).
CREATE OR REPLACE FUNCTION taskq.backoff_seconds(
    p_mode text, p_base int, p_cap int, p_failures int
) RETURNS int
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT greatest(1, round(
        least(p_cap,
              CASE p_mode
                  WHEN 'exponential' THEN p_base::numeric * pow(2, least(greatest(p_failures - 1, 0), 16))
                  ELSE p_base::numeric
              END)
        * (0.85 + random() * 0.30)
    ))::int;
$$;
ALTER FUNCTION taskq.backoff_seconds(text, int, int, int) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.backoff_seconds(text, int, int, int) FROM PUBLIC;

-- Append-only audit emitter; message truncation is load-bearing (Spec SS4).
CREATE OR REPLACE FUNCTION taskq.emit_event(
    p_job_id uuid, p_attempt_id uuid, p_event_type text,
    p_actor text, p_message text, p_data jsonb DEFAULT NULL
) RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    INSERT INTO taskq.job_events (job_id, attempt_id, event_type, actor, message, data)
    VALUES (p_job_id, p_attempt_id, p_event_type, p_actor, left(p_message, 500), p_data);
$$;
ALTER FUNCTION taskq.emit_event(uuid, uuid, text, text, text, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.emit_event(uuid, uuid, text, text, text, jsonb) FROM PUBLIC;

-- Capability probe. 0.1 seeds meta.capabilities.active = [] (no followups /
-- dependencies / workflows / schedules / archive).
CREATE OR REPLACE FUNCTION taskq.has_capability(p_name text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT COALESCE((SELECT (value -> 'active') ? p_name FROM taskq.meta
                     WHERE key = 'capabilities'), false)
$$;
ALTER FUNCTION taskq.has_capability(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.has_capability(text) FROM PUBLIC;

-- The one reclaim authority's per-row body (Spec SS5.8's engine, target-aware —
-- R2-02). Returns true iff THIS call reclaimed the row. Re-checks everything
-- under lock, so expire sugar / batch reaper / idle micro-reap share one code
-- path. (Manifest SS1 body, verbatim.)
CREATE OR REPLACE FUNCTION taskq.reap_job(p_job_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_job taskq.jobs%ROWTYPE; v_delay int; v_poison boolean;
BEGIN
    SELECT * INTO v_job FROM taskq.jobs
     WHERE id = p_job_id AND status = 'running' AND lease_expires_at <= now()
     FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN false; END IF;   -- settled/heartbeated/locked meanwhile: decline

    UPDATE taskq.job_attempts SET status = 'expired', outcome = 'lease_expired', finished_at = now()
     WHERE id = v_job.current_attempt_id AND status = 'running';

    v_poison := (v_job.expiry_streak + 1 >= 3);
    IF v_job.cancel_requested_at IS NOT NULL THEN
        UPDATE taskq.jobs SET status='cancelled', outcome='canceled_after_expiry',
               worker_id=NULL, current_attempt_id=NULL, lease_expires_at=NULL,
               finished_at=now(), updated_at=now() WHERE id = p_job_id;
        PERFORM taskq.emit_event(p_job_id, v_job.current_attempt_id, 'cancelled', 'system',
                                 'lease expired with cancel pending', NULL);
    ELSIF v_poison OR v_job.failure_count + 1 >= v_job.max_attempts THEN
        UPDATE taskq.jobs SET status='failed',
               outcome = CASE WHEN v_poison THEN 'poison' ELSE 'retry_exhausted' END,
               failure_count = failure_count + 1, expiry_streak = expiry_streak + 1,
               worker_id=NULL, current_attempt_id=NULL, lease_expires_at=NULL,
               error = COALESCE(v_job.error, 'lease expired'),
               finished_at=now(), updated_at=now() WHERE id = p_job_id;
        PERFORM taskq.emit_event(p_job_id, v_job.current_attempt_id, 'failed', 'system',
                                 CASE WHEN v_poison THEN 'poison quarantine' ELSE 'expiry exhausted budget' END, NULL);
    ELSE
        v_delay := taskq.backoff_seconds(v_job.backoff_mode, v_job.backoff_base_seconds,
                                         v_job.backoff_cap_seconds, v_job.failure_count + 1);
        UPDATE taskq.jobs SET status='queued', scheduled_at = now() + make_interval(secs => v_delay),
               failure_count = failure_count + 1, expiry_streak = expiry_streak + 1,
               worker_id=NULL, current_attempt_id=NULL, lease_expires_at=NULL, updated_at=now()
         WHERE id = p_job_id;
        PERFORM taskq.emit_event(p_job_id, v_job.current_attempt_id, 'lease_expired', 'system',
                                 format('requeued +%ss', v_delay), NULL);
    END IF;
    RETURN true;
END $$;
ALTER FUNCTION taskq.reap_job(uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.reap_job(uuid) FROM PUBLIC;

-- Batch reaper: scans jobs_running_idx, filters lease expiry in the heap,
-- loops taskq.reap_job (Manifest SS1's stated shape). The reaper does not
-- NOTIFY (no stampede). Called by tick() and the idle-claim micro-reap only —
-- no read function in taskq mutates state.
CREATE OR REPLACE FUNCTION taskq.reap_expired(p_limit int DEFAULT 100) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_id uuid; v_n int := 0;
BEGIN
    FOR v_id IN
        SELECT id FROM taskq.jobs
         WHERE status = 'running' AND lease_expires_at <= now()
         ORDER BY lease_expires_at
         LIMIT greatest(COALESCE(p_limit, 0), 0)
    LOOP
        IF taskq.reap_job(v_id) THEN v_n := v_n + 1; END IF;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.reap_expired(int) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.reap_expired(int) FROM PUBLIC;

-- Cancel stragglers: cancel_requested_at set while non-running (cancel raced a
-- requeue; such rows are invisible to the claim predicate). (Manifest SS1 body.)
CREATE OR REPLACE FUNCTION taskq.finalize_cancel_stragglers(p_limit int) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_n int := 0; v_id uuid;
BEGIN
    FOR v_id IN SELECT id FROM taskq.jobs
                 WHERE cancel_requested_at IS NOT NULL AND status IN ('queued','blocked')
                 ORDER BY id LIMIT least(p_limit, 200) FOR UPDATE SKIP LOCKED LOOP
        UPDATE taskq.jobs SET status='cancelled', outcome='canceled',
               finished_at=now(), updated_at=now() WHERE id = v_id;
        PERFORM taskq.emit_event(v_id, NULL, 'cancelled', 'system', 'cancel straggler sweep', NULL);
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.finalize_cancel_stragglers(int) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.finalize_cancel_stragglers(int) FROM PUBLIC;

-- 0.1 janitor due marker (R2-09/ADR-009): atomic claim; next_due advances on
-- claim; a failing janitor records last_error and is due again next tick.
CREATE OR REPLACE FUNCTION taskq.claim_janitor_due() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_due timestamptz;
BEGIN
    INSERT INTO taskq.control_state (key, data) VALUES ('janitor_daily', jsonb_build_object('next_due', now()))
        ON CONFLICT (key) DO NOTHING;
    SELECT (data->>'next_due')::timestamptz INTO v_due
      FROM taskq.control_state WHERE key = 'janitor_daily' FOR UPDATE;
    IF v_due IS NULL OR v_due <= now() THEN
        UPDATE taskq.control_state
           SET data = jsonb_set(COALESCE(data,'{}'), '{next_due}',
                                to_jsonb(now() + interval '24 hours')),
               last_started_at = now()
         WHERE key = 'janitor_daily';
        RETURN true;
    END IF;
    RETURN false;
END $$;
ALTER FUNCTION taskq.claim_janitor_due() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_janitor_due() FROM PUBLIC;

-- Stats snapshot (Spec SS12.1): per-queue counts riding the partial indexes;
-- never a full aggregate. Written to control_state key 'stats_snapshot'.
CREATE OR REPLACE FUNCTION taskq.refresh_stats_snapshot() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v jsonb;
BEGIN
    SELECT jsonb_object_agg(q.name, jsonb_build_object(
        'ready',   (SELECT count(*) FROM taskq.jobs j WHERE j.queue=q.name AND j.status='queued'
                     AND j.cancel_requested_at IS NULL AND j.scheduled_at <= now()),
        'scheduled',(SELECT count(*) FROM taskq.jobs j WHERE j.queue=q.name AND j.status='queued'
                     AND j.cancel_requested_at IS NULL AND j.scheduled_at > now()),
        'running', (SELECT count(*) FROM taskq.jobs j WHERE j.queue=q.name AND j.status='running'),
        'oldest_ready_seconds', COALESCE((SELECT extract(epoch FROM now()-min(j.scheduled_at))::bigint
                     FROM taskq.jobs j WHERE j.queue=q.name AND j.status='queued'
                     AND j.cancel_requested_at IS NULL AND j.scheduled_at <= now()), 0),
        'paused',  q.paused_at IS NOT NULL))
      INTO v FROM taskq.queues q;
    INSERT INTO taskq.control_state (key, data, last_finished_at)
    VALUES ('stats_snapshot', jsonb_build_object('as_of', now(), 'queues', COALESCE(v,'{}'::jsonb)), now())
    ON CONFLICT (key) DO UPDATE
      SET data = EXCLUDED.data, last_finished_at = now();
END $$;
ALTER FUNCTION taskq.refresh_stats_snapshot() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.refresh_stats_snapshot() FROM PUBLIC;

-- ============================================================================
-- 7. Producer functions (Manifest SS2) — EXEC taskq_producer
-- ============================================================================

-- Enqueue — index-enforced idempotency, transactional (Spec SS5.2 v1.6,
-- 0.1-stripped: dependency/workflow parameters are capability-gated TQ501;
-- no dep locking, no archive consult, status is always 'queued').
-- The queue is the outbox: call inside the caller's domain transaction.
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
    p_depends_on       uuid[]       DEFAULT NULL,   -- 0.2 capability; TQ501 if supplied
    p_workflow_id      uuid         DEFAULT NULL,   -- 0.2 capability; TQ501 if supplied
    p_step_key         text         DEFAULT NULL,   -- 0.2 capability; TQ501 if supplied
    p_parent_job_id    uuid         DEFAULT NULL,   -- lineage only; not capability-gated
    p_headers          jsonb        DEFAULT NULL
    -- v1.6 (R2-07): no p_internal — the settle-path depth exemption lives in
    -- the owner-only 0.2 followup inserter, which producers cannot execute.
) RETURNS TABLE (job_id uuid, created boolean)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q           taskq.queues%ROWTYPE;
    v_id        uuid;
    v_existing  uuid;
    v_created   boolean := false;
    v_try       int;
    v_scheduled timestamptz := COALESCE(p_scheduled_at, now());
    v_mode      text;
    v_base      int;
    v_cap       int;
BEGIN
    -- 0.1 capability gates (Protocol H-12: inactive fields are rejected with
    -- TQ501, never silently ignored).
    IF p_depends_on IS NOT NULL AND cardinality(p_depends_on) > 0 THEN
        RAISE EXCEPTION 'dependencies are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;
    IF p_workflow_id IS NOT NULL OR p_step_key IS NOT NULL THEN
        RAISE EXCEPTION 'workflows are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;

    -- Public-boundary validation (R2-07 + H-09) — TQ422, never raw cast/check errors.
    IF COALESCE(p_job_type, '') = '' OR char_length(p_job_type) > 120 THEN
        RAISE EXCEPTION 'job_type is required (<= 120 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_priority IS NOT NULL AND (p_priority < 0 OR p_priority > 1000) THEN
        RAISE EXCEPTION 'priority must be 0..1000, got %', p_priority USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND (p_lease_seconds < 15 OR p_lease_seconds > 86400) THEN
        RAISE EXCEPTION 'lease_seconds must be 15..86400, got %', p_lease_seconds USING ERRCODE = 'TQ422';
    END IF;
    IF p_max_attempts IS NOT NULL AND (p_max_attempts < 1 OR p_max_attempts > 100) THEN
        RAISE EXCEPTION 'max_attempts must be 1..100, got %', p_max_attempts USING ERRCODE = 'TQ422';
    END IF;
    IF p_backoff_mode IS NOT NULL AND p_backoff_mode NOT IN ('fixed','exponential') THEN
        RAISE EXCEPTION 'backoff_mode must be fixed|exponential, got %', p_backoff_mode USING ERRCODE = 'TQ422';
    END IF;
    IF p_backoff_base IS NOT NULL AND (p_backoff_base < 1 OR p_backoff_base > 86400) THEN
        RAISE EXCEPTION 'backoff_base must be 1..86400, got %', p_backoff_base USING ERRCODE = 'TQ422';
    END IF;
    IF p_backoff_cap IS NOT NULL AND p_backoff_cap < 1 THEN
        RAISE EXCEPTION 'backoff_cap must be >= 1, got %', p_backoff_cap USING ERRCODE = 'TQ422';
    END IF;
    IF p_idempotency_key IS NOT NULL
       AND (p_idempotency_key = '' OR char_length(p_idempotency_key) > 255) THEN
        RAISE EXCEPTION 'idempotency_key must be 1..255 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_concurrency_key IS NOT NULL
       AND (p_concurrency_key = '' OR char_length(p_concurrency_key) > 120) THEN
        RAISE EXCEPTION 'concurrency_key must be 1..120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_affinity_key IS NOT NULL
       AND (p_affinity_key = '' OR char_length(p_affinity_key) > 120) THEN
        RAISE EXCEPTION 'affinity_key must be 1..120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_payload IS NOT NULL AND jsonb_typeof(p_payload) <> 'object' THEN
        RAISE EXCEPTION 'payload must be a json object' USING ERRCODE = 'TQ422';
    END IF;
    IF p_payload IS NOT NULL AND octet_length(p_payload::text) > 65536 THEN
        RAISE EXCEPTION 'payload exceeds the 64KB limit' USING ERRCODE = 'TQ422';
    END IF;
    IF p_headers IS NOT NULL AND jsonb_typeof(p_headers) <> 'object' THEN
        RAISE EXCEPTION 'headers must be a json object' USING ERRCODE = 'TQ422';
    END IF;
    IF p_headers IS NOT NULL AND octet_length(p_headers::text) > 8192 THEN
        RAISE EXCEPTION 'headers exceed the 8KB limit' USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;

    v_mode := COALESCE(p_backoff_mode, q.default_backoff_mode);
    v_base := COALESCE(p_backoff_base, q.default_backoff_base);
    v_cap  := COALESCE(p_backoff_cap,  q.default_backoff_cap);
    IF v_cap < v_base THEN
        RAISE EXCEPTION 'backoff_cap % is below backoff_base %', v_cap, v_base USING ERRCODE = 'TQ422';
    END IF;

    -- Advisory producer backpressure, off by default. Bounded EXISTENCE probe,
    -- never count(*): walks at most max_depth index entries and stops.
    -- v1.6 (R2-07): probe at max_depth - 1 — the v1.5 OFFSET max_depth accepted
    -- row N+1 before rejecting. Still explicitly ADVISORY under concurrency/bulk.
    IF q.max_depth IS NOT NULL AND EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE queue = p_queue AND status IN ('blocked','queued')
        OFFSET greatest(q.max_depth - 1, 0) LIMIT 1) THEN
        RAISE EXCEPTION 'queue % at max_depth %', p_queue, q.max_depth USING ERRCODE = 'TQ429';
    END IF;

    -- Insert with a bounded convergence loop. The loser path re-selects the
    -- active holder in a LATER statement snapshot; if the holder settled in
    -- that gap the key is free again and the honest answer is to RETRY THE
    -- INSERT — never (NULL, false).
    FOR v_try IN 1..3 LOOP
        v_id := taskq.uuid7();
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, concurrency_key, affinity_key,
            parent_job_id, pending_deps,
            scheduled_at, lease_seconds, max_attempts,
            backoff_mode, backoff_base_seconds, backoff_cap_seconds
        ) VALUES (
            v_id, p_queue, p_job_type, 'queued',
            COALESCE(p_priority, q.default_priority),
            COALESCE(p_payload, '{}'::jsonb), p_headers,
            p_idempotency_key, p_concurrency_key, p_affinity_key,
            p_parent_job_id, 0,
            v_scheduled,
            COALESCE(p_lease_seconds, q.default_lease_seconds),
            COALESCE(p_max_attempts, q.default_max_attempts),
            v_mode, v_base, v_cap
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING;

        IF FOUND THEN v_created := true; EXIT; END IF;

        -- Loser of the idempotency race / pre-existing active job: return it.
        -- Race-free BY THE INDEX, not by this select. Never an exception, so
        -- transactional callers need no rollback handling.
        SELECT j.id INTO v_existing FROM taskq.jobs j
        WHERE j.queue = p_queue AND j.idempotency_key = p_idempotency_key
          AND j.status IN ('blocked','queued','running')
        ORDER BY j.created_at DESC LIMIT 1;
        IF v_existing IS NOT NULL THEN
            RETURN QUERY SELECT v_existing, false;       -- created is ALWAYS truthfully reported
            RETURN;
        END IF;
        -- Holder settled between the two statements: loop retries the INSERT.
        -- PG19: this loop collapses into ON CONFLICT DO SELECT (Spec SS15).
    END LOOP;

    IF NOT v_created THEN
        RAISE EXCEPTION 'taskq: idempotency insert did not converge for key % on queue %',
            p_idempotency_key, p_queue USING ERRCODE = 'TQ500';   -- 3 flaps in one call: pathological
    END IF;

    PERFORM taskq.emit_event(v_id, NULL, 'enqueued', 'system', NULL,
        jsonb_build_object('status', 'queued', 'scheduled_at', v_scheduled));

    IF v_scheduled <= now() AND q.notify_enabled THEN
        PERFORM pg_notify('taskq_' || p_queue, '');      -- payload-free; commit-gated; the server
    END IF;                                              -- dedups identical NOTIFYs per txn

    RETURN QUERY SELECT v_id, true;
END $$;
ALTER FUNCTION taskq.enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, int, text, int, int, uuid[], uuid, text, uuid, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, int, text, int, int, uuid[], uuid, text, uuid, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, int, text, int, int, uuid[], uuid, text, uuid, jsonb) TO taskq_producer;

-- Bulk enqueue (Manifest SS2 body-contract, implemented in full):
-- one transaction, one queue, <=1000 specs, no deps, one depth probe, one
-- NOTIFY, one ordered typed result per input (R2-12; Protocol H-05).
CREATE OR REPLACE FUNCTION taskq.enqueue_many(p_queue text, p_jobs jsonb)
RETURNS TABLE (input_index int, job_id uuid, outcome text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q             taskq.queues%ROWTYPE;
    v_n           int;
    v_i           int;
    v_try         int;
    v_spec        jsonb;
    v_field       text;
    v_key         text;
    v_existing    uuid;
    v_ids         uuid[] := '{}';   -- pre-assigned id per input ordinal
    v_out         uuid[];           -- resolved job id per input ordinal
    v_outcome     text[];           -- 'created' | 'existed' per input ordinal
    v_created_set uuid[];           -- ids created by the pass-1 multi-row insert
    v_all_created uuid[];           -- pass-1 + pass-2 creations (events + NOTIFY)
BEGIN
    IF p_jobs IS NULL OR jsonb_typeof(p_jobs) <> 'array'
       OR jsonb_array_length(p_jobs) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'p_jobs must be an array of 1..1000 specs' USING ERRCODE = 'TQ422';
    END IF;
    v_n := jsonb_array_length(p_jobs);

    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;

    -- Per-spec validation — ALL before any insert, so rejection means zero
    -- rows. TQ422 always carries the input index. Dependency/workflow fields
    -- are invalid items in 0.1 bulk (Manifest SS2; single enqueue gates the
    -- same fields with TQ501 — see DERIVATION NOTES item 3).
    FOR v_i IN 1..v_n LOOP
        v_spec := p_jobs -> (v_i - 1);
        IF jsonb_typeof(v_spec) <> 'object' THEN
            RAISE EXCEPTION 'taskq: spec % must be a json object', v_i USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            FOR v_field IN SELECT jsonb_object_keys(v_spec) LOOP
                IF v_field IN ('depends_on','workflow_id','step_key') THEN
                    RAISE EXCEPTION 'dependency/workflow field "%" is not available in 0.1', v_field;
                ELSIF v_field NOT IN ('job_type','payload','headers','priority','scheduled_at',
                                      'idempotency_key','concurrency_key','affinity_key',
                                      'max_attempts','lease_seconds','backoff_mode','backoff_base',
                                      'backoff_cap','parent_job_id') THEN
                    RAISE EXCEPTION 'unknown field "%"', v_field;
                END IF;
            END LOOP;
            IF COALESCE(v_spec->>'job_type', '') = '' OR char_length(v_spec->>'job_type') > 120 THEN
                RAISE EXCEPTION 'job_type is required (<= 120 chars)';
            END IF;
            IF v_spec ? 'payload' AND jsonb_typeof(v_spec->'payload') <> 'object' THEN
                RAISE EXCEPTION 'payload must be a json object';
            END IF;
            IF octet_length((v_spec->'payload')::text) > 65536 THEN
                RAISE EXCEPTION 'payload exceeds the 64KB limit';
            END IF;
            IF v_spec ? 'headers' AND jsonb_typeof(v_spec->'headers') <> 'object' THEN
                RAISE EXCEPTION 'headers must be a json object';
            END IF;
            IF octet_length((v_spec->'headers')::text) > 8192 THEN
                RAISE EXCEPTION 'headers exceed the 8KB limit';
            END IF;
            IF (v_spec->>'priority')::int NOT BETWEEN 0 AND 1000 THEN
                RAISE EXCEPTION 'priority must be 0..1000';
            END IF;
            IF (v_spec->>'lease_seconds')::int NOT BETWEEN 15 AND 86400 THEN
                RAISE EXCEPTION 'lease_seconds must be 15..86400';
            END IF;
            IF (v_spec->>'max_attempts')::int NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'max_attempts must be 1..100';
            END IF;
            IF v_spec ? 'backoff_mode' AND (v_spec->>'backoff_mode') NOT IN ('fixed','exponential') THEN
                RAISE EXCEPTION 'backoff_mode must be fixed|exponential';
            END IF;
            IF (v_spec->>'backoff_base')::int NOT BETWEEN 1 AND 86400 THEN
                RAISE EXCEPTION 'backoff_base must be 1..86400';
            END IF;
            IF COALESCE((v_spec->>'backoff_cap')::int,  q.default_backoff_cap)
             < COALESCE((v_spec->>'backoff_base')::int, q.default_backoff_base) THEN
                RAISE EXCEPTION 'backoff_cap below backoff_base';
            END IF;
            IF v_spec ? 'idempotency_key'
               AND (COALESCE(v_spec->>'idempotency_key','') = ''
                    OR char_length(v_spec->>'idempotency_key') > 255) THEN
                RAISE EXCEPTION 'idempotency_key must be 1..255 chars';
            END IF;
            IF char_length(v_spec->>'concurrency_key') > 120 THEN
                RAISE EXCEPTION 'concurrency_key exceeds 120 chars';
            END IF;
            IF char_length(v_spec->>'affinity_key') > 120 THEN
                RAISE EXCEPTION 'affinity_key exceeds 120 chars';
            END IF;
            -- Guarded casts: bad values surface as TQ422 below, never native 22P02.
            PERFORM (v_spec->>'scheduled_at')::timestamptz;
            PERFORM (v_spec->>'parent_job_id')::uuid;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'taskq: spec % invalid: %', v_i, SQLERRM USING ERRCODE = 'TQ422';
        END;
        v_ids := v_ids || taskq.uuid7();
    END LOOP;

    -- One depth probe per call (never per spec) — advisory, as single enqueue.
    IF q.max_depth IS NOT NULL AND EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE queue = p_queue AND status IN ('blocked','queued')
        OFFSET greatest(q.max_depth - 1, 0) LIMIT 1) THEN
        RAISE EXCEPTION 'queue % at max_depth %', p_queue, q.max_depth USING ERRCODE = 'TQ429';
    END IF;

    -- Pass 1: ONE multi-row INSERT ... ON CONFLICT DO NOTHING, input order
    -- preserved (WITH ORDINALITY + ORDER BY): intra-request duplicate keys
    -- resolve first-occurrence-creates; later duplicates conflict and report
    -- existed in pass 2. Pre-assigned ids map returned rows to ordinals.
    WITH specs AS (
        SELECT a.ord::int AS i, a.spec
        FROM jsonb_array_elements(p_jobs) WITH ORDINALITY AS a(spec, ord)
    ), ins AS (
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, concurrency_key, affinity_key, parent_job_id, pending_deps,
            scheduled_at, lease_seconds, max_attempts,
            backoff_mode, backoff_base_seconds, backoff_cap_seconds)
        SELECT v_ids[s.i], p_queue, s.spec->>'job_type', 'queued',
               COALESCE((s.spec->>'priority')::smallint, q.default_priority),
               COALESCE(s.spec->'payload', '{}'::jsonb),
               s.spec->'headers',
               s.spec->>'idempotency_key',
               s.spec->>'concurrency_key',
               s.spec->>'affinity_key',
               (s.spec->>'parent_job_id')::uuid,
               0,
               COALESCE((s.spec->>'scheduled_at')::timestamptz, now()),
               COALESCE((s.spec->>'lease_seconds')::int, q.default_lease_seconds),
               COALESCE((s.spec->>'max_attempts')::smallint, q.default_max_attempts),
               COALESCE(s.spec->>'backoff_mode', q.default_backoff_mode),
               COALESCE((s.spec->>'backoff_base')::int, q.default_backoff_base),
               COALESCE((s.spec->>'backoff_cap')::int,  q.default_backoff_cap)
        FROM specs s
        ORDER BY s.i
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING
        RETURNING id
    )
    SELECT COALESCE(array_agg(id), '{}') INTO v_created_set FROM ins;

    v_all_created := v_created_set;
    v_out         := array_fill(NULL::uuid, ARRAY[v_n]);
    v_outcome     := array_fill(NULL::text, ARRAY[v_n]);

    -- Pass 2: resolve conflicted ordinals (necessarily keyed) in LATER
    -- statement snapshots; a holder that settled mid-call is retried through
    -- the same converge-or-TQ500 rule as single enqueue (bounded at 3 rounds;
    -- TQ500 rolls the WHOLE batch back — no partial batches).
    FOR v_i IN 1..v_n LOOP
        IF v_ids[v_i] = ANY (v_created_set) THEN
            v_out[v_i] := v_ids[v_i];
            v_outcome[v_i] := 'created';
            CONTINUE;
        END IF;
        v_spec := p_jobs -> (v_i - 1);
        v_key  := v_spec->>'idempotency_key';
        FOR v_try IN 1..3 LOOP
            v_existing := NULL;
            SELECT j.id INTO v_existing FROM taskq.jobs j
            WHERE j.queue = p_queue AND j.idempotency_key = v_key
              AND j.status IN ('blocked','queued','running')
            ORDER BY j.created_at DESC LIMIT 1;
            IF v_existing IS NOT NULL THEN
                v_out[v_i] := v_existing;
                v_outcome[v_i] := 'existed';
                EXIT;
            END IF;
            -- Holder settled in the gap: retry THIS spec's insert.
            INSERT INTO taskq.jobs (
                id, queue, job_type, status, priority, payload, headers,
                idempotency_key, concurrency_key, affinity_key, parent_job_id, pending_deps,
                scheduled_at, lease_seconds, max_attempts,
                backoff_mode, backoff_base_seconds, backoff_cap_seconds)
            SELECT v_ids[v_i], p_queue, v_spec->>'job_type', 'queued',
                   COALESCE((v_spec->>'priority')::smallint, q.default_priority),
                   COALESCE(v_spec->'payload', '{}'::jsonb),
                   v_spec->'headers',
                   v_spec->>'idempotency_key',
                   v_spec->>'concurrency_key',
                   v_spec->>'affinity_key',
                   (v_spec->>'parent_job_id')::uuid,
                   0,
                   COALESCE((v_spec->>'scheduled_at')::timestamptz, now()),
                   COALESCE((v_spec->>'lease_seconds')::int, q.default_lease_seconds),
                   COALESCE((v_spec->>'max_attempts')::smallint, q.default_max_attempts),
                   COALESCE(v_spec->>'backoff_mode', q.default_backoff_mode),
                   COALESCE((v_spec->>'backoff_base')::int, q.default_backoff_base),
                   COALESCE((v_spec->>'backoff_cap')::int,  q.default_backoff_cap)
            ON CONFLICT (queue, idempotency_key)
                WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
                DO NOTHING;
            IF FOUND THEN
                v_out[v_i] := v_ids[v_i];
                v_outcome[v_i] := 'created';
                v_all_created := v_all_created || v_ids[v_i];
                EXIT;
            END IF;
        END LOOP;
        IF v_out[v_i] IS NULL THEN
            RAISE EXCEPTION 'taskq: idempotency insert did not converge for key % on queue % (spec %)',
                v_key, p_queue, v_i USING ERRCODE = 'TQ500';
        END IF;
    END LOOP;

    -- Enqueued events for every created row (parity with single enqueue).
    IF cardinality(v_all_created) > 0 THEN
        INSERT INTO taskq.job_events (job_id, attempt_id, event_type, actor, message, data)
        SELECT j.id, NULL, 'enqueued', 'system', NULL,
               jsonb_build_object('status', j.status, 'scheduled_at', j.scheduled_at)
          FROM taskq.jobs j WHERE j.id = ANY (v_all_created);
    END IF;

    -- Pass 3: ONE NOTIFY for the queue when any created row is immediately runnable.
    IF q.notify_enabled AND EXISTS (
        SELECT 1 FROM taskq.jobs j
         WHERE j.id = ANY (v_all_created) AND j.status = 'queued' AND j.scheduled_at <= now()) THEN
        PERFORM pg_notify('taskq_' || p_queue, '');
    END IF;

    -- Ordered typed result: one row per input, in input order.
    RETURN QUERY
    SELECT g.i, v_out[g.i], v_outcome[g.i]
    FROM generate_series(1, v_n) AS g(i)
    ORDER BY g.i;
END $$;
ALTER FUNCTION taskq.enqueue_many(text, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.enqueue_many(text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.enqueue_many(text, jsonb) TO taskq_producer;

-- ============================================================================
-- 8. Runner functions (Manifest SS3) — EXEC taskq_runner
-- ============================================================================

-- Claim — typed batch result (Protocol H-01), cap-aware, deadlock-free, idle
-- micro-reap (Spec SS5.3 admission body wrapped in the Manifest's
-- taskq.claim_batch shape: resolve queue first, targeted miss -> unavailable).
CREATE OR REPLACE FUNCTION taskq.claim_jobs(
    p_queue         text,
    p_worker_id     text,
    p_batch         int    DEFAULT 1,
    p_job_types     text[] DEFAULT NULL,
    p_lease_seconds int    DEFAULT NULL,
    p_affinity_key  text   DEFAULT NULL,
    p_job_id        uuid   DEFAULT NULL    -- targeted claim: exactly this job or nothing
) RETURNS taskq.claim_batch
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
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
    v_paused_at  timestamptz;
    v_jobs       taskq.claimed_job[] := '{}';
BEGIN
    -- Public-boundary validation (R2-07 + H-09) — TQ422.
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF v_batch < 1 OR v_batch > 50 THEN
        RAISE EXCEPTION 'claim batch must be 1..50, got %', v_batch USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND (p_lease_seconds < 15 OR p_lease_seconds > 86400) THEN
        RAISE EXCEPTION 'lease override must be 15..86400 seconds, got %', p_lease_seconds
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_types IS NOT NULL
       AND (cardinality(p_job_types) < 1 OR cardinality(p_job_types) > 20) THEN
        RAISE EXCEPTION 'job type filter must have 1..20 entries' USING ERRCODE = 'TQ422';
    END IF;
    IF p_affinity_key IS NOT NULL AND char_length(p_affinity_key) > 120 THEN
        RAISE EXCEPTION 'affinity_key exceeds 120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;   -- targeted claim is singular by definition

    -- H-01: typed queue resolution — never inferred from an empty set.
    SELECT q.paused_at INTO v_paused_at FROM taskq.queues q WHERE q.name = p_queue;
    IF NOT FOUND THEN
        RETURN ROW('unknown_queue', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    IF v_paused_at IS NOT NULL THEN
        RETURN ROW('paused', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;

    -- Saturated-key set, computed ONCE per call — never a per-candidate
    -- correlated count. The running set is small (<= worker count): one
    -- grouped pass over jobs_running_idx; candidates under a saturated key
    -- are excluded by a cheap array test.
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
              AND (p_job_id IS NULL OR j.id = p_job_id)  -- targeted claim
              AND (p_job_types IS NULL OR j.job_type = ANY (p_job_types))
              AND NOT (j.id = ANY (v_skip))
              -- cheap racy pre-filter: array test against the per-call saturated set
              AND (j.concurrency_key IS NULL OR NOT (j.concurrency_key = ANY (v_saturated)))
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
        END IF;

        EXIT WHEN v_job.id IS NULL;

        -- Strict admission: serialize same-key admission with a TRY advisory
        -- xact lock — the loser SKIPS this key this round, never waits, so no
        -- lock-wait ordering exists (provably deadlock-free). Unknown key =
        -- mutex(1), fail-closed. max_running = 0 = paused resource.
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
        v_jobs := v_jobs || ROW(
            v_job.id, v_job.queue, v_job.job_type, v_job.priority, v_job.payload,
            v_job.headers, v_job.progress, v_attempt_id, (v_job.attempt_count + 1)::int,
            v_job.failure_count, v_job.max_attempts,
            now() + make_interval(secs => v_lease),
            v_job.workflow_id, v_job.step_key)::taskq.claimed_job;
    END LOOP;

    -- Idle-claim micro-reap: ONLY when nothing was claimable, a tiny bounded
    -- reap so lease recovery never depends solely on the tick being alive.
    IF v_claimed = 0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            -- Targeted miss: missing / not-ready / already-owned for this
            -- queue — no existence detail (Protocol SS3.3).
            RETURN ROW('unavailable', '{}'::taskq.claimed_job[])::taskq.claim_batch;
        END IF;
        RETURN ROW('empty', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed', v_jobs)::taskq.claim_batch;
END $$;
ALTER FUNCTION taskq.claim_jobs(text, text, int, text[], int, text, uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text, text, int, text[], int, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text, text, int, text[], int, text, uuid) TO taskq_runner;

-- Heartbeat — lease extension, cancel channel, checkpoint carrier, typed loss
-- (Spec SS5.4 + Manifest lease-override bounds). A pure lease bump is a HOT
-- update: no indexed column is touched.
CREATE OR REPLACE FUNCTION taskq.heartbeat(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_lease_seconds int DEFAULT NULL, p_progress jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL
) RETURNS TABLE (ok boolean, cancel_requested boolean, lease_expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_row taskq.jobs%ROWTYPE;
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND (p_lease_seconds < 15 OR p_lease_seconds > 86400) THEN
        RAISE EXCEPTION 'lease override must be 15..86400 seconds, got %', p_lease_seconds
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_progress IS NOT NULL AND octet_length(p_progress::text) > 2048 THEN
        RAISE EXCEPTION 'progress exceeds the 2KB limit — keep it a cursor, not a result set'
            USING ERRCODE = 'TQ422';
    END IF;

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
ALTER FUNCTION taskq.heartbeat(uuid, uuid, text, int, jsonb, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.heartbeat(uuid, uuid, text, int, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.heartbeat(uuid, uuid, text, int, jsonb, jsonb) TO taskq_runner;

-- Complete — CAS-fenced, verb-aware replay (H-03), lock-and-read-first
-- (Spec SS5.5 v1.6, 0.1-stripped: TQ501 capability gate for non-empty
-- followups; no dependent-lock pass, no dep-unlock cascade, no followup loop).
CREATE OR REPLACE FUNCTION taskq.complete_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_result jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL,
    p_followups jsonb DEFAULT NULL      -- 0.2 capability; non-empty array -> TQ501 in 0.1
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_job record;
    v_att text;
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_result IS NOT NULL AND octet_length(p_result::text) > 8192 THEN
        RAISE EXCEPTION 'result exceeds the 8KB limit — bulky output goes in app tables'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_typeof(p_followups) <> 'array' THEN
        RAISE EXCEPTION 'p_followups must be a jsonb array, got %', jsonb_typeof(p_followups)
            USING ERRCODE = 'TQ422';
    END IF;

    -- LOCK-AND-READ FIRST (R2-01/ADR-007) — no mutation until replay
    -- recognition, the fence, and the capability gate have all passed.
    SELECT j.status, j.current_attempt_id, j.finished_by_attempt_id, j.queue
      INTO v_job
      FROM taskq.jobs j WHERE j.id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN ('lost', NULL, NULL)::taskq.settle_result;          -- unknown id
    END IF;
    IF v_job.status <> 'running' OR v_job.current_attempt_id IS DISTINCT FROM p_attempt_id THEN
        -- Verb-aware replay over the attempt ledger (Manifest delta b): the
        -- attempt status IS the verb record. Same verb -> already_settled;
        -- any other settled status (incl. reaper 'expired') -> settle_conflict;
        -- absent/still-running mismatch -> lost.
        SELECT a.status INTO v_att FROM taskq.job_attempts a
         WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att = 'succeeded' THEN
            RETURN ('already_settled', v_job.status, NULL)::taskq.settle_result;
        ELSIF v_att IN ('failed','released','snoozed','cancelled','expired') THEN
            RETURN ('settle_conflict', v_job.status, NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;          -- genuinely fenced out
    END IF;

    -- 0.1 capability gate — before any state change. Registered SQLSTATE
    -- (never message-text-only): the worker terminal-fails the parent as
    -- 'unsupported_followup' and soft-stops (version skew is fatal).
    IF p_followups IS NOT NULL AND jsonb_typeof(p_followups) = 'array'
       AND jsonb_array_length(p_followups) > 0
       AND NOT taskq.has_capability('followups') THEN
        RAISE EXCEPTION 'followups are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;

    -- All gates passed — mutate. (complete_job deliberately does NOT check
    -- pending cancel: a valid completion wins until the worker observes
    -- cancellation — Spec SS3.2.)
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

    RETURN ('ok', 'succeeded', NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.complete_job(uuid, uuid, text, jsonb, jsonb, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.complete_job(uuid, uuid, text, jsonb, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.complete_job(uuid, uuid, text, jsonb, jsonb, jsonb) TO taskq_runner;

-- Fail — one retry engine; verb-aware replay; pending cancel branches BEFORE
-- failure accounting (Spec SS5.6 v1.6, 0.1-stripped: no dependent cascade).
CREATE OR REPLACE FUNCTION taskq.fail_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_error text,
    p_retryable boolean DEFAULT true,
    p_retry_after_seconds int DEFAULT NULL,     -- hint normalization ("1h30m", ISO) is client-side
    p_progress jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_job taskq.jobs%ROWTYPE;
    v_att text;
    v_delay int;
    v_next timestamptz;
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_progress IS NOT NULL AND octet_length(p_progress::text) > 2048 THEN
        RAISE EXCEPTION 'progress exceeds the 2KB limit' USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO v_job FROM taskq.jobs
    WHERE id = p_job_id AND status = 'running' AND current_attempt_id = p_attempt_id
    FOR UPDATE;

    IF NOT FOUND THEN
        -- Verb-aware replay (Manifest delta b): same verb -> already_settled;
        -- any other settled attempt status -> settle_conflict; absent or
        -- running-mismatch -> lost.
        SELECT a.status INTO v_att FROM taskq.job_attempts a
        WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att = 'failed' THEN
            RETURN ('already_settled',
                    (SELECT status FROM taskq.jobs WHERE id = p_job_id),
                    NULL)::taskq.settle_result;
        ELSIF v_att IN ('succeeded','released','snoozed','cancelled','expired') THEN
            RETURN ('settle_conflict',
                    (SELECT status FROM taskq.jobs WHERE id = p_job_id),
                    NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;
    END IF;

    -- PENDING CANCEL BRANCHES BEFORE FAILURE ACCOUNTING (v1.6, R2-03): lands
    -- cancelled with the budget UNTOUCHED and the attempt marked cancelled.
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
        RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
    END IF;

    -- Validate the caller-supplied retry hint at the public boundary (R2-07).
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

    -- Terminal failure = the dead-letter state.
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
    RETURN ('dead', 'failed', NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.fail_job(uuid, uuid, text, text, boolean, int, jsonb, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.fail_job(uuid, uuid, text, text, boolean, int, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.fail_job(uuid, uuid, text, text, boolean, int, jsonb, jsonb) TO taskq_runner;

-- Snooze — budget-free give-back; honors a pending cancel; rejects negative
-- delay with TQ422 (Manifest: replaces Spec SS5.7's silent clamp).
CREATE OR REPLACE FUNCTION taskq.snooze_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_delay_seconds int, p_reason text DEFAULT NULL, p_progress jsonb DEFAULT NULL
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_att text; v_cancelled boolean; v_next timestamptz;
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_delay_seconds IS NULL OR p_delay_seconds < 0 OR p_delay_seconds > 2592000 THEN
        RAISE EXCEPTION 'snooze delay must be 0..2592000 seconds, got %', p_delay_seconds
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_progress IS NOT NULL AND octet_length(p_progress::text) > 2048 THEN
        RAISE EXCEPTION 'progress exceeds the 2KB limit' USING ERRCODE = 'TQ422';
    END IF;
    v_next := now() + make_interval(secs => p_delay_seconds);

    -- A snooze HONORS a pending cancel (like fail_job and release_job): a
    -- running job that was operator-cancelled and then snoozed by its worker
    -- must terminalize as 'cancelled', never park as an unclaimable queued row.
    UPDATE taskq.jobs j SET
        status       = CASE WHEN j.cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE 'queued' END,
        outcome      = CASE WHEN j.cancel_requested_at IS NOT NULL THEN 'canceled' ELSE j.outcome END,
        finished_at  = CASE WHEN j.cancel_requested_at IS NOT NULL THEN now() ELSE NULL END,
        scheduled_at = v_next, expiry_streak = 0,
        worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
        progress = COALESCE(p_progress, j.progress), updated_at = now()
    WHERE j.id = p_job_id AND j.status = 'running' AND j.current_attempt_id = p_attempt_id
    RETURNING (j.status = 'cancelled') INTO v_cancelled;
    IF NOT FOUND THEN                                    -- FOUND, never a NULLable flag (Spec SS5 preamble)
        -- Verb-aware replay (Manifest delta b).
        SELECT a.status INTO v_att FROM taskq.job_attempts a
        WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att = 'snoozed' THEN
            RETURN ('already_settled',
                    (SELECT status FROM taskq.jobs WHERE id = p_job_id),
                    NULL)::taskq.settle_result;
        ELSIF v_att IN ('succeeded','failed','released','cancelled','expired') THEN
            RETURN ('settle_conflict',
                    (SELECT status FROM taskq.jobs WHERE id = p_job_id),
                    NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;
    END IF;
    UPDATE taskq.job_attempts SET
           status  = CASE WHEN v_cancelled THEN 'cancelled' ELSE 'snoozed' END,
           outcome = CASE WHEN v_cancelled THEN 'canceled'  ELSE 'snoozed' END,
           error   = p_reason,          -- caller text lives in error/stats, NEVER in outcome (Spec SS3.1)
           finished_at = now()
    WHERE id = p_attempt_id AND status = 'running';
    IF v_cancelled THEN
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id, p_reason, NULL);
        RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
    END IF;
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'snoozed', p_worker_id, p_reason,
        jsonb_build_object('next_at', v_next));
    RETURN ('ok', 'queued', v_next)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.snooze_job(uuid, uuid, text, int, text, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.snooze_job(uuid, uuid, text, int, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.snooze_job(uuid, uuid, text, int, text, jsonb) TO taskq_runner;

-- Release — budget-free requeue (drain/shutdown/no-handler); pending cancel
-- wins (Manifest SS3 body, verbatim; 0.1 strips the dependent cascade line).
CREATE OR REPLACE FUNCTION taskq.release_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_cause text DEFAULT 'released',            -- released | worker_shutdown | no_handler
    p_delay_seconds int DEFAULT 0, p_progress jsonb DEFAULT NULL
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_job taskq.jobs%ROWTYPE; v_att text;
BEGIN
    IF p_cause NOT IN ('released','worker_shutdown','no_handler') THEN
        RAISE EXCEPTION 'invalid release cause %', p_cause USING ERRCODE = 'TQ422';
    END IF;
    IF p_delay_seconds < 0 OR p_delay_seconds > 86400 THEN
        RAISE EXCEPTION 'release delay must be 0..86400, got %', p_delay_seconds USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_job FROM taskq.jobs
     WHERE id = p_job_id AND status = 'running' AND current_attempt_id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN
        SELECT a.status INTO v_att FROM taskq.job_attempts a
         WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att = 'released' THEN
            RETURN ('already_settled', (SELECT status FROM taskq.jobs WHERE id = p_job_id), NULL)::taskq.settle_result;
        ELSIF v_att IN ('succeeded','failed','snoozed','cancelled','expired') THEN
            RETURN ('settle_conflict', (SELECT status FROM taskq.jobs WHERE id = p_job_id), NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;
    END IF;

    IF v_job.cancel_requested_at IS NOT NULL THEN
        UPDATE taskq.jobs SET status='cancelled', outcome='canceled',
               worker_id=NULL, current_attempt_id=NULL, lease_expires_at=NULL,
               progress = COALESCE(p_progress, progress),
               finished_at=now(), finished_by_attempt_id=p_attempt_id, updated_at=now()
         WHERE id = p_job_id;
        UPDATE taskq.job_attempts SET status='cancelled', outcome='canceled', finished_at=now()
         WHERE id = p_attempt_id AND status = 'running';
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id, 'cancel on release', NULL);
        RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
    END IF;

    UPDATE taskq.jobs SET status='queued',
           scheduled_at = now() + make_interval(secs => p_delay_seconds),
           release_count = release_count + 1,                    -- budget NOT consumed
           worker_id=NULL, current_attempt_id=NULL, lease_expires_at=NULL,
           progress = COALESCE(p_progress, progress), updated_at=now()
     WHERE id = p_job_id;
    UPDATE taskq.job_attempts SET status='released', outcome=p_cause, finished_at=now()
     WHERE id = p_attempt_id AND status='running';
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'released', p_worker_id, p_cause, NULL);
    RETURN ('ok', 'queued', (SELECT scheduled_at FROM taskq.jobs WHERE id = p_job_id))::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.release_job(uuid, uuid, text, text, int, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.release_job(uuid, uuid, text, text, int, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.release_job(uuid, uuid, text, text, int, jsonb) TO taskq_runner;

-- Fenced worker-side cancel (ADR-007). Same replay semantics as every settle
-- verb (Manifest SS3 body, verbatim; 0.1 strips the dependent cascade line).
CREATE OR REPLACE FUNCTION taskq.cancel_running_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_reason text
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_job taskq.jobs%ROWTYPE; v_att text;
BEGIN
    SELECT * INTO v_job FROM taskq.jobs
     WHERE id = p_job_id AND status = 'running' AND current_attempt_id = p_attempt_id FOR UPDATE;
    IF NOT FOUND THEN
        SELECT a.status INTO v_att FROM taskq.job_attempts a
         WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att = 'cancelled' THEN
            RETURN ('already_settled', 'cancelled', NULL)::taskq.settle_result;
        ELSIF v_att IN ('succeeded','failed','released','snoozed','expired') THEN
            RETURN ('settle_conflict', (SELECT status FROM taskq.jobs WHERE id = p_job_id), NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost', NULL, NULL)::taskq.settle_result;
    END IF;
    UPDATE taskq.jobs SET status='cancelled', outcome='canceled',
           worker_id=NULL, current_attempt_id=NULL, lease_expires_at=NULL,
           error = left(p_reason, 2000),
           finished_at=now(), finished_by_attempt_id=p_attempt_id, updated_at=now()
     WHERE id = p_job_id;
    UPDATE taskq.job_attempts SET status='cancelled', outcome='canceled',
           finished_at=now(), error=left(p_reason, 2000)
     WHERE id = p_attempt_id AND status='running';
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id, left(p_reason,500), NULL);
    RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.cancel_running_job(uuid, uuid, text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.cancel_running_job(uuid, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.cancel_running_job(uuid, uuid, text, text) TO taskq_runner;

-- Advisory presence — never a reclaim input (Spec SS11.2; Manifest SS3 body,
-- verbatim).
CREATE OR REPLACE FUNCTION taskq.worker_heartbeat(
    p_worker_id text, p_queues text[], p_hostname text DEFAULT NULL,
    p_pid int DEFAULT NULL, p_version text DEFAULT NULL, p_meta jsonb DEFAULT NULL
) RETURNS TABLE (shutdown_requested boolean)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF COALESCE(p_worker_id,'') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    INSERT INTO taskq.workers AS w (worker_id, queues, hostname, pid, version, meta)
    VALUES (p_worker_id, p_queues, p_hostname, p_pid, p_version, p_meta)
    ON CONFLICT (worker_id) DO UPDATE
       SET queues=EXCLUDED.queues, hostname=EXCLUDED.hostname, pid=EXCLUDED.pid,
           version=EXCLUDED.version, meta=EXCLUDED.meta, last_seen_at=now();
    RETURN QUERY SELECT (w.shutdown_requested_at IS NOT NULL) FROM taskq.workers w
                  WHERE w.worker_id = p_worker_id;
END $$;
ALTER FUNCTION taskq.worker_heartbeat(text, text[], text, int, text, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.worker_heartbeat(text, text[], text, int, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.worker_heartbeat(text, text[], text, int, text, jsonb) TO taskq_runner;

-- ============================================================================
-- 9. Observer functions + safe views (Manifest SS4) — EXEC/SELECT taskq_observer
-- ============================================================================

-- ADR-006 authorization projection: exactly four safe fields, nothing else, ever.
CREATE OR REPLACE FUNCTION taskq.get_authorization_projection(p_job_id uuid)
RETURNS TABLE (job_id uuid, queue text, job_type text, status text)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT j.id, j.queue, j.job_type, j.status FROM taskq.jobs j WHERE j.id = p_job_id
$$;
ALTER FUNCTION taskq.get_authorization_projection(uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_authorization_projection(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_authorization_projection(uuid) TO taskq_observer;

-- Protocol H-07 frozen detail projection. include_* flags are POLICY-CHECKED
-- BY THE FACADE (queue read + explicit request); SQL only bounds sizes.
-- Never fences/headers.
CREATE OR REPLACE FUNCTION taskq.get_job(
    p_job_id uuid,
    p_include_error boolean DEFAULT false, p_include_result boolean DEFAULT false,
    p_include_progress boolean DEFAULT false, p_include_payload boolean DEFAULT false
) RETURNS TABLE (
    job_id uuid, queue text, job_type text, status text, outcome text, priority smallint,
    attempt_count smallint, failure_count smallint, max_attempts smallint,
    created_at timestamptz, scheduled_at timestamptz, started_at timestamptz,
    finished_at timestamptz, updated_at timestamptz,
    error text, result jsonb, progress jsonb, payload jsonb
) LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT j.id, j.queue, j.job_type, j.status, j.outcome, j.priority,
           j.attempt_count, j.failure_count, j.max_attempts,
           j.created_at, j.scheduled_at, j.started_at, j.finished_at, j.updated_at,
           CASE WHEN p_include_error    THEN left(j.error, 2048) END,
           CASE WHEN p_include_result   THEN j.result END,
           CASE WHEN p_include_progress THEN j.progress END,
           CASE WHEN p_include_payload  THEN j.payload END
      FROM taskq.jobs j WHERE j.id = p_job_id
$$;
ALTER FUNCTION taskq.get_job(uuid, boolean, boolean, boolean, boolean) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_job(uuid, boolean, boolean, boolean, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_job(uuid, boolean, boolean, boolean, boolean) TO taskq_observer;

-- Tick-refreshed per-queue stats snapshot (staleness <= one tick).
CREATE OR REPLACE FUNCTION taskq.get_queue_stats(p_queue text DEFAULT NULL)
RETURNS TABLE (as_of timestamptz, queue text, stats jsonb)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT (c.data->>'as_of')::timestamptz, q.key, q.value
      FROM taskq.control_state c,
           LATERAL jsonb_each(c.data->'queues') q(key, value)
     WHERE c.key = 'stats_snapshot' AND (p_queue IS NULL OR q.key = p_queue)
$$;
ALTER FUNCTION taskq.get_queue_stats(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_queue_stats(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_stats(text) TO taskq_observer;

-- Contract/capability projection for client startup skew checks.
CREATE OR REPLACE FUNCTION taskq.get_contract_meta()
RETURNS TABLE (contract_version text, capabilities jsonb)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT (SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'),
           (SELECT value          FROM taskq.meta WHERE key='capabilities')
$$;
ALTER FUNCTION taskq.get_contract_meta() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_contract_meta() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_contract_meta() TO taskq_observer;

-- Metrics (Spec SS12.2 shape) — implemented over the snapshot (ready/
-- scheduled/oldest-age per queue) + jobs_running_idx / jobs_finished_idx
-- counts + control_state ages + janitor-recorded index sizes. Never a full
-- hot-table aggregate (Manifest SS4).
CREATE OR REPLACE FUNCTION taskq.metrics()
RETURNS TABLE (name text, labels jsonb, value numeric)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT 'taskq_ready'::text, jsonb_build_object('queue', s.queue),
           COALESCE((s.stats->>'ready')::numeric, 0)
      FROM taskq.get_queue_stats(NULL) s
    UNION ALL
    SELECT 'taskq_scheduled'::text, jsonb_build_object('queue', s.queue),
           COALESCE((s.stats->>'scheduled')::numeric, 0)
      FROM taskq.get_queue_stats(NULL) s
    UNION ALL
    SELECT 'taskq_oldest_ready_seconds'::text, jsonb_build_object('queue', s.queue),
           COALESCE((s.stats->>'oldest_ready_seconds')::numeric, 0)
      FROM taskq.get_queue_stats(NULL) s
    UNION ALL
    SELECT 'taskq_running'::text, jsonb_build_object('queue', j.queue), count(*)::numeric
      FROM taskq.jobs j WHERE j.status = 'running' GROUP BY j.queue
    UNION ALL
    SELECT 'taskq_dead_total'::text, jsonb_build_object('queue', j.queue), count(*)::numeric
      FROM taskq.jobs j WHERE j.status = 'failed' GROUP BY j.queue
    UNION ALL
    SELECT 'taskq_tick_age_seconds'::text, '{}'::jsonb,
           COALESCE(extract(epoch FROM now() - c.last_finished_at), 0)::numeric
      FROM taskq.control_state c WHERE c.key = 'tick'
    UNION ALL
    SELECT 'taskq_workers_online'::text, '{}'::jsonb, count(*)::numeric
      FROM taskq.workers w WHERE w.last_seen_at > now() - interval '180 seconds'
    UNION ALL
    SELECT 'taskq_index_bytes'::text, jsonb_build_object('index', ib.key), ib.value::numeric
      FROM taskq.control_state c2,
           LATERAL jsonb_each_text(c2.data->'index_bytes') ib(key, value)
     WHERE c2.key = 'janitor_daily'
$$;
ALTER FUNCTION taskq.metrics() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.metrics() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.metrics() TO taskq_observer;

-- Safe views (Spec SS12.1; Manifest SS4 names exactly these three for 0.1 —
-- workflow_status is 0.2, rates_15m is not in the 0.1 set). Owner-owned, so
-- observer needs no base-table SELECT. queue_stats is the ad-hoc psql
-- surface, NOT the scrape path — taskq.metrics() rides the snapshot.

CREATE OR REPLACE VIEW taskq.queue_stats AS
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
ALTER VIEW taskq.queue_stats OWNER TO taskq_owner;
GRANT SELECT ON taskq.queue_stats TO taskq_observer;

CREATE OR REPLACE VIEW taskq.dead_jobs AS
SELECT id, queue, job_type, outcome, error, failure_count, expiry_streak, finished_at,
       workflow_id, payload
FROM taskq.jobs WHERE status = 'failed'
ORDER BY finished_at DESC;
ALTER VIEW taskq.dead_jobs OWNER TO taskq_owner;
GRANT SELECT ON taskq.dead_jobs TO taskq_observer;

CREATE OR REPLACE VIEW taskq.worker_status AS
SELECT w.*, (now() - w.last_seen_at) < interval '180 seconds' AS online,
       (SELECT count(*) FROM taskq.jobs j
         WHERE j.worker_id = w.worker_id AND j.status = 'running') AS running_jobs
FROM taskq.workers w;
ALTER VIEW taskq.worker_status OWNER TO taskq_owner;
GRANT SELECT ON taskq.worker_status TO taskq_observer;

-- ============================================================================
-- 10. Operator functions (Manifest SS5) — EXEC taskq_operator
--     Every job-targeted operator mutation records an event with the caller-
--     supplied actor ('operator:<who>'); queue/worker-level verbs emit no
--     event row in 0.1 (DERIVATION NOTES item 9).
-- ============================================================================

-- Upsert a queue profile from validated fields; unknown field -> TQ422;
-- returns created|updated|unchanged + the canonical profile (Manifest SS5).
CREATE OR REPLACE FUNCTION taskq.ensure_queue(
    p_name text, p_profile jsonb DEFAULT '{}'::jsonb, p_actor text DEFAULT NULL
) RETURNS TABLE (result text, profile jsonb)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_field  text;
    v_old    taskq.queues%ROWTYPE;
    v_new    taskq.queues%ROWTYPE;
    v_result text;
BEGIN
    IF p_name IS NULL OR p_name !~ '^[a-z0-9_]{1,57}$' THEN
        RAISE EXCEPTION 'queue name must match ^[a-z0-9_]{1,57}$' USING ERRCODE = 'TQ422';
    END IF;
    IF p_profile IS NULL THEN p_profile := '{}'::jsonb; END IF;
    IF jsonb_typeof(p_profile) <> 'object' THEN
        RAISE EXCEPTION 'profile must be a json object' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_field IN SELECT jsonb_object_keys(p_profile) LOOP
        IF v_field NOT IN ('default_priority','default_lease_seconds','default_max_attempts',
                           'default_backoff_mode','default_backoff_base','default_backoff_cap',
                           'retention_hours','failed_retention_hours','max_depth','notify_enabled') THEN
            RAISE EXCEPTION 'unknown queue profile field "%"', v_field USING ERRCODE = 'TQ422';
        END IF;
    END LOOP;

    SELECT * INTO v_old FROM taskq.queues WHERE name = p_name FOR UPDATE;
    IF NOT FOUND THEN
        v_old := NULL;
        v_new.name                   := p_name;
        v_new.default_priority       := 100;
        v_new.default_lease_seconds  := 300;
        v_new.default_max_attempts   := 5;
        v_new.default_backoff_mode   := 'exponential';
        v_new.default_backoff_base   := 30;
        v_new.default_backoff_cap    := 3600;
        v_new.retention_hours        := 48;
        v_new.failed_retention_hours := 336;
        v_new.max_depth              := NULL;
        v_new.notify_enabled         := true;
    ELSE
        v_new := v_old;
    END IF;

    -- Overlay + validate each provided field (TQ422, never raw cast/check errors).
    BEGIN
        IF p_profile ? 'default_priority' THEN
            v_new.default_priority := (p_profile->>'default_priority')::smallint;
        END IF;
        IF p_profile ? 'default_lease_seconds' THEN
            v_new.default_lease_seconds := (p_profile->>'default_lease_seconds')::int;
        END IF;
        IF p_profile ? 'default_max_attempts' THEN
            v_new.default_max_attempts := (p_profile->>'default_max_attempts')::smallint;
        END IF;
        IF p_profile ? 'default_backoff_mode' THEN
            v_new.default_backoff_mode := p_profile->>'default_backoff_mode';
        END IF;
        IF p_profile ? 'default_backoff_base' THEN
            v_new.default_backoff_base := (p_profile->>'default_backoff_base')::int;
        END IF;
        IF p_profile ? 'default_backoff_cap' THEN
            v_new.default_backoff_cap := (p_profile->>'default_backoff_cap')::int;
        END IF;
        IF p_profile ? 'retention_hours' THEN
            v_new.retention_hours := (p_profile->>'retention_hours')::int;
        END IF;
        IF p_profile ? 'failed_retention_hours' THEN
            v_new.failed_retention_hours := (p_profile->>'failed_retention_hours')::int;
        END IF;
        IF p_profile ? 'max_depth' THEN
            v_new.max_depth := (p_profile->>'max_depth')::int;   -- json null clears to NULL
        END IF;
        IF p_profile ? 'notify_enabled' THEN
            v_new.notify_enabled := (p_profile->>'notify_enabled')::boolean;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'invalid queue profile value: %', SQLERRM USING ERRCODE = 'TQ422';
    END;

    IF v_new.default_priority IS NULL OR v_new.default_priority NOT BETWEEN 0 AND 1000 THEN
        RAISE EXCEPTION 'default_priority must be 0..1000' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_lease_seconds IS NULL OR v_new.default_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'default_lease_seconds must be 15..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_max_attempts IS NULL OR v_new.default_max_attempts NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'default_max_attempts must be 1..100' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_backoff_mode IS NULL OR v_new.default_backoff_mode NOT IN ('fixed','exponential') THEN
        RAISE EXCEPTION 'default_backoff_mode must be fixed|exponential' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_backoff_base IS NULL OR v_new.default_backoff_base NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'default_backoff_base must be 1..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_backoff_cap IS NULL OR v_new.default_backoff_cap < v_new.default_backoff_base THEN
        RAISE EXCEPTION 'default_backoff_cap must be >= default_backoff_base' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.retention_hours IS NULL OR v_new.retention_hours < 1 THEN
        RAISE EXCEPTION 'retention_hours must be >= 1' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.failed_retention_hours IS NULL OR v_new.failed_retention_hours < 1 THEN
        RAISE EXCEPTION 'failed_retention_hours must be >= 1' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.max_depth IS NOT NULL AND v_new.max_depth <= 0 THEN
        RAISE EXCEPTION 'max_depth must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.notify_enabled IS NULL THEN
        RAISE EXCEPTION 'notify_enabled must be a boolean' USING ERRCODE = 'TQ422';
    END IF;

    IF v_old.name IS NULL THEN
        INSERT INTO taskq.queues (
            name, default_priority, default_lease_seconds, default_max_attempts,
            default_backoff_mode, default_backoff_base, default_backoff_cap,
            retention_hours, failed_retention_hours, max_depth, notify_enabled)
        VALUES (
            v_new.name, v_new.default_priority, v_new.default_lease_seconds, v_new.default_max_attempts,
            v_new.default_backoff_mode, v_new.default_backoff_base, v_new.default_backoff_cap,
            v_new.retention_hours, v_new.failed_retention_hours, v_new.max_depth, v_new.notify_enabled);
        v_result := 'created';
    ELSIF (v_new.default_priority, v_new.default_lease_seconds, v_new.default_max_attempts,
           v_new.default_backoff_mode, v_new.default_backoff_base, v_new.default_backoff_cap,
           v_new.retention_hours, v_new.failed_retention_hours, v_new.max_depth, v_new.notify_enabled)
          IS NOT DISTINCT FROM
          (v_old.default_priority, v_old.default_lease_seconds, v_old.default_max_attempts,
           v_old.default_backoff_mode, v_old.default_backoff_base, v_old.default_backoff_cap,
           v_old.retention_hours, v_old.failed_retention_hours, v_old.max_depth, v_old.notify_enabled) THEN
        v_result := 'unchanged';
    ELSE
        UPDATE taskq.queues SET
            default_priority       = v_new.default_priority,
            default_lease_seconds  = v_new.default_lease_seconds,
            default_max_attempts   = v_new.default_max_attempts,
            default_backoff_mode   = v_new.default_backoff_mode,
            default_backoff_base   = v_new.default_backoff_base,
            default_backoff_cap    = v_new.default_backoff_cap,
            retention_hours        = v_new.retention_hours,
            failed_retention_hours = v_new.failed_retention_hours,
            max_depth              = v_new.max_depth,
            notify_enabled         = v_new.notify_enabled,
            updated_at             = now()
        WHERE name = p_name;
        v_result := 'updated';
    END IF;

    RETURN QUERY SELECT v_result, jsonb_build_object(
        'name',                   v_new.name,
        'paused',                 v_new.paused_at IS NOT NULL,
        'default_priority',       v_new.default_priority,
        'default_lease_seconds',  v_new.default_lease_seconds,
        'default_max_attempts',   v_new.default_max_attempts,
        'default_backoff_mode',   v_new.default_backoff_mode,
        'default_backoff_base',   v_new.default_backoff_base,
        'default_backoff_cap',    v_new.default_backoff_cap,
        'retention_hours',        v_new.retention_hours,
        'failed_retention_hours', v_new.failed_retention_hours,
        'max_depth',              v_new.max_depth,
        'notify_enabled',         v_new.notify_enabled);
END $$;
ALTER FUNCTION taskq.ensure_queue(text, jsonb, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.ensure_queue(text, jsonb, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.ensure_queue(text, jsonb, text) TO taskq_operator;

-- Pause claims (intake continues — blocking intake corrupts caller
-- transactions). Idempotent; TQ001 unknown queue.
CREATE OR REPLACE FUNCTION taskq.pause_queue(p_name text, p_actor text, p_reason text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_paused timestamptz;
BEGIN
    SELECT paused_at INTO v_paused FROM taskq.queues WHERE name = p_name FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_name USING ERRCODE = 'TQ001';
    END IF;
    IF v_paused IS NOT NULL THEN RETURN 'already_paused'; END IF;
    UPDATE taskq.queues SET paused_at = now(), pause_reason = p_reason, updated_at = now()
     WHERE name = p_name;
    RETURN 'paused';
END $$;
ALTER FUNCTION taskq.pause_queue(text, text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.pause_queue(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.pause_queue(text, text, text) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.resume_queue(p_name text, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_paused timestamptz;
BEGIN
    SELECT paused_at INTO v_paused FROM taskq.queues WHERE name = p_name FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_name USING ERRCODE = 'TQ001';
    END IF;
    IF v_paused IS NULL THEN RETURN 'already_resumed'; END IF;
    UPDATE taskq.queues SET paused_at = NULL, pause_reason = NULL, updated_at = now()
     WHERE name = p_name;
    RETURN 'resumed';
END $$;
ALTER FUNCTION taskq.resume_queue(text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.resume_queue(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.resume_queue(text, text) TO taskq_operator;

-- Upsert a per-resource concurrency cap; max_running = 0 is a pause valve.
CREATE OR REPLACE FUNCTION taskq.set_concurrency_limit(p_key text, p_max_running int, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_old int;
BEGIN
    IF p_key IS NULL OR p_key !~ '^[a-z0-9_.:-]{1,120}$' THEN
        RAISE EXCEPTION 'concurrency key must match ^[a-z0-9_.:-]{1,120}$' USING ERRCODE = 'TQ422';
    END IF;
    IF p_max_running IS NULL OR p_max_running < 0 THEN
        RAISE EXCEPTION 'max_running must be >= 0, got %', p_max_running USING ERRCODE = 'TQ422';
    END IF;
    SELECT max_running INTO v_old FROM taskq.concurrency_limits WHERE key = p_key FOR UPDATE;
    IF NOT FOUND THEN
        INSERT INTO taskq.concurrency_limits (key, max_running) VALUES (p_key, p_max_running);
        RETURN 'created';
    ELSIF v_old = p_max_running THEN
        RETURN 'unchanged';
    END IF;
    UPDATE taskq.concurrency_limits SET max_running = p_max_running, updated_at = now()
     WHERE key = p_key;
    RETURN 'updated';
END $$;
ALTER FUNCTION taskq.set_concurrency_limit(text, int, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_concurrency_limit(text, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_concurrency_limit(text, int, text) TO taskq_operator;

-- Drain signal: exact worker / queue subscribers / fleet (both NULL).
-- Returns the matched worker count. First request timestamp is preserved.
CREATE OR REPLACE FUNCTION taskq.request_worker_shutdown(
    p_worker_id text, p_queue text, p_actor text
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_n int;
BEGIN
    UPDATE taskq.workers
       SET shutdown_requested_at = COALESCE(shutdown_requested_at, now())
     WHERE (p_worker_id IS NULL OR worker_id = p_worker_id)
       AND (p_queue IS NULL OR p_queue = ANY (queues));
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.request_worker_shutdown(text, text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.request_worker_shutdown(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.request_worker_shutdown(text, text, text) TO taskq_operator;

-- Bounded purge: cancel (never delete) queued/blocked rows oldest-first,
-- <= least(limit, 1000); returns the cancelled count.
CREATE OR REPLACE FUNCTION taskq.purge_queued(
    p_queue text, p_limit int, p_actor text, p_reason text DEFAULT NULL
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_n int := 0; v_id uuid;
BEGIN
    PERFORM 1 FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;
    FOR v_id IN
        SELECT id FROM taskq.jobs
         WHERE queue = p_queue AND status IN ('queued','blocked')
         ORDER BY created_at, id
         LIMIT least(greatest(COALESCE(p_limit, 0), 0), 1000)
         FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE taskq.jobs SET status = 'cancelled', outcome = 'canceled',
               error = COALESCE(p_reason, 'purged by operator'),
               cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = p_reason,
               finished_at = now(), updated_at = now()
         WHERE id = v_id;
        PERFORM taskq.emit_event(v_id, NULL, 'cancelled', p_actor,
                                 COALESCE(p_reason, 'purged'), NULL);
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.purge_queued(text, int, text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.purge_queued(text, int, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.purge_queued(text, int, text, text) TO taskq_operator;

-- Rush: queued only — scheduled_at = now() + NOTIFY; else TQ409 with the
-- current status. TQ001 unknown id.
CREATE OR REPLACE FUNCTION taskq.run_now(p_job_id uuid, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_job record;
BEGIN
    SELECT j.status, j.queue INTO v_job FROM taskq.jobs j WHERE j.id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001';
    END IF;
    IF v_job.status <> 'queued' THEN
        RAISE EXCEPTION 'taskq: job % is % — run_now applies to queued jobs only',
            p_job_id, v_job.status USING ERRCODE = 'TQ409';
    END IF;
    UPDATE taskq.jobs SET scheduled_at = now(), updated_at = now() WHERE id = p_job_id;
    PERFORM taskq.emit_event(p_job_id, NULL, 'run_now', p_actor, NULL, NULL);
    PERFORM pg_notify('taskq_' || v_job.queue, '');
    RETURN 'ok';
END $$;
ALTER FUNCTION taskq.run_now(uuid, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.run_now(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.run_now(uuid, text) TO taskq_operator;

-- Reprioritize: queued/blocked only; priority 0..1000 else TQ422; else TQ409.
CREATE OR REPLACE FUNCTION taskq.reprioritize(p_job_id uuid, p_priority smallint, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_status text;
BEGIN
    IF p_priority IS NULL OR p_priority < 0 OR p_priority > 1000 THEN
        RAISE EXCEPTION 'priority must be 0..1000, got %', p_priority USING ERRCODE = 'TQ422';
    END IF;
    SELECT status INTO v_status FROM taskq.jobs WHERE id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001';
    END IF;
    IF v_status NOT IN ('queued','blocked') THEN
        RAISE EXCEPTION 'taskq: job % is % — reprioritize applies to queued/blocked jobs only',
            p_job_id, v_status USING ERRCODE = 'TQ409';
    END IF;
    UPDATE taskq.jobs SET priority = p_priority, updated_at = now() WHERE id = p_job_id;
    PERFORM taskq.emit_event(p_job_id, NULL, 'reprioritized', p_actor, NULL,
                             jsonb_build_object('priority', p_priority));
    RETURN 'ok';
END $$;
ALTER FUNCTION taskq.reprioritize(uuid, smallint, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.reprioritize(uuid, smallint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.reprioritize(uuid, smallint, text) TO taskq_operator;

-- Operator cancel: immediate for blocked/queued, cooperative for running
-- (Spec SS5.9, typed result vocabulary per Manifest SS5 — see DERIVATION
-- NOTES item 5). The job_deps delete is a structural no-op in 0.1.
CREATE OR REPLACE FUNCTION taskq.cancel_job(p_job_id uuid, p_actor text, p_reason text DEFAULT NULL)
RETURNS TABLE (result text, job_status text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_job taskq.jobs%ROWTYPE;
BEGIN
    SELECT * INTO v_job FROM taskq.jobs WHERE id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001';
    END IF;
    IF v_job.status IN ('succeeded','failed','cancelled') THEN
        RETURN QUERY SELECT 'already_terminal'::text, v_job.status;
        RETURN;
    END IF;

    IF v_job.status IN ('blocked','queued') THEN
        UPDATE taskq.jobs SET status = 'cancelled', outcome = 'canceled',
               error = COALESCE(p_reason, 'cancelled by operator'),
               cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = p_reason,
               finished_at = now(), updated_at = now() WHERE id = p_job_id;
        DELETE FROM taskq.job_deps WHERE job_id = p_job_id;         -- no deps can exist in 0.1
        PERFORM taskq.emit_event(p_job_id, NULL, 'cancelled', p_actor, p_reason, NULL);
        RETURN QUERY SELECT 'cancelled'::text, 'cancelled'::text;
        RETURN;
    END IF;

    UPDATE taskq.jobs SET cancel_requested_at = now(), cancel_reason = p_reason,
           updated_at = now() WHERE id = p_job_id;
    PERFORM taskq.emit_event(p_job_id, v_job.current_attempt_id, 'cancel_requested',
                             p_actor, p_reason, NULL);
    RETURN QUERY SELECT 'cancel_requested'::text, 'running'::text;
    -- Worker sees cancel_requested on next heartbeat; the reaper terminalizes
    -- as cancelled if it never responds.
END $$;
ALTER FUNCTION taskq.cancel_job(uuid, text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.cancel_job(uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.cancel_job(uuid, text, text) TO taskq_operator;

-- Dead-letter redrive (Manifest signature: job_id, actor, reset_progress).
-- TQ409 not_redrivable / idempotency_collision (reason token in DETAIL);
-- TQ001 unknown id. Same id; a new attempt exists only after a claim.
CREATE OR REPLACE FUNCTION taskq.redrive_job(
    p_job_id uuid, p_actor text, p_reset_progress boolean DEFAULT false
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_queue text; v_status text;
BEGIN
    UPDATE taskq.jobs SET
        status = 'queued', scheduled_at = now(),
        failure_count = 0, expiry_streak = 0,
        outcome = NULL, finished_at = NULL, finished_by_attempt_id = NULL,
        cancel_requested_at = NULL, cancel_reason = NULL,
        progress = CASE WHEN p_reset_progress THEN NULL ELSE progress END,
        updated_at = now()
    WHERE id = p_job_id AND status = 'failed'
    RETURNING queue INTO v_queue;
    IF NOT FOUND THEN
        SELECT status INTO v_status FROM taskq.jobs WHERE id = p_job_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001';
        END IF;
        RAISE EXCEPTION 'taskq: job % is % — only failed jobs are redrivable', p_job_id, v_status
            USING ERRCODE = 'TQ409', DETAIL = 'reason=not_redrivable';
    END IF;
    PERFORM taskq.emit_event(p_job_id, NULL, 'redriven', p_actor, 'operator redrive from failed', NULL);
    PERFORM pg_notify('taskq_' || v_queue, '');
    RETURN true;
EXCEPTION WHEN unique_violation THEN
    -- Redrive-vs-dedup collision, made explicit: a NEW active job now holds
    -- the same idempotency key. The operator chooses: cancel the new job and
    -- redrive, or leave the dead row. Never a raw unique-violation leak.
    RAISE EXCEPTION 'taskq: redrive blocked — a new active job holds the idempotency key of %',
        p_job_id USING ERRCODE = 'TQ409', DETAIL = 'reason=idempotency_collision';
END $$;
ALTER FUNCTION taskq.redrive_job(uuid, text, boolean) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.redrive_job(uuid, text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.redrive_job(uuid, text, boolean) TO taskq_operator;

-- Bounded bulk redrive: newest-failed-first, per-row via redrive_job,
-- collisions skipped (Manifest SS5 body, verbatim).
CREATE OR REPLACE FUNCTION taskq.redrive_failed(p_queue text, p_limit int, p_actor text)
RETURNS TABLE (redriven int, skipped int)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_id uuid; v_r int := 0; v_s int := 0;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'limit must be 1..500' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_id IN SELECT id FROM taskq.jobs
                 WHERE queue = p_queue AND status = 'failed'
                 ORDER BY finished_at DESC LIMIT p_limit LOOP
        BEGIN
            PERFORM taskq.redrive_job(v_id, p_actor, false);
            v_r := v_r + 1;
        EXCEPTION WHEN SQLSTATE 'TQ409' THEN
            v_s := v_s + 1;    -- active-key collision or state raced: skip, keep going
        END;
    END LOOP;
    RETURN QUERY SELECT v_r, v_s;
END $$;
ALTER FUNCTION taskq.redrive_failed(text, int, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.redrive_failed(text, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.redrive_failed(text, int, text) TO taskq_operator;

-- Force-expire ONE wedged running job: backdate the lease AND reap in the
-- SAME transaction while holding the row lock — a racing heartbeat blocks on
-- the lock, then fails its WHERE and returns ok=false (Spec SS5.9 v1.6,
-- targeted reap_job). Typed 'not_running'; TQ001 unknown id.
CREATE OR REPLACE FUNCTION taskq.expire_job(p_job_id uuid, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    UPDATE taskq.jobs SET lease_expires_at = now() - interval '1 second', updated_at = now()
    WHERE id = p_job_id AND status = 'running';
    IF NOT FOUND THEN
        PERFORM 1 FROM taskq.jobs WHERE id = p_job_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001';
        END IF;
        RETURN 'not_running';
    END IF;
    PERFORM taskq.emit_event(p_job_id, NULL, 'expire_requested', p_actor, 'operator expire', NULL);
    PERFORM taskq.reap_job(p_job_id);   -- one reclaim authority for batch + targeted reaping
    RETURN 'expired_and_reaped';
END $$;
ALTER FUNCTION taskq.expire_job(uuid, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.expire_job(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.expire_job(uuid, text) TO taskq_operator;

-- Operator "this WORKER is dead" — sugar over the one reclaim authority;
-- budget/backoff/poison all apply normally. v1.6 (R2-02): capture the TARGET
-- ids and reap exactly those (Spec SS5.9 body, verbatim). Typed result:
-- matched (backdated), reaped (reclaimed here), skipped (state changed
-- between backdate and lock — reap_job re-checks under lock and declines).
CREATE OR REPLACE FUNCTION taskq.expire_worker_leases(p_worker_id text, p_actor text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
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
    -- lease_expired); the operator invocation is audited by the caller —
    -- job_events.job_id is NOT NULL, so no summary row here.
    RETURN jsonb_build_object(
        'matched', COALESCE(array_length(v_ids, 1), 0),
        'reaped',  v_reaped,
        'skipped', COALESCE(array_length(v_ids, 1), 0) - v_reaped);
END $$;
ALTER FUNCTION taskq.expire_worker_leases(text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.expire_worker_leases(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.expire_worker_leases(text, text) TO taskq_operator;

-- ============================================================================
-- 11. Housekeeper functions (Manifest SS6) — EXEC taskq_housekeeper AND
--     taskq_operator (manual escape hatch, ADR-011). No public HTTP route.
-- ============================================================================

-- 0.1 janitor: transactional, bounded, per-pass error records (ADR-010: no
-- REINDEX — that is the external maintenance CLI). Retention = bounded
-- deletes in 0.1 (the archive is 0.3). Row budgets keep the pass inside the
-- tick's tolerance (R2-09). (Manifest SS6 body, verbatim.)
CREATE OR REPLACE FUNCTION taskq.janitor() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v jsonb := '{}'; v_n int;
BEGIN
    BEGIN  -- terminal retention (succeeded/cancelled past queue retention_hours)
        WITH del AS (
            DELETE FROM taskq.jobs j USING taskq.queues q
             WHERE j.queue = q.name AND j.status IN ('succeeded','cancelled')
               AND j.finished_at < now() - make_interval(hours => q.retention_hours)
               AND j.id IN (SELECT id FROM taskq.jobs j2
                             WHERE j2.status IN ('succeeded','cancelled')
                             ORDER BY j2.finished_at LIMIT 5000)
             RETURNING j.id)
        SELECT count(*) INTO v_n FROM del;
        v := v || jsonb_build_object('terminal_deleted', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'retention: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN  -- dead-letter retention (failed past failed_retention_hours)
        WITH del AS (
            DELETE FROM taskq.jobs j USING taskq.queues q
             WHERE j.queue = q.name AND j.status = 'failed'
               AND j.finished_at < now() - make_interval(hours => q.failed_retention_hours)
               AND j.id IN (SELECT id FROM taskq.jobs j2 WHERE j2.status = 'failed'
                             ORDER BY j2.finished_at LIMIT 2000)
             RETURNING j.id)
        SELECT count(*) INTO v_n FROM del;
        v := v || jsonb_build_object('failed_deleted', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'dead: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN  -- event pruning tiers (Spec SS13.4)
        DELETE FROM taskq.job_events WHERE id IN (
            SELECT id FROM taskq.job_events
             WHERE created_at < now() - interval '30 days' ORDER BY id LIMIT 20000);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v := v || jsonb_build_object('events_pruned', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'events: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN  -- stale presence (>7 days)
        DELETE FROM taskq.workers WHERE last_seen_at < now() - interval '7 days';
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v := v || jsonb_build_object('workers_pruned', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'workers: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN  -- index-size recording for SS12.2 metrics
        UPDATE taskq.control_state
           SET data = jsonb_set(COALESCE(data,'{}'), '{index_bytes}',
               (SELECT COALESCE(jsonb_object_agg(indexrelname, pg_relation_size(indexrelid)), '{}')
                  FROM pg_catalog.pg_stat_user_indexes
                 WHERE schemaname = 'taskq' AND relname = 'jobs')),
               last_finished_at = now()
         WHERE key = 'janitor_daily';
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'sizes: '||SQLERRM WHERE key='janitor_daily';
    END;
    RETURN v;
END $$;
ALTER FUNCTION taskq.janitor() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.janitor() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.janitor() TO taskq_housekeeper, taskq_operator;

-- The tick — savepoint-per-pass, observable, leaderless (Spec SS11.4 v1.6,
-- 0.1-stripped per ADR-009: the finalize_dep_stragglers and
-- finalize_workflows passes are 0.2 and absent from this migration).
-- Ordering is load-bearing: reaping ALWAYS runs first — a slow janitor can
-- degrade retention, never lease recovery.
CREATE OR REPLACE FUNCTION taskq.tick(p_reap_limit int DEFAULT 200)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_out jsonb := '{}'; v_n int;
BEGIN
    -- Fleet-wide dedup: at most one concurrent ticker, zero waiting.
    IF NOT pg_try_advisory_xact_lock(hashtextextended('taskq:tick', 0)) THEN
        RETURN jsonb_build_object('skipped', true);
    END IF;
    UPDATE taskq.control_state SET last_started_at = now() WHERE key = 'tick';
    INSERT INTO taskq.control_state (key, last_started_at) VALUES ('tick', now())
        ON CONFLICT (key) DO UPDATE SET last_started_at = now();

    -- Savepoint per pass: one failing pass logs to control_state.last_error;
    -- the remaining passes still run.
    BEGIN
        v_n := taskq.reap_expired(p_reap_limit);
        v_out := v_out || jsonb_build_object('reaped', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'reap: ' || SQLERRM WHERE key = 'tick';
    END;

    BEGIN
        -- Finalize cancel-requested queued/blocked stragglers (cancel raced a
        -- transition; such rows are invisible to the claim predicate).
        v_n := taskq.finalize_cancel_stragglers(50);
        v_out := v_out || jsonb_build_object('cancel_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'cancel: ' || SQLERRM WHERE key = 'tick';
    END;

    BEGIN
        -- Stats snapshot for exporters/dashboards (Spec SS12.1): bounded,
        -- index-backed, written to control_state key 'stats_snapshot'.
        PERFORM taskq.refresh_stats_snapshot();
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'stats: ' || SQLERRM WHERE key = 'tick';
    END;

    -- The 0.1 DUE-GATED DAILY JANITOR (R2-09, ADR-009 carve-out): schedules
    -- are a 0.2 capability, so the tick itself carries the trigger — claim
    -- the 'janitor_daily' due marker atomically; if due, run the janitor's
    -- independently bounded passes. next_due advances on claim; a failed
    -- pass records last_error and stays due on the next tick. In 0.2 the
    -- seeded schedule row replaces this trigger.
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
ALTER FUNCTION taskq.tick(int) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.tick(int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.tick(int) TO taskq_housekeeper, taskq_operator;

-- ============================================================================
-- 12. 0.1 seeding (Spec SS4 v1.6 / ADR-009): control-state rows + contract
--     meta ONLY. No '_system' queue, no schedule rows (schedules are 0.2),
--     no host queues (hosts provision their own via ensure_queue/bootstrap).
-- ============================================================================

INSERT INTO taskq.control_state (key) VALUES ('tick')
ON CONFLICT (key) DO NOTHING;

INSERT INTO taskq.control_state (key, data)
VALUES ('janitor_daily', jsonb_build_object('next_due', now()))
ON CONFLICT (key) DO NOTHING;

INSERT INTO taskq.control_state (key) VALUES ('stats_snapshot')
ON CONFLICT (key) DO NOTHING;

INSERT INTO taskq.meta (key, value) VALUES ('contract_version', '"0.1"'::jsonb)
ON CONFLICT (key) DO NOTHING;

INSERT INTO taskq.meta (key, value) VALUES ('capabilities', '{"active": []}'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Migration ledger (ADR-004): the RUNNER is the single ledger writer — it
-- records (id = filename stem, package_version, sha256) after applying this
-- file. The file itself only guarantees the table exists (above) and never
-- self-inserts; two writers produced id drift at Stage-1 integration.

-- ============================================================================
-- 13. Self-check: the migration fails atomically if any taskq function
--     escaped the SECURITY DEFINER + pinned search_path hardening contract
--     (ADR-010/011 — the created-but-not-hardened definer function is the
--     textbook escalation surface).
-- ============================================================================

DO $do$
DECLARE v_bad text;
BEGIN
    SELECT string_agg(p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', ', '
                      ORDER BY p.proname)
      INTO v_bad
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND p.prokind = 'f'
       AND (NOT p.prosecdef
            OR p.proconfig IS NULL
            OR NOT EXISTS (SELECT 1 FROM unnest(p.proconfig) AS c(setting)
                            WHERE c.setting LIKE 'search_path=%'));
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'taskq migration 0001 self-check failed — functions missing SECURITY DEFINER or a pinned search_path: %',
            v_bad;
    END IF;
END
$do$;

-- End of migration 0001.
