-- outlabs-taskq — migration 0019: scheduler target-identity backing
-- SQL contract 0.2.7 / ADR-037. Additive and inactive: no existing runtime
-- function requires attestation until migration 0020 activates scheduler_v2.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract
    FROM taskq.meta
    WHERE key = 'contract_version'
    FOR UPDATE;
    SELECT value INTO v_capabilities
    FROM taskq.meta
    WHERE key = 'capabilities'
    FOR UPDATE;

    IF v_contract IS DISTINCT FROM '"0.2.6"'::jsonb THEN
        RAISE EXCEPTION '0019 requires SQL contract 0.2.6, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0019 requires the exact active 0018 capability set, found %',
            v_capabilities
            USING ERRCODE = 'TQ500';
    END IF;
    IF to_regclass('taskq.target_identity') IS NOT NULL
       OR to_regclass('taskq.target_binding_events') IS NOT NULL
       OR to_regtype('taskq.target_identity_profile') IS NOT NULL THEN
        RAISE EXCEPTION '0019 target-identity backing already exists'
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

CREATE TYPE taskq.target_identity_profile AS (
    installation_id uuid,
    environment text,
    binding_version bigint,
    bound_at timestamptz,
    bound_by text,
    contract_version text,
    capabilities jsonb
);
ALTER TYPE taskq.target_identity_profile OWNER TO taskq_owner;

CREATE TABLE taskq.target_identity (
    singleton boolean PRIMARY KEY DEFAULT true,
    installation_id uuid NOT NULL,
    environment text NOT NULL DEFAULT 'unbound',
    binding_version bigint NOT NULL DEFAULT 0,
    bound_at timestamptz,
    bound_by text,
    attestation_secret bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT target_identity_singleton_ck CHECK (singleton),
    CONSTRAINT target_identity_environment_ck CHECK (
        environment = 'unbound'
        OR environment ~ '^[a-z0-9][a-z0-9_-]{0,62}$'
    ),
    CONSTRAINT target_identity_binding_version_ck CHECK (binding_version >= 0),
    CONSTRAINT target_identity_bound_shape_ck CHECK (
        (environment = 'unbound'
         AND binding_version = 0
         AND bound_at IS NULL
         AND bound_by IS NULL)
        OR
        (environment <> 'unbound'
         AND binding_version > 0
         AND bound_at IS NOT NULL
         AND octet_length(bound_by) BETWEEN 1 AND 255)
    ),
    CONSTRAINT target_identity_secret_ck CHECK (octet_length(attestation_secret) = 32)
);
ALTER TABLE taskq.target_identity OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.target_identity FROM PUBLIC;

CREATE TABLE taskq.target_binding_events (
    event_id uuid PRIMARY KEY DEFAULT taskq.uuid7(),
    binding_version bigint NOT NULL UNIQUE,
    old_installation_id uuid NOT NULL,
    new_installation_id uuid NOT NULL,
    old_environment text NOT NULL,
    new_environment text NOT NULL,
    actor text NOT NULL,
    reason text,
    rotated boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT target_binding_events_version_ck CHECK (binding_version > 0),
    CONSTRAINT target_binding_events_actor_ck CHECK (
        octet_length(actor) BETWEEN 1 AND 255
    ),
    CONSTRAINT target_binding_events_reason_ck CHECK (
        reason IS NULL OR octet_length(reason) BETWEEN 1 AND 500
    ),
    CONSTRAINT target_binding_events_environment_ck CHECK (
        new_environment ~ '^[a-z0-9][a-z0-9_-]{0,62}$'
    ),
    CONSTRAINT target_binding_events_rotation_ck CHECK (
        rotated = (old_installation_id IS DISTINCT FROM new_installation_id)
    )
);
ALTER TABLE taskq.target_binding_events OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.target_binding_events FROM PUBLIC;

INSERT INTO taskq.target_identity(
    singleton,
    installation_id,
    environment,
    binding_version,
    attestation_secret
) VALUES (
    true,
    taskq.uuid7(),
    'unbound',
    0,
    uuid_send(gen_random_uuid()) || uuid_send(gen_random_uuid())
);

