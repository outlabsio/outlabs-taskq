-- outlabs-taskq — migration 0012: activate independently proven finite projections
-- Metadata-only activation at SQL contract 0.2.3 / ADR-029.
-- B9 evidence: commit 988309c on PostgreSQL 16.14 and 18.3.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.3"'::jsonb THEN
        RAISE EXCEPTION '0012 requires SQL contract 0.2.3, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready","schedules"]}'::jsonb THEN
        RAISE EXCEPTION '0012 requires the exact inactive 0011 capability set, found %',
            v_capabilities;
    END IF;
    IF to_regprocedure('taskq.get_workflow_page(uuid,integer,uuid)') IS NULL
       OR to_regclass('taskq.taskq_jobs_running_page_idx') IS NULL
       OR to_regclass('taskq.taskq_jobs_finished_page_idx') IS NULL
       OR to_regclass('taskq.taskq_jobs_workflow_page_idx') IS NULL
       OR to_regclass('taskq.workflow_member_counts') IS NULL THEN
        RAISE EXCEPTION '0012 requires the complete 0011 finite-projection catalog';
    END IF;
END $$;

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('capabilities','{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules"]}'::jsonb,now())
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now();
