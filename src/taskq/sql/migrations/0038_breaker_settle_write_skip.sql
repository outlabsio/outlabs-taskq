-- outlabs-taskq — migration 0038: breaker settle write-skip (body-only, no contract change)
--
-- Body-only optimization (no contract/capability change; the trigger function's
-- signature is unchanged, so the machine manifest is unaffected — 0028 precedent).
--
-- 0.6.4 (migration 0036) unified the closed-state settle handler and, in doing so,
-- made it UPDATE the queue_flow row on EVERY closed-state settle — even a healthy
-- success on a breaker with no rate/latency config and a zero streak, where nothing
-- actually changed. 0.6.3 skipped that no-op write. For a high-throughput breaker
-- queue that is the common case (every job succeeds), so the write is pure overhead:
-- one single-row queue_flow UPDATE + WAL per settle for no state change.
--
-- This restores the skip: the non-trip closed-state branch now writes only when a
-- tracked field actually changed (streak, or the rate/latency window state). The
-- latency and rate feeds still write on every settle that advances their window —
-- so slow-success latency tripping and intermittent-failure rate tripping are
-- unchanged; only the genuinely-nothing-changed settle stops writing. Trip paths,
-- half-open probing, recovery, and event emission are all byte-identical to 0.6.4.

CREATE OR REPLACE FUNCTION taskq._breaker_on_settle()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_flow taskq.queue_flow%ROWTYPE;
    v_streak integer;
    v_win_start timestamptz; v_win_fail integer; v_win_succ integer;
    v_lat_start timestamptz; v_lat_sum bigint; v_lat_count integer;
    v_lat_ms numeric;
    v_streak_trip boolean; v_rate_trip boolean; v_lat_trip boolean;
    v_reason text;
