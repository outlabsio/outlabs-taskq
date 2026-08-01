-- outlabs-taskq — migration 0010: native recurring schedules
-- SQL contract 0.2.2 / ADR-027 / Protocol document revision 1.0.11.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.1"'::jsonb THEN
        RAISE EXCEPTION '0010 requires SQL contract 0.2.1, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready"]}'::jsonb THEN
        RAISE EXCEPTION '0010 requires the exact 0009 capability set, found %', v_capabilities;
    END IF;
    IF to_regclass('taskq.schedules') IS NOT NULL
       OR to_regclass('taskq.schedule_occurrences') IS NOT NULL THEN
        RAISE EXCEPTION '0010 requires absent schedule relations';
    END IF;
END $$;

CREATE TYPE taskq.schedule_profile AS (
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
ALTER TYPE taskq.schedule_profile OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_auth_projection AS (
    name text,
    queue text
);
ALTER TYPE taskq.schedule_auth_projection OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_write_result AS (
    outcome text,
    profile taskq.schedule_profile
);
ALTER TYPE taskq.schedule_write_result OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_claim AS (
    schedule_id uuid,
    name text,
    definition_version bigint,
    as_of timestamptz,
    target jsonb,
    recurrence jsonb,
    catchup_policy text,
    max_catchup integer,
    initialized boolean,
    next_fire_at timestamptz,
    token uuid,
    lease_seconds integer
);
ALTER TYPE taskq.schedule_claim OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_claim_batch AS (
    state text,
    schedules taskq.schedule_claim[]
);
ALTER TYPE taskq.schedule_claim_batch OWNER TO taskq_owner;

CREATE TYPE taskq.schedule_action_result AS (
    outcome text,
    replayed boolean,
    schedule_id uuid,
    jobs_enqueued integer,
    next_fire_at timestamptz,
    state text,
    version bigint
);
ALTER TYPE taskq.schedule_action_result OWNER TO taskq_owner;

CREATE TABLE taskq.schedules (
    id uuid PRIMARY KEY DEFAULT taskq.uuid7(),
    name text NOT NULL UNIQUE,
    target jsonb NOT NULL,
    recurrence jsonb NOT NULL,
    catchup_policy text NOT NULL,
    max_catchup integer NOT NULL,
    state text NOT NULL DEFAULT 'active',
    initialized boolean NOT NULL DEFAULT false,
    next_fire_at timestamptz NOT NULL DEFAULT now(),
    last_fire_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    claim_token uuid,
    claim_as_of timestamptz,
    claimed_by text,
    claim_expires_at timestamptz,
    retry_not_before timestamptz,
    last_error text,
    last_action_token uuid,
    last_action_hash text,
    last_action_result jsonb,
    created_by text NOT NULL,
    updated_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    CONSTRAINT schedules_name_ck CHECK (
        octet_length(name) BETWEEN 1 AND 120
        AND name ~ '^[a-z0-9][a-z0-9_.-]*$'
    ),
    CONSTRAINT schedules_target_ck CHECK (
        jsonb_typeof(target) = 'object'
        AND target->>'kind' IN ('job', 'maintenance')
    ),
    CONSTRAINT schedules_recurrence_ck CHECK (
        jsonb_typeof(recurrence) = 'object'
        AND recurrence->>'kind' IN ('interval', 'cron')
    ),
    CONSTRAINT schedules_catchup_ck CHECK (
        catchup_policy IN ('skip', 'fire_once', 'fire_all')
        AND max_catchup BETWEEN 1 AND 100
    ),
    CONSTRAINT schedules_state_ck CHECK (state IN ('active', 'paused', 'retired')),
    CONSTRAINT schedules_version_ck CHECK (version > 0),
    CONSTRAINT schedules_claim_shape_ck CHECK (
        (claim_token IS NULL AND claim_as_of IS NULL AND claimed_by IS NULL
         AND claim_expires_at IS NULL)
        OR
        (claim_token IS NOT NULL AND claim_as_of IS NOT NULL AND claimed_by IS NOT NULL
         AND claim_expires_at IS NOT NULL)
    ),
    CONSTRAINT schedules_last_action_shape_ck CHECK (
        (last_action_token IS NULL AND last_action_hash IS NULL AND last_action_result IS NULL)
        OR
        (last_action_token IS NOT NULL
         AND last_action_hash ~ '^[0-9a-f]{64}$'
         AND jsonb_typeof(last_action_result) = 'object')
    ),
    CONSTRAINT schedules_retired_shape_ck CHECK (
        (state = 'retired') = (retired_at IS NOT NULL)
    )
);
ALTER TABLE taskq.schedules OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.schedules FROM PUBLIC;

CREATE INDEX schedules_due_idx
    ON taskq.schedules (next_fire_at, id)
    WHERE state = 'active';

CREATE TABLE taskq.schedule_occurrences (
    schedule_id uuid NOT NULL REFERENCES taskq.schedules(id),
    due_at timestamptz NOT NULL,
    job_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (schedule_id, due_at)
);
ALTER TABLE taskq.schedule_occurrences OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.schedule_occurrences FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.get_schedule(
    p_name text
) RETURNS taskq.schedule_profile
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
BEGIN
    SELECT * INTO v_schedule
    FROM taskq.schedules
    WHERE name = p_name
      AND target->>'kind' = 'job';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    RETURN (
        v_schedule.id, v_schedule.name, v_schedule.target, v_schedule.recurrence,
        v_schedule.catchup_policy, v_schedule.max_catchup, v_schedule.state,
        v_schedule.next_fire_at, v_schedule.last_fire_at, v_schedule.version
    )::taskq.schedule_profile;
END $$;
ALTER FUNCTION taskq.get_schedule(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_schedule(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_schedule(text) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.get_schedule_authorization_projection(
    p_name text
) RETURNS taskq.schedule_auth_projection
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_result taskq.schedule_auth_projection;
BEGIN
    SELECT s.name, s.target->>'queue'
    INTO v_result
    FROM taskq.schedules AS s
    WHERE s.name = p_name
      AND s.target->>'kind' = 'job';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    RETURN v_result;
END $$;
ALTER FUNCTION taskq.get_schedule_authorization_projection(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_schedule_authorization_projection(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_schedule_authorization_projection(text) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.put_schedule(
    p_name text,
    p_definition jsonb,
    p_actor text,
    p_expected_version bigint DEFAULT NULL
) RETURNS taskq.schedule_write_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_existing taskq.schedules%ROWTYPE;
    v_after taskq.schedules%ROWTYPE;
    v_profile taskq.schedule_profile;
    v_target jsonb;
    v_recurrence jsonb;
    v_normalized jsonb;
    v_kind text;
    v_rec_kind text;
    v_queue text;
    v_expression text;
    v_timezone text;
    v_fields text[];
    v_field text;
    v_item text;
    v_base text;
    v_range text[];
    v_step text;
    v_index integer;
    v_min integer;
    v_max integer;
    v_start integer;
    v_end integer;
    v_paused boolean;
    v_state text;
    v_real_change boolean;
BEGIN
    IF p_name IS NULL
       OR octet_length(p_name) NOT BETWEEN 1 AND 120
       OR p_name !~ '^[a-z0-9][a-z0-9_.-]*$'
       OR p_name = 'taskq-janitor-daily' THEN
        RAISE EXCEPTION 'invalid schedule name' USING ERRCODE = 'TQ422';
    END IF;
    IF p_actor IS NULL OR octet_length(p_actor) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'schedule actor is required' USING ERRCODE = 'TQ422';
    END IF;
    IF p_expected_version IS NOT NULL AND p_expected_version <= 0 THEN
        RAISE EXCEPTION 'expected version must be positive' USING ERRCODE = 'TQ422';
    END IF;
    IF p_definition IS NULL
       OR jsonb_typeof(p_definition) <> 'object'
       OR NOT p_definition ?& ARRAY[
           'target','recurrence','catchup_policy','max_catchup'
       ]
       OR p_definition - ARRAY[
           'target','recurrence','catchup_policy','max_catchup','paused'
       ] <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid schedule definition' USING ERRCODE = 'TQ422';
    END IF;

    v_target := p_definition->'target';
    v_recurrence := p_definition->'recurrence';
    IF jsonb_typeof(v_target) <> 'object'
       OR NOT v_target ?& ARRAY['kind','queue','job_type']
       OR v_target - ARRAY[
           'kind','queue','job_type','payload','headers','priority','max_attempts',
           'lease_seconds','backoff_mode','backoff_base','backoff_cap',
           'concurrency_key','affinity_key'
       ] <> '{}'::jsonb THEN
        RAISE EXCEPTION 'invalid schedule target' USING ERRCODE = 'TQ422';
    END IF;
    v_kind := v_target->>'kind';
    v_queue := v_target->>'queue';
    IF v_kind IS DISTINCT FROM 'job'
       OR v_queue IS NULL
       OR octet_length(v_queue) NOT BETWEEN 1 AND 57
       OR v_queue !~ '^[a-z0-9_]+$'
       OR COALESCE(octet_length(v_target->>'job_type'), 0) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'invalid schedule job target' USING ERRCODE = 'TQ422';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM taskq.queues WHERE name = v_queue) THEN
        RAISE EXCEPTION 'taskq: no such queue' USING ERRCODE = 'TQ001';
    END IF;
    IF v_target ? 'payload'
       AND (jsonb_typeof(v_target->'payload') <> 'object'
            OR octet_length((v_target->'payload')::text) > 65536) THEN
        RAISE EXCEPTION 'schedule payload must be an object at most 64KB'
            USING ERRCODE = 'TQ422';
    END IF;
    IF v_target ? 'headers'
       AND (jsonb_typeof(v_target->'headers') <> 'object'
            OR octet_length((v_target->'headers')::text) > 8192) THEN
        RAISE EXCEPTION 'schedule headers must be an object at most 8KB'
            USING ERRCODE = 'TQ422';
    END IF;

    BEGIN
        IF v_target ? 'priority'
           AND jsonb_typeof(v_target->'priority') <> 'null'
           AND (
               jsonb_typeof(v_target->'priority') <> 'number'
               OR (v_target->>'priority')::integer NOT BETWEEN 0 AND 1000
           ) THEN
            RAISE EXCEPTION 'invalid schedule priority' USING ERRCODE = 'TQ422';
        END IF;
        IF v_target ? 'max_attempts'
           AND jsonb_typeof(v_target->'max_attempts') <> 'null'
           AND (
               jsonb_typeof(v_target->'max_attempts') <> 'number'
               OR (v_target->>'max_attempts')::integer NOT BETWEEN 1 AND 100
           ) THEN
            RAISE EXCEPTION 'invalid schedule max_attempts' USING ERRCODE = 'TQ422';
        END IF;
        IF v_target ? 'lease_seconds'
           AND jsonb_typeof(v_target->'lease_seconds') <> 'null'
           AND (
               jsonb_typeof(v_target->'lease_seconds') <> 'number'
               OR (v_target->>'lease_seconds')::integer NOT BETWEEN 15 AND 86400
           ) THEN
            RAISE EXCEPTION 'invalid schedule lease_seconds' USING ERRCODE = 'TQ422';
        END IF;
        IF v_target ? 'backoff_base'
           AND jsonb_typeof(v_target->'backoff_base') <> 'null'
           AND (
               jsonb_typeof(v_target->'backoff_base') <> 'number'
               OR (v_target->>'backoff_base')::integer NOT BETWEEN 0 AND 86400
           ) THEN
            RAISE EXCEPTION 'invalid schedule backoff_base' USING ERRCODE = 'TQ422';
        END IF;
        IF v_target ? 'backoff_cap'
           AND jsonb_typeof(v_target->'backoff_cap') <> 'null'
           AND (
               jsonb_typeof(v_target->'backoff_cap') <> 'number'
               OR (v_target->>'backoff_cap')::integer NOT BETWEEN 0 AND 604800
           ) THEN
            RAISE EXCEPTION 'invalid schedule backoff_cap' USING ERRCODE = 'TQ422';
        END IF;
    EXCEPTION
        WHEN invalid_text_representation OR numeric_value_out_of_range THEN
            RAISE EXCEPTION 'invalid numeric schedule field' USING ERRCODE = 'TQ422';
    END;
    IF v_target ? 'backoff_mode'
       AND jsonb_typeof(v_target->'backoff_mode') <> 'null'
       AND v_target->>'backoff_mode' NOT IN ('fixed','exponential') THEN
        RAISE EXCEPTION 'invalid schedule backoff_mode' USING ERRCODE = 'TQ422';
    END IF;
    IF v_target ? 'concurrency_key'
       AND jsonb_typeof(v_target->'concurrency_key') <> 'null'
       AND COALESCE(octet_length(v_target->>'concurrency_key'), 0) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'invalid schedule concurrency_key' USING ERRCODE = 'TQ422';
    END IF;
    IF v_target ? 'affinity_key'
       AND jsonb_typeof(v_target->'affinity_key') <> 'null'
       AND COALESCE(octet_length(v_target->>'affinity_key'), 0) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'invalid schedule affinity_key' USING ERRCODE = 'TQ422';
    END IF;

    v_target := jsonb_build_object(
        'kind', 'job',
        'queue', v_queue,
        'job_type', v_target->>'job_type',
        'payload', COALESCE(v_target->'payload', '{}'::jsonb),
        'headers', COALESCE(v_target->'headers', '{}'::jsonb),
        'priority', v_target->'priority',
        'max_attempts', v_target->'max_attempts',
        'lease_seconds', v_target->'lease_seconds',
        'backoff_mode', v_target->'backoff_mode',
        'backoff_base', v_target->'backoff_base',
        'backoff_cap', v_target->'backoff_cap',
        'concurrency_key', v_target->'concurrency_key',
        'affinity_key', v_target->'affinity_key'
    );

    IF jsonb_typeof(v_recurrence) <> 'object' OR NOT v_recurrence ? 'kind' THEN
        RAISE EXCEPTION 'invalid schedule recurrence' USING ERRCODE = 'TQ422';
    END IF;
    v_rec_kind := v_recurrence->>'kind';
    IF v_rec_kind = 'interval' THEN
        IF NOT v_recurrence ? 'interval_seconds'
           OR v_recurrence - ARRAY['kind','interval_seconds'] <> '{}'::jsonb
           OR jsonb_typeof(v_recurrence->'interval_seconds') <> 'number' THEN
            RAISE EXCEPTION 'invalid interval recurrence' USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            IF (v_recurrence->>'interval_seconds')::integer
               NOT BETWEEN 60 AND 31536000 THEN
                RAISE EXCEPTION 'interval_seconds must be 60..31536000'
                    USING ERRCODE = 'TQ422';
            END IF;
        EXCEPTION
            WHEN invalid_text_representation OR numeric_value_out_of_range THEN
                RAISE EXCEPTION 'invalid interval_seconds' USING ERRCODE = 'TQ422';
        END;
        v_recurrence := jsonb_build_object(
            'kind', 'interval',
            'interval_seconds', (v_recurrence->>'interval_seconds')::integer
        );
    ELSIF v_rec_kind = 'cron' THEN
        IF NOT v_recurrence ?& ARRAY['expression','timezone']
           OR v_recurrence - ARRAY['kind','expression','timezone'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid cron recurrence' USING ERRCODE = 'TQ422';
        END IF;
        v_expression := v_recurrence->>'expression';
        v_timezone := v_recurrence->>'timezone';
        IF COALESCE(octet_length(v_expression), 0) NOT BETWEEN 1 AND 255
           OR COALESCE(octet_length(v_timezone), 0) NOT BETWEEN 1 AND 255
           OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = v_timezone
           ) THEN
            RAISE EXCEPTION 'invalid cron expression or timezone' USING ERRCODE = 'TQ422';
        END IF;
        v_fields := regexp_split_to_array(btrim(v_expression), '[[:space:]]+');
        IF cardinality(v_fields) <> 5 THEN
            RAISE EXCEPTION 'cron requires exactly five fields' USING ERRCODE = 'TQ422';
        END IF;
        FOR v_index IN 1..5 LOOP
            v_field := v_fields[v_index];
            IF v_field = '' THEN
                RAISE EXCEPTION 'empty cron field' USING ERRCODE = 'TQ422';
            END IF;
            IF v_index = 1 THEN v_min := 0; v_max := 59;
            ELSIF v_index = 2 THEN v_min := 0; v_max := 23;
            ELSIF v_index = 3 THEN v_min := 1; v_max := 31;
            ELSIF v_index = 4 THEN v_min := 1; v_max := 12;
            ELSE v_min := 0; v_max := 7;
            END IF;
            FOREACH v_item IN ARRAY string_to_array(v_field, ',') LOOP
                IF v_item !~ '^(\*|[0-9]+(-[0-9]+)?)(/[0-9]+)?$' THEN
                    RAISE EXCEPTION 'unsupported cron token' USING ERRCODE = 'TQ422';
                END IF;
                IF position('/' IN v_item) > 0 THEN
                    v_step := split_part(v_item, '/', 2);
                    IF v_step::integer <= 0 THEN
                        RAISE EXCEPTION 'cron step must be positive' USING ERRCODE = 'TQ422';
                    END IF;
                    v_base := split_part(v_item, '/', 1);
                ELSE
                    v_base := v_item;
                END IF;
                IF v_base <> '*' THEN
                    v_range := string_to_array(v_base, '-');
                    v_start := v_range[1]::integer;
                    v_end := CASE
                        WHEN cardinality(v_range) = 2 THEN v_range[2]::integer
                        ELSE v_start
                    END;
                    IF cardinality(v_range) > 2
                       OR v_start NOT BETWEEN v_min AND v_max
                       OR v_end NOT BETWEEN v_min AND v_max
                       OR v_start > v_end THEN
                        RAISE EXCEPTION 'cron value out of range' USING ERRCODE = 'TQ422';
                    END IF;
                END IF;
            END LOOP;
        END LOOP;
        v_recurrence := jsonb_build_object(
            'kind', 'cron', 'expression', array_to_string(v_fields, ' '),
            'timezone', v_timezone
        );
    ELSE
        RAISE EXCEPTION 'recurrence kind must be interval or cron'
            USING ERRCODE = 'TQ422';
    END IF;

    BEGIN
        IF p_definition->>'catchup_policy' NOT IN ('skip','fire_once','fire_all')
           OR jsonb_typeof(p_definition->'max_catchup') <> 'number'
           OR (p_definition->>'max_catchup')::integer NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION 'invalid catchup settings' USING ERRCODE = 'TQ422';
        END IF;
    EXCEPTION
        WHEN invalid_text_representation OR numeric_value_out_of_range THEN
            RAISE EXCEPTION 'invalid catchup settings' USING ERRCODE = 'TQ422';
    END;
    IF p_definition ? 'paused'
       AND jsonb_typeof(p_definition->'paused') <> 'boolean' THEN
        RAISE EXCEPTION 'paused must be boolean' USING ERRCODE = 'TQ422';
    END IF;
    v_paused := COALESCE((p_definition->>'paused')::boolean, false);
    v_state := CASE WHEN v_paused THEN 'paused' ELSE 'active' END;
    v_normalized := jsonb_build_object(
        'target', v_target,
        'recurrence', v_recurrence,
        'catchup_policy', p_definition->>'catchup_policy',
        'max_catchup', (p_definition->>'max_catchup')::integer,
        'paused', v_paused
    );

    SELECT * INTO v_existing
    FROM taskq.schedules
    WHERE name = p_name
    FOR UPDATE;
    IF NOT FOUND THEN
        IF p_expected_version IS NOT NULL THEN
            RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
        END IF;
        INSERT INTO taskq.schedules (
            name, target, recurrence, catchup_policy, max_catchup, state,
            initialized, next_fire_at, version, created_by, updated_by
        ) VALUES (
            p_name, v_target, v_recurrence, p_definition->>'catchup_policy',
            (p_definition->>'max_catchup')::integer, v_state,
            false, now(), 1, p_actor, p_actor
        )
        RETURNING * INTO v_after;
        v_profile := (
            v_after.id, v_after.name, v_after.target, v_after.recurrence,
            v_after.catchup_policy, v_after.max_catchup, v_after.state,
            v_after.next_fire_at, v_after.last_fire_at, v_after.version
        )::taskq.schedule_profile;
        RETURN ('created', v_profile)::taskq.schedule_write_result;
    END IF;
    IF v_existing.target->>'kind' <> 'job' THEN
        RAISE EXCEPTION 'reserved maintenance schedule' USING ERRCODE = 'TQ422';
    END IF;
    IF p_expected_version IS NULL THEN
        IF jsonb_build_object(
            'target', v_existing.target,
            'recurrence', v_existing.recurrence,
            'catchup_policy', v_existing.catchup_policy,
            'max_catchup', v_existing.max_catchup,
            'paused', v_existing.state = 'paused'
        ) IS DISTINCT FROM v_normalized THEN
            RAISE EXCEPTION 'schedule identity mismatch'
                USING ERRCODE = 'TQ409',
                      DETAIL = jsonb_build_object(
                          'reason','schedule_mismatch',
                          'current_version',v_existing.version
                      )::text;
        END IF;
        v_profile := (
            v_existing.id, v_existing.name, v_existing.target, v_existing.recurrence,
            v_existing.catchup_policy, v_existing.max_catchup, v_existing.state,
            v_existing.next_fire_at, v_existing.last_fire_at, v_existing.version
        )::taskq.schedule_profile;
        RETURN ('unchanged', v_profile)::taskq.schedule_write_result;
    END IF;
    IF p_expected_version IS DISTINCT FROM v_existing.version THEN
        RAISE EXCEPTION 'schedule version conflict'
            USING ERRCODE = 'TQ409',
                  DETAIL = jsonb_build_object(
                      'reason','schedule_version_conflict',
                      'current_version',v_existing.version
                  )::text;
    END IF;
    IF v_existing.state = 'retired' THEN
        RAISE EXCEPTION 'schedule is retired'
            USING ERRCODE = 'TQ409',
                  DETAIL = jsonb_build_object(
                      'reason','schedule_retired',
                      'current_version',v_existing.version
                  )::text;
    END IF;
    v_real_change := v_existing.target IS DISTINCT FROM v_target
        OR v_existing.recurrence IS DISTINCT FROM v_recurrence
        OR v_existing.catchup_policy IS DISTINCT FROM p_definition->>'catchup_policy'
        OR v_existing.max_catchup IS DISTINCT FROM (p_definition->>'max_catchup')::integer
        OR v_existing.state IS DISTINCT FROM v_state;
    IF NOT v_real_change THEN
        v_profile := (
            v_existing.id, v_existing.name, v_existing.target, v_existing.recurrence,
            v_existing.catchup_policy, v_existing.max_catchup, v_existing.state,
            v_existing.next_fire_at, v_existing.last_fire_at, v_existing.version
        )::taskq.schedule_profile;
        RETURN ('unchanged', v_profile)::taskq.schedule_write_result;
    END IF;

    UPDATE taskq.schedules
    SET target = v_target,
        recurrence = v_recurrence,
        catchup_policy = p_definition->>'catchup_policy',
        max_catchup = (p_definition->>'max_catchup')::integer,
        state = v_state,
        initialized = CASE
            WHEN v_existing.state = 'active' AND v_state = 'paused'
                 AND v_existing.target IS NOT DISTINCT FROM v_target
                 AND v_existing.recurrence IS NOT DISTINCT FROM v_recurrence
                 AND v_existing.catchup_policy IS NOT DISTINCT FROM
                     p_definition->>'catchup_policy'
                 AND v_existing.max_catchup IS NOT DISTINCT FROM
                     (p_definition->>'max_catchup')::integer
            THEN v_existing.initialized
            ELSE false
        END,
        next_fire_at = CASE
            WHEN v_existing.state = 'active' AND v_state = 'paused'
                 AND v_existing.target IS NOT DISTINCT FROM v_target
                 AND v_existing.recurrence IS NOT DISTINCT FROM v_recurrence
                 AND v_existing.catchup_policy IS NOT DISTINCT FROM
                     p_definition->>'catchup_policy'
                 AND v_existing.max_catchup IS NOT DISTINCT FROM
                     (p_definition->>'max_catchup')::integer
            THEN v_existing.next_fire_at
            ELSE now()
        END,
        version = version + 1,
        claim_token = NULL,
        claim_as_of = NULL,
        claimed_by = NULL,
        claim_expires_at = NULL,
        retry_not_before = NULL,
        last_error = NULL,
        last_action_token = NULL,
        last_action_hash = NULL,
        last_action_result = NULL,
        updated_by = p_actor,
        updated_at = now()
    WHERE id = v_existing.id
    RETURNING * INTO v_after;
    v_profile := (
        v_after.id, v_after.name, v_after.target, v_after.recurrence,
        v_after.catchup_policy, v_after.max_catchup, v_after.state,
        v_after.next_fire_at, v_after.last_fire_at, v_after.version
    )::taskq.schedule_profile;
    RETURN ('updated', v_profile)::taskq.schedule_write_result;
END $$;
ALTER FUNCTION taskq.put_schedule(text,jsonb,text,bigint) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.put_schedule(text,jsonb,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.put_schedule(text,jsonb,text,bigint) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.retire_schedule(
    p_name text,
    p_expected_version bigint,
    p_actor text
) RETURNS taskq.schedule_write_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_profile taskq.schedule_profile;
    v_outcome text;
BEGIN
    IF p_expected_version IS NULL OR p_expected_version <= 0
       OR p_actor IS NULL OR octet_length(p_actor) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'expected version and actor are required'
            USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_schedule
    FROM taskq.schedules
    WHERE name = p_name
    FOR UPDATE;
    IF NOT FOUND OR v_schedule.target->>'kind' <> 'job' THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF p_expected_version IS DISTINCT FROM v_schedule.version THEN
        RAISE EXCEPTION 'schedule version conflict'
            USING ERRCODE = 'TQ409',
                  DETAIL = jsonb_build_object(
                      'reason','schedule_version_conflict',
                      'current_version',v_schedule.version
                  )::text;
    END IF;
    IF v_schedule.state = 'retired' THEN
        v_outcome := 'already_retired';
    ELSE
        UPDATE taskq.schedules
        SET state = 'retired',
            retired_at = now(),
            version = version + 1,
            claim_token = NULL,
            claim_as_of = NULL,
            claimed_by = NULL,
            claim_expires_at = NULL,
            retry_not_before = NULL,
            last_action_token = NULL,
            last_action_hash = NULL,
            last_action_result = NULL,
            updated_by = p_actor,
            updated_at = now()
        WHERE id = v_schedule.id
        RETURNING * INTO v_schedule;
        v_outcome := 'retired';
    END IF;
    v_profile := (
        v_schedule.id, v_schedule.name, v_schedule.target, v_schedule.recurrence,
        v_schedule.catchup_policy, v_schedule.max_catchup, v_schedule.state,
        v_schedule.next_fire_at, v_schedule.last_fire_at, v_schedule.version
    )::taskq.schedule_profile;
    RETURN (v_outcome, v_profile)::taskq.schedule_write_result;
END $$;
ALTER FUNCTION taskq.retire_schedule(text,bigint,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.retire_schedule(text,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.retire_schedule(text,bigint,text) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.claim_schedules(
    p_worker_id text,
    p_limit integer DEFAULT 10,
    p_lease_seconds integer DEFAULT 60
) RETURNS taskq.schedule_claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_claim taskq.schedule_claim;
    v_claims taskq.schedule_claim[] := '{}'::taskq.schedule_claim[];
    v_now timestamptz := now();
    v_token uuid;
BEGIN
    IF p_worker_id IS NULL OR octet_length(p_worker_id) NOT BETWEEN 1 AND 200
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 5 AND 300 THEN
        RAISE EXCEPTION 'invalid schedule claim arguments' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_schedule IN
        SELECT * FROM taskq.schedules
        WHERE state = 'active'
          AND next_fire_at <= v_now
          AND (retry_not_before IS NULL OR retry_not_before <= v_now)
          AND (claim_token IS NULL OR claim_expires_at <= v_now)
        ORDER BY next_fire_at, id
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    LOOP
        v_token := taskq.uuid7();
        UPDATE taskq.schedules
        SET claim_token = v_token,
            claim_as_of = v_now,
            claimed_by = p_worker_id,
            claim_expires_at = v_now + make_interval(secs => p_lease_seconds),
            updated_at = v_now
        WHERE id = v_schedule.id;
        v_claim := (
            v_schedule.id, v_schedule.name, v_schedule.version, v_now,
            v_schedule.target, v_schedule.recurrence, v_schedule.catchup_policy,
            v_schedule.max_catchup, v_schedule.initialized, v_schedule.next_fire_at,
            v_token, p_lease_seconds
        )::taskq.schedule_claim;
        v_claims := array_append(v_claims, v_claim);
    END LOOP;
    RETURN (
        CASE WHEN cardinality(v_claims) = 0 THEN 'empty' ELSE 'claimed' END,
        v_claims
    )::taskq.schedule_claim_batch;
END $$;
ALTER FUNCTION taskq.claim_schedules(text,integer,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_schedules(text,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_schedules(text,integer,integer) TO taskq_housekeeper;

CREATE OR REPLACE FUNCTION taskq.fire_schedule(
    p_schedule_id uuid,
    p_token uuid,
    p_definition_version bigint,
    p_occurrences timestamptz[],
    p_next_fire_at timestamptz
) RETURNS taskq.schedule_action_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_hash text;
    v_stored jsonb;
    v_outcome text;
    v_due timestamptz;
    v_previous timestamptz;
    v_job_id uuid;
    v_created boolean;
    v_jobs integer := 0;
    v_count integer;
    v_key text;
    v_result taskq.schedule_action_result;
BEGIN
    IF p_schedule_id IS NULL OR p_token IS NULL
       OR p_token = '00000000-0000-0000-0000-000000000000'::uuid
       OR p_definition_version IS NULL OR p_definition_version <= 0
       OR p_occurrences IS NULL OR p_next_fire_at IS NULL
       OR EXISTS (SELECT 1 FROM unnest(p_occurrences) AS x(value) WHERE value IS NULL) THEN
        RAISE EXCEPTION 'invalid schedule fire arguments' USING ERRCODE = 'TQ422';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'kind','fire',
        'version',p_definition_version,
        'occurrences',p_occurrences,
        'next_fire_at',p_next_fire_at
    )::text, 'UTF8')), 'hex');

    SELECT * INTO v_schedule
    FROM taskq.schedules
    WHERE id = p_schedule_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF v_schedule.last_action_token = p_token THEN
        IF v_schedule.last_action_hash IS DISTINCT FROM v_hash THEN
            RETURN (
                'stale', false, v_schedule.id, 0, v_schedule.next_fire_at,
                v_schedule.state, v_schedule.version
            )::taskq.schedule_action_result;
        END IF;
        v_stored := v_schedule.last_action_result;
        RETURN (
            v_stored->>'outcome', true, v_schedule.id,
            (v_stored->>'jobs_enqueued')::integer,
            (v_stored->>'next_fire_at')::timestamptz,
            v_stored->>'state', (v_stored->>'version')::bigint
        )::taskq.schedule_action_result;
    END IF;
    IF v_schedule.state <> 'active'
       OR v_schedule.version IS DISTINCT FROM p_definition_version
       OR v_schedule.claim_token IS DISTINCT FROM p_token
       OR v_schedule.claim_expires_at < now() THEN
        RETURN (
            'stale', false, v_schedule.id, 0, v_schedule.next_fire_at,
            v_schedule.state, v_schedule.version
        )::taskq.schedule_action_result;
    END IF;

    v_count := cardinality(p_occurrences);
    IF v_count > v_schedule.max_catchup OR p_next_fire_at <= v_schedule.next_fire_at THEN
        RAISE EXCEPTION 'invalid schedule fire bounds' USING ERRCODE = 'TQ422';
    END IF;
    v_previous := NULL;
    FOREACH v_due IN ARRAY p_occurrences LOOP
        IF v_due > v_schedule.claim_as_of
           OR (v_previous IS NOT NULL AND v_due <= v_previous) THEN
            RAISE EXCEPTION 'schedule occurrences must be ordered and due'
                USING ERRCODE = 'TQ422';
        END IF;
        v_previous := v_due;
    END LOOP;

    IF NOT v_schedule.initialized THEN
        IF v_count <> 0 OR p_next_fire_at <= v_schedule.claim_as_of THEN
            RAISE EXCEPTION 'initial schedule compilation cannot fire'
                USING ERRCODE = 'TQ422';
        END IF;
        v_outcome := 'initialized';
    ELSIF v_schedule.catchup_policy = 'skip' THEN
        IF v_count <> 0 OR p_next_fire_at <= v_schedule.claim_as_of THEN
            RAISE EXCEPTION 'skip must advance beyond database as_of'
                USING ERRCODE = 'TQ422';
        END IF;
        v_outcome := 'skipped';
    ELSIF v_schedule.catchup_policy = 'fire_once' THEN
        IF v_count <> 1
           OR p_occurrences[1] < v_schedule.next_fire_at
           OR p_next_fire_at <= v_schedule.claim_as_of THEN
            RAISE EXCEPTION 'fire_once requires one latest due occurrence'
                USING ERRCODE = 'TQ422';
        END IF;
        v_outcome := 'fired';
    ELSE
        IF v_count NOT BETWEEN 1 AND v_schedule.max_catchup
           OR p_occurrences[1] IS DISTINCT FROM v_schedule.next_fire_at
           OR p_next_fire_at <= p_occurrences[v_count]
           OR (v_count < v_schedule.max_catchup
               AND p_next_fire_at <= v_schedule.claim_as_of) THEN
            RAISE EXCEPTION 'fire_all requires a bounded oldest-first prefix'
                USING ERRCODE = 'TQ422';
        END IF;
        v_outcome := 'fired';
    END IF;

    FOREACH v_due IN ARRAY p_occurrences LOOP
        INSERT INTO taskq.schedule_occurrences(schedule_id, due_at)
        VALUES (v_schedule.id, v_due)
        ON CONFLICT DO NOTHING;
        IF FOUND THEN
            IF v_schedule.target->>'kind' = 'maintenance' THEN
                IF v_schedule.name <> 'taskq-janitor-daily'
                   OR v_schedule.target->>'maintenance' <> 'janitor' THEN
                    RAISE EXCEPTION 'unknown maintenance schedule target'
                        USING ERRCODE = 'TQ500';
                END IF;
                PERFORM taskq.janitor();
            ELSE
                v_key := 'schedule:' || v_schedule.id::text || ':'
                    || floor(extract(epoch FROM v_due) * 1000000)::numeric::text;
                SELECT e.job_id, e.created
                INTO v_job_id, v_created
                FROM taskq.enqueue(
                    v_schedule.target->>'queue',
                    v_schedule.target->>'job_type',
                    v_schedule.target->'payload',
                    (v_schedule.target->>'priority')::smallint,
                    v_due,
                    v_key,
                    v_schedule.target->>'concurrency_key',
                    v_schedule.target->>'affinity_key',
                    (v_schedule.target->>'max_attempts')::smallint,
                    (v_schedule.target->>'lease_seconds')::integer,
                    v_schedule.target->>'backoff_mode',
                    (v_schedule.target->>'backoff_base')::integer,
                    (v_schedule.target->>'backoff_cap')::integer,
                    NULL, NULL, NULL, NULL,
                    v_schedule.target->'headers'
                ) AS e;
                UPDATE taskq.schedule_occurrences
                SET job_id = v_job_id
                WHERE schedule_id = v_schedule.id AND due_at = v_due;
                v_jobs := v_jobs + 1;
            END IF;
        END IF;
    END LOOP;

    v_stored := jsonb_build_object(
        'outcome', v_outcome,
        'jobs_enqueued', v_jobs,
        'next_fire_at', p_next_fire_at,
        'state', v_schedule.state,
        'version', v_schedule.version
    );
    UPDATE taskq.schedules
    SET initialized = true,
        next_fire_at = p_next_fire_at,
        last_fire_at = CASE
            WHEN v_count > 0 THEN p_occurrences[v_count]
            ELSE last_fire_at
        END,
        claim_token = NULL,
        claim_as_of = NULL,
        claimed_by = NULL,
        claim_expires_at = NULL,
        retry_not_before = NULL,
        last_error = NULL,
        last_action_token = p_token,
        last_action_hash = v_hash,
        last_action_result = v_stored,
        updated_at = now()
    WHERE id = v_schedule.id;
    v_result := (
        v_outcome, false, v_schedule.id, v_jobs, p_next_fire_at,
        v_schedule.state, v_schedule.version
    )::taskq.schedule_action_result;
    RETURN v_result;
END $$;
ALTER FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
    TO taskq_housekeeper;

CREATE OR REPLACE FUNCTION taskq.schedule_error(
    p_schedule_id uuid,
    p_token uuid,
    p_definition_version bigint,
    p_error text,
    p_retry_seconds integer DEFAULT 30
) RETURNS taskq.schedule_action_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_hash text;
    v_stored jsonb;
BEGIN
    IF p_schedule_id IS NULL OR p_token IS NULL
       OR p_token = '00000000-0000-0000-0000-000000000000'::uuid
       OR p_definition_version IS NULL OR p_definition_version <= 0
       OR p_error IS NULL
       OR p_retry_seconds IS NULL OR p_retry_seconds NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'invalid schedule error arguments' USING ERRCODE = 'TQ422';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'kind','error',
        'version',p_definition_version,
        'error',p_error,
        'retry_seconds',p_retry_seconds
    )::text, 'UTF8')), 'hex');
    SELECT * INTO v_schedule
    FROM taskq.schedules
    WHERE id = p_schedule_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF v_schedule.last_action_token = p_token THEN
        IF v_schedule.last_action_hash IS DISTINCT FROM v_hash THEN
            RETURN (
                'stale', false, v_schedule.id, 0, v_schedule.next_fire_at,
                v_schedule.state, v_schedule.version
            )::taskq.schedule_action_result;
        END IF;
        v_stored := v_schedule.last_action_result;
        RETURN (
            v_stored->>'outcome', true, v_schedule.id,
            (v_stored->>'jobs_enqueued')::integer,
            (v_stored->>'next_fire_at')::timestamptz,
            v_stored->>'state', (v_stored->>'version')::bigint
        )::taskq.schedule_action_result;
    END IF;
    IF v_schedule.state <> 'active'
       OR v_schedule.version IS DISTINCT FROM p_definition_version
       OR v_schedule.claim_token IS DISTINCT FROM p_token
       OR v_schedule.claim_expires_at < now() THEN
        RETURN (
            'stale', false, v_schedule.id, 0, v_schedule.next_fire_at,
            v_schedule.state, v_schedule.version
        )::taskq.schedule_action_result;
    END IF;
    v_stored := jsonb_build_object(
        'outcome', 'error_recorded',
        'jobs_enqueued', 0,
        'next_fire_at', v_schedule.next_fire_at,
        'state', v_schedule.state,
        'version', v_schedule.version
    );
    UPDATE taskq.schedules
    SET claim_token = NULL,
        claim_as_of = NULL,
        claimed_by = NULL,
        claim_expires_at = NULL,
        retry_not_before = now() + make_interval(secs => p_retry_seconds),
        last_error = taskq.truncate_utf8(p_error, 2048),
        last_action_token = p_token,
        last_action_hash = v_hash,
        last_action_result = v_stored,
        updated_at = now()
    WHERE id = v_schedule.id;
    RETURN (
        'error_recorded', false, v_schedule.id, 0, v_schedule.next_fire_at,
        v_schedule.state, v_schedule.version
    )::taskq.schedule_action_result;
END $$;
ALTER FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer)
    TO taskq_housekeeper;

