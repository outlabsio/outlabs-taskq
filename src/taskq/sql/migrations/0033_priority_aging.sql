-- outlabs-taskq — migration 0033: priority aging (SQL contract 0.6.1)
--
-- Per-queue, opt-in priority aging (research P15, S6 Wave 3 fairness). A queued
-- job's effective claim priority improves the longer it waits, so a sustained
-- high-priority flood cannot starve low-priority work indefinitely — without
-- inverting priority for fresh work.
--
-- DESIGN (see docs/Task Queue 0.6 Circuit Breaker Specification.md S7):
--   * Config `priority_aging_seconds` lives on the queue_flow row (set via the
--     set_priority_aging verb), NULL = off (strict priority, today's behavior).
--   * The normal claim candidate scan (overload 1) orders by an effective
--     priority = priority - LEAST(1000, floor(age / aging_seconds)); priority is
--     ascending (lower = more urgent), so waiting SUBTRACTS. Purely computed in
--     the ORDER BY — no columns written, no settle-path work, no extra state.
--     A CASE guard makes an unconfigured queue byte-identical (age term -> 0).
--   * Scope: aging applies to the normal claim path only. Workflow-continuation
--     claims (overload 2) keep strict priority — their frontier ordering has its
--     own semantics; aging there is out of scope for this slice.
--
-- MIGRATION NOTES / COMPATIBILITY:
--   * Additive: queue_flow gains one column; overload 1 of _claim_jobs_unattested
--     is redefined (same signature, so no machine-manifest function-row change);
--     one new operator verb. Ungated — the claim reads NULL until a queue opts in
--     via the verb, so nothing changes for existing queues. Contract 0.6.0 -> 0.6.1.
--   * Rolling-deploy safe: old code ignores the column; new code degrades to
--     strict priority against a pre-0033 database.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.6.0"'::jsonb THEN
        RAISE EXCEPTION '0033 requires SQL contract 0.6.0, found %', v_contract;
    END IF;
END $$;

ALTER TABLE taskq.queue_flow
    ADD COLUMN priority_aging_seconds integer
        CHECK (priority_aging_seconds IS NULL OR priority_aging_seconds > 0);

CREATE OR REPLACE FUNCTION taskq._claim_jobs_unattested(
    p_queue text,
    p_worker_id text,
    p_batch integer DEFAULT 1,
    p_job_types text[] DEFAULT NULL,
    p_lease_seconds integer DEFAULT NULL,
    p_affinity_key text DEFAULT NULL,
    p_job_id uuid DEFAULT NULL,
    p_accept_throttled boolean DEFAULT false
) RETURNS taskq.claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_job taskq.jobs%ROWTYPE;
    v_attempt_id uuid;
    v_lease integer;
    v_skip uuid[] := '{}';
    v_claimed integer := 0;
    v_scans integer := 0;
    v_cap integer;
    v_running integer;
    v_affinity text := p_affinity_key;
    v_batch integer := p_batch;
    v_saturated text[] := '{}';
    v_queue taskq.queues%ROWTYPE;
    v_scale numeric := 1.0;
    v_eff_cap integer;
    v_qrunning bigint;
    v_granted integer;
    v_retry integer;
    v_jobs taskq.claimed_job[] := '{}';
    v_aging_seconds integer;
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_batch IS NULL OR v_batch NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'claim batch must be 1..50' USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND p_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'lease override must be 15..86400 seconds'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_types IS NOT NULL AND cardinality(p_job_types) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'job type filter must have 1..20 entries'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_affinity_key IS NOT NULL AND char_length(p_affinity_key) > 120 THEN
        RAISE EXCEPTION 'affinity_key exceeds 120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;

    SELECT q.* INTO v_queue FROM taskq.queues AS q WHERE q.name = p_queue;
    IF NOT FOUND THEN
        RETURN ROW('unknown_queue','{}'::taskq.claimed_job[],NULL)::taskq.claim_batch;
    END IF;
    IF v_queue.paused_at IS NOT NULL THEN
        RETURN ROW('paused','{}'::taskq.claimed_job[],NULL)::taskq.claim_batch;
    END IF;
    IF v_queue.max_running IS NOT NULL OR v_queue.claim_rate_per_minute IS NOT NULL THEN
        v_scale := taskq._flow_scale(p_queue, v_queue.ramp_seconds);
        IF v_queue.max_running IS NOT NULL THEN
            v_eff_cap := greatest(1, floor(v_queue.max_running * v_scale)::integer);
            SELECT c.running INTO v_qrunning
              FROM taskq.queue_counters c WHERE c.queue = p_queue;
            IF COALESCE(v_qrunning, 0) >= v_eff_cap THEN
                RETURN taskq._throttled_or_empty(p_accept_throttled, 1);
            END IF;
            v_batch := least(v_batch, v_eff_cap - COALESCE(v_qrunning, 0)::integer);
        END IF;
        IF v_queue.claim_rate_per_minute IS NOT NULL THEN
            SELECT g.granted, g.retry_after INTO v_granted, v_retry
              FROM taskq._flow_consume_queue(
                  p_queue, v_queue.claim_rate_per_minute,
                  COALESCE(v_queue.claim_burst, v_queue.claim_rate_per_minute),
                  v_scale, v_batch) g;
            IF v_granted = 0 THEN
                RETURN taskq._throttled_or_empty(p_accept_throttled, v_retry);
            END IF;
            v_batch := least(v_batch, v_granted);
        END IF;
    END IF;
    SELECT COALESCE(array_agg(k.key),'{}') INTO v_saturated
    FROM (
        SELECT r.concurrency_key AS key, count(*) AS c
        FROM taskq.jobs AS r
        WHERE r.status = 'running' AND r.concurrency_key IS NOT NULL
        GROUP BY r.concurrency_key
    ) AS k
    WHERE k.c >= COALESCE(
        (SELECT l.max_running FROM taskq.concurrency_limits AS l WHERE l.key = k.key),1
    );

    SELECT priority_aging_seconds INTO v_aging_seconds
      FROM taskq.queue_flow WHERE queue = p_queue;
    WHILE v_claimed < v_batch AND v_scans < v_batch + 20 LOOP
        v_scans := v_scans + 1;
        v_job := NULL;
        IF v_affinity IS NOT NULL AND p_job_id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs AS j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.continuation_policy_hash IS NULL
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND (j.expires_at IS NULL OR j.expires_at > now())
              AND (j.workflow_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taskq.workflows AS w
                    WHERE w.id = j.workflow_id AND w.cancel_requested_at IS NOT NULL))
              AND j.affinity_key = v_affinity
              AND (p_job_types IS NULL OR j.job_type = ANY(p_job_types))
              AND NOT (j.id = ANY(v_skip))
              AND (j.concurrency_key IS NULL
                   OR NOT (j.concurrency_key = ANY(v_saturated)))
            ORDER BY j.priority - CASE WHEN v_aging_seconds IS NULL THEN 0
                ELSE LEAST(1000, floor(extract(epoch FROM (now() - j.scheduled_at))
                     / v_aging_seconds)::integer) END,
                j.scheduled_at, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED;
            IF v_job.id IS NULL THEN v_affinity := NULL; END IF;
        END IF;
        IF v_job.id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs AS j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.continuation_policy_hash IS NULL
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND (j.expires_at IS NULL OR j.expires_at > now())
              AND (j.workflow_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taskq.workflows AS w
                    WHERE w.id = j.workflow_id AND w.cancel_requested_at IS NOT NULL))
              AND (p_job_id IS NULL OR j.id = p_job_id)
              AND (p_job_types IS NULL OR j.job_type = ANY(p_job_types))
              AND NOT (j.id = ANY(v_skip))
              AND (j.concurrency_key IS NULL
                   OR NOT (j.concurrency_key = ANY(v_saturated)))
            ORDER BY j.priority - CASE WHEN v_aging_seconds IS NULL THEN 0
                ELSE LEAST(1000, floor(extract(epoch FROM (now() - j.scheduled_at))
                     / v_aging_seconds)::integer) END,
                j.scheduled_at, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED;
        END IF;
        EXIT WHEN v_job.id IS NULL;
        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended('taskq.ck:' || v_job.concurrency_key,0)
            ) THEN
                v_skip := v_skip || v_job.id; CONTINUE;
            END IF;
            SELECT COALESCE((SELECT l.max_running FROM taskq.concurrency_limits AS l
                             WHERE l.key = v_job.concurrency_key),1) INTO v_cap;
            SELECT count(*) INTO v_running FROM taskq.jobs AS r
            WHERE r.status = 'running' AND r.concurrency_key = v_job.concurrency_key;
            IF v_running >= v_cap THEN v_skip := v_skip || v_job.id; CONTINUE; END IF;
        END IF;
        IF v_job.flow_key IS NOT NULL AND NOT taskq.flow_key_admit(v_job.flow_key) THEN
            v_skip := v_skip || v_job.id; CONTINUE;
        END IF;
        v_attempt_id := taskq.uuid7();
        v_lease := COALESCE(p_lease_seconds,v_job.lease_seconds);
        UPDATE taskq.jobs AS j
        SET status='running',worker_id=p_worker_id,current_attempt_id=v_attempt_id,
            attempt_count=j.attempt_count+1,
            lease_expires_at=now()+make_interval(secs=>v_lease),
            started_at=COALESCE(j.started_at,now()),updated_at=now()
        WHERE j.id=v_job.id;
        INSERT INTO taskq.job_attempts(id,job_id,worker_id,lease_seconds)
        VALUES(v_attempt_id,v_job.id,p_worker_id,v_lease);
        PERFORM taskq.emit_event(v_job.id,v_attempt_id,'claimed',p_worker_id,NULL,
            jsonb_build_object('attempt',v_job.attempt_count+1));
        v_claimed := v_claimed + 1;
        v_jobs := v_jobs || ROW(
            v_job.id,v_job.queue,v_job.job_type,v_job.priority,v_job.payload,
            v_job.headers,v_job.progress,v_attempt_id,
            (v_job.attempt_count+1)::integer,v_job.failure_count,
            v_job.max_attempts,now()+make_interval(secs=>v_lease),
            v_job.workflow_id,v_job.step_key,v_lease,NULL
        )::taskq.claimed_job;
    END LOOP;
    IF v_claimed = 0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            RETURN ROW('unavailable','{}'::taskq.claimed_job[],NULL)::taskq.claim_batch;
        END IF;
        RETURN ROW('empty','{}'::taskq.claimed_job[],NULL)::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed',v_jobs,NULL)::taskq.claim_batch;
