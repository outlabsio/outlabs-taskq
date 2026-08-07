-- outlabs-taskq — migration 0037: queue-scoped operator audit log (SQL contract 0.6.5)
--
-- Closes two flow-control observability gaps in one slice:
--   1. Manual breaker verbs (trip_breaker / force_close_breaker) emitted NO record
--      — only automatic settle-driven transitions emit job_events. An operator who
--      force-tripped a queue left no trace of who/when.
--   2. No config-history — set_breaker_config/rate/latency and set_priority_aging
--      changed queue_flow in place with no before/after record.
--
-- Both are now captured in a new append-only, queue-scoped audit table. The write
-- lives INSIDE each operator verb (not a table trigger) because only the verb sees
-- its actor argument — actor attribution is the whole point of the log.
--
-- DESIGN:
--   * taskq.queue_audit — append-only (id/queue/event_type/actor/detail/created_at),
--     modelled on job_events. Written only by taskq._audit_queue() (owner-internal),
--     read via taskq.list_queue_audit() (operator + observer).
--   * The six queue-scoped operator verbs are CREATE OR REPLACE'd to record their
--     action after a successful mutation: config setters log {before, after} of the
--     config subset they own (real config-history); trip/force-close log the state
--     transition. Signatures/attributes are byte-identical, so the function manifest
--     is unchanged for them — only the two new functions + the new table move it.
--   * set_flow_limit is key-scoped (not queue-scoped) and stays out of this slice.
--
-- COMPATIBILITY: additive; contract 0.6.4 -> 0.6.5. Verbs behave exactly as before
-- plus an audit row on success; a failed verb rolls back its (absent) audit row.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.6.4"'::jsonb THEN
        RAISE EXCEPTION '0037 requires SQL contract 0.6.4, found %', v_contract;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Append-only audit table (job_events precedent: identity PK, BRIN on time).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS taskq.queue_audit (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    queue       text NOT NULL,
    event_type  text NOT NULL CHECK (char_length(event_type) <= 64),
    actor       text,                    -- 'operator:<who>' (the verb's actor arg)
    detail      jsonb,                   -- {before, after} for config; {before, after} state
    created_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.queue_audit OWNER TO taskq_owner;
CREATE INDEX IF NOT EXISTS queue_audit_queue_idx ON taskq.queue_audit (queue, id);
CREATE INDEX IF NOT EXISTS queue_audit_time_brin ON taskq.queue_audit USING brin (created_at);

-- ---------------------------------------------------------------------------
-- Owner-internal writer. Called only by the SECURITY DEFINER operator verbs
-- (which run as taskq_owner), so it needs no role grant of its own.
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq._audit_queue(
    p_queue text, p_event_type text, p_actor text, p_detail jsonb
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    INSERT INTO taskq.queue_audit (queue, event_type, actor, detail)
    VALUES (p_queue, p_event_type, p_actor, p_detail);
END $$;
ALTER FUNCTION taskq._audit_queue(text, text, text, jsonb) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._audit_queue(text, text, text, jsonb) FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Read model: newest-first, keyset-paginated by id (< p_before_id).
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq.list_queue_audit(
    p_queue text, p_limit integer DEFAULT 50, p_before_id bigint DEFAULT NULL
) RETURNS TABLE (
    id bigint, event_type text, actor text, detail jsonb, created_at timestamptz
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF p_queue IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR (p_before_id IS NOT NULL AND p_before_id < 1) THEN
        RAISE EXCEPTION 'invalid queue audit page input' USING ERRCODE = 'TQ422';
    END IF;
    RETURN QUERY
        SELECT a.id, a.event_type, a.actor, a.detail, a.created_at
          FROM taskq.queue_audit AS a
         WHERE a.queue = p_queue AND (p_before_id IS NULL OR a.id < p_before_id)
         ORDER BY a.id DESC
         LIMIT p_limit;
END $$;
ALTER FUNCTION taskq.list_queue_audit(text, integer, bigint) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_queue_audit(text, integer, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_queue_audit(text, integer, bigint) TO taskq_operator;
GRANT EXECUTE ON FUNCTION taskq.list_queue_audit(text, integer, bigint) TO taskq_observer;

-- ---------------------------------------------------------------------------
-- Operator verbs, reproduced with an audit-write on success. Bodies are the
-- 0.6.4 versions verbatim plus a before/after snapshot and a _audit_queue call.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION taskq.set_breaker_config(
    p_queue text, p_failure_threshold integer, p_cooldown_seconds integer DEFAULT 30,
    p_half_open_successes integer DEFAULT 1, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_before jsonb; v_after jsonb;
BEGIN
    IF NOT taskq.has_capability('circuit_breaker') THEN
        RAISE EXCEPTION 'circuit_breaker capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"circuit_breaker_inactive"}';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM taskq.queues WHERE name = p_queue) THEN
        RAISE EXCEPTION 'taskq: no such queue' USING ERRCODE = 'TQ001';
    END IF;
    IF p_failure_threshold IS NOT NULL AND p_failure_threshold <= 0 THEN
        RAISE EXCEPTION 'failure_threshold must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    IF p_cooldown_seconds IS NULL OR p_cooldown_seconds NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'cooldown_seconds must be 1..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF p_half_open_successes IS NULL OR p_half_open_successes NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'half_open_successes must be 1..100' USING ERRCODE = 'TQ422';
    END IF;
    SELECT jsonb_build_object('failure_threshold', breaker_failure_threshold,
        'cooldown_seconds', breaker_cooldown_seconds,
        'half_open_successes', breaker_half_open_successes)
      INTO v_before FROM taskq.queue_flow WHERE queue = p_queue;
    INSERT INTO taskq.queue_flow (queue, breaker_failure_threshold,
        breaker_cooldown_seconds, breaker_half_open_successes, updated_at)
    VALUES (p_queue, p_failure_threshold, p_cooldown_seconds, p_half_open_successes, now())
    ON CONFLICT (queue) DO UPDATE SET
        breaker_failure_threshold = excluded.breaker_failure_threshold,
        breaker_cooldown_seconds = excluded.breaker_cooldown_seconds,
        breaker_half_open_successes = excluded.breaker_half_open_successes,
        updated_at = now();
    SELECT jsonb_build_object('failure_threshold', breaker_failure_threshold,
        'cooldown_seconds', breaker_cooldown_seconds,
        'half_open_successes', breaker_half_open_successes)
      INTO v_after FROM taskq.queue_flow WHERE queue = p_queue;
    PERFORM taskq._audit_queue(p_queue, 'breaker_config_set', p_actor,
        jsonb_build_object('before', v_before, 'after', v_after));
    RETURN CASE WHEN v_before IS NOT NULL THEN 'updated' ELSE 'created' END;
END $$;

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
           breaker_probe_successes = 0, ramp_started_at = now(), updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    PERFORM taskq._audit_queue(p_queue, 'breaker_force_closed', p_actor,
        jsonb_build_object('before', v_before, 'after', 'closed'));
    RETURN 'closed';
END $$;

CREATE OR REPLACE FUNCTION taskq.set_breaker_rate(
    p_queue text, p_failure_ratio numeric, p_window_seconds integer DEFAULT 60,
    p_min_volume integer DEFAULT 10, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_before jsonb; v_after jsonb;
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
    SELECT jsonb_build_object('failure_ratio', breaker_failure_ratio,
        'window_seconds', breaker_window_seconds, 'min_volume', breaker_min_volume)
      INTO v_before FROM taskq.queue_flow WHERE queue = p_queue;
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
    SELECT jsonb_build_object('failure_ratio', breaker_failure_ratio,
        'window_seconds', breaker_window_seconds, 'min_volume', breaker_min_volume)
      INTO v_after FROM taskq.queue_flow WHERE queue = p_queue;
    PERFORM taskq._audit_queue(p_queue, 'breaker_rate_set', p_actor,
        jsonb_build_object('before', v_before, 'after', v_after));
    RETURN CASE WHEN p_failure_ratio IS NULL THEN 'cleared' ELSE 'updated' END;
END $$;

CREATE OR REPLACE FUNCTION taskq.set_breaker_latency(
    p_queue text, p_threshold_ms integer, p_window_seconds integer DEFAULT 60,
    p_min_volume integer DEFAULT 10, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_before jsonb; v_after jsonb;
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
    SELECT jsonb_build_object('threshold_ms', breaker_latency_threshold_ms,
        'window_seconds', breaker_latency_window_seconds,
        'min_volume', breaker_latency_min_volume)
      INTO v_before FROM taskq.queue_flow WHERE queue = p_queue;
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
    SELECT jsonb_build_object('threshold_ms', breaker_latency_threshold_ms,
        'window_seconds', breaker_latency_window_seconds,
        'min_volume', breaker_latency_min_volume)
      INTO v_after FROM taskq.queue_flow WHERE queue = p_queue;
    PERFORM taskq._audit_queue(p_queue, 'breaker_latency_set', p_actor,
        jsonb_build_object('before', v_before, 'after', v_after));
    RETURN CASE WHEN p_threshold_ms IS NULL THEN 'cleared' ELSE 'updated' END;
END $$;

CREATE OR REPLACE FUNCTION taskq.set_priority_aging(
    p_queue text, p_aging_seconds integer, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_before jsonb; v_after jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM taskq.queues WHERE name = p_queue) THEN
        RAISE EXCEPTION 'taskq: no such queue' USING ERRCODE = 'TQ001';
    END IF;
    IF p_aging_seconds IS NOT NULL AND p_aging_seconds <= 0 THEN
        RAISE EXCEPTION 'aging_seconds must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    SELECT jsonb_build_object('aging_seconds', priority_aging_seconds)
      INTO v_before FROM taskq.queue_flow WHERE queue = p_queue;
    INSERT INTO taskq.queue_flow (queue, priority_aging_seconds, updated_at)
    VALUES (p_queue, p_aging_seconds, now())
    ON CONFLICT (queue) DO UPDATE SET
        priority_aging_seconds = excluded.priority_aging_seconds, updated_at = now();
    SELECT jsonb_build_object('aging_seconds', priority_aging_seconds)
      INTO v_after FROM taskq.queue_flow WHERE queue = p_queue;
    PERFORM taskq._audit_queue(p_queue, 'aging_set', p_actor,
        jsonb_build_object('before', v_before, 'after', v_after));
    RETURN CASE WHEN v_before IS NOT NULL THEN 'updated' ELSE 'created' END;
END $$;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.5"'::jsonb, now())
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
        RAISE EXCEPTION '0037 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
