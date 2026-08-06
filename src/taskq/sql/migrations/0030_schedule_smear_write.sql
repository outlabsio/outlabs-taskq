-- outlabs-taskq — migration 0030: schedule-smear write verb (SQL contract 0.5.2)
--
-- Adds taskq.set_schedule_smear(name, smear_seconds, actor) — the supported
-- operator verb for the schedule-firing smear whose column landed inactive in
-- 0024 and whose read/evaluation path activated in 0029 (contract 0.5.1).
-- Until now smear was reachable only by a direct UPDATE on schedules.smear_seconds;
-- this closes the write path.
--
-- DESIGN: smear is an operational anti-stampede knob, orthogonal to the schedule
-- DEFINITION — the same category as concurrency and flow-rate limits, which are
-- set by their own verbs (set_concurrency_limit, set_flow_limit), NOT baked into
-- put_schedule. So smear stays out of the schedule definition, out of the
-- manifest definition hash, and the verb does NOT bump the schedule version —
-- declarative manifest reconciliation is left completely undisturbed. NULL clears
-- the smear (schedules fire on the exact lattice again).
--
-- MIGRATION NOTES / COMPATIBILITY:
--   * Purely additive, non-breaking: one new SECURITY DEFINER function granted to
--     taskq_operator. No existing function signature, table, or composite type
--     changes, so no machine-manifest shape churn beyond the new function row.
--   * Ungated: there is no capability flag and no activation split (the 0027
--     activate-then-use pattern is for gated surfaces). A missing schedule raises
--     TQ001; an out-of-range smear raises TQ422 (bounds mirror the 0024 column
--     CHECK: NULL or 1..3600 seconds).
--   * Contract bumps 0.5.1 -> 0.5.2. Consumers on 0.5.1 keep working unchanged;
--     only callers that want the verb need the new version.

DO $$
DECLARE
    v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.5.1"'::jsonb THEN
        RAISE EXCEPTION '0030 requires SQL contract 0.5.1, found %', v_contract;
    END IF;
END $$;

CREATE FUNCTION taskq.set_schedule_smear(
    p_name text, p_smear_seconds integer, p_actor text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_existing taskq.schedules%ROWTYPE;
BEGIN
    IF p_name IS NULL OR octet_length(p_name) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'invalid schedule name' USING ERRCODE = 'TQ422';
    END IF;
    IF p_smear_seconds IS NOT NULL AND p_smear_seconds NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'smear_seconds must be NULL or 1..3600' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_existing FROM taskq.schedules WHERE name = p_name FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF v_existing.smear_seconds IS NOT DISTINCT FROM p_smear_seconds THEN
        RETURN 'unchanged';
    END IF;
    UPDATE taskq.schedules
       SET smear_seconds = p_smear_seconds, updated_at = now()
     WHERE name = p_name;
    RETURN CASE WHEN p_smear_seconds IS NULL THEN 'cleared' ELSE 'updated' END;
END $$;
ALTER FUNCTION taskq.set_schedule_smear(text, integer, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_schedule_smear(text, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_schedule_smear(text, integer, text) TO taskq_operator;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.5.2"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();

-- Function-hardening self-check (0028 precedent): every taskq function stays
-- SECURITY DEFINER with the pinned search_path, so the new verb cannot regress it.
DO $$
DECLARE
    v_bad text[];
BEGIN
    SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text)
      INTO v_bad
      FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND (
           NOT p.prosecdef
           OR p.proconfig IS NULL
           OR NOT p.proconfig @> ARRAY['search_path=pg_catalog, taskq, pg_temp']
       );
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0030 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
