-- outlabs-taskq — migration 0018: trusted host-effect fence
-- SQL contract 0.2.6 / ADR-036. No HTTP or Protocol wire change.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract
    FROM taskq.meta
    WHERE key = 'contract_version'
    FOR UPDATE;
    SELECT value INTO v_capabilities
    FROM taskq.meta
    WHERE key = 'capabilities'
    FOR UPDATE;

    IF v_contract IS DISTINCT FROM '"0.2.5"'::jsonb THEN
        RAISE EXCEPTION '0018 requires SQL contract 0.2.5, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0018 requires the exact active 0017 capability set, found %',
            v_capabilities
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

CREATE FUNCTION taskq.lock_active_effect_attempt(
    p_job_id uuid,
    p_attempt_id uuid,
    p_worker_id text,
    p_queue text,
    p_job_type text
) RETURNS TABLE(payload jsonb, workflow_id uuid, workflow_counts jsonb)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF p_job_id IS NULL OR p_attempt_id IS NULL THEN
        RAISE EXCEPTION 'job_id and attempt_id are required'
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)'
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(p_queue, '') !~ '^[a-z0-9][a-z0-9_]{0,56}$' THEN
        RAISE EXCEPTION 'invalid queue name'
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(p_job_type, '') = '' OR length(p_job_type) > 120 THEN
        RAISE EXCEPTION 'job_type required (<=120 chars)'
            USING ERRCODE = 'TQ422';
    END IF;

    RETURN QUERY
    SELECT
        j.payload,
        j.workflow_id,
        CASE
            WHEN j.workflow_id IS NULL THEN NULL
            ELSE counts.value
        END
      FROM taskq.jobs AS j
      LEFT JOIN LATERAL (
        SELECT jsonb_build_object(
            'blocked', count(*) FILTER (WHERE member.status = 'blocked'),
            'queued', count(*) FILTER (WHERE member.status = 'queued'),
            'running', count(*) FILTER (WHERE member.status = 'running'),
            'succeeded', count(*) FILTER (WHERE member.status = 'succeeded'),
            'failed', count(*) FILTER (WHERE member.status = 'failed'),
            'cancelled', count(*) FILTER (WHERE member.status = 'cancelled')
        ) AS value
        FROM taskq.jobs AS member
        WHERE member.workflow_id = j.workflow_id
      ) AS counts ON true
     WHERE j.id = p_job_id
       AND j.status = 'running'
       AND j.current_attempt_id = p_attempt_id
       AND j.worker_id = p_worker_id
       AND j.queue = p_queue
       AND j.job_type = p_job_type
       AND j.lease_expires_at > clock_timestamp()
       AND j.cancel_requested_at IS NULL
     FOR UPDATE OF j;
END $$;

ALTER FUNCTION taskq.lock_active_effect_attempt(uuid, uuid, text, text, text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION
    taskq.lock_active_effect_attempt(uuid, uuid, text, text, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    taskq.lock_active_effect_attempt(uuid, uuid, text, text, text)
    TO taskq_producer;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.2.6"'::jsonb, now())
ON CONFLICT(key) DO UPDATE
SET value = excluded.value, updated_at = now();
