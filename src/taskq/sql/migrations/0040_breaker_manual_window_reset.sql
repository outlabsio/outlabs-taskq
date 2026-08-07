-- outlabs-taskq — migration 0040: manual breaker verbs reset the rate/latency windows
--
-- Body-only fix (no contract/capability change; signatures unchanged, manifest
-- unaffected — 0028/0038 precedent). Fixes review finding F1.
--
-- force_close_breaker's reset list dates from 0.6.0 (streak + probe counters only),
-- before the 0035 rate window and 0036 latency window existed. The automatic close
-- path (the settle trigger, 0038) resets streak, probe counters AND both windows;
-- the manual recovery verb did not. Result: after an operator force-closes a
-- rate/latency-tripped breaker, the STALE tripping window survives, and the next
-- ordinary settle re-trips it — e.g. one 10ms success re-opens a latency-tripped
-- queue (avg still over threshold), one failure re-opens a rate-tripped queue.
-- The operator's intent ("resume work now") is defeated until the stale window
-- expires (window_seconds up to 86400) or they re-run set_breaker_*.
--
-- Both manual verbs now zero the rate window (start/failures/successes) and the
-- latency window (start/sum_ms/count), exactly like the settle-close path, so a
-- forced open or close starts the accumulators from a clean slate. Everything else
-- (state, streak, probe counters, ramp stamp, opened_total, the 0.6.5 audit row)
-- is byte-identical to the 0037 bodies.

CREATE OR REPLACE FUNCTION taskq.trip_breaker(p_queue text, p_actor text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_before text;
BEGIN
    IF NOT taskq.has_capability('circuit_breaker') THEN
        RAISE EXCEPTION 'circuit_breaker capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"circuit_breaker_inactive"}';
    END IF;
    SELECT breaker_state INTO v_before FROM taskq.queue_flow WHERE queue = p_queue;
    UPDATE taskq.queue_flow
       SET breaker_state = 'open', breaker_tripped_at = now(),
           breaker_probe_successes = 0, breaker_opened_total = breaker_opened_total + 1,
           breaker_window_start = NULL, breaker_window_failures = 0,
           breaker_window_successes = 0, breaker_latency_window_start = NULL,
           breaker_latency_sum_ms = 0, breaker_latency_count = 0,
           updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    PERFORM taskq._audit_queue(p_queue, 'breaker_tripped', p_actor,
        jsonb_build_object('before', v_before, 'after', 'open'));
    RETURN 'open';
END $$;

CREATE OR REPLACE FUNCTION taskq.force_close_breaker(p_queue text, p_actor text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_before text;
BEGIN
    IF NOT taskq.has_capability('circuit_breaker') THEN
        RAISE EXCEPTION 'circuit_breaker capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"circuit_breaker_inactive"}';
    END IF;
    SELECT breaker_state INTO v_before FROM taskq.queue_flow WHERE queue = p_queue;
    UPDATE taskq.queue_flow
       SET breaker_state = 'closed', breaker_failure_streak = 0,
           breaker_probe_successes = 0, ramp_started_at = now(),
           breaker_window_start = NULL, breaker_window_failures = 0,
           breaker_window_successes = 0, breaker_latency_window_start = NULL,
           breaker_latency_sum_ms = 0, breaker_latency_count = 0,
           updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    PERFORM taskq._audit_queue(p_queue, 'breaker_force_closed', p_actor,
        jsonb_build_object('before', v_before, 'after', 'closed'));
    RETURN 'closed';
END $$;

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
        RAISE EXCEPTION '0040 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
