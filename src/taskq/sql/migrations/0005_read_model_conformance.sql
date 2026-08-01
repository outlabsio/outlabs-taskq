-- outlabs-taskq — migration 0005: SQL contract 0.1.4
-- DERIVED FROM: ADR-021 + Function Manifest §4.1/§12.
-- Immutable conformance repair: queue existence precedes the per-view gate.

CREATE OR REPLACE FUNCTION taskq.list_jobs(
    p_queue text, p_view text, p_limit int DEFAULT 50, p_after jsonb DEFAULT NULL
) RETURNS taskq.job_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp AS $$
DECLARE
    v_capability text; v_as_of timestamptz; v_items taskq.job_list_item[];
    v_next jsonb; v_last taskq.job_list_item;
    v_priority smallint; v_time timestamptz; v_id uuid;
BEGIN
    IF p_queue IS NULL OR p_queue !~ '^[a-z0-9_]{1,57}$' OR p_view NOT IN ('ready','running','finished')
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid read model page input' USING ERRCODE = 'TQ422';
    END IF;
    IF p_after IS NOT NULL THEN
        IF jsonb_typeof(p_after) <> 'object' OR p_after->>'queue' IS DISTINCT FROM p_queue
           OR p_after->>'view' IS DISTINCT FROM p_view OR NOT (p_after ? 'id')
           OR (p_view = 'ready' AND (NOT (p_after ? 'priority') OR NOT (p_after ? 'scheduled_at')))
           OR (p_view = 'running' AND NOT (p_after ? 'started_at'))
           OR (p_view = 'finished' AND NOT (p_after ? 'finished_at')) THEN
            RAISE EXCEPTION 'cursor does not match queue/view' USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            v_id := (p_after->>'id')::uuid;
            IF p_view = 'ready' THEN
                v_priority := (p_after->>'priority')::smallint;
                v_time := (p_after->>'scheduled_at')::timestamptz;
            ELSIF p_view = 'running' THEN
                v_time := (p_after->>'started_at')::timestamptz;
            ELSE
                v_time := (p_after->>'finished_at')::timestamptz;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'invalid read-model cursor' USING ERRCODE='TQ422';
        END;
    END IF;
    PERFORM 1 FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'queue not found' USING ERRCODE = 'TQ001';
    END IF;
    v_capability := 'read_model_list_' || p_view;
    IF NOT taskq.has_capability(v_capability) THEN
        RAISE EXCEPTION 'read-model view inactive' USING ERRCODE='TQ501',
            DETAIL='reason=read_model_view_inactive view=' || p_view;
    END IF;
    v_as_of := now();
    IF p_view = 'ready' THEN
        SELECT ARRAY(SELECT ROW(j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
            j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,j.started_at,j.finished_at,j.updated_at)::taskq.job_list_item
            FROM (SELECT * FROM taskq.jobs j WHERE j.queue=p_queue AND j.status='queued'
                AND j.cancel_requested_at IS NULL AND j.scheduled_at<=v_as_of
                AND (p_after IS NULL OR (j.priority,j.scheduled_at,j.id) > (v_priority,v_time,v_id))
                ORDER BY j.priority,j.scheduled_at,j.id LIMIT p_limit+1) j) INTO v_items;
    ELSIF p_view = 'running' THEN
        SELECT ARRAY(SELECT ROW(j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
            j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,j.started_at,j.finished_at,j.updated_at)::taskq.job_list_item
            FROM (SELECT * FROM taskq.jobs j WHERE j.queue=p_queue AND j.status='running'
                AND (p_after IS NULL OR (j.started_at,j.id) < (v_time,v_id))
                ORDER BY j.started_at DESC,j.id DESC LIMIT p_limit+1) j) INTO v_items;
    ELSE
        SELECT ARRAY(SELECT ROW(j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
            j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,j.started_at,j.finished_at,j.updated_at)::taskq.job_list_item
            FROM (SELECT * FROM taskq.jobs j WHERE j.queue=p_queue AND j.status IN ('succeeded','failed','cancelled')
                AND (p_after IS NULL OR (j.finished_at,j.id) < (v_time,v_id))
                ORDER BY j.finished_at DESC,j.id DESC LIMIT p_limit+1) j) INTO v_items;
    END IF;
    v_items := COALESCE(v_items, ARRAY[]::taskq.job_list_item[]);
    IF cardinality(v_items) > p_limit THEN
        v_last := v_items[p_limit];
        IF p_view = 'ready' THEN
            v_next := jsonb_build_object('queue',p_queue,'view',p_view,'priority',v_last.priority,
                'scheduled_at',v_last.scheduled_at,'id',v_last.job_id);
        ELSIF p_view = 'running' THEN
            v_next := jsonb_build_object('queue',p_queue,'view',p_view,'started_at',v_last.started_at,'id',v_last.job_id);
        ELSE
            v_next := jsonb_build_object('queue',p_queue,'view',p_view,'finished_at',v_last.finished_at,'id',v_last.job_id);
        END IF;
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of,v_items,v_next)::taskq.job_page;
END $$;
ALTER FUNCTION taskq.list_jobs(text,text,int,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_jobs(text,text,int,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_jobs(text,text,int,jsonb) TO taskq_observer;

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('contract_version','"0.1.4"'::jsonb,now())
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at;
