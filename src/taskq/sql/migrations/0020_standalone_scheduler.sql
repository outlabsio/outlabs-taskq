-- outlabs-taskq — migration 0020: standalone scheduler activation
-- SQL contract 0.3.0 / ADR-037. This migration intentionally refuses an
-- unbound target: apply 0019, inspect/bind the identity, then resume migrate.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
    v_environment text;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta
    WHERE key = 'contract_version' FOR UPDATE;
    SELECT value INTO v_capabilities FROM taskq.meta
    WHERE key = 'capabilities' FOR UPDATE;
    SELECT environment INTO v_environment FROM taskq.target_identity
    WHERE singleton FOR SHARE;

    IF v_contract IS DISTINCT FROM '"0.2.7"'::jsonb THEN
        RAISE EXCEPTION '0020 requires SQL contract 0.2.7, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0020 requires the exact 0019 capability set, found %',
            v_capabilities USING ERRCODE = 'TQ500';
    END IF;
    IF v_environment IS NULL OR v_environment = 'unbound' THEN
        RAISE EXCEPTION '0020 refuses an unbound taskq target; run taskq target bind first'
            USING ERRCODE = 'TQ422', DETAIL = '{"reason":"target_unbound"}';
    END IF;
    IF to_regclass('taskq.schedule_decisions') IS NOT NULL THEN
        RAISE EXCEPTION '0020 scheduler activation already exists'
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

ALTER TABLE taskq.schedules
    ADD COLUMN namespace text NOT NULL DEFAULT 'legacy',
    ADD COLUMN source text NOT NULL DEFAULT 'api',
    ADD COLUMN manifest_key text NOT NULL DEFAULT 'legacy',
    ADD COLUMN display_name text NOT NULL DEFAULT 'legacy',
    ADD COLUMN definition_hash text NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN overlap_policy text NOT NULL DEFAULT 'forbid',
    ADD COLUMN max_lateness_seconds integer,
    ADD COLUMN consecutive_definition_errors integer NOT NULL DEFAULT 0,
    ADD COLUMN paused_reason text,
    ADD COLUMN paused_by text,
    ADD COLUMN paused_at timestamptz;

UPDATE taskq.schedules
SET namespace = CASE WHEN name = 'taskq-janitor-daily' THEN 'taskq' ELSE 'legacy' END,
    source = CASE WHEN name = 'taskq-janitor-daily' THEN 'package' ELSE 'api' END,
    manifest_key = name,
    display_name = name,
    definition_hash = encode(
        sha256(
            convert_to(
                jsonb_build_object(
                    'target', target,
                    'recurrence', recurrence,
                    'catchup_policy', catchup_policy,
                    'max_catchup', max_catchup,
                    'paused', state = 'paused',
                    'overlap', 'forbid',
                    'max_lateness_seconds', NULL
                )::text,
                'UTF8'
            )
        ),
        'hex'
    );

ALTER TABLE taskq.schedules
    ADD CONSTRAINT schedules_namespace_ck CHECK (
        octet_length(namespace) BETWEEN 1 AND 63
        AND namespace ~ '^[a-z0-9][a-z0-9_-]{0,62}$'
    ),
    ADD CONSTRAINT schedules_source_ck CHECK (
        octet_length(source) BETWEEN 1 AND 63
        AND source ~ '^[a-z0-9][a-z0-9_-]{0,62}$'
    ),
    ADD CONSTRAINT schedules_manifest_key_ck CHECK (
        octet_length(manifest_key) BETWEEN 1 AND 120
        AND manifest_key ~ '^[a-z0-9][a-z0-9_.-]*$'
    ),
    ADD CONSTRAINT schedules_display_name_ck CHECK (
        octet_length(display_name) BETWEEN 1 AND 255
    ),
    ADD CONSTRAINT schedules_definition_hash_ck CHECK (
        definition_hash ~ '^[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT schedules_overlap_policy_ck CHECK (
        overlap_policy IN ('forbid', 'allow')
    ),
    ADD CONSTRAINT schedules_max_lateness_ck CHECK (
        max_lateness_seconds IS NULL
        OR max_lateness_seconds BETWEEN 0 AND 31536000
    ),
    ADD CONSTRAINT schedules_definition_errors_ck CHECK (
        consecutive_definition_errors BETWEEN 0 AND 3
    ),
    ADD CONSTRAINT schedules_paused_audit_ck CHECK (
        (paused_reason IS NULL AND paused_by IS NULL AND paused_at IS NULL)
        OR
        (state = 'paused'
         AND octet_length(paused_reason) BETWEEN 1 AND 500
         AND octet_length(paused_by) BETWEEN 1 AND 255
         AND paused_at IS NOT NULL)
    );

CREATE INDEX schedules_manifest_owner_idx
    ON taskq.schedules(namespace, source, name)
    WHERE target->>'kind' = 'job';

