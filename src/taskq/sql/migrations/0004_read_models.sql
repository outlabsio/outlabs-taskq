-- outlabs-taskq — migration 0004: SQL contract 0.1.3
-- DERIVED FROM: ADR-019 + ADR-020 + Function Manifest §4.1/§5.1/§11.
-- The bridge runtime is the rollback floor for a database after this migration.

ALTER TABLE taskq.queues
    ADD COLUMN profile_version bigint NOT NULL DEFAULT 1;

CREATE TYPE taskq.job_list_item AS (
    job_id uuid, job_type text, status text, outcome text, priority smallint,
    attempt_count smallint, failure_count smallint, max_attempts smallint,
    created_at timestamptz, scheduled_at timestamptz, started_at timestamptz,
    finished_at timestamptz, updated_at timestamptz
);
CREATE TYPE taskq.job_page AS (
    as_of timestamptz, items taskq.job_list_item[], next_after jsonb
);
CREATE TYPE taskq.queue_profile AS (
    name text, profile_version bigint,
    default_priority smallint, default_lease_seconds int, default_max_attempts smallint,
    default_backoff_mode text, default_backoff_base int, default_backoff_cap int,
    retention_hours int, failed_retention_hours int, max_depth int,
    notify_enabled boolean, paused boolean
);
CREATE TYPE taskq.queue_profile_update AS (
    result text, profile taskq.queue_profile, current_version bigint
);
ALTER TYPE taskq.job_list_item OWNER TO taskq_owner;
ALTER TYPE taskq.job_page OWNER TO taskq_owner;
ALTER TYPE taskq.queue_profile OWNER TO taskq_owner;
ALTER TYPE taskq.queue_profile_update OWNER TO taskq_owner;

CREATE OR REPLACE FUNCTION taskq.ensure_queue(
    p_name text, p_profile jsonb DEFAULT '{}'::jsonb, p_actor text DEFAULT NULL
) RETURNS TABLE (result text, profile jsonb)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_field text;
    v_old taskq.queues%ROWTYPE;
    v_new taskq.queues%ROWTYPE;
    v_result text;
BEGIN
    IF p_name IS NULL OR p_name !~ '^[a-z0-9_]{1,57}$' THEN
        RAISE EXCEPTION 'queue name must match ^[a-z0-9_]{1,57}$' USING ERRCODE = 'TQ422';
    END IF;
    IF p_profile IS NULL THEN p_profile := '{}'::jsonb; END IF;
    IF jsonb_typeof(p_profile) <> 'object' THEN
        RAISE EXCEPTION 'profile must be a json object' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_field IN SELECT jsonb_object_keys(p_profile) LOOP
        IF v_field NOT IN ('default_priority','default_lease_seconds','default_max_attempts',
                           'default_backoff_mode','default_backoff_base','default_backoff_cap',
                           'retention_hours','failed_retention_hours','max_depth','notify_enabled') THEN
            RAISE EXCEPTION 'unknown queue profile field "%"', v_field USING ERRCODE = 'TQ422';
        END IF;
    END LOOP;

    SELECT * INTO v_old FROM taskq.queues WHERE name = p_name FOR UPDATE;
    IF NOT FOUND THEN
        v_new.name := p_name;
        v_new.profile_version := 1;
        v_new.default_priority := 100; v_new.default_lease_seconds := 300;
        v_new.default_max_attempts := 5; v_new.default_backoff_mode := 'exponential';
        v_new.default_backoff_base := 30; v_new.default_backoff_cap := 3600;
        v_new.retention_hours := 48; v_new.failed_retention_hours := 336;
        v_new.max_depth := NULL; v_new.notify_enabled := true;
    ELSE
        v_new := v_old;
    END IF;
    BEGIN
        v_new := jsonb_populate_record(v_new, p_profile);
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'invalid queue profile value: %', SQLERRM USING ERRCODE = 'TQ422';
    END;
    IF v_new.default_priority IS NULL OR v_new.default_priority NOT BETWEEN 0 AND 1000 THEN
        RAISE EXCEPTION 'default_priority must be 0..1000' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_lease_seconds IS NULL OR v_new.default_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'default_lease_seconds must be 15..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_max_attempts IS NULL OR v_new.default_max_attempts NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'default_max_attempts must be 1..100' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_backoff_mode IS NULL OR v_new.default_backoff_mode NOT IN ('fixed','exponential') THEN
        RAISE EXCEPTION 'default_backoff_mode must be fixed|exponential' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_backoff_base IS NULL OR v_new.default_backoff_base NOT BETWEEN 1 AND 86400
       OR v_new.default_backoff_cap IS NULL OR v_new.default_backoff_cap < v_new.default_backoff_base THEN
        RAISE EXCEPTION 'invalid queue backoff profile' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.retention_hours IS NULL OR v_new.retention_hours < 1
       OR v_new.failed_retention_hours IS NULL OR v_new.failed_retention_hours < 1 THEN
        RAISE EXCEPTION 'queue retention hours must be >= 1' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.max_depth IS NOT NULL AND v_new.max_depth <= 0 THEN
        RAISE EXCEPTION 'max_depth must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.notify_enabled IS NULL THEN
        RAISE EXCEPTION 'notify_enabled must be a boolean' USING ERRCODE = 'TQ422';
    END IF;

    IF v_old.name IS NULL THEN
        INSERT INTO taskq.queues (name, profile_version, default_priority, default_lease_seconds,
            default_max_attempts, default_backoff_mode, default_backoff_base, default_backoff_cap,
            retention_hours, failed_retention_hours, max_depth, notify_enabled)
        VALUES (v_new.name, 1, v_new.default_priority, v_new.default_lease_seconds,
            v_new.default_max_attempts, v_new.default_backoff_mode, v_new.default_backoff_base,
            v_new.default_backoff_cap, v_new.retention_hours, v_new.failed_retention_hours,
            v_new.max_depth, v_new.notify_enabled);
        v_result := 'created';
    ELSIF (v_new.default_priority, v_new.default_lease_seconds, v_new.default_max_attempts,
           v_new.default_backoff_mode, v_new.default_backoff_base, v_new.default_backoff_cap,
           v_new.retention_hours, v_new.failed_retention_hours, v_new.max_depth, v_new.notify_enabled)
          IS NOT DISTINCT FROM
          (v_old.default_priority, v_old.default_lease_seconds, v_old.default_max_attempts,
           v_old.default_backoff_mode, v_old.default_backoff_base, v_old.default_backoff_cap,
           v_old.retention_hours, v_old.failed_retention_hours, v_old.max_depth, v_old.notify_enabled) THEN
        v_result := 'unchanged';
    ELSE
        UPDATE taskq.queues SET default_priority=v_new.default_priority,
            default_lease_seconds=v_new.default_lease_seconds, default_max_attempts=v_new.default_max_attempts,
            default_backoff_mode=v_new.default_backoff_mode, default_backoff_base=v_new.default_backoff_base,
            default_backoff_cap=v_new.default_backoff_cap, retention_hours=v_new.retention_hours,
            failed_retention_hours=v_new.failed_retention_hours, max_depth=v_new.max_depth,
            notify_enabled=v_new.notify_enabled, profile_version=profile_version + 1, updated_at=now()
        WHERE name = p_name;
        v_result := 'updated';
    END IF;
    SELECT * INTO v_new FROM taskq.queues WHERE name = p_name;
    RETURN QUERY SELECT v_result, jsonb_build_object('name',v_new.name,'paused',v_new.paused_at IS NOT NULL,
        'profile_version',v_new.profile_version,'default_priority',v_new.default_priority,
        'default_lease_seconds',v_new.default_lease_seconds,'default_max_attempts',v_new.default_max_attempts,
        'default_backoff_mode',v_new.default_backoff_mode,'default_backoff_base',v_new.default_backoff_base,
        'default_backoff_cap',v_new.default_backoff_cap,'retention_hours',v_new.retention_hours,
        'failed_retention_hours',v_new.failed_retention_hours,'max_depth',v_new.max_depth,
        'notify_enabled',v_new.notify_enabled);
