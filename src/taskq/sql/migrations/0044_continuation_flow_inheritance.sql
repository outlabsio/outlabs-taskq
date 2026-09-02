-- outlabs-taskq — migration 0044: workflow continuation flow inheritance
-- SQL contract 0.6.8 / Protocol document revision 1.0.18.
--
-- A policy-bearing workflow continuation is part of the same provider flow as
-- its parent.  Followups are admitted by the runner at settlement rather than
-- by the producer, so they must inherit the parent's flow key inside the SQL
-- trust boundary.  Detached followups retain their historical null flow key.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.6.7"'::jsonb THEN
        RAISE EXCEPTION '0044 requires SQL contract 0.6.7, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","circuit_breaker","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_bulk_admission","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0044 requires the exact 0.6.7 capability set, found %',
            v_capabilities;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION taskq._enqueue_followup(
    p_parent_job_id uuid,
    p_parent_queue text,
    p_spec jsonb,
    p_spec_index integer
) RETURNS TABLE(job_id uuid, created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q taskq.queues%ROWTYPE;
    v_parent taskq.jobs%ROWTYPE;
    v_workflow taskq.workflows%ROWTYPE;
    v_queue text;
    v_local_step text;
    v_step text;
    v_job_type text;
    v_payload jsonb;
    v_headers jsonb;
    v_priority smallint;
    v_max_attempts smallint;
    v_lease_seconds integer;
    v_scheduled_at timestamptz;
    v_key text;
    v_member boolean := false;
    v_flow_key text;
    v_intent_hash text;
    v_id uuid;
    v_existing taskq.jobs%ROWTYPE;
    v_try integer;
BEGIN
    IF p_spec_index IS NULL OR p_spec_index < 1 OR p_spec_index > 20 THEN
        RAISE EXCEPTION 'followup index must be 1..20' USING ERRCODE = 'TQ422';
    END IF;
    IF p_spec IS NULL OR jsonb_typeof(p_spec) <> 'object' THEN
        RAISE EXCEPTION 'followup spec % must be an object', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_object_keys(p_spec) AS k(key)
        WHERE k.key NOT IN (
            'step','job_type','queue','payload','headers','priority',
            'max_attempts','lease_seconds','scheduled_at','workflow_member'
        )
    ) THEN
        RAISE EXCEPTION 'followup spec % has an unknown field', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_spec ? 'workflow_member' THEN
        IF jsonb_typeof(p_spec->'workflow_member') <> 'boolean'
           OR (p_spec->>'workflow_member')::boolean IS NOT TRUE THEN
            RAISE EXCEPTION 'workflow_member may only be true when present'
                USING ERRCODE = 'TQ422';
        END IF;
        v_member := true;
    END IF;

    SELECT * INTO v_parent FROM taskq.jobs WHERE id = p_parent_job_id;
    IF NOT FOUND OR v_parent.queue IS DISTINCT FROM p_parent_queue THEN
        RAISE EXCEPTION 'continuation parent is inconsistent'
            USING ERRCODE = 'TQ500';
    END IF;

    v_local_step := p_spec->>'step';
    v_job_type := p_spec->>'job_type';
    v_queue := COALESCE(p_spec->>'queue', p_parent_queue);
    IF v_local_step IS NULL OR octet_length(v_local_step) NOT BETWEEN 1 AND 64
       OR v_local_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
        RAISE EXCEPTION 'followup spec % has invalid step', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(v_job_type, '') = '' OR char_length(v_job_type) > 120 THEN
        RAISE EXCEPTION 'followup spec % requires job_type <= 120 chars', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(v_queue, '') = '' THEN
        RAISE EXCEPTION 'followup spec % has no queue', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    v_payload := COALESCE(p_spec->'payload', '{}'::jsonb);
    v_headers := COALESCE(p_spec->'headers', '{}'::jsonb);
    IF jsonb_typeof(v_payload) <> 'object' OR octet_length(v_payload::text) > 65536
       OR jsonb_typeof(v_headers) <> 'object' OR octet_length(v_headers::text) > 8192 THEN
        RAISE EXCEPTION 'followup spec % has invalid bounded JSON', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    BEGIN
        IF p_spec ? 'priority' THEN
            IF jsonb_typeof(p_spec->'priority') <> 'number'
               OR (p_spec->>'priority') !~ '^-?[0-9]+$' THEN RAISE data_exception; END IF;
            v_priority := (p_spec->>'priority')::smallint;
        END IF;
        IF p_spec ? 'max_attempts' THEN
            IF jsonb_typeof(p_spec->'max_attempts') <> 'number'
               OR (p_spec->>'max_attempts') !~ '^-?[0-9]+$' THEN RAISE data_exception; END IF;
            v_max_attempts := (p_spec->>'max_attempts')::smallint;
        END IF;
        IF p_spec ? 'lease_seconds' THEN
            IF jsonb_typeof(p_spec->'lease_seconds') <> 'number'
               OR (p_spec->>'lease_seconds') !~ '^-?[0-9]+$' THEN RAISE data_exception; END IF;
            v_lease_seconds := (p_spec->>'lease_seconds')::integer;
        END IF;
        IF p_spec ? 'scheduled_at' THEN
            IF jsonb_typeof(p_spec->'scheduled_at') <> 'string' THEN RAISE data_exception; END IF;
            v_scheduled_at := (p_spec->>'scheduled_at')::timestamptz;
        END IF;
    EXCEPTION WHEN data_exception OR invalid_text_representation OR datetime_field_overflow
                   OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'followup spec % has an invalid scalar field', p_spec_index
            USING ERRCODE = 'TQ422';
    END;
    IF v_priority IS NOT NULL AND v_priority NOT BETWEEN 0 AND 1000
       OR v_max_attempts IS NOT NULL AND v_max_attempts NOT BETWEEN 1 AND 100
       OR v_lease_seconds IS NOT NULL AND v_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'followup spec % has an out-of-range scalar field', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO q FROM taskq.queues WHERE name = v_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'followup spec % names unknown queue', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    v_key := 'chain:' || lower(p_parent_job_id::text) || ':' || v_local_step;
    v_step := NULL;
    v_flow_key := NULL;
    IF v_member THEN
        IF v_parent.workflow_id IS NULL OR v_parent.continuation_policy_hash IS NULL THEN
            RAISE EXCEPTION 'continuation parent is not a policy workflow member'
                USING ERRCODE = 'TQ422',
                      DETAIL = '{"reason":"continuation_parent_not_member"}';
        END IF;
        SELECT * INTO v_workflow FROM taskq.workflows
        WHERE id = v_parent.workflow_id;
        IF NOT FOUND
           OR v_workflow.continuation_policy_hash IS DISTINCT FROM
              v_parent.continuation_policy_hash THEN
            RAISE EXCEPTION 'continuation policy identity is inconsistent'
                USING ERRCODE = 'TQ500';
        END IF;
        IF NOT (v_queue = ANY(v_workflow.declared_queues)) THEN
            RAISE EXCEPTION 'continuation queue is not declared by the workflow'
                USING ERRCODE = 'TQ422',
                      DETAIL = '{"reason":"continuation_queue_undeclared"}';
        END IF;
        v_step := 'c:' || lower(p_parent_job_id::text) || ':' || v_local_step;
        v_flow_key := v_parent.flow_key;
        v_intent_hash := encode(sha256(convert_to(
            jsonb_build_object(
                'queue',v_queue,'job_type',v_job_type,'payload',v_payload,
                'headers',v_headers,'priority',COALESCE(v_priority,q.default_priority),
                'scheduled_at',CASE WHEN p_spec ? 'scheduled_at' THEN v_scheduled_at ELSE NULL END,
                'max_attempts',COALESCE(v_max_attempts,q.default_max_attempts),
                'lease_seconds',COALESCE(v_lease_seconds,q.default_lease_seconds),
                'parent_job_id',p_parent_job_id,'flow_key',v_flow_key
            )::text, 'UTF8'
        )), 'hex');
    END IF;

    v_scheduled_at := COALESCE(v_scheduled_at, now());
    FOR v_try IN 1..3 LOOP
        v_id := taskq.uuid7();
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, parent_job_id, pending_deps, scheduled_at,
            lease_seconds, max_attempts, backoff_mode,
            backoff_base_seconds, backoff_cap_seconds,
            workflow_id, step_key, workflow_intent_hash,
            continuation_policy_hash, flow_key
        ) VALUES (
            v_id, v_queue, v_job_type, 'queued',
            COALESCE(v_priority, q.default_priority), v_payload, v_headers,
            v_key, p_parent_job_id, 0, v_scheduled_at,
            COALESCE(v_lease_seconds, q.default_lease_seconds),
            COALESCE(v_max_attempts, q.default_max_attempts),
            q.default_backoff_mode, q.default_backoff_base, q.default_backoff_cap,
            CASE WHEN v_member THEN v_parent.workflow_id END,
            v_step, v_intent_hash,
            CASE WHEN v_member THEN v_parent.continuation_policy_hash END,
            v_flow_key
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING;
        IF FOUND THEN
            PERFORM taskq.emit_event(v_id, NULL, 'enqueued', 'system', NULL,
                jsonb_build_object(
                    'status','queued','scheduled_at',v_scheduled_at,'flow_key',v_flow_key
                ));
            IF v_scheduled_at <= now() AND q.notify_enabled THEN
                PERFORM pg_notify('taskq_' || v_queue, '');
            END IF;
            RETURN QUERY SELECT v_id, true;
            RETURN;
        END IF;
        SELECT j.* INTO v_existing FROM taskq.jobs AS j
        WHERE j.queue = v_queue AND j.idempotency_key = v_key
          AND j.status IN ('blocked','queued','running')
        ORDER BY j.created_at DESC LIMIT 1;
        IF FOUND THEN
            IF v_existing.parent_job_id IS DISTINCT FROM p_parent_job_id
               OR v_existing.job_type IS DISTINCT FROM v_job_type
               OR v_existing.payload IS DISTINCT FROM v_payload
               OR v_existing.headers IS DISTINCT FROM v_headers
               OR v_existing.priority IS DISTINCT FROM COALESCE(v_priority,q.default_priority)
               OR v_existing.max_attempts IS DISTINCT FROM
                  COALESCE(v_max_attempts,q.default_max_attempts)
               OR v_existing.lease_seconds IS DISTINCT FROM
                  COALESCE(v_lease_seconds,q.default_lease_seconds)
               OR v_existing.workflow_id IS DISTINCT FROM
                  (CASE WHEN v_member THEN v_parent.workflow_id END)
               OR v_existing.step_key IS DISTINCT FROM v_step
               OR v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash
               OR v_existing.continuation_policy_hash IS DISTINCT FROM
                  (CASE WHEN v_member THEN v_parent.continuation_policy_hash END)
               OR v_existing.flow_key IS DISTINCT FROM v_flow_key
               OR (p_spec ? 'scheduled_at'
                   AND v_existing.scheduled_at IS DISTINCT FROM v_scheduled_at) THEN
                RAISE EXCEPTION 'followup idempotency key has an inconsistent holder'
                    USING ERRCODE = 'TQ500';
            END IF;
            RETURN QUERY SELECT v_existing.id, false;
            RETURN;
        END IF;
    END LOOP;
    RAISE EXCEPTION 'followup idempotency insert did not converge' USING ERRCODE = 'TQ500';
END $$;
ALTER FUNCTION taskq._enqueue_followup(uuid,text,jsonb,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq._enqueue_followup(uuid,text,jsonb,integer) FROM PUBLIC;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.8"'::jsonb, now()),
    ('capabilities', '{"active":["admission_reservations","circuit_breaker","continuation_flow_inheritance","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_bulk_admission","workflow_continuations"]}'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();