CREATE TABLE taskq.schedule_decisions (
    decision_id uuid PRIMARY KEY DEFAULT taskq.uuid7(),
    schedule_id uuid NOT NULL REFERENCES taskq.schedules(id),
    action_token uuid NOT NULL,
    definition_version bigint NOT NULL,
    database_as_of timestamptz NOT NULL,
    cursor_from timestamptz NOT NULL,
    cursor_to timestamptz NOT NULL,
    action text NOT NULL,
    selected_count integer NOT NULL,
    jobs_enqueued integer NOT NULL,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    scheduler_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(schedule_id, action_token),
    CONSTRAINT schedule_decisions_version_ck CHECK (definition_version > 0),
    CONSTRAINT schedule_decisions_cursor_ck CHECK (cursor_to >= cursor_from),
    CONSTRAINT schedule_decisions_action_ck CHECK (
        action IN ('initialized','skipped','fired','error','auto_paused','paused','resumed')
    ),
    CONSTRAINT schedule_decisions_counts_ck CHECK (
        selected_count >= 0 AND jobs_enqueued >= 0 AND jobs_enqueued <= selected_count
    ),
    CONSTRAINT schedule_decisions_summary_ck CHECK (
        jsonb_typeof(summary) = 'object' AND octet_length(summary::text) <= 4096
    ),
    CONSTRAINT schedule_decisions_scheduler_ck CHECK (
        octet_length(scheduler_id) BETWEEN 1 AND 200
    )
);
ALTER TABLE taskq.schedule_decisions OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.schedule_decisions FROM PUBLIC;
CREATE INDEX schedule_decisions_retention_idx
    ON taskq.schedule_decisions(created_at, decision_id);

ALTER TABLE taskq.schedule_occurrences
    ADD COLUMN occurrence_id uuid NOT NULL DEFAULT taskq.uuid7(),
    ADD COLUMN decision_id uuid,
    ADD COLUMN outcome text;

UPDATE taskq.schedule_occurrences
SET outcome = CASE WHEN job_id IS NULL THEN 'fired' ELSE 'fired' END;

ALTER TABLE taskq.schedule_occurrences
    ALTER COLUMN outcome SET NOT NULL,
    ADD CONSTRAINT schedule_occurrences_id_key UNIQUE(occurrence_id),
    ADD CONSTRAINT schedule_occurrences_outcome_ck CHECK (
        outcome IN ('fired','late_skipped','overlap_skipped')
    ),
    ADD CONSTRAINT schedule_occurrences_job_shape_ck CHECK (
        (outcome = 'fired') OR job_id IS NULL
    ),
    ADD CONSTRAINT schedule_occurrences_decision_fk
        FOREIGN KEY(decision_id) REFERENCES taskq.schedule_decisions(decision_id)
        DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX schedule_occurrences_retention_idx
    ON taskq.schedule_occurrences(created_at, schedule_id, due_at);

-- Preserve the audited 0.2.6 implementations as owner-private helpers. The
-- public identities below become fail-closed attested wrappers.
ALTER FUNCTION taskq.put_schedule(text,jsonb,text,bigint)
    RENAME TO _put_schedule_unattested;
ALTER FUNCTION taskq.retire_schedule(text,bigint,text)
    RENAME TO _retire_schedule_unattested;
ALTER FUNCTION taskq.claim_schedules(text,integer,integer)
    RENAME TO _claim_schedules_unattested;
-- The scheduler-v2 fire path is a complete replacement rather than a wrapper.
-- Drop the prior body so an owner-only, unattested implementation cannot become
-- a future bypass or confuse privilege audits.
DROP FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz);
ALTER FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer)
    RENAME TO _schedule_error_unattested;
ALTER FUNCTION taskq.tick(integer) RENAME TO _tick_unattested;
ALTER FUNCTION taskq.janitor() RENAME TO _janitor_unattested;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    RENAME TO _claim_jobs_unattested;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    RENAME TO _claim_jobs_unattested;

