-- outlabs-taskq — migration 0002: SQL contract 0.1.1
-- DERIVED FROM: ADR-012 + Function Manifest §9.
-- Migration 0001 is immutable; this patch changes no public function identity.

CREATE OR REPLACE FUNCTION taskq.truncate_utf8(p_value text, p_max_bytes int)
RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
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
ALTER FUNCTION taskq.truncate_utf8(text, int) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.truncate_utf8(text, int) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.emit_event(
    p_job_id uuid, p_attempt_id uuid, p_event_type text,
    p_actor text, p_message text, p_data jsonb DEFAULT NULL
) RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    INSERT INTO taskq.job_events (job_id, attempt_id, event_type, actor, message, data)
    VALUES (p_job_id, p_attempt_id, p_event_type, p_actor, taskq.truncate_utf8(p_message, 500), p_data);
$$;
ALTER FUNCTION taskq.emit_event(uuid, uuid, text, text, text, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.emit_event(uuid, uuid, text, text, text, jsonb) FROM PUBLIC;

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
            error = taskq.truncate_utf8(p_error, 2048),
            finished_at = now(), finished_by_attempt_id = p_attempt_id, updated_at = now()
        WHERE id = p_job_id;
        UPDATE taskq.job_attempts SET status = 'cancelled', outcome = 'canceled',
               finished_at = now(), error = taskq.truncate_utf8(p_error, 2048), stats = COALESCE(p_stats, stats)
        WHERE id = p_attempt_id AND status = 'running';
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id,
            p_error, NULL);
        RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
    END IF;

    -- Validate the caller-supplied retry hint at the public boundary (R2-07).
    IF p_retry_after_seconds IS NOT NULL
       AND (p_retry_after_seconds < 0 OR p_retry_after_seconds > 2592000) THEN
        RAISE EXCEPTION 'retry_after_seconds must be 0..2592000, got %',
            p_retry_after_seconds USING ERRCODE = 'TQ422';
    END IF;

    UPDATE taskq.job_attempts SET status = 'failed',
           finished_at = now(), error = taskq.truncate_utf8(p_error, 2048), stats = COALESCE(p_stats, stats)
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
            error = taskq.truncate_utf8(p_error, 2048), updated_at = now()
        WHERE id = p_job_id;
        UPDATE taskq.job_attempts SET outcome = 'retry_scheduled' WHERE id = p_attempt_id;
        PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'retry_scheduled', p_worker_id,
            p_error, jsonb_build_object('delay_seconds', v_delay, 'next_at', v_next,
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
        error = taskq.truncate_utf8(p_error, 2048),
        finished_at = now(), finished_by_attempt_id = p_attempt_id, updated_at = now()
    WHERE id = p_job_id;
    UPDATE taskq.job_attempts
        SET outcome = CASE WHEN p_retryable THEN 'retry_exhausted' ELSE 'non_retryable' END
        WHERE id = p_attempt_id;
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'failed',
        p_worker_id, p_error, NULL);
    RETURN ('dead', 'failed', NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.fail_job(uuid, uuid, text, text, boolean, int, jsonb, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.fail_job(uuid, uuid, text, text, boolean, int, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.fail_job(uuid, uuid, text, text, boolean, int, jsonb, jsonb) TO taskq_runner;
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
           error   = taskq.truncate_utf8(p_reason, 2048),          -- caller text lives in error/stats, NEVER in outcome (Spec SS3.1)
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
    IF p_cause IS NULL OR p_cause NOT IN ('released','worker_shutdown','no_handler') THEN
        RAISE EXCEPTION 'invalid release cause %', p_cause USING ERRCODE = 'TQ422';
    END IF;
    IF p_delay_seconds IS NULL OR p_delay_seconds < 0 OR p_delay_seconds > 86400 THEN
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
           error = taskq.truncate_utf8(p_reason, 2048),
           finished_at=now(), finished_by_attempt_id=p_attempt_id, updated_at=now()
     WHERE id = p_job_id;
    UPDATE taskq.job_attempts SET status='cancelled', outcome='canceled',
           finished_at=now(), error=taskq.truncate_utf8(p_reason, 2048)
     WHERE id = p_attempt_id AND status='running';
    PERFORM taskq.emit_event(p_job_id, p_attempt_id, 'cancelled', p_worker_id, p_reason, NULL);
    RETURN ('ok', 'cancelled', NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.cancel_running_job(uuid, uuid, text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.cancel_running_job(uuid, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.cancel_running_job(uuid, uuid, text, text) TO taskq_runner;
CREATE OR REPLACE FUNCTION taskq.purge_queued(
    p_queue text, p_limit int, p_actor text, p_reason text DEFAULT NULL
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_n int := 0; v_id uuid;
BEGIN
    IF p_limit IS NULL THEN
        RAISE EXCEPTION 'purge limit must not be null' USING ERRCODE = 'TQ422';
    END IF;
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
               error = taskq.truncate_utf8(COALESCE(p_reason, 'purged by operator'), 2048),
               cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = taskq.truncate_utf8(p_reason, 2048),
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
               error = taskq.truncate_utf8(COALESCE(p_reason, 'cancelled by operator'), 2048),
               cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = taskq.truncate_utf8(p_reason, 2048),
               finished_at = now(), updated_at = now() WHERE id = p_job_id;
        DELETE FROM taskq.job_deps WHERE job_id = p_job_id;         -- no deps can exist in 0.1
        PERFORM taskq.emit_event(p_job_id, NULL, 'cancelled', p_actor, p_reason, NULL);
        RETURN QUERY SELECT 'cancelled'::text, 'cancelled'::text;
        RETURN;
    END IF;

    UPDATE taskq.jobs SET cancel_requested_at = now(), cancel_reason = taskq.truncate_utf8(p_reason, 2048),
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
CREATE OR REPLACE FUNCTION taskq.redrive_failed(p_queue text, p_limit int, p_actor text)
RETURNS TABLE (redriven int, skipped int)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_id uuid; v_r int := 0; v_s int := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN
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
CREATE OR REPLACE FUNCTION taskq.tick(p_reap_limit int DEFAULT 200)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_out jsonb := '{}'; v_n int;
BEGIN
    IF p_reap_limit IS NULL THEN
        RAISE EXCEPTION 'reap limit must not be null' USING ERRCODE = 'TQ422';
    END IF;
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

-- Bring pre-0.1.1 diagnostic rows under the byte ceilings before advertising
-- the patched contract. Application roles have no table DML; migration owner only.
UPDATE taskq.jobs
   SET error = taskq.truncate_utf8(error, 2048),
       cancel_reason = taskq.truncate_utf8(cancel_reason, 2048)
 WHERE octet_length(COALESCE(error, '')) > 2048
    OR octet_length(COALESCE(cancel_reason, '')) > 2048;

UPDATE taskq.job_attempts
   SET error = taskq.truncate_utf8(error, 2048)
 WHERE octet_length(COALESCE(error, '')) > 2048;

UPDATE taskq.job_events
   SET message = taskq.truncate_utf8(message, 500)
 WHERE octet_length(COALESCE(message, '')) > 500;

INSERT INTO taskq.meta (key, value, updated_at)
VALUES ('contract_version', '"0.1.1"'::jsonb, now())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;

