-- outlabs-taskq — migration 0027: activate flow control
-- SQL contract 0.5.0 — metadata activation per the 0012/0015/0023 precedent.
-- Preflights the exact 0026 catalog, then activates the flow_control
-- capability (try_enqueue, set_flow_limit) and bumps the contract to 0.5.0.
-- Per-queue enforcement remains NULL-gated regardless of the capability;
-- activation only opens the new typed surfaces.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.4.3"'::jsonb THEN
        RAISE EXCEPTION '0027 requires SQL contract 0.4.3, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0027 requires the exact 0023 capability set, found %', v_capabilities;
    END IF;
    IF to_regclass('taskq.queue_flow') IS NULL
       OR to_regclass('taskq.flow_limits') IS NULL
       OR to_regclass('taskq.flow_state') IS NULL
       OR to_regprocedure('taskq.try_enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)') IS NULL
       OR to_regprocedure('taskq.set_flow_limit(text,integer,integer,text)') IS NULL
       OR to_regprocedure('taskq.flow_key_admit(text)') IS NULL
       OR to_regprocedure('taskq.expire_ttl(integer)') IS NULL
       OR to_regprocedure('taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,boolean)') IS NULL
       OR to_regprocedure('taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[],boolean)') IS NULL THEN
        RAISE EXCEPTION '0027 requires the complete 0024-0026 flow-control catalog';
    END IF;
END $$;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.5.0"'::jsonb, now()),
    ('capabilities', '{"active":["admission_reservations","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();