END $$;
ALTER FUNCTION taskq.ensure_queue(text,jsonb,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.ensure_queue(text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.ensure_queue(text,jsonb,text) TO taskq_operator;

CREATE FUNCTION taskq.get_queue_profile(p_queue text)
RETURNS taskq.queue_profile LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp AS $$
    SELECT ROW(q.name,q.profile_version,q.default_priority,q.default_lease_seconds,q.default_max_attempts,
        q.default_backoff_mode,q.default_backoff_base,q.default_backoff_cap,q.retention_hours,
        q.failed_retention_hours,q.max_depth,q.notify_enabled,q.paused_at IS NOT NULL)::taskq.queue_profile
    FROM taskq.queues q WHERE q.name = p_queue
$$;
ALTER FUNCTION taskq.get_queue_profile(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_queue_profile(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_profile(text) TO taskq_observer;

CREATE FUNCTION taskq.update_queue_profile(
    p_name text, p_profile jsonb, p_actor text, p_expected_version bigint
) RETURNS taskq.queue_profile_update
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp AS $$
DECLARE v_version bigint; v_profile taskq.queue_profile;
BEGIN
    IF p_name IS NULL OR p_name !~ '^[a-z0-9_]{1,57}$' OR p_expected_version IS NULL OR p_expected_version < 1 THEN
        RAISE EXCEPTION 'queue and positive expected version required' USING ERRCODE = 'TQ422';
    END IF;
    SELECT profile_version INTO v_version FROM taskq.queues WHERE name=p_name FOR UPDATE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF v_version <> p_expected_version THEN
        RETURN ROW('profile_version_conflict', NULL::taskq.queue_profile, v_version)::taskq.queue_profile_update;
    END IF;
    PERFORM taskq.ensure_queue(p_name, p_profile, p_actor);
    SELECT * INTO v_profile FROM taskq.get_queue_profile(p_name);
    RETURN ROW('updated',v_profile,v_profile.profile_version)::taskq.queue_profile_update;
END $$;
ALTER FUNCTION taskq.update_queue_profile(text,jsonb,text,bigint) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.update_queue_profile(text,jsonb,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.update_queue_profile(text,jsonb,text,bigint) TO taskq_operator;

CREATE FUNCTION taskq.list_jobs(
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
    ('contract_version','"0.1.3"'::jsonb,now())
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at;
