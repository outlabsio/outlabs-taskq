-- outlabs-taskq — migration 0036: breaker latency tripping (SQL contract 0.6.4)
--
-- Completes the breaker trip-trigger set (streak / rate / latency, research P16).
-- The breaker already catches downstream FAILURE (consecutive streak, sustained
-- rate); this adds the DEGRADATION case — a downstream that is slow but still
-- succeeding. Trips when the average execution latency over a rolling window
-- exceeds a threshold. Purely additive; a breaker with no latency config behaves
-- exactly as under 0.6.3.
--
-- DESIGN:
--   * New verb set_breaker_latency(queue, threshold_ms, window_seconds, min_volume,
--     actor) — NULL threshold disables. Its own tumbling window (parallel to the
--     rate window — zero config ambiguity), requiring a configured breaker.
--   * The settle trigger feeds EVERY terminal settle's execution latency into the
--     latency window (a slow success raises latency too, unlike the rate window
--     which only rises on failures). Latency for a settle = now() - the settling
--     attempt's claimed_at (job_attempts.claimed_at via NEW.finished_by_attempt_id);
--     jobs.started_at is the FIRST claim (COALESCE-preserved across retries) and
--     would include retry wait, so it is not used.
--   * A configured breaker now trips on streak OR rate OR latency; the
--     breaker_opened event's `reason` gains 'latency'. Recovery resets all windows.
--
-- COMPATIBILITY: additive; contract 0.6.3 -> 0.6.4. The per-settle latency lookup
-- runs only for queues that set breaker_latency_threshold_ms (opt-in overhead).

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.6.3"'::jsonb THEN
        RAISE EXCEPTION '0036 requires SQL contract 0.6.3, found %', v_contract;
    END IF;
END $$;

ALTER TABLE taskq.queue_flow
    ADD COLUMN breaker_latency_threshold_ms integer
        CHECK (breaker_latency_threshold_ms IS NULL OR breaker_latency_threshold_ms > 0),
    ADD COLUMN breaker_latency_window_seconds integer
        CHECK (breaker_latency_window_seconds IS NULL
               OR breaker_latency_window_seconds BETWEEN 1 AND 86400),
    ADD COLUMN breaker_latency_min_volume integer
        CHECK (breaker_latency_min_volume IS NULL OR breaker_latency_min_volume > 0),
    ADD COLUMN breaker_latency_window_start timestamptz,
    ADD COLUMN breaker_latency_sum_ms bigint NOT NULL DEFAULT 0,
    ADD COLUMN breaker_latency_count integer NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- Operator verb: configure (or clear, with NULL threshold) latency tripping.
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq.set_breaker_latency(
    p_queue text, p_threshold_ms integer, p_window_seconds integer DEFAULT 60,
    p_min_volume integer DEFAULT 10, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF NOT taskq.has_capability('circuit_breaker') THEN
        RAISE EXCEPTION 'circuit_breaker capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"circuit_breaker_inactive"}';
    END IF;
    IF p_threshold_ms IS NOT NULL AND p_threshold_ms <= 0 THEN
        RAISE EXCEPTION 'threshold_ms must be > 0' USING ERRCODE = 'TQ422';
    END IF;
    IF p_threshold_ms IS NOT NULL THEN
        IF p_window_seconds IS NULL OR p_window_seconds NOT BETWEEN 1 AND 86400 THEN
            RAISE EXCEPTION 'window_seconds must be 1..86400' USING ERRCODE = 'TQ422';
        END IF;
        IF p_min_volume IS NULL OR p_min_volume <= 0 THEN
            RAISE EXCEPTION 'min_volume must be > 0' USING ERRCODE = 'TQ422';
        END IF;
    END IF;
    UPDATE taskq.queue_flow
       SET breaker_latency_threshold_ms = p_threshold_ms,
           breaker_latency_window_seconds = CASE WHEN p_threshold_ms IS NULL THEN NULL
                                            ELSE p_window_seconds END,
           breaker_latency_min_volume = CASE WHEN p_threshold_ms IS NULL THEN NULL
                                        ELSE p_min_volume END,
           breaker_latency_window_start = NULL, breaker_latency_sum_ms = 0,
           breaker_latency_count = 0, updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    RETURN CASE WHEN p_threshold_ms IS NULL THEN 'cleared' ELSE 'updated' END;
END $$;
ALTER FUNCTION taskq.set_breaker_latency(text, integer, integer, integer, text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_breaker_latency(text, integer, integer, integer, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_breaker_latency(text, integer, integer, integer, text)
    TO taskq_operator;

-- ---------------------------------------------------------------------------
-- Settle trigger: streak OR rate OR latency; latency fed on every settle.
-- ---------------------------------------------------------------------------
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
    ELSE
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

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.4"'::jsonb, now())
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
        RAISE EXCEPTION '0036 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
