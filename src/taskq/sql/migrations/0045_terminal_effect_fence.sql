-- outlabs-taskq — migration 0045: trusted terminal host-effect fence
-- SQL contract 0.6.9. Additive, SQL-only borrowed-transaction surface.
-- No existing function, HTTP command, table privilege or historical migration changes.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta
    WHERE key = 'contract_version' FOR UPDATE;
    IF v_contract IS DISTINCT FROM '"0.6.8"'::jsonb THEN
        RAISE EXCEPTION '0045 requires SQL contract 0.6.8, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

CREATE FUNCTION taskq.lock_terminal_effect_job(
    p_job_id uuid,
    p_queue text,
    p_job_type text,
    p_expected_environment text,
    p_expected_installation_id uuid,
    p_allow_production boolean DEFAULT false
) RETURNS TABLE(status text, outcome text, finished_at timestamptz, payload jsonb, workflow_id uuid)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF p_job_id IS NULL OR p_expected_installation_id IS NULL THEN
        RAISE EXCEPTION 'job_id and expected_installation_id are required'
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(p_queue, '') !~ '^[a-z0-9][a-z0-9_]{0,56}$'
       OR COALESCE(p_job_type, '') = '' OR length(p_job_type) > 120 THEN
        RAISE EXCEPTION 'valid queue and job_type are required'
            USING ERRCODE = 'TQ422';
    END IF;
    -- Reuse installation/environment/production authorization attestation. As
    -- with the live effect fence, only the trusted host producer can call this.
    PERFORM taskq.attest_target(
        p_expected_environment, p_expected_installation_id, p_allow_production
    );
    -- READ COMMITTED rechecks this predicate after waiting for a redrive lock.
    -- Holding the returned lock prevents redrive/retention until caller commit.
    RETURN QUERY SELECT j.status, j.outcome, j.finished_at, j.payload, j.workflow_id
      FROM taskq.jobs AS j
     WHERE j.id = p_job_id AND j.queue = p_queue AND j.job_type = p_job_type
       AND j.status IN ('succeeded', 'failed', 'cancelled')
     FOR UPDATE OF j;
END $$;
ALTER FUNCTION taskq.lock_terminal_effect_job(uuid,text,text,text,uuid,boolean)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.lock_terminal_effect_job(uuid,text,text,text,uuid,boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.lock_terminal_effect_job(uuid,text,text,text,uuid,boolean)
    TO taskq_producer;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.6.9"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();
