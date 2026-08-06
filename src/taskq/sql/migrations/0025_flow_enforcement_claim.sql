-- outlabs-taskq — migration 0025: flow enforcement, claim layer (inactive)
-- SQL contract 0.4.2 — Wave 2b slice 2 per the 0.5 Flow Enforcement
-- Specification. Adds the typed throttle verdict to the claim surface:
--
--   * taskq.claim_batch gains a trailing retry_after_seconds attribute
--     (additive-composite precedent, 0003).
--   * Both claim overloads gain p_accept_throttled DEFAULT false. Queue-level
--     flow gates (max_running over the O(1) counters, GCRA claim rate, ramp
--     scaling after resume) return 'throttled' + retry hint to declaring
--     callers and today's 'empty' to everyone else. Enforcement only runs for
--     queues that configured it — a NULL-profiled queue takes no new reads.
--   * Per-candidate flow_key buckets are consumed via advisory TRY locks and
--     dry/contended candidates are SKIPPED in-scan (the concurrency-key
--     posture: brief under-admission, never waiting, provably deadlock-free).
--   * The candidate predicates skip TTL-expired rows (0024's expire_ttl pass
--     settles them).
--
-- The gate order inside a claim is paused -> max_running -> claim rate; the
-- reported retry hint is the first gate that closed. All gating math is
-- factored into helpers so both claim bodies carry a small identical
-- injection rather than divergent logic.

DO $$
DECLARE
    v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.4.1"'::jsonb THEN
        RAISE EXCEPTION '0025 requires SQL contract 0.4.1, found %', v_contract;
    END IF;
    IF to_regclass('taskq.queue_flow') IS NULL OR to_regclass('taskq.flow_limits') IS NULL THEN
        RAISE EXCEPTION '0025 requires the 0024 flow tables';
    END IF;
END $$;

-- ============================================================================
-- 1. Composite + drops (recreate below with the new parameter)
-- ============================================================================

DROP FUNCTION taskq.claim_jobs(text, text, integer, text[], integer, text, uuid);
DROP FUNCTION taskq.claim_jobs(text, text, integer, text[], integer, text, uuid, text[]);
DROP FUNCTION taskq._claim_jobs_unattested(text, text, integer, text[], integer, text, uuid);
DROP FUNCTION taskq._claim_jobs_unattested(text, text, integer, text[], integer, text, uuid, text[]);

ALTER TYPE taskq.claim_batch ADD ATTRIBUTE retry_after_seconds integer;

-- ============================================================================
-- 2. Gating helpers (owner-internal)
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq._throttled_or_empty(
    p_accept boolean, p_retry_after integer
) RETURNS taskq.claim_batch
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT ROW(
        CASE WHEN p_accept THEN 'throttled' ELSE 'empty' END,
        '{}'::taskq.claimed_job[],
        CASE WHEN p_accept THEN greatest(1, COALESCE(p_retry_after, 1)) END
    )::taskq.claim_batch
$$;
ALTER FUNCTION taskq._throttled_or_empty(boolean, integer) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._throttled_or_empty(boolean, integer) FROM PUBLIC;

-- Ramp scale for a queue: 1.0 outside a ramp window, else elapsed fraction
-- (floored at 0.02 so a ramp never computes to zero capacity).
CREATE OR REPLACE FUNCTION taskq._flow_scale(p_queue text, p_ramp_seconds integer)
RETURNS numeric
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_started timestamptz; v_elapsed numeric;
BEGIN
    IF p_ramp_seconds IS NULL THEN RETURN 1.0; END IF;
    SELECT f.ramp_started_at INTO v_started FROM taskq.queue_flow f WHERE f.queue = p_queue;
    IF v_started IS NULL THEN RETURN 1.0; END IF;
    v_elapsed := extract(epoch FROM (now() - v_started));
    IF v_elapsed >= p_ramp_seconds THEN RETURN 1.0; END IF;
    RETURN greatest(v_elapsed / p_ramp_seconds, 0.02);
END $$;
ALTER FUNCTION taskq._flow_scale(text, integer) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._flow_scale(text, integer) FROM PUBLIC;

