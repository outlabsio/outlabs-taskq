-- outlabs-taskq — migration 0015: activate independently proven worker presence
-- Metadata-only activation at SQL contract 0.2.4 / ADR-033.
-- B9 evidence: commit 15b24ff on PostgreSQL 16.14 and 18.3.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.4"'::jsonb THEN
        RAISE EXCEPTION '0015 requires SQL contract 0.2.4, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules"]}'::jsonb THEN
        RAISE EXCEPTION '0015 requires the exact inactive 0014 capability set, found %',
            v_capabilities;
    END IF;
    IF to_regtype('taskq.worker_presence_item') IS NULL
       OR to_regtype('taskq.worker_presence_page') IS NULL
       OR to_regprocedure(
           'taskq.list_worker_presence(integer,timestamp with time zone,text)'
       ) IS NULL
       OR to_regclass('taskq.workers_presence_page_idx') IS NULL
       OR to_regclass('taskq.taskq_jobs_worker_running_idx') IS NULL THEN
        RAISE EXCEPTION '0015 requires the complete 0014 worker-presence catalog';
    END IF;
END $$;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES (
    'capabilities',
    '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence"]}'::jsonb,
    now()
)
ON CONFLICT(key) DO UPDATE
SET value = excluded.value, updated_at = now();
