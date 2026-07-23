# taskq — 0.1.x / 0.2.x Function Manifest

> **Status:** CANONICAL for SQL contract 0.2.3 — 2026-07-23. Closes R2-08 and incorporates ADR-012/013/019/021/023/024/026/027/029: every function the contract ships is listed here with identity, grants, raises, and an executable body (or a pointer to its normative body in the Unified Spec §5/§11, Durable Admission Reservation Specification, or Native Orchestration Specification as amended here). Migrations 0001 through 0012 derive from THIS document; `verify()` compares the live catalog against this manifest (ADR-011 §4). A function not listed here does not exist in 0.2.3 — no success-returning stubs.
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
CREATE TYPE taskq.admission_reservation AS (
    outcome text, handle uuid, job_id uuid,
    reservation_expires_at timestamptz, retry_after_seconds integer,
    receipt jsonb, receipt_expires_at timestamptz
);
CREATE TYPE taskq.admission_finish_result AS (
    outcome text, job_id uuid, receipt jsonb, receipt_expires_at timestamptz
);
CREATE TYPE taskq.admission_cancel_result AS (
    outcome text, job_id uuid, receipt jsonb, receipt_expires_at timestamptz
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
| `taskq._enqueue_followup(parent_job_id,parent_queue,spec,spec_index)` | owner-only 0.2 body specified by §15; absent before migration 0008 |

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
| `taskq.reserve_admission(p_queue, p_idempotency_key, p_intent_hash, p_handle, p_reservation_ttl_seconds, p_receipt_ttl_seconds)` | §14 / Durable Admission Specification §4.1 | TQ001, TQ409, TQ422 | T2-ADM, T3-ADM-RACE |
| `taskq.finish_admission(p_queue, p_idempotency_key, p_handle, p_job, p_receipt)` | §14 / Durable Admission Specification §4.2 | TQ001, TQ409, TQ422, TQ429, TQ500 | T2-ADM, T3-ADM-RACE |
| `taskq.cancel_admission(p_queue, p_idempotency_key, p_handle)` | §14 / Durable Admission Specification §4.3 | TQ001, TQ409, TQ422 | T2-ADM, T3-ADM-RACE |

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
| `taskq.complete_job(...)` | spec §5.5 (v1.6), ADR-007/024 lossless follow-ups, verb-aware replay (H-03) | TQ422; TQ500 only for an inconsistent derived-key holder; TQ501 only when connected to a supported pre-0008 database | T2-COMPLETE, T3-SETTLE, T3-FOLLOWUP |
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
queue/view are `TQ422`. It then establishes the queue exists before checking a
view capability: an unknown queue raises the established `TQ001` marker for
every view. Each existing-queue view then requires its exact capability:
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
column. An existing queue with an empty active view returns the fixed empty SQL
page. Direct SQL and HTTP therefore share the same `unknown → TQ001`,
`existing + inactive → TQ501`, and `existing + empty active → 200 empty`
dispositions. Direct SQL returns precisely this page composite: **a direct SQL client must
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

Before 0008, `_enqueue_followup` is absent. In 0.2.0 it is activated exactly as §15 defines.
Before 0009, `cancel_dependents`, workflow cancellation/finalizer helpers,
`create_workflow`/`seal_workflow`/`cancel_workflow`, and the workflow authorization projection are
absent. In 0.2.1 they activate exactly as §16 defines. Before 0010, schedule
relations/composites/functions are absent; in 0.2.2 they activate exactly as §17 defines. All
archive objects/functions, every list
form **other than ADR-019's exact queue-scoped `list_jobs` page**, and every later 0.2/0.3
composite field remain absent. SQL contract 0.2.2's read-model surface still contains only the
three finite H-08 views; general/all-queue, arbitrary-filter, payload, workflow and timeline lists
remain absent. ADR-023's admission functions are the separate producer surface defined in §14.
T2 asserts each installed contract's catalog contains exactly its manifest function set.

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
9. **Follow-up collision classification:** §15.5's inconsistent derived-key holder is the existing
   registered, non-retryable `TQ500` internal error, not deterministic `TQ422`. It indicates that
   database state already violates the derived-child invariant. The error unwinds the complete
   transaction and exposes no partial parent or child mutation; it adds no SQL identity or wire shape.

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
6. **Supported-runtime sets.** Per ADR-020, a runtime declares a closed set of supported SQL
   contract revisions and startup uses exact membership in that set; it never loosens compatibility
   through a prefix or version range. The 0004 bridge runtime declares `{0.1.2, 0.1.3}` while the
   pre-bridge `{0.1.2}` runtime continues to reject `0.1.3`. The database reports its exact revision;
   this rule adds no SQL function, grant, capability activation, or wire field.

## 12. Contract patch 0.1.4 — ADR-021 (2026-07-20)

Migration `0005_read_model_conformance.sql` is an immutable conformance repair:

1. **Unknown queue before capability.** It replaces only the existing
   `taskq.list_jobs(text,text,int,jsonb)` body. After public-input validation,
   an unknown queue raises the established `TQ001` marker before any view-capability
   check. Existing inactive views continue to raise `TQ501` with
   `read_model_view_inactive`; existing empty active views return the fixed empty page.
   No function identity, composite, grant, index, or error channel changes.
2. **Version and evidence.** It updates `taskq.meta['contract_version']` to
   `"0.1.4"`. `verify()`, catalog parity, fresh install, and the complete
   `0001 → 0002 → 0003 → 0004 → 0005` chain assert the three-case vector on
   PostgreSQL 16 and 18.
3. **Supported runtime and activation floor.** The bridge runtime declares the closed set
   `{0.1.2, 0.1.3, 0.1.4}`; the historical `{0.1.2}` runtime continues to reject
   both newer metadata revisions. Activating any `read_model_*` capability requires
   metadata 0.1.4 or later. This migration does not itself activate a view.
4. **SQL parity.** A direct SQL client must not get a wider projection or a different
   unknown/inactive/empty disposition merely because it bypasses HTTP.

## 13. Ready read-model activation — migration 0006 (2026-07-21)

Migration `0006_activate_ready_read_model.sql` is the immutable, metadata-only activation
vehicle required by ADR-019 decision #3. It does not change the SQL contract revision, function
catalog, composites, grants, indexes, or Protocol surface.

1. **Precondition and exact result.** The migration requires the database metadata revision to be
   exactly `0.1.4`, then writes the complete capability value
   `{"active":["read_model_list_ready"]}`. `read_model_list_running` and
   `read_model_list_finished` are therefore inactive, rather than merely not asserted active.
2. **Evidence and scope.** The sole activation is justified by the committed B9 evidence
   `7fe2c6b` (`bench: add read model B9 evidence`): `ready` is bounded on PostgreSQL 16 and 18
   using the existing claim index; `running` and `finished` were rejected and remain `TQ501`.
   This migration contains no manual-DML substitute or configuration switch.
3. **Verification and upgrade transition.** `verify()` compares the capability set by exact
   equality. Fresh installation and the full `0001 → 0002 → 0003 → 0004 → 0005 → 0006` chain on
   PostgreSQL 16 and 18 prove that `ready` returns `TQ501` at 0005 and its bounded 200 page after
   0006.
4. **Deactivation.** A future deactivation requires its own immutable metadata migration
   (a later metadata successor); operations must never edit `taskq.meta` manually to reverse this
   activation.

## 14. Contract patch 0.1.5 — ADR-023 (2026-07-22)

Immutable migration `0007_admission_reservations.sql` is the sole implementation vehicle for the
durable two-phase admission primitive.

1. **Private ledger and job link.** It creates private table `taskq.admissions`, owned by
   `taskq_owner` with no application-role table grants. `(queue, idempotency_key)` is unique;
   key length is 1–255; `intent_hash` is exactly 64 lowercase hexadecimal SHA-256 characters;
   `state` is `reserved | admitted | cancelled`; handle, database-clock expiry, immutable receipt
   policy/data, database-computed canonical finish SHA-256, admitted job id, and lifecycle
   timestamps are stored. It appends nullable unique
   `taskq.jobs.admission_id` referencing the ledger. Ordinary enqueue leaves this column null and
   retains its existing active-only `jobs_idem_uq` semantics.
2. **Exact SQL identities and grants.** The three new identities are:

   ```text
   taskq.reserve_admission(text,text,text,uuid,integer,integer)
     RETURNS taskq.admission_reservation
   taskq.finish_admission(text,text,uuid,jsonb,jsonb)
     RETURNS taskq.admission_finish_result
   taskq.cancel_admission(text,text,uuid)
     RETURNS taskq.admission_cancel_result
   ```

   Their argument names/defaults are respectively `(p_queue, p_idempotency_key, p_intent_hash,
   p_handle, p_reservation_ttl_seconds DEFAULT 300, p_receipt_ttl_seconds DEFAULT 2592000)`,
   `(p_queue, p_idempotency_key, p_handle, p_job, p_receipt DEFAULT '{}'::jsonb)`, and
   `(p_queue, p_idempotency_key, p_handle)`. All are volatile `plpgsql SECURITY DEFINER`, owner
   `taskq_owner`, pinned path, PUBLIC-revoked, and EXEC only to `taskq_producer`.
3. **Reserve authority.** Reserve validates queue before state, locks the unique key, and follows
   Protocol §2.6 exactly. Same-handle replay never extends its stamped expiry. A competing handle
   never receives the current handle, job command, or receipt. Expired/cancelled rows may be
   reacquired. An admitted same-intent row returns its durable result until cleanup eligibility;
   a different intent on an unexpired reservation or retained admission is `TQ409
   idempotency_mismatch`; expired/cancelled rows may begin a new intent.
4. **Atomic finish.** Finish locks and validates the current unexpired handle, strictly validates
   the fixed 0.1.5 job JSON and 2KB receipt, invokes existing enqueue validation/backpressure with
   no ordinary idempotency key, links the one created job, and stores the receipt plus
   database-computed SHA-256 of canonical `{job, receipt}` JSON atomically. Same-handle,
   same-content post-commit replay returns `existed`; changed content is `TQ409 finish_mismatch`.
   A rollback, validation/backpressure error,
   cancellation, stale handle, or expiry exposes no partial job/admission state.
5. **Cancellation and non-confusion.** Cancel affects only an unadmitted current reservation.
   Its outcomes are `cancelled | already_cancelled | expired | already_admitted`; it never invokes
   job cancellation/release or changes worker budgets. A wrong handle is `TQ409
   reservation_conflict`.
6. **Retention and bounded cleanup.** Receipt TTL is 3,600–31,536,000 seconds. An admitted row is
   cleanup-eligible only after its database-stamped receipt expiry and after its hot job row is
   absent. The existing `taskq.janitor()` performs an index-backed bounded pass; expired/cancelled
   unadmitted rows receive a diagnostic grace period. No manual DML is a supported cleanup or
   deactivation mechanism.
7. **Metadata and compatibility.** Migration 0007 requires contract metadata exactly `0.1.4`,
   updates it to `0.1.5`, and replaces capability metadata by exact equality with
   `{"active":["admission_reservations","read_model_list_ready"]}`. The route-free bridge runtime
   declares `{0.1.2, 0.1.3, 0.1.4, 0.1.5}` while preserving the historical pre-bridge rejection.
   Applying 0007 raises the database rollback floor to that bridge; production application is a
   separate host decision.
8. **Evidence.** `verify()`, independent catalog parity, fresh install, and full 0001→0007 upgrade
   assert the table/column/index/composite/function/grant/metadata surface on PostgreSQL 16 and 18.
   T2/T3/T6 cover every outcome plus reserve/finish/cancel races, response loss, expiry takeover,
   backpressure rollback, retention, authorization order, strict wire models, SQL/HTTP parity,
   and bounded cleanup.

## 15. Contract 0.2.0 — ADR-024 native follow-ups (2026-07-22)

Immutable migration `0008_followups.sql` is the sole activation vehicle. It requires contract
metadata exactly `0.1.5` and the exact 0007 capability set before changing anything.

1. **Public identity.** No public SQL function is added or removed. The existing identity remains:

   ```text
   taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) -> taskq.settle_result
   EXEC taskq_runner; raises TQ422; TQ500 only for an inconsistent derived-key holder
   ```

   On a pre-0008 supported database, non-empty follow-ups retain the existing `TQ501` inactive
   behavior. On 0.2.0, capability `followups` selects the ADR-007/024 body.

2. **Private helper.** Migration 0008 adds exactly:

   ```text
   taskq._enqueue_followup(uuid,text,jsonb,integer)
     -> TABLE(job_id uuid, created boolean)
   arguments: parent_job_id, parent_queue, closed child spec, one-based spec index
   owner taskq_owner; SECURITY DEFINER; pinned search_path; VOLATILE; PUBLIC revoked;
   no application-role EXECUTE; raises TQ422; TQ500 only for an inconsistent derived-key holder
   ```

   It is not a protocol command. It validates the closed Protocol §2.7 child object, resolves the
   inherited queue, derives `chain:<parent_job_id>:<step>`, performs ordinary enqueue validation
   and insertion while omitting only producer `max_depth`, emits the ordinary enqueue event/notify,
   and returns truthful `created | existed`. It accepts no caller-supplied key, parent, workflow,
   dependency, status, actor, fence, concurrency/affinity key or backoff override from `p_spec`.

3. **Validate before mutation.** `complete_job` lock-and-read/replay handling remains first. For a
   live matching attempt it validates: JSON array, count at most 20, object items, exact allowed
   key set, required/unique step grammar, required job type, JSON object payload/headers, all
   ordinary bounds, and every resolved queue's existence. Any deterministic error is `TQ422`
   before parent, attempt, event or child mutation.

4. **One transaction.** Parent and attempt success, succeeded event, every helper call and every
   child event/notify commit together. No savepoint catches a child failure. A residual failure
   unwinds all effects. Same-verb replay returns `already_settled` before validation/helper calls;
   stale and cross-verb outcomes retain H-03 behavior.

5. **Depth and key rules.** Follow-ups are continuations of accepted work, so the private helper
   does not run producer `max_depth`. All other profile and field limits apply. A duplicate derived
   active key resolves through the ordinary authoritative convergence path and returns `existed`;
   any inconsistent holder is a registered internal failure, never a second child.

6. **Metadata and compatibility.** Migration 0008 replaces metadata by exact equality:

   ```json
   {"active":["admission_reservations","followups","read_model_list_ready"]}
   ```

   and sets contract version `0.2.0`. The bridge set is exactly
   `{0.1.2, 0.1.3, 0.1.4, 0.1.5, 0.2.0}`. It exposes typed follow-ups only when capability metadata
   contains `followups`; applying 0008 raises the database rollback floor to that bridge.
   Deactivation requires a later immutable metadata migration.

7. **Authorization parity.** Direct SQL uses the trusted runner-role boundary. HTTP authenticates,
   projects and authorizes the parent queue before body decode, then authorizes `run` for every
   distinct resolved child queue before calling SQL. Any denial makes zero settlement/child change.

8. **Evidence.** `verify()` and independent catalog parity assert the exact helper identity,
   hardening, grants and metadata. Fresh and 0001→0008 chains run on PostgreSQL 16 and 18. T2/T3/T6
   cover every validation branch, 0/1/20/21 children, same/cross-queue authorization, response loss,
   stale/cross-verb replay, child collision, concurrent completion, Nth-child rollback, depth
   exemption, fake/SQL/HTTP graph parity, resources and artifact isolation.

## 16. Contract 0.2.1 — ADR-026 sealed workflows and dependencies (2026-07-23)

Immutable migration `0009_workflows.sql` is the sole activation vehicle. It
requires contract metadata exactly `0.2.0`, the exact 0008 capability set, and
empty inactive workflow/dependency relations before changing anything.

1. **Composite types.** Migration 0009 adds exactly:

   ```text
   taskq.workflow_result AS (
     outcome text,
     workflow_id uuid,
     status text
   )

   taskq.workflow_auth_projection AS (
     workflow_id uuid,
     declared_queues text[]
   )
   ```

   H-02 additive-only evolution applies. No key, params, actor, cancellation
   reason, member id, dependency id, fence or diagnostic appears in either
   projection.

2. **Application-callable identities.** The new exact functions are:

   ```text
   taskq.create_workflow(text,text,jsonb,text[],text)
     arguments: p_workflow_key, p_kind, p_params, p_declared_queues, p_actor
     RETURNS taskq.workflow_result
     EXEC taskq_producer; raises TQ001, TQ409, TQ422

   taskq.seal_workflow(uuid,text)
     arguments: p_workflow_id, p_actor
     RETURNS taskq.workflow_result
     EXEC taskq_producer; raises TQ001

   taskq.cancel_workflow(uuid,text,text)
     arguments: p_workflow_id, p_actor, p_reason
     RETURNS taskq.workflow_result
     EXEC taskq_operator; raises TQ001, TQ422

   taskq.get_workflow_authorization_projection(uuid)
     arguments: p_workflow_id
     RETURNS taskq.workflow_auth_projection
     EXEC taskq_observer; raises TQ001
   ```

   Every identity is volatile except the stable authorization projection; all
   are `SECURITY DEFINER`, owned by `taskq_owner`, path-pinned and
   PUBLIC-revoked. There is no workflow list/detail function in 0.2.1.

3. **Owner-private identities.** Migration 0009 also adds:

   ```text
   taskq.cancel_dependents(uuid,text,integer) RETURNS integer
   taskq.advance_workflow_cancellations(integer) RETURNS integer
   taskq.finalize_dep_stragglers(integer) RETURNS integer
   taskq.finalize_workflows(integer) RETURNS integer
   ```

   Arguments are respectively `(p_job_id, p_reason, p_limit DEFAULT 100)` and
   `(p_limit DEFAULT 100)` for each bounded pass. They are volatile
   `plpgsql SECURITY DEFINER`, owner-only, path-pinned, PUBLIC-revoked, have no
   application-role EXECUTE and are not Protocol commands.

4. **Stored shape and indexes.** `taskq.workflows` appends non-null sorted
   `declared_queues text[]`, plus nullable `sealed_at`, `sealed_by`,
   `cancel_requested_at`, `cancel_requested_by`, and byte-safe
   `cancel_reason`. `taskq.jobs` appends nullable
   `workflow_intent_hash text`, constrained to lowercase SHA-256 hex and to be
   present exactly when workflow id and step key are present. Migration 0009
   adds a permanent unique `(workflow_id, step_key)` index and replaces the
   inactive workflow indexes with exact bounded-finalizer/member-state indexes.
   Raw DML remains owner-only.

5. **Creation and sealing.** Protocol §2.8's bounds, replay identity and
   outcomes are exact. Creation validates every queue before insertion and
   stores a canonical sorted set. A key conflict compares kind, canonical params
   and queues under the authoritative row; exact replay returns `existed`,
   mismatch raises `TQ409`. Seal locks the workflow row, stamps database time
   once, and immediately finalizes an empty/all-terminal graph. Repeated seal
   returns `already_sealed`.

6. **Existing enqueue activation.** The public identity remains:

   ```text
   taskq.enqueue(
     text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
     text,integer,integer,uuid[],uuid,text,uuid,jsonb
   ) RETURNS TABLE(job_id uuid, created boolean)
   ```

   Migration 0009 replaces only its body. Non-null workflow fields require
   capability `dependencies_workflows`; pre-0009 behavior remains `TQ501`.
   Under 0.2.1 it enforces Protocol §2.8, locks workflow then distinct parent
   rows in ascending id order, validates all state before insert, stores the
   canonical intent hash, inserts only live edges, and returns truthful
   `created | existed`. Same-step replay is resolved before the sealed check.
   Mismatch/sealed/terminal-dependency conflicts are registered `TQ409`;
   unknown workflow/parent is `TQ001`; malformed graph input is `TQ422`;
   ordinary depth/convergence outcomes remain `TQ429`/`TQ500`.

7. **Settlement and convergence body replacements.** Migration 0009 replaces
   `complete_job` to delete direct satisfied edges, decrement exact
   `pending_deps`, promote zero-pending members once and notify each affected
   queue at most once. Every terminal-failure/cancellation path in
   `fail_job`, `snooze_job`, `release_job`, `cancel_running_job`,
   `cancel_job`, and `reap_job` advances the bounded dependent-cancellation
   frontier. `tick` runs bounded cancellation, dependency-straggler and sealed
   workflow-finalizer passes under its existing savepoint isolation.
   Workflow cancellation is a durable intent and one bounded first pass, never
   forged settlement.

8. **Terminality and redrive.** Only sealed workflows finalize. Cancellation
   intent wins; otherwise failed dominates cancelled, which dominates
   all-success. Empty sealed succeeds. Final status never reopens.
   `redrive_job` raises registered non-retryable `TQ409` for any workflow
   member and `redrive_failed` excludes workflow members; corrected graphs use
   a new workflow key.

9. **Metadata and compatibility.** Migration 0009 replaces metadata by exact
   equality:

   ```json
   {"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready"]}
   ```

   and sets contract version `0.2.1`. The bridge set is exactly
   `{0.1.2, 0.1.3, 0.1.4, 0.1.5, 0.2.0, 0.2.1}` and exposes no workflow
   surface without exact capability metadata. Applying 0009 raises the database
   rollback floor to that bridge. Deactivation requires an immutable migration.

10. **Authorization and evidence.** Direct SQL retains the trusted capability
    boundary. HTTP follows Protocol §2.8's all-declared-queue ordering with zero
    lookup/mutation before complete authorization. `verify()`, independent
    catalog parity, fresh/full 0001→0009 chains, bridge negatives, exact
    metadata equality, every create/seal/cancel/enqueue outcome, fan-out,
    fan-in, diamond, sibling and settle/enqueue/seal/cancel races, bounded
    cascade/finalization plans, SQL/HTTP/fake parity, resource cleanup and
    installed artifacts run on PostgreSQL 16 and 18.

## 17. Contract 0.2.2 — ADR-027 native schedules (2026-07-23)

Immutable migration `0010_schedules.sql` is the sole activation vehicle. It
requires contract metadata exactly `0.2.1`, the exact 0009 capability set and
no schedule relations before changing anything.

1. **Composite types.** Migration 0010 adds exactly:

   ```text
   taskq.schedule_profile AS (
     schedule_id uuid,
     name text,
     target jsonb,
     recurrence jsonb,
     catchup_policy text,
     max_catchup integer,
     state text,
     next_fire_at timestamptz,
     last_fire_at timestamptz,
     version bigint
   )

   taskq.schedule_auth_projection AS (
     name text,
     queue text
   )

   taskq.schedule_write_result AS (
     outcome text,
     profile taskq.schedule_profile
   )

   taskq.schedule_claim AS (
     schedule_id uuid,
     name text,
     definition_version bigint,
     as_of timestamptz,
     target jsonb,
     recurrence jsonb,
     catchup_policy text,
     max_catchup integer,
     initialized boolean,
     next_fire_at timestamptz,
     token uuid,
     lease_seconds integer
   )

   taskq.schedule_claim_batch AS (
     state text,
     schedules taskq.schedule_claim[]
   )

   taskq.schedule_action_result AS (
     outcome text,
     replayed boolean,
     schedule_id uuid,
     jobs_enqueued integer,
     next_fire_at timestamptz,
     state text,
     version bigint
   )
   ```

   H-02 additive-only evolution applies. Profile/auth composites expose no
   claim token, actor, diagnostic, definition hash, lease/retry metadata or
   maintenance internals.

2. **Operator identities.** The new exact functions are:

   ```text
   taskq.put_schedule(text,jsonb,text,bigint)
     arguments: p_name, p_definition, p_actor, p_expected_version DEFAULT NULL
     RETURNS taskq.schedule_write_result
     EXEC taskq_operator; raises TQ001, TQ409, TQ422

   taskq.get_schedule(text)
     arguments: p_name
     RETURNS taskq.schedule_profile
     EXEC taskq_operator; raises TQ001

   taskq.retire_schedule(text,bigint,text)
     arguments: p_name, p_expected_version, p_actor
     RETURNS taskq.schedule_write_result
     EXEC taskq_operator; raises TQ001, TQ409, TQ422

   taskq.get_schedule_authorization_projection(text)
     arguments: p_name
     RETURNS taskq.schedule_auth_projection
     EXEC taskq_operator; raises TQ001
   ```

   `p_definition` is the exact Protocol §2.9 PUT object. It never accepts the
   reserved maintenance target. Expected version omission has create/exact
   replay semantics; a positive value has compare-and-set update semantics.
   Every identity is volatile except the stable authorization projection and
   profile read; all are `SECURITY DEFINER`, owner-owned, path-pinned and
   PUBLIC-revoked.

3. **Housekeeper identities.** The new exact direct-SQL-only functions are:

   ```text
   taskq.claim_schedules(text,integer,integer)
     arguments: p_worker_id, p_limit DEFAULT 10, p_lease_seconds DEFAULT 60
     RETURNS taskq.schedule_claim_batch
     EXEC taskq_housekeeper; raises TQ422

   taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
     arguments: p_schedule_id, p_token, p_definition_version,
                p_occurrences, p_next_fire_at
     RETURNS taskq.schedule_action_result
     EXEC taskq_housekeeper; raises TQ001, TQ422, TQ500

   taskq.schedule_error(uuid,uuid,bigint,text,integer)
     arguments: p_schedule_id, p_token, p_definition_version, p_error,
                p_retry_seconds DEFAULT 30
     RETURNS taskq.schedule_action_result
     EXEC taskq_housekeeper; raises TQ001, TQ422
   ```

   Claim limits are `1..100`, leases `5..300`, retry seconds `1..3600`, tokens
   are non-nil and diagnostics are byte-truncated to 2,048. Action outcome is
   one of `initialized|fired|skipped|error_recorded|stale`; exact response
   replay returns the original outcome with `replayed=true`. A stale
   lease/token/version is data outcome `stale`, not success and not an
   unregistered error.

4. **Stored shape and raw privilege wall.** `taskq.schedules` stores id/name,
   target and recurrence as canonical bounded JSONB, catch-up settings,
   `active|paused|retired` state, initialization/due/fire/version fields,
   database claim/retry fields, byte-bounded diagnostics, actors/timestamps,
   and one previous action token/hash/result for response replay. It has unique
   name and bounded due-claim indexes. `taskq.schedule_occurrences` stores
   schedule id, due instant and optional job id with permanent unique
   `(schedule_id, due_at)` identity. Both relations are owner-only; no
   application role or PUBLIC receives raw relation privileges.

5. **Definition lifecycle.** Protocol §2.9's grammar, bounds, profile, ETag
   matrix and authorization ordering are exact. Create stamps database
   `next_fire_at`, version 1 and uninitialized state. Exact replay returns
   `unchanged`; mismatch and stale update use only their registered reason plus
   current version. Mutation of a retired definition uses only
   `schedule_retired` plus current version; exact DELETE replay is
   `already_retired`. A real update increments once, invalidates any claim, and
   resets compile-first state. Pausing preserves recurrence position; resuming
   increments version, invalidates any claim and resets compile-first at
   database time. Retire is permanent, idempotent and never deletes identity.

6. **Deterministic recurrence boundary.** SQL owns due truth, claims and
   atomicity; the package evaluator owns only the closed interval/cron
   calculation from `schedule_claim.as_of` and `next_fire_at`. Protocol §2.9's
   grammar and DST rules are normative. Initial/resume compilation produces no
   occurrence. Client wall time is never an evaluator input.

7. **Fire validation and atomicity.** `fire_schedule` locks the schedule and
   first resolves exact previous-action replay. A live action requires matching
   token/version and unexpired database lease. It validates policy-shaped
   cardinality/order, all occurrence instants no later than claim `as_of`, and
   strict recurrence advancement. `fire_all` starts at authoritative
   `next_fire_at`; `skip` is empty; `fire_once` contains exactly one due
   instant; initialization is empty. Every job occurrence uses the permanent
   occurrence identity and idempotency key derived from schedule id + UTC due
   instant. All jobs, events, occurrences and schedule advancement commit
   together. Any failure leaves all unchanged.

8. **Finite janitor exception.** Migration 0010 alone seeds
   `taskq-janitor-daily` as reserved `maintenance:janitor`, cron
   `0 3 * * *` UTC, `fire_once`, `max_catchup=1`. Operator functions reject
   maintenance targets and the reserved name. A successful due fire calls only
   the existing bounded owner function `taskq.janitor()` in the same action;
   no dynamic function selection exists. Migration 0010 replaces `taskq.tick`
   so it no longer calls `claim_janitor_due`; the owner-only compatibility
   helper remains inert. Seed and trigger replacement are one transaction.

9. **Error and response-loss behavior.** `schedule_error` never advances
   recurrence truth. It records a bounded error, clears the claim and stamps
   `retry_not_before = now() + retry_seconds`. Both action functions store a
   canonical action-input hash and bounded result; replay of the exact last
   token/input returns the original result with `replayed=true`, while changed
   input returns `stale`. No duplicate
   occurrence or janitor pass can result from response loss.

10. **Metadata and compatibility.** Migration 0010 replaces metadata by exact
    equality:

    ```json
    {"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready","schedules"]}
    ```

    and sets contract version `0.2.2`. The bridge set is exactly
    `{0.1.2, 0.1.3, 0.1.4, 0.1.5, 0.2.0, 0.2.1, 0.2.2}` and exposes no schedule
    surface/loop without exact capability metadata. Applying 0010 raises the
    database rollback floor to that bridge. Deactivation requires a later
    immutable metadata migration, never manual DML.

11. **Evidence.** `verify()` and an independent catalog/privilege oracle assert
    the exact function/type/relation/index/metadata set. Fresh/full 0001→0010
    chains, bridge negatives, all definition/action outcomes, interval/cron and
    DST edges, all catch-up policies, response loss, skewed clocks, concurrent
    claims/fires/updates, one-only janitor takeover, bounded plans, SQL/HTTP/fake
    parity, resource cleanup and installed artifacts run on PostgreSQL 16/18.

## 18. Contract 0.2.3 — ADR-029 finite projections (2026-07-23)

Migration `0011_finite_projections.sql` requires exact contract 0.2.2 and its
exact capability set. It adds bounded backing while leaving every new FR-02D
capability inactive. Metadata-only migration
`0012_activate_finite_projections.sql` requires exact 0.2.3 plus the 0011
catalog and activates only independently proven views.

1. **Composite types.** Migration 0011 adds exactly:

   ```text
   taskq.workflow_read_profile AS (
     workflow_id uuid, kind text, status text, sealed boolean,
     cancel_requested boolean, declared_queues text[],
     created_at timestamptz, updated_at timestamptz, finished_at timestamptz
   )

   taskq.workflow_state_counts AS (
     blocked bigint, queued bigint, running bigint,
     succeeded bigint, failed bigint, cancelled bigint
   )

   taskq.workflow_member_projection AS (
     job_id uuid, queue text, job_type text, step_key text, status text,
     outcome text, pending_deps integer, attempt_count integer,
     failure_count integer, created_at timestamptz, scheduled_at timestamptz,
     started_at timestamptz, finished_at timestamptz, updated_at timestamptz
   )

   taskq.workflow_page AS (
     as_of timestamptz, profile taskq.workflow_read_profile,
     counts taskq.workflow_state_counts,
     items taskq.workflow_member_projection[], next_after uuid
   )
   ```

   H-02 applies. No composite contains workflow key/params/creator, payload,
   headers, result, progress, error, attempt/event data, fence, token, worker,
   diagnostic or provider metadata.

2. **Public identity.**

   ```text
   taskq.get_workflow_page(uuid,integer,uuid)
     arguments: p_workflow_id, p_limit DEFAULT 50, p_after DEFAULT NULL
     RETURNS taskq.workflow_page
     EXEC taskq_observer; raises TQ001, TQ422, TQ501
   ```

   The function is `STABLE SECURITY DEFINER`, owner-owned, path-pinned and
   PUBLIC-revoked. It validates limit `1..100`, checks capability
   `read_model_workflow`, reads one exact workflow and at most `limit + 1`
   members ordered by UUID ascending, and stamps database `as_of`. An unknown
   workflow is TQ001 regardless of capability state. A direct SQL client must
   not get a wider projection merely because it bypasses HTTP.

3. **Private exact counts.** Owner-private relation
   `taskq.workflow_member_counts` has primary key/FK `workflow_id` with cascade
   and six non-negative bigint columns for
   `blocked|queued|running|succeeded|failed|cancelled`. Owner-private
   `taskq.update_workflow_member_counts()` returns trigger, is volatile,
   `SECURITY DEFINER`, path-pinned and PUBLIC-revoked. It adjusts exactly the
   old/new workflow-status buckets on job insert, delete, workflow change or
   status change. Migration 0011 backfills before creating the trigger.
   No application role receives relation or function privilege.

4. **Indexes.** Migration 0011 adds exactly:

   ```text
   taskq_jobs_running_page_idx
     ON taskq.jobs(queue, started_at DESC, id DESC)
     WHERE status = 'running'
   taskq_jobs_finished_page_idx
     ON taskq.jobs(queue, finished_at DESC, id DESC)
     WHERE status IN ('succeeded','failed','cancelled')
   taskq_jobs_workflow_page_idx
     ON taskq.jobs(workflow_id, id)
     WHERE workflow_id IS NOT NULL
   ```

5. **Queue views.** Existing `taskq.list_jobs` identity, fields, cursor and
   outcomes remain byte-for-byte. Migration 0011 adds indexes only. Migration
   0012 may add existing capabilities `read_model_list_running` and
   `read_model_list_finished` only when each has a PostgreSQL 16/18 million-row
   B9 proof. A rejected view stays independently TQ501.

6. **Metadata and compatibility.** The ADR-020 supported set becomes exactly
   `{0.1.2,0.1.3,0.1.4,0.1.5,0.2.0,0.2.1,0.2.2,0.2.3}` before migration 0011.
   Migration 0011 sets version 0.2.3 while retaining exactly:

   ```json
   {"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready","schedules"]}
   ```

   After proof, migration 0012 replaces metadata by exact equality with the
   preceding set plus only the proven subset of
   `read_model_list_running`, `read_model_list_finished`, and
   `read_model_workflow`. Activation and deactivation are immutable metadata
   migrations, never manual DML.

7. **Evidence.** `verify()` and an independent oracle assert exact
   types/functions/tables/triggers/indexes/grants/metadata. Fresh/full chains
   on PostgreSQL 16/18 prove counter backfill and every status transition,
   concurrent member changes, empty and paginated workflows, cursor binding,
   unknown/inactive outcomes, redaction, SQL/HTTP/raw-state parity,
   authorization-before-decode, bounded plans and artifacts. No timeline,
   arbitrary filter, raw relation grant or all-queue projection exists.
