-- outlabs-taskq — migration 0021: SQL contract 0.3.1 CLI read model
-- Additive, bounded operator projections. No attempt fences or raw-table access.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta
    WHERE key = 'contract_version' FOR UPDATE;
    SELECT value INTO v_capabilities FROM taskq.meta
    WHERE key = 'capabilities' FOR UPDATE;
    IF v_contract IS DISTINCT FROM '"0.3.0"'::jsonb THEN
        RAISE EXCEPTION '0021 requires contract 0.3.0, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0021 requires the exact 0020 capability set, found %',
            v_capabilities USING ERRCODE = 'TQ500';
    END IF;
END $$;

CREATE TYPE taskq.job_event_list_item AS (
    event_id bigint,
    event_type text,
    actor text,
    created_at timestamptz,
    message text,
    data jsonb
);
ALTER TYPE taskq.job_event_list_item OWNER TO taskq_owner;

CREATE TYPE taskq.job_event_page AS (
    as_of timestamptz,
    items taskq.job_event_list_item[],
    next_after bigint
);
ALTER TYPE taskq.job_event_page OWNER TO taskq_owner;

CREATE TYPE taskq.workflow_list_item AS (
    workflow_id uuid,
    workflow_key text,
    kind text,
    status text,
    sealed boolean,
    cancel_requested boolean,
    declared_queues text[],
    created_at timestamptz,
    updated_at timestamptz,
    finished_at timestamptz
);
ALTER TYPE taskq.workflow_list_item OWNER TO taskq_owner;

CREATE TYPE taskq.workflow_list_page AS (
    as_of timestamptz,
    items taskq.workflow_list_item[],
    next_after jsonb
);
ALTER TYPE taskq.workflow_list_page OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_list_item AS (
    schedule_id uuid,
    name text,
    target jsonb,
    recurrence jsonb,
    catchup_policy text,
    max_catchup integer,
    state text,
    next_fire_at timestamptz,
    last_fire_at timestamptz,
    version bigint
);
ALTER TYPE taskq.schedule_list_item OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_list_page AS (
    as_of timestamptz,
    items taskq.schedule_list_item[],
    next_after text
);
ALTER TYPE taskq.schedule_list_page OWNER TO taskq_owner;

-- PostgreSQL 16/18 bounded-plan gates over 60k jobs, 20k workflows, and 20k
-- schedules showed full scans plus top-N sorts without these exact projections.
CREATE INDEX taskq_jobs_scheduled_page_idx
    ON taskq.jobs (queue, scheduled_at, id)
    WHERE status = 'queued' AND cancel_requested_at IS NULL;
CREATE INDEX taskq_jobs_blocked_page_idx
    ON taskq.jobs (queue, created_at, id)
    WHERE status = 'blocked' AND cancel_requested_at IS NULL;
CREATE INDEX taskq_jobs_cancel_requested_page_idx
    ON taskq.jobs (queue, cancel_requested_at DESC, id DESC)
    WHERE cancel_requested_at IS NOT NULL
      AND status IN ('blocked','queued','running');
CREATE INDEX taskq_workflows_running_page_idx
    ON taskq.workflows (updated_at DESC, id DESC)
    WHERE status = 'running';
CREATE INDEX taskq_workflows_finished_page_idx
    ON taskq.workflows (finished_at DESC, id DESC)
    WHERE status <> 'running';
CREATE INDEX taskq_schedules_active_page_idx
    ON taskq.schedules (name)
    WHERE state = 'active' AND target->>'kind' = 'job';
CREATE INDEX taskq_schedules_paused_page_idx
    ON taskq.schedules (name)
    WHERE state = 'paused' AND target->>'kind' = 'job';
CREATE INDEX taskq_schedules_retired_page_idx
    ON taskq.schedules (name)
    WHERE state = 'retired' AND target->>'kind' = 'job';

CREATE OR REPLACE FUNCTION taskq.list_jobs(
    p_queue text,
    p_view text,
    p_limit integer DEFAULT 50,
    p_after jsonb DEFAULT NULL
) RETURNS taskq.job_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_capability text;
    v_as_of timestamptz;
    v_items taskq.job_list_item[];
    v_next jsonb;
    v_last taskq.job_list_item;
    v_priority smallint;
    v_time timestamptz;
    v_id uuid;
