-- outlabs-taskq — migration 0003: SQL contract 0.1.2
-- DERIVED FROM: ADR-013 + Function Manifest §10.
-- Migration 0001/0002 are immutable; this patch adds no public function identity.

-- H-02 permits append-only composite evolution. Existing attributes retain
-- their names, order, and types; lease_expires_at remains for observability.
ALTER TYPE taskq.claimed_job ADD ATTRIBUTE lease_seconds integer;

CREATE OR REPLACE FUNCTION taskq.claim_jobs(
    p_queue         text,
    p_worker_id     text,
    p_batch         int    DEFAULT 1,
    p_job_types     text[] DEFAULT NULL,
    p_lease_seconds int    DEFAULT NULL,
    p_affinity_key  text   DEFAULT NULL,
    p_job_id        uuid   DEFAULT NULL
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
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_batch IS NULL OR v_batch < 1 OR v_batch > 50 THEN
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
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;

    SELECT q.paused_at INTO v_paused_at FROM taskq.queues q WHERE q.name = p_queue;
    IF NOT FOUND THEN
        RETURN ROW('unknown_queue', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    IF v_paused_at IS NOT NULL THEN
        RETURN ROW('paused', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;

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
            IF v_job.id IS NULL THEN v_affinity := NULL; END IF;
        END IF;

        IF v_job.id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND (p_job_id IS NULL OR j.id = p_job_id)
              AND (p_job_types IS NULL OR j.job_type = ANY (p_job_types))
              AND NOT (j.id = ANY (v_skip))
              AND (j.concurrency_key IS NULL OR NOT (j.concurrency_key = ANY (v_saturated)))
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
        END IF;

        EXIT WHEN v_job.id IS NULL;

        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                       hashtextextended('taskq.ck:' || v_job.concurrency_key, 0)) THEN
                v_skip := v_skip || v_job.id; CONTINUE;
            END IF;
            SELECT COALESCE((SELECT l.max_running FROM taskq.concurrency_limits l
                              WHERE l.key = v_job.concurrency_key), 1) INTO v_cap;
            SELECT count(*) INTO v_running FROM taskq.jobs r
             WHERE r.status = 'running' AND r.concurrency_key = v_job.concurrency_key;
            IF v_running >= v_cap THEN
                v_skip := v_skip || v_job.id; CONTINUE;
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
        WHERE j.id = v_job.id;

        INSERT INTO taskq.job_attempts (id, job_id, worker_id, lease_seconds)
        VALUES (v_attempt_id, v_job.id, p_worker_id, v_lease);

        PERFORM taskq.emit_event(v_job.id, v_attempt_id, 'claimed', p_worker_id, NULL,
            jsonb_build_object('attempt', v_job.attempt_count + 1));

        v_claimed := v_claimed + 1;
        v_jobs := v_jobs || ROW(
            v_job.id, v_job.queue, v_job.job_type, v_job.priority, v_job.payload,
            v_job.headers, v_job.progress, v_attempt_id, (v_job.attempt_count + 1)::int,
            v_job.failure_count, v_job.max_attempts,
            now() + make_interval(secs => v_lease),
            v_job.workflow_id, v_job.step_key,
            v_lease)::taskq.claimed_job;
    END LOOP;

    IF v_claimed = 0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            RETURN ROW('unavailable', '{}'::taskq.claimed_job[])::taskq.claim_batch;
        END IF;
        RETURN ROW('empty', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed', v_jobs)::taskq.claim_batch;
END $$;
ALTER FUNCTION taskq.claim_jobs(text, text, int, text[], int, text, uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text, text, int, text[], int, text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text, text, int, text[], int, text, uuid) TO taskq_runner;

INSERT INTO taskq.meta (key, value, updated_at)
VALUES ('contract_version', '"0.1.2"'::jsonb, now())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;
