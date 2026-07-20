# taskq — 0.1.x Function Manifest

> **Status:** CANONICAL for SQL contract 0.1.3 — 2026-07-20. Closes R2-08 and incorporates ADR-012/013/019: every function the 0.1.x contract ships is listed here with identity, grants, raises, and an executable body (or a pointer to its normative body in the Unified Spec §5/§11 as amended here). Migrations 0001 + 0002 + 0003 + 0004 derive from THIS document; `verify()` compares the live catalog against this manifest (ADR-011 §4). A function not listed here does not exist in 0.1.3 — no success-returning stubs.
> **Two deltas vs spec §5 (protocol v1 hole closures — where this manifest and older spec text differ, the manifest wins for 0.1):**
> **(a) H-01:** `claim_jobs` returns `taskq.claim_batch (state, jobs[])`, not a bare SETOF — `state ∈ claimed|empty|paused|unknown_queue|unavailable`.
> **(b) H-03:** settle replays are **verb-aware**: same verb re-settled → `already_settled`; different verb against a settled attempt → `settle_conflict` (the attempt-ledger status IS the verb record: succeeded↔complete, failed↔fail, released↔release, snoozed↔snooze, cancelled↔cancel_running, expired↔reaper).

## 0. Manifest conventions (apply to every entry)

Every function: `LANGUAGE plpgsql` (or `sql` where noted), `SECURITY DEFINER`, **owner `taskq_owner`**, `SET search_path = pg_catalog, taskq, pg_temp`, fully qualified references, `REVOKE EXECUTE ... FROM PUBLIC` in the creating migration, `GRANT EXECUTE` exactly as the entry's **EXEC** line says (ADR-010/011). Public-boundary validation raises use `USING ERRCODE` from the protocol registry (TQ001/TQ409/TQ422/TQ429/TQ500/TQ501). Omission invokes a declared default; explicit `NULL` for a documented non-null domain raises `TQ422` (ADR-012). Entries marked **spec** have their normative body in the Unified Spec section cited (with the v1.6 fixes and manifest amendments applied); entries with SQL here are the previously missing bodies. Test ids reference the harness suites.

Composite types frozen for 0.1 (H-02; additive evolution only). Contract 0.1.3 has the following complete `claimed_job` shape; `lease_seconds` is appended and no existing attribute moves:

```sql
CREATE TYPE taskq.claimed_job AS (
    job_id uuid, queue text, job_type text, priority smallint,
    payload jsonb, headers jsonb, progress jsonb,
    attempt_id uuid, attempt_number integer,
    failure_count smallint, max_attempts smallint,
    lease_expires_at timestamptz,
    workflow_id uuid, step_key text,
    lease_seconds integer
);
CREATE TYPE taskq.claim_batch AS (
    state text,                 -- claimed | empty | paused | unknown_queue | unavailable
    jobs  taskq.claimed_job[]   -- non-empty only when state = 'claimed'
);
CREATE TYPE taskq.job_list_item AS (
    job_id uuid, job_type text, status text, outcome text, priority smallint,
    attempt_count smallint, failure_count smallint, max_attempts smallint,
    created_at timestamptz, scheduled_at timestamptz, started_at timestamptz,
    finished_at timestamptz, updated_at timestamptz
);
CREATE TYPE taskq.job_page AS (
    as_of timestamptz, items taskq.job_list_item[], next_after jsonb
);
CREATE TYPE taskq.queue_profile AS (
    name text, profile_version bigint,
    default_priority smallint, default_lease_seconds int, default_max_attempts smallint,
    default_backoff_mode text, default_backoff_base int, default_backoff_cap int,
    retention_hours int, failed_retention_hours int, max_depth int,
    notify_enabled boolean, paused boolean
);
CREATE TYPE taskq.queue_profile_update AS (
    result text, profile taskq.queue_profile, current_version bigint
);
-- taskq.claimed_job and taskq.settle_result: as spec §4, with settle_result.result
-- gaining the 'settle_conflict' value (H-03).
```