BEGIN
    SELECT * INTO v_flow FROM taskq.queue_flow WHERE queue = NEW.queue FOR UPDATE;
    IF NOT FOUND OR v_flow.breaker_failure_threshold IS NULL THEN
        RETURN NULL;
    END IF;

    IF v_flow.breaker_state = 'half_open' THEN
        IF NEW.status = 'failed' THEN
            UPDATE taskq.queue_flow
               SET breaker_state = 'open', breaker_tripped_at = now(),
                   breaker_probe_successes = 0,
                   breaker_opened_total = breaker_opened_total + 1, updated_at = now()
             WHERE queue = NEW.queue;
            PERFORM taskq.emit_event(NEW.id, NULL, 'breaker_reopened', 'system',
                'half-open probe failed; breaker re-opened',
                jsonb_build_object('queue', NEW.queue, 'reason', 'probe_failed',
                    'opened_total', v_flow.breaker_opened_total + 1));
        ELSIF NEW.status = 'succeeded' THEN
            IF v_flow.breaker_probe_successes + 1
               >= COALESCE(v_flow.breaker_half_open_successes, 1) THEN
                UPDATE taskq.queue_flow
                   SET breaker_state = 'closed', breaker_failure_streak = 0,
                       breaker_probe_successes = 0, breaker_window_start = NULL,
                       breaker_window_failures = 0, breaker_window_successes = 0,
                       breaker_latency_window_start = NULL, breaker_latency_sum_ms = 0,
                       breaker_latency_count = 0, ramp_started_at = now(), updated_at = now()
                 WHERE queue = NEW.queue;
                PERFORM taskq.emit_event(NEW.id, NULL, 'breaker_closed', 'system',
                    'half-open probes succeeded; breaker closed and ramping',
                    jsonb_build_object('queue', NEW.queue,
                        'opened_total', v_flow.breaker_opened_total));
            ELSE
                UPDATE taskq.queue_flow
                   SET breaker_probe_successes = v_flow.breaker_probe_successes + 1,
                       updated_at = now()
                 WHERE queue = NEW.queue;
            END IF;
        END IF;
        RETURN NULL;
    END IF;

    IF v_flow.breaker_state <> 'closed' THEN
        RETURN NULL;  -- open: no settles feed the closed-state logic
    END IF;

    -- Latency window (fed on every terminal settle when configured).
    v_lat_start := v_flow.breaker_latency_window_start;
    v_lat_sum := v_flow.breaker_latency_sum_ms;
    v_lat_count := v_flow.breaker_latency_count;
    v_lat_trip := false;
    IF v_flow.breaker_latency_threshold_ms IS NOT NULL THEN
        SELECT extract(epoch FROM (now() - a.claimed_at)) * 1000 INTO v_lat_ms
          FROM taskq.job_attempts a WHERE a.id = NEW.finished_by_attempt_id;
        IF v_lat_ms IS NOT NULL THEN
            IF v_lat_start IS NULL
               OR now() >= v_lat_start
                          + make_interval(secs => v_flow.breaker_latency_window_seconds) THEN
                v_lat_start := now(); v_lat_sum := v_lat_ms::bigint; v_lat_count := 1;
            ELSE
                v_lat_sum := v_lat_sum + v_lat_ms::bigint;
                v_lat_count := v_lat_count + 1;
            END IF;
            v_lat_trip := v_lat_count >= v_flow.breaker_latency_min_volume
                AND v_lat_sum / v_lat_count >= v_flow.breaker_latency_threshold_ms;
        END IF;
    END IF;

    -- Rate window + streak.
    v_win_start := v_flow.breaker_window_start;
    v_win_fail := v_flow.breaker_window_failures;
    v_win_succ := v_flow.breaker_window_successes;
    v_rate_trip := false;
    v_streak_trip := false;
    IF NEW.status = 'failed' THEN
        v_streak := v_flow.breaker_failure_streak + 1;
        IF v_flow.breaker_failure_ratio IS NOT NULL THEN
            IF v_win_start IS NULL
               OR now() >= v_win_start + make_interval(secs => v_flow.breaker_window_seconds) THEN
                v_win_start := now(); v_win_fail := 1; v_win_succ := 0;
            ELSE
                v_win_fail := v_win_fail + 1;
            END IF;
            v_rate_trip := (v_win_fail + v_win_succ) >= v_flow.breaker_min_volume
                AND v_win_fail::numeric / (v_win_fail + v_win_succ)
                    >= v_flow.breaker_failure_ratio;
        END IF;
        v_streak_trip := v_streak >= v_flow.breaker_failure_threshold;
    ELSE  -- succeeded
        v_streak := 0;
        IF v_flow.breaker_failure_ratio IS NOT NULL THEN
            IF v_win_start IS NULL
               OR now() >= v_win_start + make_interval(secs => v_flow.breaker_window_seconds) THEN
                v_win_start := now(); v_win_fail := 0; v_win_succ := 1;
            ELSE
                v_win_succ := v_win_succ + 1;
            END IF;
        END IF;
    END IF;

    IF v_streak_trip OR v_rate_trip OR v_lat_trip THEN
        v_reason := CASE WHEN v_streak_trip THEN 'streak'
                         WHEN v_rate_trip THEN 'rate' ELSE 'latency' END;
        UPDATE taskq.queue_flow
           SET breaker_state = 'open', breaker_tripped_at = now(),
               breaker_failure_streak = v_streak,
               breaker_window_start = v_win_start, breaker_window_failures = v_win_fail,
               breaker_window_successes = v_win_succ,
               breaker_latency_window_start = v_lat_start,
               breaker_latency_sum_ms = v_lat_sum, breaker_latency_count = v_lat_count,
               breaker_opened_total = breaker_opened_total + 1, updated_at = now()
         WHERE queue = NEW.queue;
        PERFORM taskq.emit_event(NEW.id, NULL, 'breaker_opened', 'system',
            CASE v_reason
                WHEN 'streak' THEN 'consecutive failures reached the breaker threshold'
                WHEN 'rate' THEN 'failure rate over the window reached the breaker ratio'
                ELSE 'average latency over the window reached the breaker threshold' END,
            jsonb_build_object('queue', NEW.queue, 'reason', v_reason,
                'failure_streak', v_streak, 'threshold', v_flow.breaker_failure_threshold,
                'window_failures', v_win_fail, 'window_total', v_win_fail + v_win_succ,
                'latency_avg_ms', CASE WHEN v_lat_count > 0 THEN v_lat_sum / v_lat_count END,
                'opened_total', v_flow.breaker_opened_total + 1));
    ELSIF v_streak IS DISTINCT FROM v_flow.breaker_failure_streak
       OR v_win_start IS DISTINCT FROM v_flow.breaker_window_start
       OR v_win_fail IS DISTINCT FROM v_flow.breaker_window_failures
       OR v_win_succ IS DISTINCT FROM v_flow.breaker_window_successes
       OR v_lat_start IS DISTINCT FROM v_flow.breaker_latency_window_start
       OR v_lat_sum IS DISTINCT FROM v_flow.breaker_latency_sum_ms
       OR v_lat_count IS DISTINCT FROM v_flow.breaker_latency_count THEN
        -- Something advanced (streak, or a rate/latency window) but did not trip:
        -- persist it. A genuinely no-op settle (healthy success, nothing configured
        -- to accumulate) falls through and writes nothing.
        UPDATE taskq.queue_flow
           SET breaker_failure_streak = v_streak,
               breaker_window_start = v_win_start, breaker_window_failures = v_win_fail,
               breaker_window_successes = v_win_succ,
               breaker_latency_window_start = v_lat_start,
               breaker_latency_sum_ms = v_lat_sum, breaker_latency_count = v_lat_count,
               updated_at = now()
         WHERE queue = NEW.queue;
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION taskq._breaker_on_settle() OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._breaker_on_settle() FROM PUBLIC;

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
        RAISE EXCEPTION '0038 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