REVOKE ALL ON FUNCTION taskq._put_schedule_unattested(text,jsonb,text,bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._retire_schedule_unattested(text,bigint,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._claim_schedules_unattested(text,integer,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._schedule_error_unattested(uuid,uuid,bigint,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._tick_unattested(integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._janitor_unattested() FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION taskq._put_schedule_unattested(text,jsonb,text,bigint)
    FROM taskq_operator;
REVOKE ALL ON FUNCTION taskq._retire_schedule_unattested(text,bigint,text)
    FROM taskq_operator;
REVOKE ALL ON FUNCTION taskq._claim_schedules_unattested(text,integer,integer)
    FROM taskq_housekeeper;
REVOKE ALL ON FUNCTION taskq._schedule_error_unattested(uuid,uuid,bigint,text,integer)
    FROM taskq_housekeeper;
REVOKE ALL ON FUNCTION taskq._tick_unattested(integer)
    FROM taskq_housekeeper, taskq_operator;
REVOKE ALL ON FUNCTION taskq._janitor_unattested()
    FROM taskq_housekeeper, taskq_operator;
REVOKE ALL ON FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid)
    FROM taskq_runner;
REVOKE ALL ON FUNCTION taskq._claim_jobs_unattested(text,text,integer,text[],integer,text,uuid,text[])
    FROM taskq_runner;

CREATE FUNCTION taskq.put_schedule(
    p_name text,
    p_definition jsonb,
    p_actor text,
    p_expected_version bigint DEFAULT NULL
) RETURNS taskq.schedule_write_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_result taskq.schedule_write_result;
BEGIN
    PERFORM taskq.require_target_attestation();
    IF p_definition->'target'->'headers' ? 'taskq_schedule' THEN
        RAISE EXCEPTION 'taskq_schedule header is reserved' USING ERRCODE = 'TQ422';
    END IF;
    -- The 0.2.6 helper predates paused audit columns. Clear the optional audit
    -- first when the desired definition resumes; any later failure rolls the
    -- whole function transaction back.
    IF COALESCE((p_definition->>'paused')::boolean, false) = false THEN
        UPDATE taskq.schedules
        SET paused_reason = NULL, paused_by = NULL, paused_at = NULL
        WHERE name = p_name;
    END IF;
    v_result := taskq._put_schedule_unattested(
        p_name, p_definition, p_actor, p_expected_version
    );
    UPDATE taskq.schedules
    SET manifest_key = CASE WHEN manifest_key = 'legacy' THEN name ELSE manifest_key END,
        display_name = CASE WHEN display_name = 'legacy' THEN name ELSE display_name END,
        definition_hash = encode(sha256(convert_to(jsonb_build_object(
            'target', target,
            'recurrence', recurrence,
            'catchup_policy', catchup_policy,
            'max_catchup', max_catchup,
            'paused', state = 'paused',
            'overlap', overlap_policy,
            'max_lateness_seconds', max_lateness_seconds
        )::text, 'UTF8')), 'hex'),
        consecutive_definition_errors = CASE
            WHEN v_result.outcome = 'updated' THEN 0 ELSE consecutive_definition_errors END,
        paused_reason = CASE
            WHEN v_result.outcome = 'updated' THEN NULL ELSE paused_reason END,
        paused_by = CASE WHEN v_result.outcome = 'updated' THEN NULL ELSE paused_by END,
        paused_at = CASE WHEN v_result.outcome = 'updated' THEN NULL ELSE paused_at END
    WHERE id = (v_result.profile).schedule_id;
    RETURN v_result;
END $$;
ALTER FUNCTION taskq.put_schedule(text,jsonb,text,bigint) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.put_schedule(text,jsonb,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.put_schedule(text,jsonb,text,bigint) TO taskq_operator;

CREATE FUNCTION taskq.put_managed_schedule(
    p_name text,
    p_definition jsonb,
    p_namespace text,
    p_source text,
    p_manifest_key text,
    p_display_name text,
    p_definition_hash text,
    p_overlap_policy text,
    p_max_lateness_seconds integer,
    p_actor text,
    p_expected_version bigint DEFAULT NULL
) RETURNS taskq.schedule_write_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_before taskq.schedules%ROWTYPE;
    v_after taskq.schedules%ROWTYPE;
    v_result taskq.schedule_write_result;
    v_profile taskq.schedule_profile;
    v_metadata_changed boolean;
BEGIN
    PERFORM taskq.require_target_attestation();
    IF p_namespace IS NULL OR p_namespace !~ '^[a-z0-9][a-z0-9_-]{0,62}$'
       OR p_source IS NULL OR p_source !~ '^[a-z0-9][a-z0-9_-]{0,62}$'
       OR p_manifest_key IS NULL
       OR p_manifest_key !~ '^[a-z0-9][a-z0-9_.-]*$'
       OR p_name IS DISTINCT FROM p_namespace || '.' || p_manifest_key
       OR octet_length(p_name) NOT BETWEEN 1 AND 120
       OR p_display_name IS NULL OR octet_length(p_display_name) NOT BETWEEN 1 AND 255
       OR p_definition_hash IS NULL OR p_definition_hash !~ '^[0-9a-f]{64}$'
       OR p_overlap_policy NOT IN ('forbid','allow')
       OR (p_max_lateness_seconds IS NOT NULL
           AND p_max_lateness_seconds NOT BETWEEN 0 AND 31536000)
       OR p_definition->'target'->'headers' ? 'taskq_schedule' THEN
        RAISE EXCEPTION 'invalid managed schedule request' USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO v_before FROM taskq.schedules WHERE name = p_name FOR UPDATE;
    IF FOUND AND (
        v_before.namespace IS DISTINCT FROM p_namespace
        OR v_before.source IS DISTINCT FROM p_source
        OR v_before.manifest_key IS DISTINCT FROM p_manifest_key
    ) THEN
        RAISE EXCEPTION 'schedule owner mismatch'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"schedule_owner_mismatch"}';
    END IF;
    v_metadata_changed := FOUND AND (
        v_before.display_name IS DISTINCT FROM p_display_name
        OR v_before.definition_hash IS DISTINCT FROM p_definition_hash
        OR v_before.overlap_policy IS DISTINCT FROM p_overlap_policy
        OR v_before.max_lateness_seconds IS DISTINCT FROM p_max_lateness_seconds
    );

    IF COALESCE((p_definition->>'paused')::boolean, false) = false THEN
        UPDATE taskq.schedules
        SET paused_reason = NULL, paused_by = NULL, paused_at = NULL
        WHERE name = p_name;
    END IF;

    v_result := taskq._put_schedule_unattested(
        p_name, p_definition, p_actor, p_expected_version
    );
    IF v_result.outcome = 'unchanged' AND v_metadata_changed THEN
        UPDATE taskq.schedules
        SET namespace = p_namespace,
            source = p_source,
            manifest_key = p_manifest_key,
            display_name = p_display_name,
            definition_hash = p_definition_hash,
            overlap_policy = p_overlap_policy,
            max_lateness_seconds = p_max_lateness_seconds,
            consecutive_definition_errors = 0,
            paused_reason = NULL,
            paused_by = NULL,
            paused_at = NULL,
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
        WHERE id = (v_result.profile).schedule_id
        RETURNING * INTO v_after;
        v_result.outcome := 'updated';
    ELSE
        UPDATE taskq.schedules
        SET namespace = p_namespace,
            source = p_source,
            manifest_key = p_manifest_key,
            display_name = p_display_name,
            definition_hash = p_definition_hash,
            overlap_policy = p_overlap_policy,
            max_lateness_seconds = p_max_lateness_seconds,
            consecutive_definition_errors = CASE
                WHEN v_result.outcome IN ('created','updated') THEN 0
                ELSE consecutive_definition_errors END,
            paused_reason = CASE
                WHEN v_result.outcome IN ('created','updated') THEN NULL ELSE paused_reason END,
            paused_by = CASE
                WHEN v_result.outcome IN ('created','updated') THEN NULL ELSE paused_by END,
            paused_at = CASE
                WHEN v_result.outcome IN ('created','updated') THEN NULL ELSE paused_at END
        WHERE id = (v_result.profile).schedule_id
        RETURNING * INTO v_after;
    END IF;
    v_profile := (
        v_after.id, v_after.name, v_after.target, v_after.recurrence,
        v_after.catchup_policy, v_after.max_catchup, v_after.state,
        v_after.next_fire_at, v_after.last_fire_at, v_after.version
    )::taskq.schedule_profile;
    v_result.profile := v_profile;
    RETURN v_result;
END $$;
ALTER FUNCTION taskq.put_managed_schedule(text,jsonb,text,text,text,text,text,text,integer,text,bigint)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.put_managed_schedule(text,jsonb,text,text,text,text,text,text,integer,text,bigint)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.put_managed_schedule(text,jsonb,text,text,text,text,text,text,integer,text,bigint)
    TO taskq_operator;

CREATE FUNCTION taskq.list_managed_schedules(
    p_namespace text,
    p_source text,
    p_limit integer DEFAULT 100,
    p_after_name text DEFAULT NULL
) RETURNS TABLE(
    name text,
    manifest_key text,
    display_name text,
    definition_hash text,
    target jsonb,
    recurrence jsonb,
    catchup_policy text,
    max_catchup integer,
    overlap_policy text,
    max_lateness_seconds integer,
    state text,
    version bigint
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF p_namespace IS NULL OR p_source IS NULL
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'invalid managed schedule query' USING ERRCODE = 'TQ422';
    END IF;
    RETURN QUERY
    SELECT s.name, s.manifest_key, s.display_name, s.definition_hash,
           s.target, s.recurrence, s.catchup_policy, s.max_catchup,
           s.overlap_policy, s.max_lateness_seconds, s.state, s.version
    FROM taskq.schedules AS s
    WHERE s.namespace = p_namespace
      AND s.source = p_source
      AND s.target->>'kind' = 'job'
      AND (p_after_name IS NULL OR s.name > p_after_name)
    ORDER BY s.name
    LIMIT p_limit;
END $$;
ALTER FUNCTION taskq.list_managed_schedules(text,text,integer,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.list_managed_schedules(text,text,integer,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.list_managed_schedules(text,text,integer,text)
    TO taskq_observer, taskq_operator;

CREATE FUNCTION taskq.retire_schedule(
    p_name text,
    p_expected_version bigint,
    p_actor text
) RETURNS taskq.schedule_write_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._retire_schedule_unattested(p_name, p_expected_version, p_actor);
END $$;
ALTER FUNCTION taskq.retire_schedule(text,bigint,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.retire_schedule(text,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.retire_schedule(text,bigint,text) TO taskq_operator;

CREATE FUNCTION taskq.set_schedule_state(
    p_name text,
    p_state text,
    p_expected_version bigint,
    p_actor text,
    p_reason text
) RETURNS taskq.schedule_write_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_profile taskq.schedule_profile;
    v_token uuid := taskq.uuid7();
    v_outcome text;
BEGIN
    PERFORM taskq.require_target_attestation();
    IF p_state NOT IN ('active','paused') OR p_expected_version IS NULL
       OR p_expected_version <= 0
       OR p_actor IS NULL OR octet_length(p_actor) NOT BETWEEN 1 AND 255
       OR p_reason IS NULL OR octet_length(p_reason) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'invalid schedule state request' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_schedule FROM taskq.schedules WHERE name = p_name FOR UPDATE;
    IF NOT FOUND OR v_schedule.target->>'kind' <> 'job' THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF v_schedule.version IS DISTINCT FROM p_expected_version THEN
        RAISE EXCEPTION 'schedule version conflict'
            USING ERRCODE = 'TQ409', DETAIL = jsonb_build_object(
                'reason','schedule_version_conflict','current_version',v_schedule.version
            )::text;
    END IF;
    IF v_schedule.state = 'retired' THEN
        RAISE EXCEPTION 'schedule is retired' USING ERRCODE = 'TQ409';
    END IF;
    IF v_schedule.state = p_state THEN
        v_outcome := 'unchanged';
    ELSE
        v_outcome := 'updated';
        UPDATE taskq.schedules
        SET state = p_state,
            initialized = CASE WHEN p_state = 'active' THEN false ELSE initialized END,
            next_fire_at = CASE WHEN p_state = 'active' THEN now() ELSE next_fire_at END,
            version = version + 1,
            claim_token = NULL,
            claim_as_of = NULL,
            claimed_by = NULL,
            claim_expires_at = NULL,
            retry_not_before = NULL,
            consecutive_definition_errors = CASE
                WHEN p_state = 'active' THEN 0 ELSE consecutive_definition_errors END,
            paused_reason = CASE WHEN p_state = 'paused' THEN p_reason ELSE NULL END,
            paused_by = CASE WHEN p_state = 'paused' THEN p_actor ELSE NULL END,
            paused_at = CASE WHEN p_state = 'paused' THEN now() ELSE NULL END,
            updated_by = p_actor,
            updated_at = now()
        WHERE id = v_schedule.id
        RETURNING * INTO v_schedule;
        INSERT INTO taskq.schedule_decisions(
            decision_id, schedule_id, action_token, definition_version,
            database_as_of, cursor_from, cursor_to, action, selected_count,
            jobs_enqueued, summary, scheduler_id
        ) VALUES (
            taskq.uuid7(), v_schedule.id, v_token, v_schedule.version,
            now(), v_schedule.next_fire_at, v_schedule.next_fire_at,
            CASE WHEN p_state = 'active' THEN 'resumed' ELSE 'paused' END,
            0, 0, jsonb_build_object('reason', p_reason), p_actor
        );
    END IF;
    v_profile := (
        v_schedule.id, v_schedule.name, v_schedule.target, v_schedule.recurrence,
        v_schedule.catchup_policy, v_schedule.max_catchup, v_schedule.state,
        v_schedule.next_fire_at, v_schedule.last_fire_at, v_schedule.version
    )::taskq.schedule_profile;
    RETURN (v_outcome, v_profile)::taskq.schedule_write_result;
END $$;
ALTER FUNCTION taskq.set_schedule_state(text,text,bigint,text,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.set_schedule_state(text,text,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.set_schedule_state(text,text,bigint,text,text)
    TO taskq_operator;

CREATE FUNCTION taskq.claim_schedules(
    p_worker_id text,
    p_limit integer DEFAULT 10,
    p_lease_seconds integer DEFAULT 60
) RETURNS taskq.schedule_claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._claim_schedules_unattested(p_worker_id, p_limit, p_lease_seconds);
END $$;
ALTER FUNCTION taskq.claim_schedules(text,integer,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_schedules(text,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_schedules(text,integer,integer) TO taskq_housekeeper;

CREATE FUNCTION taskq.fire_schedule(
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
    v_decision_id uuid := taskq.uuid7();
    v_occurrence_id uuid;
    v_occurrence_outcome text;
    v_headers jsonb;
    v_lateness integer;
    v_fired integer := 0;
    v_late_skipped integer := 0;
    v_overlap_skipped integer := 0;
BEGIN
    PERFORM taskq.require_target_attestation();
    IF p_schedule_id IS NULL OR p_token IS NULL
       OR p_token = '00000000-0000-0000-0000-000000000000'::uuid
       OR p_definition_version IS NULL OR p_definition_version <= 0
       OR p_occurrences IS NULL OR p_next_fire_at IS NULL
       OR EXISTS (SELECT 1 FROM unnest(p_occurrences) AS x(value) WHERE value IS NULL) THEN
        RAISE EXCEPTION 'invalid schedule fire arguments' USING ERRCODE = 'TQ422';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'kind','fire', 'version',p_definition_version,
        'occurrences',p_occurrences, 'next_fire_at',p_next_fire_at
    )::text, 'UTF8')), 'hex');

    SELECT * INTO v_schedule FROM taskq.schedules
    WHERE id = p_schedule_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF v_schedule.last_action_token = p_token THEN
        IF v_schedule.last_action_hash IS DISTINCT FROM v_hash THEN
            RETURN ('stale',false,v_schedule.id,0,v_schedule.next_fire_at,
                    v_schedule.state,v_schedule.version)::taskq.schedule_action_result;
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
        RETURN ('stale',false,v_schedule.id,0,v_schedule.next_fire_at,
                v_schedule.state,v_schedule.version)::taskq.schedule_action_result;
    END IF;

    v_count := cardinality(p_occurrences);
    IF v_count > v_schedule.max_catchup OR p_next_fire_at <= v_schedule.next_fire_at THEN
        RAISE EXCEPTION 'invalid schedule fire bounds' USING ERRCODE = 'TQ422';
    END IF;
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
        IF v_count <> 1 OR p_occurrences[1] < v_schedule.next_fire_at
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
        IF EXISTS (
            SELECT 1 FROM taskq.schedule_occurrences AS existing
            WHERE existing.schedule_id = v_schedule.id AND existing.due_at = v_due
        ) THEN
            CONTINUE;
        END IF;
        v_occurrence_id := taskq.uuid7();
        v_lateness := greatest(0, floor(extract(epoch FROM
            (v_schedule.claim_as_of - v_due)))::integer);
        IF v_schedule.max_lateness_seconds IS NOT NULL
           AND v_lateness > v_schedule.max_lateness_seconds THEN
            v_occurrence_outcome := 'late_skipped';
            v_late_skipped := v_late_skipped + 1;
        ELSIF v_schedule.overlap_policy = 'forbid'
           AND v_schedule.target->>'kind' = 'job'
           AND EXISTS (
               SELECT 1
               FROM taskq.schedule_occurrences AS earlier
               JOIN taskq.jobs AS active_job ON active_job.id = earlier.job_id
               WHERE earlier.schedule_id = v_schedule.id
                 AND earlier.due_at < v_due
                 AND active_job.status IN ('blocked','queued','running')
           ) THEN
            v_occurrence_outcome := 'overlap_skipped';
            v_overlap_skipped := v_overlap_skipped + 1;
        ELSE
            v_occurrence_outcome := 'fired';
            v_fired := v_fired + 1;
        END IF;

        IF v_occurrence_outcome = 'fired'
           AND v_schedule.target->>'kind' = 'maintenance' THEN
            IF v_schedule.name <> 'taskq-janitor-daily'
               OR v_schedule.target->>'maintenance' <> 'janitor' THEN
                RAISE EXCEPTION 'unknown maintenance schedule target'
                    USING ERRCODE = 'TQ500';
            END IF;
            PERFORM taskq.janitor();
        ELSIF v_occurrence_outcome = 'fired' THEN
            v_key := 'schedule:' || v_schedule.id::text || ':'
                || floor(extract(epoch FROM v_due) * 1000000)::numeric::text;
            v_headers := v_schedule.target->'headers' || jsonb_build_object(
                'taskq_schedule', jsonb_build_object(
                    'schedule_id', v_schedule.id,
                    'schedule_key', v_schedule.namespace || '.' || v_schedule.manifest_key,
                    'occurrence_id', v_occurrence_id,
                    'definition_version', v_schedule.version,
                    'scheduled_for', v_due,
                    'enqueued_at', v_schedule.claim_as_of,
                    'lateness_seconds', v_lateness,
                    'backfilled', v_due < v_schedule.claim_as_of
                )
            );
            SELECT e.job_id, e.created INTO v_job_id, v_created
            FROM taskq.enqueue(
                v_schedule.target->>'queue', v_schedule.target->>'job_type',
                v_schedule.target->'payload',
                (v_schedule.target->>'priority')::smallint, v_due, v_key,
                v_schedule.target->>'concurrency_key',
                v_schedule.target->>'affinity_key',
                (v_schedule.target->>'max_attempts')::smallint,
                (v_schedule.target->>'lease_seconds')::integer,
                v_schedule.target->>'backoff_mode',
                (v_schedule.target->>'backoff_base')::integer,
                (v_schedule.target->>'backoff_cap')::integer,
                NULL, NULL, NULL, NULL, v_headers
            ) AS e;
            v_jobs := v_jobs + 1;
        ELSE
            v_job_id := NULL;
        END IF;
        INSERT INTO taskq.schedule_occurrences(
            schedule_id, due_at, occurrence_id, decision_id, outcome, job_id
        ) VALUES (
            v_schedule.id, v_due, v_occurrence_id, v_decision_id,
            v_occurrence_outcome, v_job_id
        );
        v_job_id := NULL;
    END LOOP;

    INSERT INTO taskq.schedule_decisions(
        decision_id, schedule_id, action_token, definition_version,
        database_as_of, cursor_from, cursor_to, action, selected_count,
        jobs_enqueued, summary, scheduler_id
    ) VALUES (
        v_decision_id, v_schedule.id, p_token, v_schedule.version,
        v_schedule.claim_as_of, v_schedule.next_fire_at, p_next_fire_at,
        v_outcome, v_count, v_jobs,
        jsonb_build_object(
            'fired',v_fired,
            'late_skipped',v_late_skipped,
            'overlap_skipped',v_overlap_skipped
        ),
        v_schedule.claimed_by
    );

    v_stored := jsonb_build_object(
        'outcome',v_outcome, 'jobs_enqueued',v_jobs,
        'next_fire_at',p_next_fire_at, 'state',v_schedule.state,
        'version',v_schedule.version
    );
    UPDATE taskq.schedules
    SET initialized = true,
        next_fire_at = p_next_fire_at,
        last_fire_at = CASE WHEN v_count > 0 THEN p_occurrences[v_count]
                            ELSE last_fire_at END,
        claim_token = NULL, claim_as_of = NULL, claimed_by = NULL,
        claim_expires_at = NULL, retry_not_before = NULL, last_error = NULL,
        consecutive_definition_errors = 0,
        last_action_token = p_token, last_action_hash = v_hash,
        last_action_result = v_stored, updated_at = now()
    WHERE id = v_schedule.id;
    v_result := (v_outcome,false,v_schedule.id,v_jobs,p_next_fire_at,
                 v_schedule.state,v_schedule.version)::taskq.schedule_action_result;
    RETURN v_result;
END $$;
ALTER FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)
    TO taskq_housekeeper;

CREATE FUNCTION taskq.schedule_error(
    p_schedule_id uuid,
    p_token uuid,
    p_definition_version bigint,
    p_error text,
    p_retry_seconds integer,
    p_deterministic boolean
) RETURNS taskq.schedule_action_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_hash text;
    v_stored jsonb;
    v_count integer;
    v_auto_pause boolean;
    v_state text;
    v_version bigint;
    v_outcome text;
BEGIN
    PERFORM taskq.require_target_attestation();
    IF p_schedule_id IS NULL OR p_token IS NULL
       OR p_definition_version IS NULL OR p_definition_version <= 0
       OR p_error IS NULL OR p_retry_seconds NOT BETWEEN 1 AND 3600
       OR p_deterministic IS NULL THEN
        RAISE EXCEPTION 'invalid schedule error arguments' USING ERRCODE = 'TQ422';
    END IF;
    v_hash := encode(sha256(convert_to(jsonb_build_object(
        'kind','error', 'version',p_definition_version,
        'error',p_error, 'retry_seconds',p_retry_seconds,
        'deterministic',p_deterministic
    )::text, 'UTF8')), 'hex');
    SELECT * INTO v_schedule FROM taskq.schedules
    WHERE id = p_schedule_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such schedule' USING ERRCODE = 'TQ001';
    END IF;
    IF v_schedule.last_action_token = p_token THEN
        IF v_schedule.last_action_hash IS DISTINCT FROM v_hash THEN
            RETURN ('stale',false,v_schedule.id,0,v_schedule.next_fire_at,
                    v_schedule.state,v_schedule.version)::taskq.schedule_action_result;
        END IF;
        v_stored := v_schedule.last_action_result;
        RETURN (
            v_stored->>'outcome', true, v_schedule.id, 0,
            (v_stored->>'next_fire_at')::timestamptz,
            v_stored->>'state', (v_stored->>'version')::bigint
        )::taskq.schedule_action_result;
    END IF;
    IF v_schedule.state <> 'active'
       OR v_schedule.version IS DISTINCT FROM p_definition_version
       OR v_schedule.claim_token IS DISTINCT FROM p_token
       OR v_schedule.claim_expires_at < now() THEN
        RETURN ('stale',false,v_schedule.id,0,v_schedule.next_fire_at,
                v_schedule.state,v_schedule.version)::taskq.schedule_action_result;
    END IF;
    v_count := CASE WHEN p_deterministic
        THEN least(3, v_schedule.consecutive_definition_errors + 1)
        ELSE v_schedule.consecutive_definition_errors END;
    v_auto_pause := p_deterministic AND v_count >= 3;
    v_state := CASE WHEN v_auto_pause THEN 'paused' ELSE v_schedule.state END;
    v_version := v_schedule.version + CASE WHEN v_auto_pause THEN 1 ELSE 0 END;
    v_outcome := CASE WHEN v_auto_pause THEN 'auto_paused' ELSE 'error_recorded' END;
    v_stored := jsonb_build_object(
        'outcome',v_outcome, 'jobs_enqueued',0,
        'next_fire_at',v_schedule.next_fire_at,
        'state',v_state, 'version',v_version
    );
    UPDATE taskq.schedules
    SET state = v_state,
        version = v_version,
        claim_token = NULL, claim_as_of = NULL, claimed_by = NULL,
        claim_expires_at = NULL,
        retry_not_before = CASE WHEN v_auto_pause THEN NULL
            ELSE now() + make_interval(secs => p_retry_seconds) END,
        last_error = taskq.truncate_utf8(p_error, 2048),
        consecutive_definition_errors = v_count,
        paused_reason = CASE WHEN v_auto_pause THEN 'deterministic_definition_error'
                             ELSE paused_reason END,
        paused_by = CASE WHEN v_auto_pause THEN 'taskq-scheduler' ELSE paused_by END,
        paused_at = CASE WHEN v_auto_pause THEN now() ELSE paused_at END,
        last_action_token = p_token, last_action_hash = v_hash,
        last_action_result = v_stored, updated_at = now()
    WHERE id = v_schedule.id;
    INSERT INTO taskq.schedule_decisions(
        decision_id, schedule_id, action_token, definition_version,
        database_as_of, cursor_from, cursor_to, action, selected_count,
        jobs_enqueued, summary, scheduler_id
    ) VALUES (
        taskq.uuid7(), v_schedule.id, p_token, p_definition_version,
        v_schedule.claim_as_of, v_schedule.next_fire_at, v_schedule.next_fire_at,
        CASE WHEN v_auto_pause THEN 'auto_paused' ELSE 'error' END,
        0, 0, jsonb_build_object(
            'deterministic',p_deterministic,
            'consecutive_definition_errors',v_count
        ), v_schedule.claimed_by
    );
    RETURN (v_outcome,false,v_schedule.id,0,v_schedule.next_fire_at,
            v_state,v_version)::taskq.schedule_action_result;
END $$;
ALTER FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer,boolean)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer,boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer,boolean)
    TO taskq_housekeeper;

CREATE FUNCTION taskq.schedule_error(
    p_schedule_id uuid,
    p_token uuid,
    p_definition_version bigint,
    p_error text,
    p_retry_seconds integer DEFAULT 30
) RETURNS taskq.schedule_action_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    RETURN taskq.schedule_error(
        p_schedule_id, p_token, p_definition_version, p_error,
        p_retry_seconds, false
    );
END $$;
ALTER FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.schedule_error(uuid,uuid,bigint,text,integer)
    TO taskq_housekeeper;

CREATE FUNCTION taskq.janitor() RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_result jsonb;
    v_occurrences integer;
    v_decisions integer;
BEGIN
    PERFORM taskq.require_target_attestation();
    v_result := taskq._janitor_unattested();
    DELETE FROM taskq.schedule_occurrences AS occurrence
    USING taskq.schedules AS schedule
    WHERE occurrence.schedule_id = schedule.id
      AND occurrence.created_at < now() - interval '90 days'
      AND occurrence.due_at < schedule.next_fire_at
      AND occurrence.job_id IS NULL;
    GET DIAGNOSTICS v_occurrences = ROW_COUNT;
    DELETE FROM taskq.schedule_decisions AS decision
    USING taskq.schedules AS schedule
    WHERE decision.schedule_id = schedule.id
      AND decision.created_at < now() - interval '90 days'
      AND decision.cursor_to < schedule.next_fire_at
      AND NOT EXISTS (
          SELECT 1 FROM taskq.schedule_occurrences AS occurrence
          WHERE occurrence.decision_id = decision.decision_id
      );
    GET DIAGNOSTICS v_decisions = ROW_COUNT;
    RETURN v_result || jsonb_build_object(
        'schedule_occurrences_pruned',v_occurrences,
        'schedule_decisions_pruned',v_decisions
    );
END $$;
ALTER FUNCTION taskq.janitor() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.janitor() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.janitor() TO taskq_housekeeper, taskq_operator;

CREATE FUNCTION taskq.tick(p_reap_limit integer DEFAULT 200) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._tick_unattested(p_reap_limit);
END $$;
ALTER FUNCTION taskq.tick(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.tick(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.tick(integer) TO taskq_housekeeper, taskq_operator;

-- Direct-DSN workers attest in the same transaction before either claim
-- overload. HTTP workers continue to use the remote authenticated boundary.
CREATE FUNCTION taskq.claim_jobs(
    p_queue text,
    p_worker_id text,
    p_batch integer DEFAULT 1,
    p_job_types text[] DEFAULT NULL,
    p_lease_seconds integer DEFAULT NULL,
    p_affinity_key text DEFAULT NULL,
    p_job_id uuid DEFAULT NULL
) RETURNS taskq.claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._claim_jobs_unattested(
        p_queue,p_worker_id,p_batch,p_job_types,p_lease_seconds,p_affinity_key,p_job_id
    );
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    TO taskq_runner;

CREATE FUNCTION taskq.claim_jobs(
    p_queue text,
    p_worker_id text,
    p_batch integer,
    p_job_types text[],
    p_lease_seconds integer,
    p_affinity_key text,
    p_job_id uuid,
    p_continuation_policy_hashes text[]
) RETURNS taskq.claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    PERFORM taskq.require_target_attestation();
    RETURN taskq._claim_jobs_unattested(
        p_queue,p_worker_id,p_batch,p_job_types,p_lease_seconds,p_affinity_key,p_job_id,
        p_continuation_policy_hashes
    );
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    TO taskq_runner;

CREATE FUNCTION taskq.get_scheduler_health()
RETURNS TABLE(
    database_time timestamptz,
    active_schedules bigint,
    due_schedules bigint,
    oldest_due_at timestamptz,
    last_decision_at timestamptz,
    auto_paused_schedules bigint
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT now(),
           count(*) FILTER (WHERE state = 'active'),
           count(*) FILTER (WHERE state = 'active' AND next_fire_at <= now()),
           min(next_fire_at) FILTER (WHERE state = 'active' AND next_fire_at <= now()),
           (SELECT max(created_at) FROM taskq.schedule_decisions),
           count(*) FILTER (
               WHERE state = 'paused'
                 AND paused_reason = 'deterministic_definition_error'
           )
    FROM taskq.schedules
$$;
ALTER FUNCTION taskq.get_scheduler_health() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_scheduler_health() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_scheduler_health()
    TO taskq_observer, taskq_operator, taskq_housekeeper;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.3.0"'::jsonb, now()),
    ('capabilities', '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb, now())
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
        RAISE EXCEPTION '0020 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