## 1. Internal helpers (owner-only — no application-role EXECUTE)

| Function | Body |
|---|---|
| `taskq.uuid7()` | spec §4 (PG18 native / SQL fallback) |
| `taskq.backoff_seconds(mode,base,cap,failures)` | spec §5.1 (fixed ±15% jitter in 0.1) |
| `taskq.emit_event(job_id,attempt_id,type,actor,msg,data)` | spec §4 (truncating) |
| `taskq.truncate_utf8(value,max_bytes)` | below; added in 0.1.1 by ADR-012 |
| `taskq.reap_job(job_id)` | below |
| `taskq.finalize_cancel_stragglers(limit)` | below |
| `taskq.claim_janitor_due()` | below |
| `taskq.refresh_stats_snapshot()` | below |
| `taskq.has_capability(name)` | below |

```sql
-- Longest valid UTF-8 prefix within a byte budget. Owner-only: no application
-- capability role receives EXECUTE. Binary search keeps cost logarithmic.
CREATE FUNCTION taskq.truncate_utf8(p_value text, p_max_bytes int)
RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SECURITY DEFINER AS $$
DECLARE
    v_low int := 0;
    v_high int;
    v_mid int;
BEGIN
    IF p_value IS NULL THEN RETURN NULL; END IF;
    IF p_max_bytes IS NULL OR p_max_bytes < 0 THEN
        RAISE EXCEPTION 'byte limit must be non-negative' USING ERRCODE = '22023';
    END IF;
    IF octet_length(p_value) <= p_max_bytes THEN RETURN p_value; END IF;
    v_high := least(char_length(p_value), p_max_bytes);
    WHILE v_low < v_high LOOP
        v_mid := (v_low + v_high + 1) / 2;
        IF octet_length(left(p_value, v_mid)) <= p_max_bytes THEN
            v_low := v_mid;
        ELSE
            v_high := v_mid - 1;
        END IF;
    END LOOP;
    RETURN left(p_value, v_low);
END $$;

CREATE FUNCTION taskq.has_capability(p_name text) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT COALESCE((SELECT (value -> 'active') ? p_name FROM taskq.meta
                     WHERE key = 'capabilities'), false)
$$;  -- 0.1 seeds meta.capabilities.active = [] (no followups/dependencies/workflows/schedules/archive)

-- The one reclaim authority's per-row body (spec §5.8's engine, target-aware — R2-02).
-- Returns true iff THIS call reclaimed the row. Re-checks everything under lock, so
-- expire sugar / batch reaper / idle micro-reap share one code path.
CREATE FUNCTION taskq.reap_job(p_job_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
-- reap_expired(limit) = SELECT id ... WHERE status='running' AND lease_expires_at <= now()
-- ORDER BY lease_expires_at LIMIT p (scans jobs_running_idx, filters heap) → PERFORM reap_job(id).

-- Cancel stragglers: cancel_requested_at set while non-running (cancel raced a requeue).
CREATE FUNCTION taskq.finalize_cancel_stragglers(p_limit int) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER AS $$
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

-- 0.1 janitor due marker (R2-09/ADR-009): atomic claim; next_due advances on claim;
-- a failing janitor records last_error and is due again next tick.
CREATE FUNCTION taskq.claim_janitor_due() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER AS $$
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

-- Stats snapshot (§12.1): per-queue counts riding the partial indexes; never a full aggregate.
CREATE FUNCTION taskq.refresh_stats_snapshot() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
```

## 2. Producer functions — EXEC `taskq_producer`

| Function | Body | Raises | Tests |
|---|---|---|---|
| `taskq.enqueue(...)` (no `p_internal`) | spec §5.2 (v1.6) | TQ001, TQ422, TQ429, TQ500 | T2-ENQ, T3-DEDUP |
| `taskq.enqueue_many(p_queue, p_jobs jsonb)` | below | TQ001, TQ422, TQ429, TQ500 | T2-BULK, T3-BULK, B2 |