-- Queue-level GCRA: grant up to p_want claim tokens. Serializes on the
-- queue_flow row (taken FIRST in the claim, before any candidate locks, so
-- the lock order is uniform). Returns (granted, retry_after_seconds).
CREATE OR REPLACE FUNCTION taskq._flow_consume_queue(
    p_queue text, p_rate_per_minute integer, p_burst integer,
    p_scale numeric, p_want integer
) RETURNS TABLE (granted integer, retry_after integer)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_rate integer := greatest(1, floor(p_rate_per_minute * p_scale)::integer);
    v_t numeric;
    v_bw numeric;
    v_now timestamptz := now();
    v_tat timestamptz;
    v_grant integer;
BEGIN
    v_t := 60.0 / v_rate;
    v_bw := v_t * greatest(1, p_burst);
    INSERT INTO taskq.queue_flow (queue, tat, updated_at)
    VALUES (p_queue, NULL, v_now)
    ON CONFLICT (queue) DO NOTHING;
    SELECT f.tat INTO v_tat FROM taskq.queue_flow f WHERE f.queue = p_queue FOR UPDATE;
    -- Idle/fresh buckets clamp tat to now: exactly one full burst available,
    -- never more (tat = now + bw means empty; tat = now means full).
    v_tat := greatest(COALESCE(v_tat, v_now), v_now);
    v_grant := least(p_want,
        floor((extract(epoch FROM (v_now - v_tat)) + v_bw) / v_t)::integer);
    -- With the idle clamp above this is at most p_burst.

    IF v_grant <= 0 THEN
        RETURN QUERY SELECT 0,
            greatest(1, ceil(extract(epoch FROM (v_tat - v_now)) - v_bw + v_t))::integer;
        RETURN;
    END IF;
    UPDATE taskq.queue_flow
       SET tat = v_tat + make_interval(secs => v_grant * v_t), updated_at = v_now
     WHERE queue = p_queue;
    RETURN QUERY SELECT v_grant, NULL::integer;
END $$;
ALTER FUNCTION taskq._flow_consume_queue(text, integer, integer, numeric, integer)
    OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._flow_consume_queue(text, integer, integer, numeric, integer)
    FROM PUBLIC;

-- Per-key GCRA: consume one token. Unknown keys are unlimited (politeness
-- must not serialize the world). Advisory TRY lock: a contended or dry key
-- skips the candidate this round — never waits, never deadlocks.
CREATE OR REPLACE FUNCTION taskq.flow_key_admit(p_key text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_rate integer;
    v_burst integer;
    v_t numeric;
    v_bw numeric;
    v_now timestamptz := now();
    v_tat timestamptz;
BEGIN
    SELECT l.rate_per_minute, COALESCE(l.burst, l.rate_per_minute)
      INTO v_rate, v_burst
      FROM taskq.flow_limits l WHERE l.key = p_key;
    IF NOT FOUND THEN RETURN true; END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('taskq.fk:' || p_key, 0)) THEN
        RETURN false;
    END IF;
    v_t := 60.0 / v_rate;
    v_bw := v_t * v_burst;
    INSERT INTO taskq.flow_state (key, tat, updated_at) VALUES (p_key, NULL, v_now)
    ON CONFLICT (key) DO NOTHING;
    SELECT f.tat INTO v_tat FROM taskq.flow_state f WHERE f.key = p_key;
    v_tat := greatest(COALESCE(v_tat, v_now), v_now);
    IF extract(epoch FROM (v_tat - v_now)) > v_bw - v_t THEN
        RETURN false;
    END IF;
    UPDATE taskq.flow_state
       SET tat = v_tat + make_interval(secs => v_t), updated_at = v_now
     WHERE key = p_key;
    RETURN true;
END $$;
ALTER FUNCTION taskq.flow_key_admit(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.flow_key_admit(text) FROM PUBLIC;

-- ============================================================================
-- 3. Claim bodies (0016 bodies + the flow injection; unattested, owner-only)
-- ============================================================================

CREATE FUNCTION taskq._claim_jobs_unattested(
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
            ORDER BY j.priority, j.scheduled_at, j.id
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
            ORDER BY j.priority, j.scheduled_at, j.id
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
ALTER FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,boolean)
    OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,boolean)
    FROM PUBLIC;

