-- outlabs-taskq — migration 0035: breaker rate/window tripping (SQL contract 0.6.3)
--
-- Closes the streak-only breaker's intermittent-failure blindness (flagged in the
-- 0.6 review). Streak tripping resets on any success, so a downstream failing at,
-- say, 50% (fail, succeed, fail, succeed) never trips. This adds an OPTIONAL
-- rolling-window failure-RATE trip alongside the streak — a configured breaker now
-- trips on EITHER a consecutive-failure streak (fast, hard-down) OR a sustained
-- failure ratio over a window (slow, flaky). Purely additive; a breaker that sets
-- no rate config behaves exactly as under 0.6.2.
--
-- DESIGN:
--   * New verb set_breaker_rate(queue, failure_ratio, window_seconds, min_volume,
--     actor). NULL ratio disables rate tripping. Requires an already-configured
--     breaker (set_breaker_config first). Streak stays the always-on baseline.
--   * Tumbling window on queue_flow (window_start + failure/success counts): reset
--     when now >= window_start + window_seconds, then count this settle. Trip when
--     total settles in the window >= min_volume AND failures/total >= failure_ratio.
--     Checked on failures only (a success only lowers the ratio). Cheap: three
--     integer/timestamp columns, no arrays.
--   * The breaker_opened event gains a `reason` ('streak' | 'rate') and window
--     counts, so the audit timeline says WHY it tripped.
--   * On recovery (close) the window resets, so each closed period starts fresh.
--
-- COMPATIBILITY: additive; contract 0.6.2 -> 0.6.3. set_breaker_config is unchanged
-- and does NOT clear rate config — disabling the breaker with set_breaker_config(--off)
-- leaves any rate config dormant (it is only evaluated while the breaker is on); use
-- set_breaker_rate(--off) to clear it explicitly.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.6.2"'::jsonb THEN
        RAISE EXCEPTION '0035 requires SQL contract 0.6.2, found %', v_contract;
    END IF;
END $$;