```sql
-- One transaction, one queue, ≤1000 specs, no deps, one depth probe, one NOTIFY,
-- one ordered typed result per input (R2-12; protocol H-05).
CREATE FUNCTION taskq.enqueue_many(p_queue text, p_jobs jsonb)
RETURNS TABLE (input_index int, job_id uuid, outcome text)
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_n int;
BEGIN
    IF jsonb_typeof(p_jobs) <> 'array' OR jsonb_array_length(p_jobs) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'p_jobs must be an array of 1..1000 specs' USING ERRCODE = 'TQ422';
    END IF;
    -- Per-spec validation (job_type present, no dependency/workflow fields in 0.1,
    -- payload size caps) — TQ422 with the input index; then the single depth probe
    -- (TQ429) — both BEFORE any insert, so rejection means zero rows.
    -- Pass 1: multi-row INSERT ... ON CONFLICT DO NOTHING RETURNING captures created
    -- ids + their input ordinals (WITH ORDINALITY over jsonb_array_elements; intra-
    -- request duplicate keys resolve first-occurrence-creates, later report existed).
    -- Pass 2: for conflicted ordinals, a later-snapshot SELECT resolves the existing
    -- active holder per key; holders that settled mid-call are retried through the
    -- same converge-or-TQ500 rule as single enqueue (bounded at 3 rounds; TQ500
    -- rolls the WHOLE batch back — no partial batches).
    -- Pass 3: emit ONE NOTIFY for the queue when any created row is immediately
    -- runnable. Return rows in input order.
    -- (Full body authored in migration 0001 from this contract; the three-pass
    -- structure and its invariants above are normative.)
    RETURN;
END $$;
```

## 3. Runner functions — EXEC `taskq_runner`

| Function | Body | Raises | Tests |
|---|---|---|---|
| `taskq.claim_jobs(...) RETURNS taskq.claim_batch` | spec §5.3 wrapped per H-01: resolve queue first (`unknown_queue`/`paused` states), targeted claim miss → `unavailable`, else SKIP-LOCKED batch → `claimed`/`empty`; input bounds validated (batch 1–50, lease 15–86400) | TQ422 | T2-CLAIM, T3-RACE, B3 |
| `taskq.heartbeat(...)` | spec §5.4 + lease-override bounds (TQ422) | TQ422 | T2-HB |
| `taskq.complete_job(...)` | spec §5.5 (v1.6) + verb-aware replay (H-03) | TQ422, TQ501 | T2-COMPLETE, T3-SETTLE |
| `taskq.fail_job(...)` | spec §5.6 (v1.6) + verb-aware replay | TQ422 | T2-FAIL |
| `taskq.snooze_job(...)` | spec §5.7 + reject negative delay (TQ422, replacing silent clamp) | TQ422 | T2-SNOOZE |
| `taskq.release_job(...)` | below | TQ422 | T2-RELEASE |
| `taskq.cancel_running_job(...)` | below | — | T2-CANCELRUN |
| `taskq.worker_heartbeat(...)` | below | TQ422 | T2-PRESENCE |

```sql
-- Budget-free requeue (drain/shutdown/no-handler). Pending cancel wins.
CREATE FUNCTION taskq.release_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_cause text DEFAULT 'released',            -- released | worker_shutdown | no_handler
    p_delay_seconds int DEFAULT 0, p_progress jsonb DEFAULT NULL
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
        PERFORM taskq.cancel_dependents(p_job_id, 'dependency cancelled');  -- [0.2; no-op absent deps]
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

-- Fenced worker-side cancel (ADR-007). Same replay semantics as every settle verb.
CREATE FUNCTION taskq.cancel_running_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text, p_reason text
) RETURNS taskq.settle_result
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
    PERFORM taskq.cancel_dependents(p_job_id, 'dependency cancelled');      -- [0.2; no-op absent deps]
    RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
END $$;

-- Advisory presence (never a reclaim input — §11.2).
CREATE FUNCTION taskq.worker_heartbeat(
    p_worker_id text, p_queues text[], p_hostname text DEFAULT NULL,
    p_pid int DEFAULT NULL, p_version text DEFAULT NULL, p_meta jsonb DEFAULT NULL
) RETURNS TABLE (shutdown_requested boolean)
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
```