BEGIN
    IF p_queue IS NULL OR p_queue !~ '^[a-z0-9_]{1,57}$'
       OR p_view NOT IN (
           'ready','scheduled','blocked','running','cancel_requested','failed','finished'
       )
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid read model page input' USING ERRCODE = 'TQ422';
    END IF;
    IF p_after IS NOT NULL THEN
        IF jsonb_typeof(p_after) <> 'object'
           OR p_after->>'queue' IS DISTINCT FROM p_queue
           OR p_after->>'view' IS DISTINCT FROM p_view
           OR NOT (p_after ? 'id')
           OR (p_view = 'ready' AND (
               NOT (p_after ? 'priority') OR NOT (p_after ? 'scheduled_at')
           ))
           OR (p_view IN ('scheduled','blocked') AND NOT (p_after ? 'ordered_at'))
           OR (p_view = 'running' AND NOT (p_after ? 'started_at'))
           OR (p_view = 'cancel_requested' AND NOT (p_after ? 'cancel_requested_at'))
           OR (p_view IN ('failed','finished') AND NOT (p_after ? 'finished_at')) THEN
            RAISE EXCEPTION 'cursor does not match queue/view' USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            v_id := (p_after->>'id')::uuid;
            IF p_view = 'ready' THEN
                v_priority := (p_after->>'priority')::smallint;
                v_time := (p_after->>'scheduled_at')::timestamptz;
            ELSIF p_view IN ('scheduled','blocked') THEN
                v_time := (p_after->>'ordered_at')::timestamptz;
            ELSIF p_view = 'running' THEN
                v_time := (p_after->>'started_at')::timestamptz;
            ELSIF p_view = 'cancel_requested' THEN
                v_time := (p_after->>'cancel_requested_at')::timestamptz;
            ELSE
                v_time := (p_after->>'finished_at')::timestamptz;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'invalid read-model cursor' USING ERRCODE = 'TQ422';
        END;
    END IF;

    PERFORM 1 FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'queue not found' USING ERRCODE = 'TQ001';
    END IF;
    IF p_view IN ('ready','running','finished') THEN
        v_capability := 'read_model_list_' || p_view;
    ELSE
        v_capability := 'read_model_job_views_v2';
    END IF;
    IF NOT taskq.has_capability(v_capability) THEN
        RAISE EXCEPTION 'read-model view inactive' USING ERRCODE = 'TQ501',
            DETAIL = 'reason=read_model_view_inactive view=' || p_view;
    END IF;

    v_as_of := now();
    IF p_view = 'ready' THEN
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.status = 'queued'
                  AND j.cancel_requested_at IS NULL AND j.scheduled_at <= v_as_of
                  AND (p_after IS NULL OR
                       (j.priority,j.scheduled_at,j.id) > (v_priority,v_time,v_id))
                ORDER BY j.priority,j.scheduled_at,j.id LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    ELSIF p_view = 'scheduled' THEN
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.status = 'queued'
                  AND j.cancel_requested_at IS NULL AND j.scheduled_at > v_as_of
                  AND (p_after IS NULL OR (j.scheduled_at,j.id) > (v_time,v_id))
                ORDER BY j.scheduled_at,j.id LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    ELSIF p_view = 'blocked' THEN
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.status = 'blocked'
                  AND j.cancel_requested_at IS NULL
                  AND (p_after IS NULL OR (j.created_at,j.id) > (v_time,v_id))
                ORDER BY j.created_at,j.id LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    ELSIF p_view = 'running' THEN
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.status = 'running'
                  AND (p_after IS NULL OR (j.started_at,j.id) < (v_time,v_id))
                ORDER BY j.started_at DESC,j.id DESC LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    ELSIF p_view = 'cancel_requested' THEN
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.cancel_requested_at IS NOT NULL
                  AND j.status IN ('blocked','queued','running')
                  AND (p_after IS NULL OR
                       (j.cancel_requested_at,j.id) < (v_time,v_id))
                ORDER BY j.cancel_requested_at DESC,j.id DESC LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    ELSIF p_view = 'failed' THEN
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.status = 'failed'
                  AND (p_after IS NULL OR (j.finished_at,j.id) < (v_time,v_id))
                ORDER BY j.finished_at DESC,j.id DESC LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    ELSE
        SELECT ARRAY(
            SELECT ROW(
                j.id,j.job_type,j.status,j.outcome,j.priority,j.attempt_count,
                j.failure_count,j.max_attempts,j.created_at,j.scheduled_at,
                j.started_at,j.finished_at,j.updated_at
            )::taskq.job_list_item
            FROM (
                SELECT * FROM taskq.jobs AS j
                WHERE j.queue = p_queue AND j.status IN ('succeeded','failed','cancelled')
                  AND (p_after IS NULL OR (j.finished_at,j.id) < (v_time,v_id))
                ORDER BY j.finished_at DESC,j.id DESC LIMIT p_limit + 1
            ) AS j
        ) INTO v_items;
    END IF;

    v_items := COALESCE(v_items, ARRAY[]::taskq.job_list_item[]);
    IF cardinality(v_items) > p_limit THEN
        v_last := v_items[p_limit];
        IF p_view = 'ready' THEN
            v_next := jsonb_build_object(
                'queue',p_queue,'view',p_view,'priority',v_last.priority,
                'scheduled_at',v_last.scheduled_at,'id',v_last.job_id
            );
        ELSIF p_view = 'scheduled' THEN
            v_next := jsonb_build_object(
                'queue',p_queue,'view',p_view,'ordered_at',v_last.scheduled_at,
                'id',v_last.job_id
            );
        ELSIF p_view = 'blocked' THEN
            v_next := jsonb_build_object(
                'queue',p_queue,'view',p_view,'ordered_at',v_last.created_at,
                'id',v_last.job_id
            );
        ELSIF p_view = 'running' THEN
            v_next := jsonb_build_object(
                'queue',p_queue,'view',p_view,'started_at',v_last.started_at,
                'id',v_last.job_id
            );
        ELSIF p_view = 'cancel_requested' THEN
            SELECT j.cancel_requested_at INTO v_time
            FROM taskq.jobs AS j WHERE j.id = v_last.job_id;
            v_next := jsonb_build_object(
                'queue',p_queue,'view',p_view,'cancel_requested_at',v_time,
                'id',v_last.job_id
            );
        ELSE
            v_next := jsonb_build_object(
                'queue',p_queue,'view',p_view,'finished_at',v_last.finished_at,
                'id',v_last.job_id
            );
        END IF;
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of,v_items,v_next)::taskq.job_page;
END $$;
ALTER FUNCTION taskq.list_jobs(text,text,integer,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_jobs(text,text,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_jobs(text,text,integer,jsonb) TO taskq_observer;

CREATE FUNCTION taskq.list_job_events(
    p_job_id uuid,
    p_limit integer DEFAULT 50,
    p_after bigint DEFAULT NULL,
    p_include_details boolean DEFAULT false
) RETURNS taskq.job_event_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_as_of timestamptz;
    v_items taskq.job_event_list_item[];
    v_next bigint;
BEGIN
    IF p_job_id IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR (p_after IS NOT NULL AND p_after < 1) OR p_include_details IS NULL THEN
        RAISE EXCEPTION 'invalid job event page input' USING ERRCODE = 'TQ422';
    END IF;
    PERFORM 1 FROM taskq.jobs WHERE id = p_job_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such job' USING ERRCODE = 'TQ001';
    END IF;
    IF NOT taskq.has_capability('read_model_job_events') THEN
        RAISE EXCEPTION 'job event read model inactive' USING ERRCODE = 'TQ501';
    END IF;
    v_as_of := now();
    SELECT ARRAY(
        SELECT ROW(
            e.id,e.event_type,e.actor,e.created_at,
            CASE WHEN p_include_details THEN e.message END,
            CASE WHEN p_include_details THEN e.data END
        )::taskq.job_event_list_item
        FROM taskq.job_events AS e
        WHERE e.job_id = p_job_id AND (p_after IS NULL OR e.id > p_after)
        ORDER BY e.id
        LIMIT p_limit + 1
    ) INTO v_items;
    v_items := COALESCE(v_items, ARRAY[]::taskq.job_event_list_item[]);
    IF cardinality(v_items) > p_limit THEN
        v_next := v_items[p_limit].event_id;
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of,v_items,v_next)::taskq.job_event_page;
END $$;
ALTER FUNCTION taskq.list_job_events(uuid,integer,bigint,boolean) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_job_events(uuid,integer,bigint,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_job_events(uuid,integer,bigint,boolean)
    TO taskq_observer;

CREATE FUNCTION taskq.list_workflows(
    p_view text,
    p_limit integer DEFAULT 50,
    p_after jsonb DEFAULT NULL
) RETURNS taskq.workflow_list_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_as_of timestamptz;
    v_items taskq.workflow_list_item[];
    v_next jsonb;
    v_time timestamptz;
    v_id uuid;
    v_last taskq.workflow_list_item;
BEGIN
    IF p_view NOT IN ('running','finished')
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid workflow list input' USING ERRCODE = 'TQ422';
    END IF;
    IF p_after IS NOT NULL THEN
        IF jsonb_typeof(p_after) <> 'object'
           OR p_after->>'view' IS DISTINCT FROM p_view
           OR NOT (p_after ? 'id')
           OR NOT (p_after ? 'ordered_at') THEN
            RAISE EXCEPTION 'invalid workflow list cursor' USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            v_time := (p_after->>'ordered_at')::timestamptz;
            v_id := (p_after->>'id')::uuid;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'invalid workflow list cursor' USING ERRCODE = 'TQ422';
        END;
    END IF;
    IF NOT taskq.has_capability('read_model_workflow_list') THEN
        RAISE EXCEPTION 'workflow list inactive' USING ERRCODE = 'TQ501';
    END IF;
    v_as_of := now();
    IF p_view = 'running' THEN
        SELECT ARRAY(
            SELECT ROW(
                w.id,w.workflow_key,w.kind,w.status,w.sealed_at IS NOT NULL,
                w.cancel_requested_at IS NOT NULL,w.declared_queues,w.created_at,
                w.updated_at,w.finished_at
            )::taskq.workflow_list_item
            FROM taskq.workflows AS w
            WHERE w.status = 'running'
              AND (p_after IS NULL OR (w.updated_at,w.id) < (v_time,v_id))
            ORDER BY w.updated_at DESC,w.id DESC LIMIT p_limit + 1
        ) INTO v_items;
    ELSE
        SELECT ARRAY(
            SELECT ROW(
                w.id,w.workflow_key,w.kind,w.status,w.sealed_at IS NOT NULL,
                w.cancel_requested_at IS NOT NULL,w.declared_queues,w.created_at,
                w.updated_at,w.finished_at
            )::taskq.workflow_list_item
            FROM taskq.workflows AS w
            WHERE w.status <> 'running'
              AND (p_after IS NULL OR (w.finished_at,w.id) < (v_time,v_id))
            ORDER BY w.finished_at DESC,w.id DESC LIMIT p_limit + 1
        ) INTO v_items;
    END IF;
    v_items := COALESCE(v_items, ARRAY[]::taskq.workflow_list_item[]);
    IF cardinality(v_items) > p_limit THEN
        v_last := v_items[p_limit];
        v_next := jsonb_build_object(
            'view',p_view,
            'ordered_at',CASE WHEN p_view = 'running' THEN v_last.updated_at
                              ELSE v_last.finished_at END,
            'id',v_last.workflow_id
        );
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of,v_items,v_next)::taskq.workflow_list_page;
END $$;
ALTER FUNCTION taskq.list_workflows(text,integer,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_workflows(text,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_workflows(text,integer,jsonb) TO taskq_observer;

CREATE FUNCTION taskq.list_schedules(
    p_view text,
    p_limit integer DEFAULT 50,
    p_after text DEFAULT NULL
) RETURNS taskq.schedule_list_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_as_of timestamptz;
    v_items taskq.schedule_list_item[];
    v_next text;
BEGIN
    IF p_view NOT IN ('active','paused','retired')
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR (p_after IS NOT NULL AND (
           octet_length(p_after) NOT BETWEEN 1 AND 120
           OR p_after !~ '^[a-z0-9][a-z0-9_.-]*$'
       )) THEN
        RAISE EXCEPTION 'invalid schedule list input' USING ERRCODE = 'TQ422';
    END IF;
    IF NOT taskq.has_capability('operator_schedule_list') THEN
        RAISE EXCEPTION 'schedule list inactive' USING ERRCODE = 'TQ501';
    END IF;
    v_as_of := now();
    SELECT ARRAY(
        SELECT ROW(
            s.id,s.name,s.target,s.recurrence,s.catchup_policy,s.max_catchup,
            s.state,s.next_fire_at,s.last_fire_at,s.version
        )::taskq.schedule_list_item
        FROM taskq.schedules AS s
        WHERE s.state = p_view AND s.target->>'kind' = 'job'
          AND (p_after IS NULL OR s.name > p_after)
        ORDER BY s.name LIMIT p_limit + 1
    ) INTO v_items;
    v_items := COALESCE(v_items, ARRAY[]::taskq.schedule_list_item[]);
    IF cardinality(v_items) > p_limit THEN
        v_next := v_items[p_limit].name;
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of,v_items,v_next)::taskq.schedule_list_page;
END $$;
ALTER FUNCTION taskq.list_schedules(text,integer,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_schedules(text,integer,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_schedules(text,integer,text) TO taskq_operator;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.3.1"'::jsonb, now()),
    ('capabilities', '{"active":["admission_reservations","dependencies_workflows","followups","operator_schedule_list","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();

DO $$
DECLARE
    v_bad text[];
BEGIN
    SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text)
      INTO v_bad
      FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND (
           NOT p.prosecdef
           OR p.proconfig IS NULL
           OR NOT p.proconfig @> ARRAY['search_path=pg_catalog, taskq, pg_temp']
       );
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0021 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
