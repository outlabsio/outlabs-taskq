-- outlabs-taskq — migration 0013: workflow page composite-assignment repair
-- SQL contract remains 0.2.3; public identity and capability state unchanged.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.3"'::jsonb THEN
        RAISE EXCEPTION '0013 requires SQL contract 0.2.3, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules"]}'::jsonb THEN
        RAISE EXCEPTION '0013 requires the exact activated 0012 capability set, found %',
            v_capabilities;
    END IF;
    IF to_regprocedure('taskq.get_workflow_page(uuid,integer,uuid)') IS NULL THEN
        RAISE EXCEPTION '0013 requires the 0011 workflow page identity';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION taskq.get_workflow_page(
    p_workflow_id uuid,
    p_limit integer DEFAULT 50,
    p_after uuid DEFAULT NULL
) RETURNS taskq.workflow_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_as_of timestamptz;
    v_workflow taskq.workflows%ROWTYPE;
    v_profile taskq.workflow_read_profile;
    v_counts taskq.workflow_state_counts;
    v_items taskq.workflow_member_projection[];
    v_next uuid;
BEGIN
    IF p_workflow_id IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid workflow page input' USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO v_workflow
    FROM taskq.workflows
    WHERE id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
    END IF;
    IF NOT taskq.has_capability('read_model_workflow') THEN
        RAISE EXCEPTION 'workflow read model inactive'
            USING ERRCODE = 'TQ501',
                  DETAIL = 'reason=read_model_view_inactive view=workflow';
    END IF;

    v_as_of := now();
    v_profile := ROW(
        v_workflow.id,
        v_workflow.kind,
        v_workflow.status,
        v_workflow.sealed_at IS NOT NULL,
        v_workflow.cancel_requested_at IS NOT NULL,
        v_workflow.declared_queues,
        v_workflow.created_at,
        v_workflow.updated_at,
        v_workflow.finished_at
    )::taskq.workflow_read_profile;

    SELECT
        c.blocked, c.queued, c.running, c.succeeded, c.failed, c.cancelled
    INTO
        v_counts.blocked, v_counts.queued, v_counts.running,
        v_counts.succeeded, v_counts.failed, v_counts.cancelled
    FROM taskq.workflow_member_counts c
    WHERE c.workflow_id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workflow counter invariant missing'
            USING ERRCODE = 'TQ500';
    END IF;

    SELECT ARRAY(
        SELECT ROW(
            j.id, j.queue, j.job_type, j.step_key, j.status, j.outcome,
            j.pending_deps, j.attempt_count::integer, j.failure_count::integer,
            j.created_at, j.scheduled_at, j.started_at, j.finished_at, j.updated_at
        )::taskq.workflow_member_projection
        FROM taskq.jobs j
        WHERE j.workflow_id = p_workflow_id
          AND (p_after IS NULL OR j.id > p_after)
        ORDER BY j.id
        LIMIT p_limit + 1
    ) INTO v_items;
    v_items := COALESCE(v_items, ARRAY[]::taskq.workflow_member_projection[]);
    IF cardinality(v_items) > p_limit THEN
        v_next := v_items[p_limit].job_id;
        v_items := v_items[1:p_limit];
    END IF;

    RETURN ROW(v_as_of, v_profile, v_counts, v_items, v_next)::taskq.workflow_page;
END $$;
ALTER FUNCTION taskq.get_workflow_page(uuid,integer,uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_workflow_page(uuid,integer,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_workflow_page(uuid,integer,uuid) TO taskq_observer;