## 4. Observer functions — EXEC `taskq_observer`

```sql
-- ADR-006 authorization projection: exactly four safe fields, nothing else, ever.
CREATE FUNCTION taskq.get_authorization_projection(p_job_id uuid)
RETURNS TABLE (job_id uuid, queue text, job_type text, status text)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT j.id, j.queue, j.job_type, j.status FROM taskq.jobs j WHERE j.id = p_job_id
$$;

-- Protocol H-07 frozen detail projection. include_* flags are POLICY-CHECKED BY THE
-- FACADE (queue read + explicit request); SQL only bounds sizes. Never fences/headers.
CREATE FUNCTION taskq.get_job(
    p_job_id uuid,
    p_include_error boolean DEFAULT false, p_include_result boolean DEFAULT false,
    p_include_progress boolean DEFAULT false, p_include_payload boolean DEFAULT false
) RETURNS TABLE (
    job_id uuid, queue text, job_type text, status text, outcome text, priority smallint,
    attempt_count smallint, failure_count smallint, max_attempts smallint,
    created_at timestamptz, scheduled_at timestamptz, started_at timestamptz,
    finished_at timestamptz, updated_at timestamptz,
    error text, result jsonb, progress jsonb, payload jsonb
) LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT j.id, j.queue, j.job_type, j.status, j.outcome, j.priority,
           j.attempt_count, j.failure_count, j.max_attempts,
           j.created_at, j.scheduled_at, j.started_at, j.finished_at, j.updated_at,
           CASE WHEN p_include_error    THEN left(j.error, 2048) END,
           CASE WHEN p_include_result   THEN j.result END,
           CASE WHEN p_include_progress THEN j.progress END,
           CASE WHEN p_include_payload  THEN j.payload END
      FROM taskq.jobs j WHERE j.id = p_job_id
$$;

CREATE FUNCTION taskq.get_queue_stats(p_queue text DEFAULT NULL)
RETURNS TABLE (as_of timestamptz, queue text, stats jsonb)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT (c.data->>'as_of')::timestamptz, q.key, q.value
      FROM taskq.control_state c,
           LATERAL jsonb_each(c.data->'queues') q(key, value)
     WHERE c.key = 'stats_snapshot' AND (p_queue IS NULL OR q.key = p_queue)
$$;

-- ADR-019 / H-08. p_after is the already-validated typed cursor object, never
-- an opaque HTTP token or SQL fragment. p_view is exactly ready|running|finished;
-- p_limit is 1..100. Each view checks its named capability and returns TQ501
-- while inactive. The page is queue-scoped and contains only taskq.job_list_item.
-- DIRECT SQL PARITY RULE: a direct SQL client must not get a wider projection
-- merely because it bypasses HTTP.
-- Function identity (normative body in §4.1):
-- taskq.list_jobs(p_queue text, p_view text, p_limit int DEFAULT 50,
--                 p_after jsonb DEFAULT NULL) RETURNS taskq.job_page

-- ADR-019 / H-11. The exact observer-safe profile, never a raw queues row.
CREATE FUNCTION taskq.get_queue_profile(p_queue text)
RETURNS taskq.queue_profile
LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT ROW(
        q.name, q.profile_version,
        q.default_priority, q.default_lease_seconds, q.default_max_attempts,
        q.default_backoff_mode, q.default_backoff_base, q.default_backoff_cap,
        q.retention_hours, q.failed_retention_hours, q.max_depth,
        q.notify_enabled, q.paused_at IS NOT NULL
    )::taskq.queue_profile
    FROM taskq.queues q WHERE q.name = p_queue
$$;
ALTER FUNCTION taskq.get_queue_profile(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_queue_profile(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_profile(text) TO taskq_observer;

CREATE FUNCTION taskq.get_contract_meta()
RETURNS TABLE (contract_version text, capabilities jsonb)
LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT (SELECT value #>> '{}' FROM taskq.meta WHERE key='contract_version'),
           (SELECT value          FROM taskq.meta WHERE key='capabilities')
$$;

-- taskq.metrics(): spec §12.2 name/labels/value rows — implemented over the snapshot
-- (ready/scheduled/oldest-age per queue) + jobs_running_idx/jobs_finished_idx counts
-- + control_state ages + janitor-recorded index sizes. No full hot-table aggregate.
-- Views (SELECT-granted to observer): queue_stats, dead_jobs, worker_status
-- (spec §12.1; workflow_status is 0.2).
```

