-- outlabs-taskq — migration 0008: lossless atomic follow-ups (SQL contract 0.2.0)
-- Derived from Function Manifest §15 / ADR-024 / ADR-025.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.1.5"'::jsonb THEN
        RAISE EXCEPTION '0008 requires SQL contract 0.1.5, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","read_model_list_ready"]}'::jsonb THEN
        RAISE EXCEPTION '0008 requires the exact 0007 capability posture';
    END IF;
END $$;

-- Owner-only continuation inserter. It deliberately omits only the producer
-- max_depth probe; every other ordinary enqueue invariant remains.
CREATE FUNCTION taskq._enqueue_followup(
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
    v_queue text;
    v_step text;
    v_job_type text;
    v_payload jsonb;
    v_headers jsonb;
    v_priority smallint;
    v_max_attempts smallint;
    v_lease_seconds integer;
    v_scheduled_at timestamptz;
    v_key text;
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
            'max_attempts','lease_seconds','scheduled_at'
        )
    ) THEN
        RAISE EXCEPTION 'followup spec % has an unknown field', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    v_step := p_spec->>'step';
    v_job_type := p_spec->>'job_type';
    v_queue := COALESCE(p_spec->>'queue', p_parent_queue);
    IF v_step IS NULL OR octet_length(v_step) NOT BETWEEN 1 AND 64
       OR v_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
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
    IF jsonb_typeof(v_payload) <> 'object' OR octet_length(v_payload::text) > 65536 THEN
        RAISE EXCEPTION 'followup spec % has invalid or oversized payload', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF jsonb_typeof(v_headers) <> 'object' OR octet_length(v_headers::text) > 8192 THEN
        RAISE EXCEPTION 'followup spec % has invalid or oversized headers', p_spec_index
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

    IF v_priority IS NOT NULL AND v_priority NOT BETWEEN 0 AND 1000 THEN
        RAISE EXCEPTION 'followup spec % priority must be 0..1000', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF v_max_attempts IS NOT NULL AND v_max_attempts NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'followup spec % max_attempts must be 1..100', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF v_lease_seconds IS NOT NULL AND v_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'followup spec % lease_seconds must be 15..86400', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO q FROM taskq.queues WHERE name = v_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'followup spec % names unknown queue', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    v_key := 'chain:' || p_parent_job_id::text || ':' || v_step;
    v_scheduled_at := COALESCE(v_scheduled_at, now());
    FOR v_try IN 1..3 LOOP
        v_id := taskq.uuid7();
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, parent_job_id, pending_deps, scheduled_at,
            lease_seconds, max_attempts, backoff_mode,
            backoff_base_seconds, backoff_cap_seconds
        ) VALUES (
            v_id, v_queue, v_job_type, 'queued',
            COALESCE(v_priority, q.default_priority), v_payload, v_headers,
            v_key, p_parent_job_id, 0, v_scheduled_at,
            COALESCE(v_lease_seconds, q.default_lease_seconds),
            COALESCE(v_max_attempts, q.default_max_attempts),
            q.default_backoff_mode, q.default_backoff_base, q.default_backoff_cap
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING;

        IF FOUND THEN
            PERFORM taskq.emit_event(v_id, NULL, 'enqueued', 'system', NULL,
                jsonb_build_object('status','queued','scheduled_at',v_scheduled_at));
            IF v_scheduled_at <= now() AND q.notify_enabled THEN
                PERFORM pg_notify('taskq_' || v_queue, '');
            END IF;
            RETURN QUERY SELECT v_id, true;
            RETURN;
        END IF;

        SELECT j.* INTO v_existing FROM taskq.jobs j
         WHERE j.queue = v_queue AND j.idempotency_key = v_key
           AND j.status IN ('blocked','queued','running')
         ORDER BY j.created_at DESC LIMIT 1;
        IF v_existing.id IS NOT NULL THEN
            IF v_existing.parent_job_id IS DISTINCT FROM p_parent_job_id
               OR v_existing.job_type IS DISTINCT FROM v_job_type
               OR v_existing.payload IS DISTINCT FROM v_payload
               OR v_existing.headers IS DISTINCT FROM v_headers
               OR v_existing.priority IS DISTINCT FROM COALESCE(v_priority, q.default_priority)
               OR v_existing.max_attempts IS DISTINCT FROM
                    COALESCE(v_max_attempts, q.default_max_attempts)
               OR v_existing.lease_seconds IS DISTINCT FROM
                    COALESCE(v_lease_seconds, q.default_lease_seconds)
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

CREATE OR REPLACE FUNCTION taskq.complete_job(
    p_job_id uuid, p_attempt_id uuid, p_worker_id text,
    p_result jsonb DEFAULT NULL, p_stats jsonb DEFAULT NULL,
    p_followups jsonb DEFAULT NULL
) RETURNS taskq.settle_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_job record;
    v_att text;
    v_spec jsonb;
    v_index integer := 0;
    v_step text;
    v_steps text[] := '{}';
    v_queue text;
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_result IS NOT NULL AND octet_length(p_result::text) > 8192 THEN
        RAISE EXCEPTION 'result exceeds the 8KB limit' USING ERRCODE = 'TQ422';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_typeof(p_followups) <> 'array' THEN
        RAISE EXCEPTION 'p_followups must be a jsonb array' USING ERRCODE = 'TQ422';
    END IF;

    SELECT j.status, j.current_attempt_id, j.finished_by_attempt_id, j.queue
      INTO v_job FROM taskq.jobs j WHERE j.id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN RETURN ('lost',NULL,NULL)::taskq.settle_result; END IF;
    IF v_job.status <> 'running' OR v_job.current_attempt_id IS DISTINCT FROM p_attempt_id THEN
        SELECT a.status INTO v_att FROM taskq.job_attempts a
         WHERE a.id = p_attempt_id AND a.job_id = p_job_id;
        IF v_att = 'succeeded' THEN
            RETURN ('already_settled',v_job.status,NULL)::taskq.settle_result;
        ELSIF v_att IN ('failed','released','snoozed','cancelled','expired') THEN
            RETURN ('settle_conflict',v_job.status,NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost',NULL,NULL)::taskq.settle_result;
    END IF;

    IF p_followups IS NOT NULL AND jsonb_array_length(p_followups) > 0
       AND NOT taskq.has_capability('followups') THEN
        RAISE EXCEPTION 'followups are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_array_length(p_followups) > 20 THEN
        RAISE EXCEPTION 'followup cap is 20 per settlement' USING ERRCODE = 'TQ422';
    END IF;

    -- Validate every item and target before changing the parent. The private
    -- helper repeats defensive validation at insertion time.
    FOR v_spec IN SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]'::jsonb)) LOOP
        v_index := v_index + 1;
        IF jsonb_typeof(v_spec) <> 'object' THEN
            RAISE EXCEPTION 'followup spec % must be an object', v_index USING ERRCODE='TQ422';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_object_keys(v_spec) AS k(key)
             WHERE k.key NOT IN (
                'step','job_type','queue','payload','headers','priority',
                'max_attempts','lease_seconds','scheduled_at'
             )
        ) THEN
            RAISE EXCEPTION 'followup spec % has an unknown field', v_index
                USING ERRCODE='TQ422';
        END IF;
        v_step := v_spec->>'step';
        IF v_step IS NULL OR octet_length(v_step) NOT BETWEEN 1 AND 64
           OR v_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
            RAISE EXCEPTION 'followup spec % has invalid step', v_index USING ERRCODE='TQ422';
        END IF;
        IF v_step = ANY(v_steps) THEN
            RAISE EXCEPTION 'duplicate followup step' USING ERRCODE='TQ422';
        END IF;
        v_steps := array_append(v_steps,v_step);
        IF COALESCE(v_spec->>'job_type','') = '' OR char_length(v_spec->>'job_type') > 120 THEN
            RAISE EXCEPTION 'followup spec % has invalid job_type', v_index USING ERRCODE='TQ422';
        END IF;
        v_queue := COALESCE(v_spec->>'queue',v_job.queue);
        IF NOT EXISTS (SELECT 1 FROM taskq.queues q WHERE q.name=v_queue) THEN
            RAISE EXCEPTION 'followup spec % names unknown queue', v_index USING ERRCODE='TQ422';
        END IF;
        -- Run the full scalar/object validation without retaining its result by
        -- duplicating the helper's bounded checks before mutation.
        IF jsonb_typeof(COALESCE(v_spec->'payload','{}'::jsonb)) <> 'object'
           OR octet_length(COALESCE(v_spec->'payload','{}'::jsonb)::text) > 65536
           OR jsonb_typeof(COALESCE(v_spec->'headers','{}'::jsonb)) <> 'object'
           OR octet_length(COALESCE(v_spec->'headers','{}'::jsonb)::text) > 8192 THEN
            RAISE EXCEPTION 'followup spec % has invalid bounded JSON', v_index USING ERRCODE='TQ422';
        END IF;
        BEGIN
            IF v_spec ? 'priority' THEN
                IF jsonb_typeof(v_spec->'priority') <> 'number'
                   OR (v_spec->>'priority') !~ '^-?[0-9]+$'
                   OR (v_spec->>'priority')::integer NOT BETWEEN 0 AND 1000 THEN
                    RAISE data_exception;
                END IF;
            END IF;
            IF v_spec ? 'max_attempts' THEN
                IF jsonb_typeof(v_spec->'max_attempts') <> 'number'
                   OR (v_spec->>'max_attempts') !~ '^-?[0-9]+$'
                   OR (v_spec->>'max_attempts')::integer NOT BETWEEN 1 AND 100 THEN
                    RAISE data_exception;
                END IF;
            END IF;
            IF v_spec ? 'lease_seconds' THEN
                IF jsonb_typeof(v_spec->'lease_seconds') <> 'number'
                   OR (v_spec->>'lease_seconds') !~ '^-?[0-9]+$'
                   OR (v_spec->>'lease_seconds')::integer NOT BETWEEN 15 AND 86400 THEN
                    RAISE data_exception;
                END IF;
            END IF;
            IF v_spec ? 'scheduled_at' THEN
                IF jsonb_typeof(v_spec->'scheduled_at') <> 'string' THEN
                    RAISE data_exception;
                END IF;
                PERFORM (v_spec->>'scheduled_at')::timestamptz;
            END IF;
        EXCEPTION WHEN data_exception OR invalid_text_representation OR datetime_field_overflow
                       OR numeric_value_out_of_range THEN
            RAISE EXCEPTION 'followup spec % has an invalid scalar field', v_index
                USING ERRCODE='TQ422';
        END;
    END LOOP;

    UPDATE taskq.jobs SET status='succeeded', outcome='success', worker_id=NULL,
        current_attempt_id=NULL, lease_expires_at=NULL,
        result=COALESCE(p_result,result), error=NULL, expiry_streak=0,
        finished_at=now(), finished_by_attempt_id=p_attempt_id, updated_at=now()
     WHERE id=p_job_id;
    UPDATE taskq.job_attempts SET status='succeeded', outcome='success',
        finished_at=now(), stats=COALESCE(p_stats,stats)
     WHERE id=p_attempt_id AND status='running';
    PERFORM taskq.emit_event(p_job_id,p_attempt_id,'succeeded',p_worker_id,NULL,NULL);

    v_index := 0;
    FOR v_spec IN SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]'::jsonb)) LOOP
        v_index := v_index + 1;
        PERFORM * FROM taskq._enqueue_followup(p_job_id,v_job.queue,v_spec,v_index);
    END LOOP;
    RETURN ('ok','succeeded',NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) TO taskq_runner;

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('contract_version','"0.2.0"'::jsonb,now()),
    ('capabilities','{"active":["admission_reservations","followups","read_model_list_ready"]}'::jsonb,now())
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now();
