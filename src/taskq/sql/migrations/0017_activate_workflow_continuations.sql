-- outlabs-taskq — migration 0017: activate native workflow continuations
-- Metadata-only activation at SQL contract 0.2.5 / ADR-035.
-- WFC-I04 dual-major race/B9 evidence completed 2026-07-28.
-- WFC-I05 owns the installed-artifact, restore, manifest, and consumer gates.

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
        RAISE EXCEPTION '0017 requires SQL contract 0.2.5, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence"]}'::jsonb THEN
        RAISE EXCEPTION '0017 requires the exact inactive 0016 capability set, found %',
            v_capabilities
            USING ERRCODE = 'TQ500';
    END IF;

    IF to_regprocedure(
           'taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])'
       ) IS NULL
       OR to_regprocedure(
           'taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)'
       ) IS NULL
       OR to_regprocedure(
           'taskq.create_workflow(text,text,jsonb,text[],text,integer,text)'
       ) IS NULL
       OR to_regprocedure(
           'taskq._reserve_workflow_members(uuid,integer,text)'
       ) IS NULL
       OR to_regclass('taskq.workflow_member_counts') IS NULL
       OR to_regclass('taskq.jobs_claim_policy_idx') IS NULL
       OR to_regclass('taskq.jobs_affinity_policy_idx') IS NULL
       OR to_regclass('taskq.jobs_workflow_cancel_idx') IS NULL THEN
        RAISE EXCEPTION '0017 requires the complete 0016 workflow-continuation catalog'
            USING ERRCODE = 'TQ500';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM taskq.jobs AS j
        WHERE j.idempotency_key LIKE 'chain:%'
          AND (
              j.parent_job_id IS NULL
              OR j.idempotency_key NOT LIKE
                    'chain:' || j.parent_job_id::text || ':%'
              OR substring(
                    j.idempotency_key
                    FROM length('chain:' || j.parent_job_id::text || ':') + 1
                 ) !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
          )
    ) THEN
        RAISE EXCEPTION '0017 found a producer-shaped retained chain: idempotency key'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"reserved_idempotency_namespace"}';
    END IF;
END $$;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES (
    'capabilities',
    '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence","workflow_continuations"]}'::jsonb,
    now()
)
ON CONFLICT(key) DO UPDATE
SET value = excluded.value, updated_at = now();