### 4.1 ADR-019 read-model rules (normative body requirements)

`taskq.list_jobs(text, text, int, jsonb)` is `STABLE SECURITY DEFINER`, owned
by `taskq_owner`, has the universal pinned search path, PUBLIC execute revoked,
and EXEC granted only to `taskq_observer`. It validates the queue grammar,
`p_view ∈ ready|running|finished`, `p_limit ∈ 1..100`, and the typed `p_after`
object before reading `taskq.jobs`. Bad inputs or a tuple not matching the
queue/view are `TQ422`. Each view first requires its exact capability:
`read_model_list_ready`, `read_model_list_running`, or
`read_model_list_finished`; an inactive one raises `TQ501` with typed reason
`read_model_view_inactive` and the requested view.

It captures database `as_of` once and returns exactly one `taskq.job_page`.
`items` contains at most `p_limit` `taskq.job_list_item` rows; `next_after` is
the full typed sort tuple for the next page or NULL. It selects `p_limit + 1`
candidates from exactly one finite view:

| View | Predicate | Order / `next_after` tuple |
|---|---|---|
| `ready` | `status='queued' AND cancel_requested_at IS NULL AND scheduled_at <= as_of` | `priority ASC, scheduled_at ASC, id ASC` |
| `running` | `status='running'` | `started_at DESC, id DESC` |
| `finished` | `status IN ('succeeded','failed','cancelled')` | `finished_at DESC, id DESC` |

No function branch selects payload, headers, worker identity, attempt id,
fence, cancellation reason, error, result, progress, events, or any JSON
column. A missing queue returns an empty SQL page; the facade maps an
authorized missing queue to `TQ001` after its own queue existence policy.
Direct SQL returns precisely this page composite: **a direct SQL client must
not get a wider projection merely because it bypasses HTTP.**

`taskq.get_queue_profile(text)` is the only observer profile projection. It
returns NULL/no row for an unknown queue and its exact `taskq.queue_profile`
type above for a known one. `paused` is `paused_at IS NOT NULL`; pause reason,
workers, IAM, host metadata, and raw queue fields never appear.

## 5. Operator functions — EXEC `taskq_operator`

| Function | Body | Raises / result |
|---|---|---|
| `taskq.cancel_job(job_id, actor, reason)` | spec §5.9 | typed `cancelled`/`cancel_requested`/`already_terminal` |
| `taskq.redrive_job(job_id, actor, reset_progress)` | spec §5.9 | TQ409 (`not_redrivable` / `idempotency_collision`) |
| `taskq.expire_job(job_id, actor)` | spec §5.9 (v1.6 targeted `reap_job`) | typed `not_running` |
| `taskq.expire_worker_leases(worker_id, actor)` | spec §5.9 (v1.6) | `{matched,reaped,skipped}` |
| `taskq.ensure_queue(name, profile jsonb, actor)` | existing three-argument bootstrap identity; upsert validated canonical profile, incrementing `profile_version` only on actual canonical-profile change; returns `created|updated|unchanged` + canonical profile including version | TQ422 |
| `taskq.update_queue_profile(name, profile jsonb, actor, expected_version bigint)` | validates the same canonical fields, locks the queue row, and applies the profile only when `expected_version` is current; returns `updated` + profile/new version or `profile_version_conflict` + current version only | typed conflict → HTTP TQ409; TQ001 / TQ422 |