INSERT INTO taskq.schedules (
    id, name, target, recurrence, catchup_policy, max_catchup, state,
    initialized, next_fire_at, version, created_by, updated_by
) VALUES (
    taskq.uuid7(),
    'taskq-janitor-daily',
    '{"kind":"maintenance","maintenance":"janitor"}'::jsonb,
    '{"kind":"cron","expression":"0 3 * * *","timezone":"UTC"}'::jsonb,
    'fire_once',
    1,
    'active',
    false,
    now(),
    1,
    'migration:0010',
    'migration:0010'
);

CREATE OR REPLACE FUNCTION taskq.tick(
    p_reap_limit integer DEFAULT 200
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_out jsonb := '{}';
    v_n integer;
BEGIN
    IF p_reap_limit IS NULL THEN
        RAISE EXCEPTION 'reap limit must not be null' USING ERRCODE = 'TQ422';
    END IF;
    IF NOT pg_try_advisory_xact_lock(hashtextextended('taskq:tick', 0)) THEN
        RETURN jsonb_build_object('skipped', true);
    END IF;
    INSERT INTO taskq.control_state (key, last_started_at)
    VALUES ('tick', now())
    ON CONFLICT (key) DO UPDATE SET last_started_at = now();

    BEGIN
        v_n := taskq.reap_expired(p_reap_limit);
        v_out := v_out || jsonb_build_object('reaped', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'reap: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.finalize_cancel_stragglers(50);
        v_out := v_out || jsonb_build_object('cancel_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'cancel: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.advance_workflow_cancellations(100);
        v_out := v_out || jsonb_build_object('workflow_cancelled', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'workflow_cancel: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.finalize_dep_stragglers(100);
        v_out := v_out || jsonb_build_object('dependency_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'dependencies: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        v_n := taskq.finalize_workflows(100);
        v_out := v_out || jsonb_build_object('workflows_finalized', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'workflows: ' || SQLERRM WHERE key = 'tick';
    END;
    BEGIN
        PERFORM taskq.refresh_stats_snapshot();
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'stats: ' || SQLERRM WHERE key = 'tick';
    END;
    UPDATE taskq.control_state SET last_finished_at = now() WHERE key = 'tick';
    RETURN v_out;
END $$;
ALTER FUNCTION taskq.tick(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.tick(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.tick(integer) TO taskq_housekeeper, taskq_operator;

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('contract_version','"0.2.2"'::jsonb,now()),
    ('capabilities','{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready","schedules"]}'::jsonb,now())
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now();