END $$;

CREATE FUNCTION taskq.set_priority_aging(
    p_queue text, p_aging_seconds integer, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_existed boolean;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM taskq.queues WHERE name = p_queue) THEN
        RAISE EXCEPTION 'taskq: no such queue' USING ERRCODE = 'TQ001';
    END IF;
    IF p_aging_seconds IS NOT NULL AND p_aging_seconds <= 0 THEN
        RAISE EXCEPTION 'aging_seconds must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    v_existed := EXISTS (SELECT 1 FROM taskq.queue_flow WHERE queue = p_queue);
    INSERT INTO taskq.queue_flow (queue, priority_aging_seconds, updated_at)
    VALUES (p_queue, p_aging_seconds, now())
    ON CONFLICT (queue) DO UPDATE SET
        priority_aging_seconds = excluded.priority_aging_seconds, updated_at = now();
    RETURN CASE WHEN v_existed THEN 'updated' ELSE 'created' END;
END $$;
ALTER FUNCTION taskq.set_priority_aging(text, integer, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_priority_aging(text, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_priority_aging(text, integer, text) TO taskq_operator;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.1"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();

DO $$
DECLARE v_bad text[];
BEGIN
    SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text)
      INTO v_bad FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND (NOT p.prosecdef OR p.proconfig IS NULL
            OR NOT p.proconfig @> ARRAY['search_path=pg_catalog, taskq, pg_temp']);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0033 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