### 5.1 ADR-019 conditional profile-update identity

`taskq.update_queue_profile(text, jsonb, text, bigint)` returns
`taskq.queue_profile_update`. It is `SECURITY DEFINER`, owned by
`taskq_owner`, has the universal pinned search path, PUBLIC execute revoked,
and EXEC granted only to `taskq_operator`. It rejects invalid queue/profile or
non-positive expected version with `TQ422`; it returns no row only for an
unknown queue (`TQ001` at the public boundary). Under the same row lock used by
`ensure_queue`, a mismatch returns
`('profile_version_conflict', NULL, current_version)` without mutation. A match
applies the same validation/normalization as `ensure_queue`, increments the
version only for a real canonical-profile change, and returns
`('updated', profile, new_version)`. This function never returns a request
echo, pause reason, worker data, or a raw queue row.
| `taskq.pause_queue(name, actor, reason)` / `resume_queue(name, actor)` | set/clear `paused_at`; idempotent (`already_*`); TQ001 unknown queue; event emitted | TQ001 |
| `taskq.set_concurrency_limit(key, max_running, actor)` | upsert `concurrency_limits`; `max_running >= 0` else TQ422; returns `created|updated|unchanged` | TQ422 |
| `taskq.request_worker_shutdown(worker_id, queue, actor)` | stamp `shutdown_requested_at` for exact worker / queue subscribers / fleet (both NULL); returns matched count | — |
| `taskq.purge_queued(queue, limit, actor, reason)` | bounded loop: cancel (never delete) queued/blocked rows oldest-first, ≤`least(limit,1000)`; returns cancelled count | TQ001 |
| `taskq.run_now(job_id, actor)` | queued only: `scheduled_at = now()` + NOTIFY; else TQ409 with current status | TQ409 |
| `taskq.reprioritize(job_id, priority, actor)` | queued/blocked only; 0–1000 else TQ422; else TQ409 | TQ409, TQ422 |
| `taskq.redrive_failed(queue, limit, actor)` | below | count + per-id skips |

```sql
-- Bounded bulk redrive: newest-failed-first, per-row via redrive_job, collisions skipped.
CREATE FUNCTION taskq.redrive_failed(p_queue text, p_limit int, p_actor text)
RETURNS TABLE (redriven int, skipped int)
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
```

## 6. Housekeeper functions — EXEC `taskq_housekeeper` + `taskq_operator`

| Function | Body |
|---|---|
| `taskq.tick(reap_limit)` | spec §11.4 (v1.6: reaper-first, cancel stragglers, stats snapshot, due-gated janitor; dep/workflow finalizer passes absent from the 0.1 migration) |
| `taskq.janitor()` | below |

```sql
-- 0.1 janitor: transactional, bounded, per-pass error records (ADR-010: no REINDEX —
-- that is the external `taskq maintenance` CLI). Retention = bounded deletes in 0.1
-- (the archive is 0.3). Row budgets keep the pass inside the tick's tolerance (R2-09).
CREATE FUNCTION taskq.janitor() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER AS $$
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
    BEGIN  -- event pruning tiers (§13.4)
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
    BEGIN  -- index-size recording for §12.2 metrics
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
```

## 7. Explicitly absent from the 0.1 migration

