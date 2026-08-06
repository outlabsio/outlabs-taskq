-- outlabs-taskq — migration 0032: activate the circuit breaker (SQL contract 0.6.0)
-- Metadata activation per the 0012/0015/0023/0027 precedent. Preflights the exact
-- 0031 catalog, then activates the `circuit_breaker` capability and bumps the
-- contract to 0.6.0. Per-queue enforcement stays gated on breaker_failure_threshold
-- regardless of the capability; activation only opens the operator verbs.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.5.2"'::jsonb THEN
        RAISE EXCEPTION '0032 requires SQL contract 0.5.2, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0032 requires the exact 0.5.0 capability set, found %', v_capabilities;
    END IF;
    IF to_regprocedure('taskq._breaker_gate(text)') IS NULL
       OR to_regprocedure('taskq._breaker_on_settle()') IS NULL
       OR to_regprocedure('taskq.set_breaker_config(text,integer,integer,integer,text)') IS NULL
       OR to_regprocedure('taskq.trip_breaker(text,text)') IS NULL
       OR to_regprocedure('taskq.force_close_breaker(text,text)') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
                       WHERE attrelid = 'taskq.queue_flow'::regclass
                         AND attname = 'breaker_state' AND NOT attisdropped) THEN
        RAISE EXCEPTION '0032 requires the complete 0031 breaker catalog';
    END IF;
END $$;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.0"'::jsonb, now()),
    ('capabilities', '{"active":["admission_reservations","circuit_breaker","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();
