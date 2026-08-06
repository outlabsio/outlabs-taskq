-- outlabs-taskq — migration 0028: redrive_failed NULL-limit guard
-- Body-only hardening (no contract/capability change): redrive_failed now
-- rejects a NULL p_limit with TQ422, matching the `IS NULL OR NOT BETWEEN`
-- convention every other bounded argument uses (claim batch, release delay,
-- purge limit, tick reap limit). The signature is unchanged
-- (text, integer, text, integer), so the machine manifest is unaffected.

CREATE OR REPLACE FUNCTION taskq.redrive_failed(
    p_queue text, p_limit int, p_actor text, p_smear_seconds integer DEFAULT 0
) RETURNS TABLE (redriven int, skipped int)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_id uuid; v_r int := 0; v_s int := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'limit must be 1..500' USING ERRCODE = 'TQ422';
    END IF;
    IF p_smear_seconds IS NULL OR p_smear_seconds NOT BETWEEN 0 AND 86400 THEN
        RAISE EXCEPTION 'smear_seconds must be 0..86400' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_id IN SELECT id FROM taskq.jobs
                 WHERE queue = p_queue AND status = 'failed'
                 ORDER BY finished_at DESC LIMIT p_limit LOOP
        BEGIN
            PERFORM taskq.redrive_job(v_id, p_actor, false);
            IF p_smear_seconds > 0 THEN
                UPDATE taskq.jobs
                   SET scheduled_at = now()
                       + make_interval(secs => random() * p_smear_seconds)
                 WHERE id = v_id;
            END IF;
            v_r := v_r + 1;
        EXCEPTION WHEN SQLSTATE 'TQ409' THEN
            v_s := v_s + 1;
        END;
    END LOOP;
    RETURN QUERY SELECT v_r, v_s;
END $$;
ALTER FUNCTION taskq.redrive_failed(text, int, text, integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.redrive_failed(text, int, text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.redrive_failed(text, int, text, integer) TO taskq_operator;

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
        RAISE EXCEPTION '0028 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