`_enqueue_followup`, `cancel_dependents` (the 0.1 bodies above call it guarded — the migration ships a no-op-absent-deps stub is **not** allowed; instead the 0.1 bodies omit the call lines entirely), `finalize_dep_stragglers`, `finalize_workflows`, `create_workflow`/`cancel_workflow`, the schedule trio, all archive objects/functions, every list form **other than ADR-019's exact queue-scoped `list_jobs` page**, and every 0.2/0.3 composite field. SQL contract 0.1.3 contains only the three finite H-08 views; general/all-queue, arbitrary-filter, payload, and timeline lists remain absent. The migration generator strips the lines marked `[0.2]`/`[0.3]` from the reference bodies; T2 asserts the 0.1 catalog contains exactly this manifest's function set.

## 8. Errata — Stage-1 integration reconciliations (2026-07-18)

Resolved when migration 0001 + the harness first met live PostgreSQL (42/42 contract tests green). These are clarifications of ambiguity, not semantic changes:

1. **Single ledger writer:** the RUNNER records `schema_migrations` rows (id = filename stem, e.g. `0001_initial`; package version; sha256 of raw file bytes). Migration files guarantee the table exists but never self-insert — two writers produced id drift on first integration.
2. **`cancel_job` returns the typed composite `(result, job_status)`** — cooperative cancel of a running job = `('cancel_requested','running')`.
3. **`release_job` signature:** this manifest's `(job_id, attempt_id, worker_id, p_cause, p_delay_seconds=0, p_progress)` is authoritative; spec §5.7 prose (`p_reason`, default delay 15) is superseded. Delay bound 0–86400 (snooze remains 0–2592000).
4. **§0 hardening applies to EVERY function including `LANGUAGE sql` helpers** (`uuid7`, `has_capability`, projections): SECURITY DEFINER + pinned search_path + owner + PUBLIC revoke. `verify()` enforces this uniformly; a NULL `proacl` counts as a PUBLIC-execute violation.
5. **Queue/worker-level operator verbs (pause/resume/shutdown-request/set-limit) emit no `job_events` row** — `job_events.job_id` is NOT NULL; their audit is the typed result + caller logging (facade actor / CLI).
6. **Verb-aware replay includes the reaper:** an attempt settled as `expired` answers any worker settle with `settle_conflict`.
7. 0.1 single-`enqueue` rejects `p_depends_on`/`p_workflow_id` with `TQ501` (capability gate); bulk specs carrying dependency fields are `TQ422` with the input index.
8. **Historical 0.1.2 posture:** no operator-minimal `list_jobs` existed. ADR-017 corrected the adopted Protocol
   drafting error: SQL contract 0.1.2 contains neither a `list_jobs` function nor a safe general-list
   projection. ADR-019 / contract 0.1.3 supersedes that posture with only §4.1's exact finite page.

## 9. Contract patch 0.1.1 — ADR-012 (2026-07-18)

Migration `0002_contract_0_1_1.sql` applies these normative deltas without changing public identities or result shapes:

1. **Explicit-null validation.** Every public parameter with a documented non-null domain checks `IS NULL` before state change and raises `TQ422`. In particular, the three round-3 counterexamples become:

   ```sql
   -- claim_jobs
   IF p_batch IS NULL OR p_batch < 1 OR p_batch > 50 THEN ... ERRCODE = 'TQ422'; END IF;
   -- release_job
   IF p_delay_seconds IS NULL OR p_delay_seconds < 0 OR p_delay_seconds > 86400 THEN ... ERRCODE = 'TQ422'; END IF;
   -- redrive_failed
   IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN ... ERRCODE = 'TQ422'; END IF;
   ```

   The migration audits all other public required/bounded arguments under the same rule; optional nullable arguments remain nullable. Function omission/default behavior is unchanged.

