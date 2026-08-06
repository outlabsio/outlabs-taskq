-- outlabs-taskq — migration 0031: circuit breaker install (SQL contract 0.6.0)
--
-- Installs the per-queue, streak-based circuit breaker (research P10, S6) in the
-- inactive-safe half of the 0027 activate-then-use pattern. 0032 activates the
-- `circuit_breaker` capability; until then the operator verbs raise TQ501, so no
-- queue can carry breaker config and the claim/settle hooks are inert — a queue
-- that never configures a breaker is byte-identical to 0.5.2.
--
-- DESIGN (see docs/Task Queue 0.6 Circuit Breaker Specification.md):
--   * Config AND state both live on the existing queue_flow row (set via
--     set_breaker_config), so ensure_queue / queue_profile are untouched.
--   * Enforcement is gated on breaker_failure_threshold IS NOT NULL (off by
--     default, per queue). Streak-based: trip on N consecutive TERMINAL failures.
--   * An open breaker returns the existing 0.5.0 `throttled` verdict + retry_after
--     (no worker change). Half-open admits one probe at a time via a
--     transaction-scoped advisory try-lock (deadlock-free single-flight).
--   * Recovery (close) stamps queue_flow.ramp_started_at so the fleet slow-starts
--     back in — the breaker composes with the 0.5.0 ramp for free.
--
-- MIGRATION NOTES / COMPATIBILITY:
--   * Additive: queue_flow gains columns; the two public claim_jobs wrappers are
--     redefined to consult the breaker before delegating (unattested claim body
--     unchanged); one new settle trigger; three new operator verbs; no existing
--     signature/type removed. Rolling-deploy safe.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.5.2"'::jsonb THEN
        RAISE EXCEPTION '0031 requires SQL contract 0.5.2, found %', v_contract;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. Breaker config + state, co-located on the queue_flow row (0024).
-- ---------------------------------------------------------------------------
ALTER TABLE taskq.queue_flow
    ADD COLUMN breaker_failure_threshold integer
        CHECK (breaker_failure_threshold IS NULL OR breaker_failure_threshold > 0),
    ADD COLUMN breaker_cooldown_seconds integer
        CHECK (breaker_cooldown_seconds IS NULL
               OR breaker_cooldown_seconds BETWEEN 1 AND 86400),
    ADD COLUMN breaker_half_open_successes integer
        CHECK (breaker_half_open_successes IS NULL
               OR breaker_half_open_successes BETWEEN 1 AND 100),
    ADD COLUMN breaker_state text NOT NULL DEFAULT 'closed'
        CHECK (breaker_state IN ('closed', 'open', 'half_open')),
    ADD COLUMN breaker_failure_streak integer NOT NULL DEFAULT 0,
    ADD COLUMN breaker_tripped_at timestamptz,
    ADD COLUMN breaker_probe_successes integer NOT NULL DEFAULT 0,
    ADD COLUMN breaker_opened_total bigint NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- 2. Claim-path gate. Returns NULL to proceed (breaker off/closed, or THIS call
--    is the anointed half-open probe), else a retry_after_seconds to throttle.
--    Runs inside the claim transaction, so a probe's advisory lock is held
--    through the delegated claim and released only at commit.
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq._breaker_gate(p_queue text)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_state text;
    v_threshold integer;
    v_cooldown integer;
    v_tripped timestamptz;
    v_remaining numeric;
BEGIN
    SELECT breaker_state, breaker_failure_threshold,
           COALESCE(breaker_cooldown_seconds, 30), breaker_tripped_at
      INTO v_state, v_threshold, v_cooldown, v_tripped
      FROM taskq.queue_flow WHERE queue = p_queue;
    -- No row, or breaker not configured, or closed: proceed.
    IF NOT FOUND OR v_threshold IS NULL OR v_state = 'closed' THEN
        RETURN NULL;
    END IF;

    IF v_state = 'open' THEN
        v_remaining := extract(epoch FROM (v_tripped + make_interval(secs => v_cooldown) - now()));
        IF v_remaining > 0 THEN
            RETURN greatest(1, ceil(v_remaining)::integer);
        END IF;
        -- Cooldown elapsed: single-flight probe election.
        IF pg_try_advisory_xact_lock(hashtextextended('taskq.breaker:' || p_queue, 0)) THEN
            UPDATE taskq.queue_flow SET breaker_state = 'half_open', updated_at = now()
             WHERE queue = p_queue;
            RETURN NULL;  -- this call is the probe
        END IF;
        RETURN 1;  -- another worker is probing
    END IF;

    -- half_open: admit one probe at a time; others wait for it to settle.
    IF pg_try_advisory_xact_lock(hashtextextended('taskq.breaker:' || p_queue, 0)) THEN
        RETURN NULL;
    END IF;
    RETURN 1;
