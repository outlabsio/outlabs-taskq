-- outlabs-taskq — migration 0023: activate queue counters
-- SQL contract 0.4.0 — enables the 0022 trigger and re-backfills the counter
-- table in the same transaction. ALTER TABLE ... ENABLE TRIGGER takes SHARE
-- ROW EXCLUSIVE on taskq.jobs, blocking concurrent job writes until commit,
-- so the backfilled counters are exactly consistent with the trigger's
-- starting point by construction. The backfill is one grouped pass over
-- taskq.jobs (bounded; million-row plan evidence recorded per the harness
-- spec activation gates). Cumulative totals seed from surviving rows.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.4.0"'::jsonb THEN
        RAISE EXCEPTION '0023 requires SQL contract 0.4.0, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","operator_schedule_list","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0023 requires the exact 0022 capability set, found %', v_capabilities;
    END IF;
    IF to_regclass('taskq.queue_counters') IS NULL
       OR to_regprocedure('taskq.update_queue_counters()') IS NULL
       OR to_regprocedure('taskq.queue_health(text)') IS NULL
       OR to_regprocedure('taskq.queue_health_internal()') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_trigger t
           WHERE t.tgname = 'jobs_queue_counters_trg'
             AND t.tgrelid = 'taskq.jobs'::regclass
       ) THEN
        RAISE EXCEPTION '0023 requires the complete 0022 queue-counters catalog';
    END IF;
END $$;

ALTER TABLE taskq.jobs ENABLE TRIGGER jobs_queue_counters_trg;

TRUNCATE taskq.queue_counters;

INSERT INTO taskq.queue_counters (
    queue, blocked, queued, running,
    enqueued_total, requeued_total, succeeded_total, failed_total, cancelled_total
)
SELECT q.name,
       COALESCE(c.blocked, 0),
       COALESCE(c.queued, 0),
       COALESCE(c.running, 0),
       COALESCE(c.total, 0),
       0,
       COALESCE(c.succeeded, 0),
       COALESCE(c.failed, 0),
       COALESCE(c.cancelled, 0)
  FROM taskq.queues q
  LEFT JOIN (
      SELECT j.queue,
             count(*) AS total,
             count(*) FILTER (WHERE j.status = 'blocked') AS blocked,
             count(*) FILTER (WHERE j.status = 'queued') AS queued,
             count(*) FILTER (WHERE j.status = 'running') AS running,
             count(*) FILTER (WHERE j.status = 'succeeded') AS succeeded,
             count(*) FILTER (WHERE j.status = 'failed') AS failed,
             count(*) FILTER (WHERE j.status = 'cancelled') AS cancelled
        FROM taskq.jobs j
       GROUP BY j.queue
  ) c ON c.queue = q.name;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('capabilities', '{"active":["admission_reservations","dependencies_workflows","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();