2. **Byte-safe diagnostic storage.** All writes to `jobs.error`, `job_attempts.error`, and `jobs.cancel_reason` use `taskq.truncate_utf8(value, 2048)`. `emit_event` stores `taskq.truncate_utf8(p_message, 500)`, making the event cap universal. At minimum this replaces character-counted or unbounded writes in `reap_job`, `fail_job`, `snooze_job`, `cancel_running_job`, and `cancel_job`.
3. **Exact helper surface.** `taskq.truncate_utf8(text,int)` is the sole new 0.1.1 function: owner `taskq_owner`, `SECURITY DEFINER`, immutable, parallel-safe, pinned path, PUBLIC EXECUTE revoked, and no application-role grant. The expected catalog therefore contains 40 functions.
4. **Version state.** Migration 0002 updates `taskq.meta['contract_version']` from JSON string `"0.1"` to `"0.1.1"`; capabilities remain unchanged. Protocol major stays v1.
5. **Required tests.** T2 includes omitted/null/min/max/out-of-range vectors for every public bounded parameter and ASCII/multibyte diagnostic vectors proving byte caps and successful settlement. T8 proves 0001→0002 upgrade, fresh-chain equivalence, immutable checksums, and old/new contract negotiation behavior.

## 10. Contract patch 0.1.2 — ADR-013 (2026-07-18)

Migration `0003_contract_0_1_2.sql` applies one additive projection correction without adding or removing a public function:

1. **Append-only claim attribute.** `ALTER TYPE taskq.claimed_job ADD ATTRIBUTE lease_seconds integer` appends the field after `step_key`. `lease_expires_at` remains unchanged. The complete shape is frozen in §0.
2. **Exact effective duration.** `claim_jobs` appends its non-null `v_lease` to each returned `claimed_job`; this is the same 15–86400 value used to update `jobs.lease_expires_at` and insert `job_attempts.lease_seconds`.
3. **Clock boundary.** Workers schedule heartbeats from `lease_seconds` on a monotonic timer and never compute a duration as `lease_expires_at - local_now()`.
4. **Version state.** Migration 0003 updates `taskq.meta['contract_version']` from JSON string `"0.1.1"` to `"0.1.2"`; capabilities and Protocol major v1 are unchanged.
5. **Required tests.** `verify()` and the independent catalog-parity projection assert the ordered 15-attribute composite. T2 proves queue-default, task-stamped, and explicit claim-override durations match the job row, attempt row, and returned projection. T8 proves a fresh chain and the full immutable `0001 → 0002 → 0003` upgrade on PostgreSQL 16 and 18.

## 11. Contract patch 0.1.3 — ADR-019 (2026-07-20)

Migration `0004_read_models.sql` is the sole implementation vehicle for these additive changes:

1. **Profile version.** It adds `taskq.queues.profile_version bigint NOT NULL DEFAULT 1` and
   replaces the existing `ensure_queue(text,jsonb,text)` body without changing its identity. The
   version increments only when canonical profile fields change, never for pause/resume.
2. **Exact observer surface.** It creates the `job_list_item`, `job_page`, `queue_profile`, and
   `queue_profile_update` composites plus hardened observer `list_jobs` and `get_queue_profile`
   functions defined in §4/§4.1. No observer base-table SELECT grant is added.
3. **Conditional profile update.** It creates operator
   `update_queue_profile(text,jsonb,text,bigint)`, returning only `updated` + canonical profile/new
   version or `profile_version_conflict` + current version. Existing `ensure_queue` stays callable.
4. **Per-view gate and indexes.** The migration records the three named H-08 capabilities. It may
   activate only a view with its committed PG16 and PG18 B9 evidence; every other view remains
   `TQ501`. Any new queue-leading running/finished index is likewise conditional on that evidence;
   `ready` begins with the existing claim index.
5. **Version and parity.** It updates `taskq.meta['contract_version']` to `"0.1.3"`. `verify()`,
   catalog parity, fresh install, and the full `0001 → 0002 → 0003 → 0004` upgrade chain must assert
   every new type, function, grant, column, capability, index, and per-view negative disposition on
   PostgreSQL 16 and 18. Direct SQL and HTTP must return the same bounded projections.