CREATE FUNCTION taskq.get_target_identity()
RETURNS taskq.target_identity_profile
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT (
        identity.installation_id,
        identity.environment,
        identity.binding_version,
        identity.bound_at,
        identity.bound_by,
        (SELECT value #>> '{}' FROM taskq.meta WHERE key = 'contract_version'),
        (SELECT value FROM taskq.meta WHERE key = 'capabilities')
    )::taskq.target_identity_profile
    FROM taskq.target_identity AS identity
    WHERE identity.singleton
$$;
ALTER FUNCTION taskq.get_target_identity() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_target_identity() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_target_identity()
    TO taskq_producer, taskq_runner, taskq_observer, taskq_operator, taskq_housekeeper;

CREATE FUNCTION taskq.bind_target_identity(
    p_expected_installation_id uuid,
    p_environment text,
    p_actor text,
    p_expected_binding_version bigint,
    p_rotate boolean DEFAULT false,
    p_reason text DEFAULT NULL
) RETURNS taskq.target_identity_profile
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_before taskq.target_identity%ROWTYPE;
    v_after taskq.target_identity%ROWTYPE;
    v_new_installation_id uuid;
BEGIN
    IF p_expected_installation_id IS NULL
       OR p_environment IS NULL
       OR p_environment = 'unbound'
       OR p_environment !~ '^[a-z0-9][a-z0-9_-]{0,62}$'
       OR p_actor IS NULL
       OR octet_length(p_actor) NOT BETWEEN 1 AND 255
       OR p_expected_binding_version IS NULL
       OR p_expected_binding_version < 0
       OR p_rotate IS NULL
       OR (p_reason IS NOT NULL AND octet_length(p_reason) NOT BETWEEN 1 AND 500) THEN
        RAISE EXCEPTION 'invalid target binding request'
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO v_before
    FROM taskq.target_identity
    WHERE singleton
    FOR UPDATE;

    IF v_before.installation_id IS DISTINCT FROM p_expected_installation_id THEN
        RAISE EXCEPTION 'target installation mismatch'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"installation_mismatch"}';
    END IF;
    IF v_before.binding_version IS DISTINCT FROM p_expected_binding_version THEN
        RAISE EXCEPTION 'target binding version conflict'
            USING ERRCODE = 'TQ409',
                  DETAIL = jsonb_build_object(
                      'reason', 'target_binding_version_conflict',
                      'current_version', v_before.binding_version
                  )::text;
    END IF;

    IF v_before.environment <> 'unbound' AND NOT p_rotate THEN
        IF v_before.environment = p_environment THEN
            RETURN taskq.get_target_identity();
        END IF;
        RAISE EXCEPTION 'bound target requires identity rotation'
            USING ERRCODE = 'TQ409',
                  DETAIL = '{"reason":"target_rotation_required"}';
    END IF;
    IF v_before.environment = 'unbound' AND p_rotate THEN
        RAISE EXCEPTION 'initial target binding cannot rotate identity'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"initial_rotation_forbidden"}';
    END IF;
    IF p_rotate AND p_reason IS NULL THEN
        RAISE EXCEPTION 'target rotation requires a reason'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"rotation_reason_required"}';
    END IF;

    v_new_installation_id := CASE
        WHEN p_rotate THEN taskq.uuid7()
        ELSE v_before.installation_id
    END;

    UPDATE taskq.target_identity
    SET installation_id = v_new_installation_id,
        environment = p_environment,
        binding_version = binding_version + 1,
        bound_at = now(),
        bound_by = p_actor,
        attestation_secret = CASE
            WHEN p_rotate THEN uuid_send(gen_random_uuid()) || uuid_send(gen_random_uuid())
            ELSE attestation_secret
        END,
        updated_at = now()
    WHERE singleton
    RETURNING * INTO v_after;

    INSERT INTO taskq.target_binding_events(
        binding_version,
        old_installation_id,
        new_installation_id,
        old_environment,
        new_environment,
        actor,
        reason,
        rotated
    ) VALUES (
        v_after.binding_version,
        v_before.installation_id,
        v_after.installation_id,
        v_before.environment,
        v_after.environment,
        p_actor,
        p_reason,
        p_rotate
    );

    RETURN taskq.get_target_identity();