END $$;
ALTER FUNCTION taskq._breaker_gate(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._breaker_gate(text) FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 3. Settle feed. A per-row trigger on terminal status transitions updates the
--    breaker for breaker-configured queues only (WHEN clause keeps it off the
--    hot path for everyone else). Mirrors the 0.4 counter-trigger placement.
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq._breaker_on_settle()
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
        ELSIF v_flow.breaker_state = 'closed' THEN
            IF v_flow.breaker_failure_streak + 1 >= v_flow.breaker_failure_threshold THEN
                UPDATE taskq.queue_flow
                   SET breaker_state = 'open', breaker_tripped_at = now(),
                       breaker_failure_streak = v_flow.breaker_failure_streak + 1,
                       breaker_opened_total = breaker_opened_total + 1, updated_at = now()
                 WHERE queue = NEW.queue;
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

CREATE TRIGGER jobs_breaker_trg
AFTER UPDATE OF status ON taskq.jobs
FOR EACH ROW
WHEN (NEW.status IN ('failed', 'succeeded') AND OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION taskq._breaker_on_settle();

-- ---------------------------------------------------------------------------
-- 4. Public claim wrappers consult the breaker before delegating (unattested
--    body unchanged). CREATE OR REPLACE preserves ownership + grants.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION taskq.claim_jobs(
    p_queue text, p_worker_id text, p_batch integer DEFAULT 1,
    p_job_types text[] DEFAULT NULL, p_lease_seconds integer DEFAULT NULL,
    p_affinity_key text DEFAULT NULL, p_job_id uuid DEFAULT NULL,
    p_accept_throttled boolean DEFAULT false
) RETURNS taskq.claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_retry integer;
BEGIN
    PERFORM taskq.require_target_attestation();
    v_retry := taskq._breaker_gate(p_queue);
    IF v_retry IS NOT NULL THEN
        RETURN taskq._throttled_or_empty(p_accept_throttled, v_retry);
    END IF;
    RETURN taskq._claim_jobs_unattested(
        p_queue, p_worker_id, p_batch, p_job_types, p_lease_seconds,
        p_affinity_key, p_job_id, p_accept_throttled);
END $$;

CREATE OR REPLACE FUNCTION taskq.claim_jobs(
    p_queue text, p_worker_id text, p_batch integer, p_job_types text[],
    p_lease_seconds integer, p_affinity_key text, p_job_id uuid,
    p_continuation_policy_hashes text[], p_accept_throttled boolean DEFAULT false
) RETURNS taskq.claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_retry integer;
BEGIN
    PERFORM taskq.require_target_attestation();
    v_retry := taskq._breaker_gate(p_queue);
    IF v_retry IS NOT NULL THEN
        RETURN taskq._throttled_or_empty(p_accept_throttled, v_retry);
    END IF;
    RETURN taskq._claim_jobs_unattested(
        p_queue, p_worker_id, p_batch, p_job_types, p_lease_seconds,
        p_affinity_key, p_job_id, p_continuation_policy_hashes, p_accept_throttled);
END $$;

-- ---------------------------------------------------------------------------
-- 5. Operator verbs (TQ501 until the circuit_breaker capability activates in 0032).
-- ---------------------------------------------------------------------------
CREATE FUNCTION taskq.set_breaker_config(
    p_queue text, p_failure_threshold integer, p_cooldown_seconds integer DEFAULT 30,
    p_half_open_successes integer DEFAULT 1, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_existed boolean;
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
    v_existed := EXISTS (SELECT 1 FROM taskq.queue_flow WHERE queue = p_queue);
    INSERT INTO taskq.queue_flow (queue, breaker_failure_threshold,
        breaker_cooldown_seconds, breaker_half_open_successes, updated_at)
    VALUES (p_queue, p_failure_threshold, p_cooldown_seconds, p_half_open_successes, now())
    ON CONFLICT (queue) DO UPDATE SET
        breaker_failure_threshold = excluded.breaker_failure_threshold,
        breaker_cooldown_seconds = excluded.breaker_cooldown_seconds,
        breaker_half_open_successes = excluded.breaker_half_open_successes,
        updated_at = now();
    RETURN CASE WHEN v_existed THEN 'updated' ELSE 'created' END;
END $$;
ALTER FUNCTION taskq.set_breaker_config(text, integer, integer, integer, text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_breaker_config(text, integer, integer, integer, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_breaker_config(text, integer, integer, integer, text)
    TO taskq_operator;

CREATE FUNCTION taskq.trip_breaker(p_queue text, p_actor text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF NOT taskq.has_capability('circuit_breaker') THEN
        RAISE EXCEPTION 'circuit_breaker capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"circuit_breaker_inactive"}';
    END IF;
    UPDATE taskq.queue_flow
       SET breaker_state = 'open', breaker_tripped_at = now(),
           breaker_probe_successes = 0, breaker_opened_total = breaker_opened_total + 1,
           updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    RETURN 'open';
END $$;
ALTER FUNCTION taskq.trip_breaker(text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.trip_breaker(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.trip_breaker(text, text) TO taskq_operator;

CREATE FUNCTION taskq.force_close_breaker(p_queue text, p_actor text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF NOT taskq.has_capability('circuit_breaker') THEN
        RAISE EXCEPTION 'circuit_breaker capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"circuit_breaker_inactive"}';
    END IF;
    UPDATE taskq.queue_flow
       SET breaker_state = 'closed', breaker_failure_streak = 0,
           breaker_probe_successes = 0, ramp_started_at = now(), updated_at = now()
     WHERE queue = p_queue AND breaker_failure_threshold IS NOT NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no configured breaker for queue' USING ERRCODE = 'TQ001';
    END IF;
    RETURN 'closed';
END $$;
ALTER FUNCTION taskq.force_close_breaker(text, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.force_close_breaker(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.force_close_breaker(text, text) TO taskq_operator;

-- ---------------------------------------------------------------------------
-- 6. Function-hardening self-check (0028 precedent).
-- ---------------------------------------------------------------------------
DO $$
DECLARE v_bad text[];
BEGIN
    SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text)
      INTO v_bad
      FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND (NOT p.prosecdef OR p.proconfig IS NULL
            OR NOT p.proconfig @> ARRAY['search_path=pg_catalog, taskq, pg_temp']);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0031 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
