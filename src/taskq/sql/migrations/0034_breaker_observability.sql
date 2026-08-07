-- outlabs-taskq — migration 0034: breaker observability (SQL contract 0.6.2)
--
-- Closes the breaker observability gap flagged in the 0.6 review: a tripped
-- breaker was invisible on the surfaces operators watch. Two additions, both
-- body-only redefinitions (no signature/type change, so the machine manifest is
-- unaffected beyond the contract-version bump):
--
--   1. queue_health gains a `breaker_open` verdict (ranked right after `paused` —
--      both are deliberate not-claiming states) and breaker detail in its jsonb,
--      so `taskq queue health` / the health read model show a tripped breaker.
--   2. The settle trigger emits `breaker_opened` / `breaker_reopened` /
--      `breaker_closed` job events (via the safe emit_event helper) on the job
--      whose settle drove the transition — an audit timeline for automatic
--      transitions, queryable queue-wide as `event_type LIKE 'breaker_%'`.
--
-- The 0.6 spec §2.3/§2.5 called for these events; this is the deferred
-- implementation. Manual verb transitions (trip_breaker/force_close_breaker) are
-- operator-initiated and recorded by their actor argument; a dedicated
-- queue-scoped audit table for those is a documented follow-up.
--
-- COMPATIBILITY: additive and non-breaking. `breaker_open` is a new verdict value
-- (verdicts are free-text, not an enum); consumers that switch on the vocabulary
-- should add a case. Contract 0.6.1 -> 0.6.2.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.6.1"'::jsonb THEN
        RAISE EXCEPTION '0034 requires SQL contract 0.6.1, found %', v_contract;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. queue_health_internal: breaker_open verdict + breaker detail.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION taskq.queue_health_internal()