END $$;
ALTER FUNCTION taskq.bind_target_identity(uuid,text,text,bigint,boolean,text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION
    taskq.bind_target_identity(uuid,text,text,bigint,boolean,text)
    FROM PUBLIC;
-- Owner/admin only. No capability-role grant.

CREATE FUNCTION taskq._target_attestation_mac()
RETURNS text
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT encode(
        sha256(
            identity.attestation_secret || convert_to(
                pg_backend_pid()::text || ':' || txid_current()::text || ':'
                || identity.installation_id::text || ':' || identity.environment || ':'
                || identity.binding_version::text,
                'UTF8'
            ) || identity.attestation_secret
        ),
        'hex'
    )
    FROM taskq.target_identity AS identity
    WHERE identity.singleton
$$;
ALTER FUNCTION taskq._target_attestation_mac() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq._target_attestation_mac() FROM PUBLIC;

CREATE FUNCTION taskq.attest_target(
    p_expected_environment text,
    p_expected_installation_id uuid DEFAULT NULL,
    p_allow_production boolean DEFAULT false
) RETURNS taskq.target_identity_profile
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_identity taskq.target_identity%ROWTYPE;
    v_mac text;
BEGIN
    IF p_expected_environment IS NULL
       OR p_expected_environment !~ '^[a-z0-9][a-z0-9_-]{0,62}$'
       OR p_allow_production IS NULL THEN
        RAISE EXCEPTION 'invalid target expectation'
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO v_identity
    FROM taskq.target_identity
    WHERE singleton
    FOR SHARE;

    IF v_identity.environment = 'unbound' THEN
        RAISE EXCEPTION 'taskq target is unbound'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"target_unbound"}';
    END IF;
    IF v_identity.environment IS DISTINCT FROM p_expected_environment THEN
        RAISE EXCEPTION 'taskq target environment mismatch'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"environment_mismatch"}';
    END IF;
    IF p_expected_installation_id IS NOT NULL
       AND v_identity.installation_id IS DISTINCT FROM p_expected_installation_id THEN
        RAISE EXCEPTION 'taskq target installation mismatch'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"installation_mismatch"}';
    END IF;
    IF v_identity.environment = 'production' AND NOT p_allow_production THEN
        RAISE EXCEPTION 'production target requires explicit permission'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"production_not_allowed"}';
    END IF;
    IF v_identity.environment = 'production'
       AND p_expected_installation_id IS NULL THEN
        RAISE EXCEPTION 'production target requires an installation pin'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"production_pin_required"}';
    END IF;

    v_mac := taskq._target_attestation_mac();
    PERFORM set_config('taskq.target_attestation', v_mac, true);
    RETURN taskq.get_target_identity();
END $$;
ALTER FUNCTION taskq.attest_target(text,uuid,boolean) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.attest_target(text,uuid,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.attest_target(text,uuid,boolean)
    TO taskq_runner, taskq_operator, taskq_housekeeper;

CREATE FUNCTION taskq.require_target_attestation()
RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_supplied text;
    v_expected text;
BEGIN
    v_supplied := current_setting('taskq.target_attestation', true);
    v_expected := taskq._target_attestation_mac();
    IF v_supplied IS NULL
       OR octet_length(v_supplied) <> 64
       OR v_supplied IS DISTINCT FROM v_expected THEN
        RAISE EXCEPTION 'taskq target attestation required'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"target_attestation_required"}';
    END IF;
END $$;
ALTER FUNCTION taskq.require_target_attestation() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.require_target_attestation() FROM PUBLIC;
-- Owner-private helper. Guarded public functions call it after 0020 activation.

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.2.7"'::jsonb, now())
ON CONFLICT(key) DO UPDATE
SET value = excluded.value, updated_at = now();

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
        RAISE EXCEPTION '0019 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
