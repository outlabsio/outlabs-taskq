-- outlabs-taskq — migration 0024: flow enforcement, producer layer (inactive)
-- SQL contract 0.4.1 — Wave 2b slice 1 of 3 per
-- "Task Queue 0.5 Flow Enforcement Specification" (S5 staging amendment:
-- 0024 producer layer, 0025 claim verdicts + profile CRUD, 0026 activation).
--
-- Installs: flow state tables (queue_flow / flow_limits / flow_state), the
-- enforcement columns (jobs.expires_at, jobs.flow_key; queues.max_running,
-- claim_rate_per_minute, claim_burst, ramp_seconds, default_ttl_seconds,
-- backpressure_retry_seconds, notify_mode; schedules.smear_seconds),
-- taskq.try_enqueue (typed producer verdict with the L4 check-order fix:
-- active-idempotency BEFORE the depth gate, so reconciliation replay works
-- at max_depth), taskq.expire_ttl + its tick pass, redrive smearing, and the
-- resume-queue ramp stamp. New surfaces (try_enqueue, set_flow_limit) raise
-- TQ501 until the flow_control capability activates (0026); everything else
-- is per-queue NULL-gated and changes nothing for existing queues.
--
-- Recorded amendments: smeared bulk redrive reuses redrive_job — its per-row
-- NOTIFYs are identical (channel, empty payload) within one transaction and
-- PostgreSQL collapses them to a single wake, so no notify storm exists to
-- avoid. TTL settlement uses outcome 'expired_ttl' with event reason data;
-- profile CRUD exposure of the new columns lands in 0025 (admin UPDATE and
-- the harness set them until then).

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.4.0"'::jsonb THEN
        RAISE EXCEPTION '0024 requires SQL contract 0.4.0, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0024 requires the exact 0023 capability set, found %', v_capabilities;
    END IF;
END $$;

-- ============================================================================
-- 1. Columns (all NULL-gated; a 0.4.0-shaped queue is unchanged)
-- ============================================================================

ALTER TABLE taskq.jobs
    ADD COLUMN expires_at timestamptz,
    ADD COLUMN flow_key text CHECK (flow_key IS NULL OR char_length(flow_key) BETWEEN 1 AND 120);

ALTER TABLE taskq.queues
    ADD COLUMN max_running integer CHECK (max_running IS NULL OR max_running > 0),
    ADD COLUMN claim_rate_per_minute integer
        CHECK (claim_rate_per_minute IS NULL OR claim_rate_per_minute > 0),
    ADD COLUMN claim_burst integer CHECK (claim_burst IS NULL OR claim_burst > 0),
    ADD COLUMN ramp_seconds integer
        CHECK (ramp_seconds IS NULL OR ramp_seconds BETWEEN 1 AND 86400),
    ADD COLUMN default_ttl_seconds integer
        CHECK (default_ttl_seconds IS NULL OR default_ttl_seconds BETWEEN 1 AND 31536000),
    ADD COLUMN backpressure_retry_seconds integer NOT NULL DEFAULT 5
        CHECK (backpressure_retry_seconds BETWEEN 1 AND 300),
    ADD COLUMN notify_mode text NOT NULL DEFAULT 'always'
        CHECK (notify_mode IN ('always', 'on_idle_transition'));

ALTER TABLE taskq.schedules
    ADD COLUMN smear_seconds integer
        CHECK (smear_seconds IS NULL OR smear_seconds BETWEEN 1 AND 3600);

CREATE INDEX jobs_ttl_idx ON taskq.jobs (expires_at)
    WHERE expires_at IS NOT NULL AND status IN ('blocked', 'queued');

-- ============================================================================
-- 2. Flow state
-- ============================================================================