RETURNS TABLE (queue text, verdict text, detail jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_lag constant numeric := 600;
    v_snapshot jsonb;
    q record;
    v_stats jsonb;
    v_levels jsonb;
    v_rates jsonb;
    v_ready numeric;
    v_oldest numeric;
    v_settled_rate numeric;
    v_eta numeric;
    v_depth numeric;
    v_online integer;
    v_verdict text;
    v_breaker_on boolean;
BEGIN
    IF NOT taskq.has_capability('queue_counters') THEN
        RETURN;
    END IF;
    SELECT c.data INTO v_snapshot FROM taskq.control_state c WHERE c.key = 'stats_snapshot';
    FOR q IN
        SELECT qs.name, qs.paused_at, qs.max_depth,
               COALESCE(c.blocked, 0) AS blocked,
               COALESCE(c.queued, 0) AS queued,
               COALESCE(c.running, 0) AS running,
               f.breaker_state, f.breaker_failure_threshold,
               f.breaker_tripped_at, f.breaker_opened_total
          FROM taskq.queues qs
          LEFT JOIN taskq.queue_counters c ON c.queue = qs.name
          LEFT JOIN taskq.queue_flow f ON f.queue = qs.name
         ORDER BY qs.name
    LOOP
        v_stats := COALESCE(v_snapshot -> 'queues' -> q.name, '{}'::jsonb);
        v_levels := jsonb_build_object(
            'blocked', q.blocked, 'queued', q.queued, 'running', q.running);
        v_rates := CASE WHEN jsonb_typeof(v_stats -> 'rates') = 'object'
                        THEN v_stats -> 'rates' ELSE NULL END;
        v_ready := COALESCE((v_stats ->> 'ready')::numeric, 0);
        v_oldest := COALESCE((v_stats ->> 'oldest_ready_seconds')::numeric, 0);
        v_settled_rate := COALESCE((v_rates ->> 'settled_per_s')::numeric, 0);
        v_eta := CASE WHEN jsonb_typeof(v_stats -> 'drain_eta_seconds') = 'number'
                      THEN (v_stats ->> 'drain_eta_seconds')::numeric ELSE NULL END;
        v_depth := q.blocked + q.queued;
        v_breaker_on := q.breaker_failure_threshold IS NOT NULL
                        AND q.breaker_state IS DISTINCT FROM 'closed';
        SELECT count(*)::integer INTO v_online
          FROM taskq.workers w
         WHERE w.last_seen_at > now() - interval '180 seconds'
           AND q.name = ANY(w.queues);
        v_verdict := CASE
            WHEN q.paused_at IS NOT NULL THEN 'paused'
            WHEN v_breaker_on AND q.breaker_state = 'open' THEN 'breaker_open'
            WHEN v_ready > 0 AND v_online = 0 THEN 'no_consumer'
            WHEN q.max_depth IS NOT NULL AND v_depth >= q.max_depth THEN 'choking'
            WHEN v_eta IS NOT NULL AND v_eta > v_lag THEN 'behind'
            WHEN v_settled_rate > 0 AND v_oldest > 2 * v_lag THEN 'starved'
            WHEN v_depth = 0 AND q.running = 0 AND v_settled_rate = 0 THEN 'inactive'
            ELSE 'ok'
        END;
        queue := q.name;
        verdict := v_verdict;
        detail := jsonb_build_object(
            'levels', v_levels,
            'ready', v_ready,
            'oldest_ready_seconds', v_oldest,
            'rates', COALESCE(v_rates, 'null'::jsonb),
            'drain_eta_seconds', COALESCE(to_jsonb(v_eta), 'null'::jsonb),
            'online_workers', v_online,
            'max_depth', COALESCE(to_jsonb(q.max_depth), 'null'::jsonb),
            'lag_seconds', v_lag,
            'breaker', CASE WHEN q.breaker_failure_threshold IS NULL THEN 'null'::jsonb
                       ELSE jsonb_build_object(
                           'state', q.breaker_state,
                           'tripped_at', to_jsonb(q.breaker_tripped_at),
                           'opened_total', q.breaker_opened_total) END);
        RETURN NEXT;
    END LOOP;
END $$;
ALTER FUNCTION taskq.queue_health_internal() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.queue_health_internal() FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 2. _breaker_on_settle: emit transition events on the driving job.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION taskq._breaker_on_settle()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_flow taskq.queue_flow%ROWTYPE;
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
                jsonb_build_object('queue', NEW.queue,
                    'opened_total', v_flow.breaker_opened_total + 1));
        ELSIF v_flow.breaker_state = 'closed' THEN
            IF v_flow.breaker_failure_streak + 1 >= v_flow.breaker_failure_threshold THEN
                UPDATE taskq.queue_flow
                   SET breaker_state = 'open', breaker_tripped_at = now(),
                       breaker_failure_streak = v_flow.breaker_failure_streak + 1,
                       breaker_opened_total = breaker_opened_total + 1, updated_at = now()
                 WHERE queue = NEW.queue;
                PERFORM taskq.emit_event(NEW.id, NULL, 'breaker_opened', 'system',
                    'consecutive failures reached the breaker threshold',
                    jsonb_build_object('queue', NEW.queue,
                        'failure_streak', v_flow.breaker_failure_streak + 1,
                        'threshold', v_flow.breaker_failure_threshold,
                        'opened_total', v_flow.breaker_opened_total + 1));
            ELSE
                UPDATE taskq.queue_flow
                   SET breaker_failure_streak = v_flow.breaker_failure_streak + 1,
                       updated_at = now()
                 WHERE queue = NEW.queue;
            END IF;
        END IF;
    ELSIF NEW.status = 'succeeded' THEN
        IF v_flow.breaker_state = 'half_open' THEN
            IF v_flow.breaker_probe_successes + 1
               >= COALESCE(v_flow.breaker_half_open_successes, 1) THEN
                UPDATE taskq.queue_flow
                   SET breaker_state = 'closed', breaker_failure_streak = 0,
                       breaker_probe_successes = 0, ramp_started_at = now(), updated_at = now()
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
        ELSIF v_flow.breaker_state = 'closed' AND v_flow.breaker_failure_streak <> 0 THEN
            UPDATE taskq.queue_flow
               SET breaker_failure_streak = 0, updated_at = now()
             WHERE queue = NEW.queue;
        END IF;
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION taskq._breaker_on_settle() OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._breaker_on_settle() FROM PUBLIC;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.2"'::jsonb, now())
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
        RAISE EXCEPTION '0034 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