ALTER TABLE taskq.queue_flow
    ADD COLUMN breaker_failure_ratio numeric
        CHECK (breaker_failure_ratio IS NULL
               OR (breaker_failure_ratio > 0 AND breaker_failure_ratio <= 1)),
    ADD COLUMN breaker_window_seconds integer
        CHECK (breaker_window_seconds IS NULL
               OR breaker_window_seconds BETWEEN 1 AND 86400),
    ADD COLUMN breaker_min_volume integer
        CHECK (breaker_min_volume IS NULL OR breaker_min_volume > 0),
    ADD COLUMN breaker_window_start timestamptz,
    ADD COLUMN breaker_window_failures integer NOT NULL DEFAULT 0,
    ADD COLUMN breaker_window_successes integer NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- Operator verb: configure (or clear, with NULL ratio) rate tripping.
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq.set_breaker_rate(
    p_queue text, p_failure_ratio numeric, p_window_seconds integer DEFAULT 60,
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
    IF p_failure_ratio IS NOT NULL
       AND (p_failure_ratio <= 0 OR p_failure_ratio > 1) THEN
        RAISE EXCEPTION 'failure_ratio must be in (0, 1]' USING ERRCODE = 'TQ422';
    END IF;
    IF p_failure_ratio IS NOT NULL THEN
        IF p_window_seconds IS NULL OR p_window_seconds NOT BETWEEN 1 AND 86400 THEN
            RAISE EXCEPTION 'window_seconds must be 1..86400' USING ERRCODE = 'TQ422';
        END IF;
        IF p_min_volume IS NULL OR p_min_volume <= 0 THEN
            RAISE EXCEPTION 'min_volume must be > 0' USING ERRCODE = 'TQ422';
        END IF;
    END IF;
    UPDATE taskq.queue_flow
       SET breaker_failure_ratio = p_failure_ratio,
           breaker_window_seconds = CASE WHEN p_failure_ratio IS NULL THEN NULL
                                    ELSE p_window_seconds END,
           breaker_min_volume = CASE WHEN p_failure_ratio IS NULL THEN NULL
                                ELSE p_min_volume END,
           breaker_window_start = NULL, breaker_window_failures = 0,
           breaker_window_successes = 0, updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    RETURN CASE WHEN p_failure_ratio IS NULL THEN 'cleared' ELSE 'updated' END;
END $$;
ALTER FUNCTION taskq.set_breaker_rate(text, numeric, integer, integer, text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_breaker_rate(text, numeric, integer, integer, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_breaker_rate(text, numeric, integer, integer, text)
    TO taskq_operator;

-- ---------------------------------------------------------------------------
-- Settle trigger: streak OR rate tripping; window maintained in the closed state.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION taskq._breaker_on_settle()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_flow taskq.queue_flow%ROWTYPE;
    v_streak integer;
    v_win_start timestamptz;
    v_win_fail integer;
    v_win_succ integer;
    v_streak_trip boolean;
    v_rate_trip boolean;
BEGIN
    SELECT * INTO v_flow FROM taskq.queue_flow WHERE queue = NEW.queue FOR UPDATE;
    IF NOT FOUND OR v_flow.breaker_failure_threshold IS NULL THEN
        RETURN NULL;  -- breaker not configured for this queue
    END IF;

    IF NEW.status = 'failed' THEN
        IF v_flow.breaker_state = 'half_open' THEN
            UPDATE taskq.queue_flow
               SET breaker_state = 'open', breaker_tripped_at = now(),
                   breaker_probe_successes = 0,
                   breaker_opened_total = breaker_opened_total + 1, updated_at = now()
             WHERE queue = NEW.queue;
            PERFORM taskq.emit_event(NEW.id, NULL, 'breaker_reopened', 'system',
                'half-open probe failed; breaker re-opened',
                jsonb_build_object('queue', NEW.queue, 'reason', 'probe_failed',
                    'opened_total', v_flow.breaker_opened_total + 1));
        ELSIF v_flow.breaker_state = 'closed' THEN
            v_streak := v_flow.breaker_failure_streak + 1;
            -- Roll the window (rate tripping only) and count this failure.
            IF v_flow.breaker_failure_ratio IS NOT NULL THEN
                IF v_flow.breaker_window_start IS NULL
                   OR now() >= v_flow.breaker_window_start
                              + make_interval(secs => v_flow.breaker_window_seconds) THEN
                    v_win_start := now(); v_win_fail := 1; v_win_succ := 0;
                ELSE
                    v_win_start := v_flow.breaker_window_start;
                    v_win_fail := v_flow.breaker_window_failures + 1;
                    v_win_succ := v_flow.breaker_window_successes;
                END IF;
                v_rate_trip := (v_win_fail + v_win_succ) >= v_flow.breaker_min_volume
                    AND v_win_fail::numeric / (v_win_fail + v_win_succ)
                        >= v_flow.breaker_failure_ratio;
            ELSE
                v_win_start := v_flow.breaker_window_start;
                v_win_fail := v_flow.breaker_window_failures;
                v_win_succ := v_flow.breaker_window_successes;
                v_rate_trip := false;
            END IF;
            v_streak_trip := v_streak >= v_flow.breaker_failure_threshold;
            IF v_streak_trip OR v_rate_trip THEN
                UPDATE taskq.queue_flow
                   SET breaker_state = 'open', breaker_tripped_at = now(),
                       breaker_failure_streak = v_streak,
                       breaker_window_start = v_win_start,
                       breaker_window_failures = v_win_fail,
                       breaker_window_successes = v_win_succ,
                       breaker_opened_total = breaker_opened_total + 1, updated_at = now()
                 WHERE queue = NEW.queue;
                PERFORM taskq.emit_event(NEW.id, NULL, 'breaker_opened', 'system',
                    CASE WHEN v_streak_trip
                         THEN 'consecutive failures reached the breaker threshold'
                         ELSE 'failure rate over the window reached the breaker ratio' END,
                    jsonb_build_object('queue', NEW.queue,
                        'reason', CASE WHEN v_streak_trip THEN 'streak' ELSE 'rate' END,
                        'failure_streak', v_streak,
                        'threshold', v_flow.breaker_failure_threshold,
                        'window_failures', v_win_fail,
                        'window_total', v_win_fail + v_win_succ,
                        'opened_total', v_flow.breaker_opened_total + 1));
            ELSE
                UPDATE taskq.queue_flow
                   SET breaker_failure_streak = v_streak,
                       breaker_window_start = v_win_start,
                       breaker_window_failures = v_win_fail,
                       breaker_window_successes = v_win_succ, updated_at = now()
                 WHERE queue = NEW.queue;
            END IF;
        END IF;
    ELSIF NEW.status = 'succeeded' THEN
        IF v_flow.breaker_state = 'half_open' THEN
            IF v_flow.breaker_probe_successes + 1
               >= COALESCE(v_flow.breaker_half_open_successes, 1) THEN
                -- Close and reset both streak and the rate window.
                UPDATE taskq.queue_flow
                   SET breaker_state = 'closed', breaker_failure_streak = 0,
                       breaker_probe_successes = 0, breaker_window_start = NULL,
                       breaker_window_failures = 0, breaker_window_successes = 0,
                       ramp_started_at = now(), updated_at = now()
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
        ELSIF v_flow.breaker_state = 'closed' THEN
            IF v_flow.breaker_failure_ratio IS NOT NULL THEN
                -- Count the success in the window (and reset the streak).
                IF v_flow.breaker_window_start IS NULL
                   OR now() >= v_flow.breaker_window_start
                              + make_interval(secs => v_flow.breaker_window_seconds) THEN
                    v_win_start := now(); v_win_fail := 0; v_win_succ := 1;
                ELSE
                    v_win_start := v_flow.breaker_window_start;
                    v_win_fail := v_flow.breaker_window_failures;
                    v_win_succ := v_flow.breaker_window_successes + 1;
                END IF;
                UPDATE taskq.queue_flow
                   SET breaker_failure_streak = 0, breaker_window_start = v_win_start,
                       breaker_window_failures = v_win_fail,
                       breaker_window_successes = v_win_succ, updated_at = now()
                 WHERE queue = NEW.queue;
            ELSIF v_flow.breaker_failure_streak <> 0 THEN
                UPDATE taskq.queue_flow
                   SET breaker_failure_streak = 0, updated_at = now()
                 WHERE queue = NEW.queue;
            END IF;
        END IF;
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION taskq._breaker_on_settle() OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._breaker_on_settle() FROM PUBLIC;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.3"'::jsonb, now())
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
        RAISE EXCEPTION '0035 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