CREATE TABLE IF NOT EXISTS taskq.queue_flow (
    queue            text PRIMARY KEY REFERENCES taskq.queues(name) ON DELETE CASCADE,
    tat              timestamptz,
    ramp_started_at  timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.queue_flow OWNER TO taskq_owner;

CREATE TABLE IF NOT EXISTS taskq.flow_limits (
    key              text PRIMARY KEY CHECK (key ~ '^[a-z0-9_.:-]{1,120}$'),
    rate_per_minute  integer NOT NULL CHECK (rate_per_minute > 0),
    burst            integer CHECK (burst IS NULL OR burst > 0),
    note             text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.flow_limits OWNER TO taskq_owner;

CREATE TABLE IF NOT EXISTS taskq.flow_state (
    key         text PRIMARY KEY,
    tat         timestamptz,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.flow_state OWNER TO taskq_owner;

CREATE OR REPLACE FUNCTION taskq.set_flow_limit(
    p_key text, p_rate_per_minute integer, p_burst integer DEFAULT NULL,
    p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_existing taskq.flow_limits%ROWTYPE;
BEGIN
    IF NOT taskq.has_capability('flow_control') THEN
        RAISE EXCEPTION 'flow_control capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"flow_control_inactive"}';
    END IF;
    IF p_key IS NULL OR p_key !~ '^[a-z0-9_.:-]{1,120}$' THEN
        RAISE EXCEPTION 'flow key must match [a-z0-9_.:-]{1,120}' USING ERRCODE = 'TQ422';
    END IF;
    IF p_rate_per_minute IS NULL OR p_rate_per_minute <= 0 THEN
        RAISE EXCEPTION 'rate_per_minute must be positive' USING ERRCODE = 'TQ422';
    END IF;
    IF p_burst IS NOT NULL AND p_burst <= 0 THEN
        RAISE EXCEPTION 'burst must be positive' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_existing FROM taskq.flow_limits WHERE key = p_key FOR UPDATE;
    IF NOT FOUND THEN
        INSERT INTO taskq.flow_limits (key, rate_per_minute, burst, note)
        VALUES (p_key, p_rate_per_minute, p_burst, p_actor);
        RETURN 'created';
    END IF;
    IF v_existing.rate_per_minute = p_rate_per_minute
       AND v_existing.burst IS NOT DISTINCT FROM p_burst THEN
        RETURN 'unchanged';
    END IF;
    UPDATE taskq.flow_limits
       SET rate_per_minute = p_rate_per_minute, burst = p_burst,
           note = COALESCE(p_actor, note), updated_at = now()
     WHERE key = p_key;
    RETURN 'updated';
END $$;
ALTER FUNCTION taskq.set_flow_limit(text, integer, integer, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_flow_limit(text, integer, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_flow_limit(text, integer, integer, text) TO taskq_operator;

-- ============================================================================
-- 3. Producer verdict — the L4 check-order fix lives here
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.try_enqueue(
    p_queue text,
    p_job_type text,
    p_payload jsonb DEFAULT '{}'::jsonb,
    p_priority smallint DEFAULT NULL,
    p_scheduled_at timestamptz DEFAULT NULL,
    p_idempotency_key text DEFAULT NULL,
    p_concurrency_key text DEFAULT NULL,
    p_affinity_key text DEFAULT NULL,
    p_max_attempts smallint DEFAULT NULL,
    p_lease_seconds integer DEFAULT NULL,
    p_backoff_mode text DEFAULT NULL,
    p_backoff_base integer DEFAULT NULL,
    p_backoff_cap integer DEFAULT NULL,
    p_depends_on uuid[] DEFAULT NULL,
    p_workflow_id uuid DEFAULT NULL,
    p_step_key text DEFAULT NULL,
    p_parent_job_id uuid DEFAULT NULL,
    p_headers jsonb DEFAULT NULL,
    p_ttl_seconds integer DEFAULT NULL
) RETURNS TABLE (outcome text, job_id uuid, retry_after_seconds integer)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q taskq.queues%ROWTYPE;
    v_existing uuid;
    v_id uuid;
    v_created boolean;
    v_ttl integer;
    v_stats jsonb;
    v_rate numeric;
    v_ready numeric;
    v_hint integer;
BEGIN
    IF NOT taskq.has_capability('flow_control') THEN
        RAISE EXCEPTION 'flow_control capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"flow_control_inactive"}';
    END IF;
    IF p_ttl_seconds IS NOT NULL AND p_ttl_seconds NOT BETWEEN 1 AND 31536000 THEN
        RAISE EXCEPTION 'ttl_seconds must be 1..31536000' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    -- Check order fix (harness finding, 2026-08-05): an already-admitted key
    -- must resolve to 'existed' BEFORE the depth gate can reject it — an
    -- existed result adds no depth, so reconciliation replay works at cap.
    IF p_idempotency_key IS NOT NULL THEN
        SELECT j.id INTO v_existing FROM taskq.jobs j
         WHERE j.queue = p_queue AND j.idempotency_key = p_idempotency_key
           AND j.status IN ('blocked', 'queued', 'running');
        IF FOUND THEN
            RETURN QUERY SELECT 'existed'::text, v_existing, NULL::integer;
            RETURN;
        END IF;
    END IF;
    BEGIN
        SELECT e.job_id, e.created INTO v_id, v_created
          FROM taskq.enqueue(
              p_queue, p_job_type, p_payload, p_priority, p_scheduled_at,
              p_idempotency_key, p_concurrency_key, p_affinity_key,
              p_max_attempts, p_lease_seconds, p_backoff_mode, p_backoff_base,
              p_backoff_cap, p_depends_on, p_workflow_id, p_step_key,
              p_parent_job_id, p_headers
          ) e;
    EXCEPTION WHEN SQLSTATE 'TQ429' THEN
        SELECT c.data -> 'queues' -> p_queue INTO v_stats
          FROM taskq.control_state c WHERE c.key = 'stats_snapshot';
        v_rate := (v_stats -> 'rates' ->> 'settled_per_s')::numeric;
        v_ready := COALESCE((v_stats ->> 'ready')::numeric, 0);
        IF v_rate IS NOT NULL AND v_rate > 0 THEN
            v_hint := least(60, greatest(1, ceil(v_ready / v_rate)))::integer;
        ELSE
            v_hint := COALESCE(q.backpressure_retry_seconds, 5);
        END IF;
        RETURN QUERY SELECT 'rejected_depth'::text, NULL::uuid, v_hint;
        RETURN;
    END;
    v_ttl := COALESCE(p_ttl_seconds, q.default_ttl_seconds);
    IF v_created AND v_ttl IS NOT NULL THEN
        UPDATE taskq.jobs
           SET expires_at = scheduled_at + make_interval(secs => v_ttl)
         WHERE id = v_id;
    END IF;
    RETURN QUERY SELECT
        CASE WHEN v_created THEN 'accepted' ELSE 'existed' END::text, v_id, NULL::integer;
END $$;
ALTER FUNCTION taskq.try_enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, integer, text, integer, integer, uuid[], uuid, text, uuid, jsonb, integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.try_enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, integer, text, integer, integer, uuid[], uuid, text, uuid, jsonb, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.try_enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, integer, text, integer, integer, uuid[], uuid, text, uuid, jsonb, integer) TO taskq_producer;

-- ============================================================================
-- 4. TTL settlement — direct settle of never-claimed rows; dependents and
--    workflow accounting converge through the existing tick passes/triggers.
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.expire_ttl(p_limit integer DEFAULT 200)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_job record; v_n integer := 0;
BEGIN
    FOR v_job IN
        SELECT id FROM taskq.jobs
         WHERE status IN ('blocked', 'queued')
           AND expires_at IS NOT NULL AND expires_at <= now()
         ORDER BY expires_at
         LIMIT least(COALESCE(p_limit, 200), 500)
           FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE taskq.jobs
           SET status = 'cancelled', outcome = 'expired_ttl',
               finished_at = now(), updated_at = now(),
               cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = COALESCE(cancel_reason, 'expired_ttl')
         WHERE id = v_job.id;
        PERFORM taskq.emit_event(
            v_job.id, NULL, 'cancelled', 'taskq-ttl', 'ttl expired',
            jsonb_build_object('reason', 'expired_ttl'));
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.expire_ttl(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.expire_ttl(integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq._tick_unattested(
    p_reap_limit integer DEFAULT 200
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_out jsonb := '{}';
    v_n integer;
BEGIN
    IF p_reap_limit IS NULL THEN
        RAISE EXCEPTION 'reap limit must not be null' USING ERRCODE = 'TQ422';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('taskq:tick', 0)) THEN
        RETURN jsonb_build_object('skipped', true);
    END IF;
    INSERT INTO taskq.control_state (key, last_started_at)
    VALUES ('tick', now())
    ON CONFLICT (key) DO UPDATE SET last_started_at = now();

    BEGIN
        v_n := taskq.reap_expired(p_reap_limit);
        v_out := v_out || jsonb_build_object('reaped', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'reap: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.expire_ttl(200);
        v_out := v_out || jsonb_build_object('ttl_expired', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'ttl: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.finalize_cancel_stragglers(50);
        v_out := v_out || jsonb_build_object('cancel_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'cancel: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.advance_workflow_cancellations(100);
        v_out := v_out || jsonb_build_object('workflow_cancelled', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'workflow_cancel: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.finalize_dep_stragglers(100);
        v_out := v_out || jsonb_build_object('dependency_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'dependencies: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.finalize_workflows(100);
        v_out := v_out || jsonb_build_object('workflows_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'workflows: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        PERFORM taskq.refresh_stats_snapshot();
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'stats: ' || SQLERRM WHERE key = 'tick';
    END;
    UPDATE taskq.control_state SET last_finished_at = now() WHERE key = 'tick';
    RETURN v_out;
END $$;
ALTER FUNCTION taskq._tick_unattested(integer) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._tick_unattested(integer) FROM PUBLIC;

-- ============================================================================
-- 5. Redrive smearing and the resume ramp stamp
-- ============================================================================

DROP FUNCTION taskq.redrive_failed(text, int, text);
CREATE OR REPLACE FUNCTION taskq.redrive_failed(
    p_queue text, p_limit int, p_actor text, p_smear_seconds integer DEFAULT 0
) RETURNS TABLE (redriven int, skipped int)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_id uuid; v_r int := 0; v_s int := 0;
BEGIN
    IF p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'limit must be 1..500' USING ERRCODE = 'TQ422';
    END IF;
    IF p_smear_seconds IS NULL OR p_smear_seconds NOT BETWEEN 0 AND 86400 THEN
        RAISE EXCEPTION 'smear_seconds must be 0..86400' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_id IN SELECT id FROM taskq.jobs
                 WHERE queue = p_queue AND status = 'failed'
                 ORDER BY finished_at DESC LIMIT p_limit LOOP
        BEGIN
            PERFORM taskq.redrive_job(v_id, p_actor, false);
            IF p_smear_seconds > 0 THEN
                -- Disperse re-entry; duplicate NOTIFYs already collapsed to
                -- one by the server within this transaction.
                UPDATE taskq.jobs
                   SET scheduled_at = now()
                       + make_interval(secs => random() * p_smear_seconds)
                 WHERE id = v_id;
            END IF;
            v_r := v_r + 1;
        EXCEPTION WHEN SQLSTATE 'TQ409' THEN
            v_s := v_s + 1;    -- active-key collision or state raced: skip, keep going
        END;
    END LOOP;
    RETURN QUERY SELECT v_r, v_s;
END $$;
ALTER FUNCTION taskq.redrive_failed(text, int, text, integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.redrive_failed(text, int, text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.redrive_failed(text, int, text, integer) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.resume_queue(p_name text, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_paused timestamptz; v_ramp integer;
BEGIN
    SELECT paused_at, ramp_seconds INTO v_paused, v_ramp
      FROM taskq.queues WHERE name = p_name FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_name USING ERRCODE = 'TQ001';
    END IF;
    IF v_paused IS NULL THEN RETURN 'already_resumed'; END IF;
    UPDATE taskq.queues SET paused_at = NULL, pause_reason = NULL, updated_at = now()
     WHERE name = p_name;
    IF v_ramp IS NOT NULL THEN
        INSERT INTO taskq.queue_flow (queue, ramp_started_at, updated_at)
        VALUES (p_name, now(), now())
        ON CONFLICT (queue) DO UPDATE SET ramp_started_at = now(), updated_at = now();
    END IF;
    RETURN 'resumed';
END $$;
ALTER FUNCTION taskq.resume_queue(text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.resume_queue(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.resume_queue(text, text) TO taskq_operator;

-- ============================================================================
-- 6. Contract bump (flow_control stays inactive until 0026) + hardening check
-- ============================================================================

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.4.1"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();

DO $$
DECLARE
    v_bad text[];
BEGIN
    SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text)
      INTO v_bad
      FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND (
           NOT p.prosecdef
           OR p.proconfig IS NULL
           OR NOT p.proconfig @> ARRAY['search_path=pg_catalog, taskq, pg_temp']
       );
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0024 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