CREATE FUNCTION taskq._claim_jobs_unattested(
    p_queue text,
    p_worker_id text,
    p_batch integer,
    p_job_types text[],
    p_lease_seconds integer,
    p_affinity_key text,
    p_job_id uuid,
    p_continuation_policy_hashes text[],
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
    v_hashes text[] := COALESCE(p_continuation_policy_hashes,'{}');
    v_saturated text[] := '{}';
    v_queue taskq.queues%ROWTYPE;
    v_scale numeric := 1.0;
    v_eff_cap integer;
    v_qrunning bigint;
    v_granted integer;
    v_retry integer;
    v_jobs taskq.claimed_job[] := '{}';
BEGIN
    IF NOT taskq.has_capability('workflow_continuations') THEN
        RAISE EXCEPTION 'workflow continuations are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;
    IF COALESCE(p_worker_id,'') = '' OR length(p_worker_id) > 200
       OR p_batch IS NULL OR p_batch NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid worker or claim batch' USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND p_lease_seconds NOT BETWEEN 15 AND 86400
       OR p_job_types IS NOT NULL AND cardinality(p_job_types) NOT BETWEEN 1 AND 20
       OR p_affinity_key IS NOT NULL AND char_length(p_affinity_key) > 120 THEN
        RAISE EXCEPTION 'invalid claim filter' USING ERRCODE = 'TQ422';
    END IF;
    IF cardinality(v_hashes) > 32
       OR cardinality(v_hashes) <> cardinality(ARRAY(SELECT DISTINCT unnest(v_hashes)))
       OR EXISTS (SELECT 1 FROM unnest(v_hashes) AS h
                  WHERE h IS NULL OR h !~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'supported continuation policies must be 0..32 distinct hashes'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;
    SELECT q.* INTO v_queue FROM taskq.queues AS q WHERE q.name=p_queue;
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
    FROM (SELECT r.concurrency_key AS key,count(*) AS c FROM taskq.jobs AS r
          WHERE r.status='running' AND r.concurrency_key IS NOT NULL
          GROUP BY r.concurrency_key) AS k
    WHERE k.c >= COALESCE((SELECT l.max_running FROM taskq.concurrency_limits AS l
                           WHERE l.key=k.key),1);

    WHILE v_claimed < v_batch AND v_scans < v_batch + 20 LOOP
        v_scans := v_scans + 1;
        v_job := NULL;
        IF p_job_id IS NOT NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs AS j
            WHERE j.id=p_job_id AND j.queue=p_queue AND j.status='queued'
              AND (j.continuation_policy_hash IS NULL
                   OR j.continuation_policy_hash=ANY(v_hashes))
              AND j.scheduled_at<=now() AND j.cancel_requested_at IS NULL
              AND (j.expires_at IS NULL OR j.expires_at > now())
              AND (p_job_types IS NULL OR j.job_type=ANY(p_job_types))
              AND (j.workflow_id IS NULL OR NOT EXISTS(
                    SELECT 1 FROM taskq.workflows AS w
                    WHERE w.id=j.workflow_id AND w.cancel_requested_at IS NOT NULL))
            FOR UPDATE OF j SKIP LOCKED;
        ELSIF v_affinity IS NOT NULL THEN
            SELECT frontier.* INTO v_job
            FROM unnest(array_prepend(NULL::text,v_hashes)) AS policy(hash)
            CROSS JOIN LATERAL (
                SELECT j.* FROM taskq.jobs AS j
                WHERE j.queue=p_queue AND j.status='queued'
                  AND j.continuation_policy_hash IS NOT DISTINCT FROM policy.hash
                  AND j.scheduled_at<=now() AND j.cancel_requested_at IS NULL
                  AND (j.expires_at IS NULL OR j.expires_at > now())
                  AND j.affinity_key=v_affinity
                  AND (p_job_types IS NULL OR j.job_type=ANY(p_job_types))
                  AND NOT (j.id=ANY(v_skip))
                  AND (j.concurrency_key IS NULL
                       OR NOT (j.concurrency_key=ANY(v_saturated)))
                  AND (j.workflow_id IS NULL OR NOT EXISTS(
                        SELECT 1 FROM taskq.workflows AS w
                        WHERE w.id=j.workflow_id AND w.cancel_requested_at IS NOT NULL))
                ORDER BY j.continuation_policy_hash,j.priority,j.scheduled_at,j.id
                LIMIT v_batch
            ) AS frontier
            ORDER BY frontier.priority,frontier.scheduled_at,frontier.id
            LIMIT 1 FOR UPDATE OF frontier SKIP LOCKED;
            IF v_job.id IS NULL THEN v_affinity := NULL; END IF;
        END IF;
        IF v_job.id IS NULL AND p_job_id IS NULL THEN
            SELECT frontier.* INTO v_job
            FROM unnest(array_prepend(NULL::text,v_hashes)) AS policy(hash)
            CROSS JOIN LATERAL (
                SELECT j.* FROM taskq.jobs AS j
                WHERE j.queue=p_queue AND j.status='queued'
                  AND j.continuation_policy_hash IS NOT DISTINCT FROM policy.hash
                  AND j.scheduled_at<=now() AND j.cancel_requested_at IS NULL
                  AND (j.expires_at IS NULL OR j.expires_at > now())
                  AND (p_job_types IS NULL OR j.job_type=ANY(p_job_types))
                  AND NOT (j.id=ANY(v_skip))
                  AND (j.concurrency_key IS NULL
                       OR NOT (j.concurrency_key=ANY(v_saturated)))
                  AND (j.workflow_id IS NULL OR NOT EXISTS(
                        SELECT 1 FROM taskq.workflows AS w
                        WHERE w.id=j.workflow_id AND w.cancel_requested_at IS NOT NULL))
                ORDER BY j.continuation_policy_hash,j.priority,j.scheduled_at,j.id
                LIMIT v_batch
            ) AS frontier
            ORDER BY frontier.priority,frontier.scheduled_at,frontier.id
            LIMIT 1 FOR UPDATE OF frontier SKIP LOCKED;
        END IF;
        EXIT WHEN v_job.id IS NULL;
        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended('taskq.ck:'||v_job.concurrency_key,0)
            ) THEN v_skip:=v_skip||v_job.id; CONTINUE; END IF;
            SELECT COALESCE((SELECT l.max_running FROM taskq.concurrency_limits AS l
                             WHERE l.key=v_job.concurrency_key),1) INTO v_cap;
            SELECT count(*) INTO v_running FROM taskq.jobs AS r
            WHERE r.status='running' AND r.concurrency_key=v_job.concurrency_key;
            IF v_running>=v_cap THEN v_skip:=v_skip||v_job.id; CONTINUE; END IF;
        END IF;
        IF v_job.flow_key IS NOT NULL AND NOT taskq.flow_key_admit(v_job.flow_key) THEN
            v_skip := v_skip || v_job.id; CONTINUE;
        END IF;
        v_attempt_id:=taskq.uuid7();
        v_lease:=COALESCE(p_lease_seconds,v_job.lease_seconds);
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
        v_claimed:=v_claimed+1;
        v_jobs:=v_jobs||ROW(
            v_job.id,v_job.queue,v_job.job_type,v_job.priority,v_job.payload,
            v_job.headers,v_job.progress,v_attempt_id,
            (v_job.attempt_count+1)::integer,v_job.failure_count,
            v_job.max_attempts,now()+make_interval(secs=>v_lease),
            v_job.workflow_id,v_job.step_key,v_lease,v_job.continuation_policy_hash
        )::taskq.claimed_job;
    END LOOP;
    IF v_claimed=0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            RETURN ROW('unavailable','{}'::taskq.claimed_job[],NULL)::taskq.claim_batch;
        END IF;
        RETURN ROW('empty','{}'::taskq.claimed_job[],NULL)::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed',v_jobs,NULL)::taskq.claim_batch;
END $$;
ALTER FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,text[],boolean)
    OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,text[],boolean)
    FROM PUBLIC;

-- ============================================================================
-- 4. Public attestation wrappers (0020 pattern) with the new parameter
-- ============================================================================

CREATE FUNCTION taskq.claim_jobs(
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
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._claim_jobs_unattested(
        p_queue, p_worker_id, p_batch, p_job_types, p_lease_seconds,
        p_affinity_key, p_job_id, p_accept_throttled);
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,boolean)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,boolean)
    TO taskq_runner;

CREATE FUNCTION taskq.claim_jobs(
    p_queue text,
    p_worker_id text,
    p_batch integer,
    p_job_types text[],
    p_lease_seconds integer,
    p_affinity_key text,
    p_job_id uuid,
    p_continuation_policy_hashes text[],
    p_accept_throttled boolean DEFAULT false
) RETURNS taskq.claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._claim_jobs_unattested(
        p_queue, p_worker_id, p_batch, p_job_types, p_lease_seconds,
        p_affinity_key, p_job_id, p_continuation_policy_hashes, p_accept_throttled);
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)
    TO taskq_runner;

-- ============================================================================
-- 5. Contract bump + hardening check
-- ============================================================================

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.4.2"'::jsonb, now())
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
        RAISE EXCEPTION '0025 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
