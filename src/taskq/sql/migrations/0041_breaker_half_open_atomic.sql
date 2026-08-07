-- outlabs-taskq — migration 0041: breaker half-open election is atomic + un-wedges
--
-- Body-only fix (no contract/capability change; signature unchanged, manifest
-- unaffected — 0028/0038 precedent). Fixes review findings T1 (single-flight
-- violation) and P1 (half-open wedge). Both are pre-existing 0.6.0 behavior in
-- taskq._breaker_gate, surfaced by the now-runnable loadlab small tier (L8 admitted
-- 2 probes ~36% of runs). The gate SQL is untouched since 0031; this is the first
-- correction.
--
-- T1 — non-atomic election. The old gate read breaker_state with a plain SELECT,
-- then took pg_try_advisory_xact_lock to elect the single probe. A racing claim
-- whose SELECT saw 'open' (cooldown elapsed) but whose lock attempt ran just after
-- the winner committed acquired the freed xact-lock and became a SECOND probe. A
-- dead downstream then saw k probes per cooldown instead of one (0.6 spec §2.4).
-- Fix: elect with a single atomic `UPDATE ... WHERE breaker_state='open'`. The row
-- lock serializes concurrent claims; exactly one transaction flips 'open'->'half_open'
-- and is the probe. A racing claim's UPDATE re-evaluates its WHERE against the winner's
-- committed row (now 'half_open'), matches nothing, and throttles.
--
-- P1 — half-open wedge. The settle trigger only reacts to failed/succeeded, so a
-- probe that resolves to any other state (cancelled, released back to queued,
-- snoozed, or lease-expired) never transitions the breaker: it strands in 'half_open'
-- and throttles every claim forever, and the requeued probe can never be re-claimed.
-- Fix: a half-open deadline. If the probe has not settled within ~cooldown of election
-- (now() > tripped_at + 2*cooldown), re-open so the cooldown+election cycle restarts.
-- This also correctly re-trips a downstream too slow to complete a probe inside the
-- operator's chosen cooldown — a slow probe means still-unhealthy, so staying
-- protective is the right response, not a false positive. The row lock keeps the
-- re-open single (opened_total increments once).

CREATE OR REPLACE FUNCTION taskq._breaker_gate(p_queue text)
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
        -- Cooldown elapsed: elect ONE probe atomically (T1). The row lock serializes
        -- concurrent claims; only the transaction that flips 'open'->'half_open' takes
        -- a row and is the probe. A racing claim finds no row here and throttles.
        UPDATE taskq.queue_flow
           SET breaker_state = 'half_open', updated_at = now()
         WHERE queue = p_queue AND breaker_state = 'open'
           AND breaker_tripped_at + make_interval(secs => v_cooldown) <= now();
        IF FOUND THEN
            RETURN NULL;  -- this call is the elected probe
        END IF;
        RETURN 1;  -- another claim won the election
    END IF;

    -- half_open: a probe is outstanding, so throttle every other claim. But if the
    -- probe never settled to succeeded/failed within its deadline (P1: cancelled /
    -- released / snoozed / lease-expired never fire the settle trigger), re-open so
    -- the cycle restarts. The row lock keeps this single.
    IF now() > v_tripped + make_interval(secs => 2 * v_cooldown) THEN
        UPDATE taskq.queue_flow
           SET breaker_state = 'open', breaker_tripped_at = now(),
               breaker_probe_successes = 0,
               breaker_opened_total = breaker_opened_total + 1, updated_at = now()
         WHERE queue = p_queue AND breaker_state = 'half_open'
           AND now() > breaker_tripped_at + make_interval(secs => 2 * v_cooldown);
    END IF;
    RETURN 1;
END $$;
ALTER FUNCTION taskq._breaker_gate(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._breaker_gate(text) FROM PUBLIC;

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
        RAISE EXCEPTION '0041 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
