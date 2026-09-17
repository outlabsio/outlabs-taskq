-- outlabs-taskq — corrective upgrade for the previously applied 0046 owner migration
-- SQL contract 0.6.11. Existing 0046 rows, trigger, bindings and ledger stay intact.
-- This migration replaces function bodies in place; it does not add columns,
-- invoke one-shot binding, or install another trigger.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta
    WHERE key = 'contract_version' FOR UPDATE;
    IF v_contract IS DISTINCT FROM '"0.6.10"'::jsonb THEN
        RAISE EXCEPTION '0047 requires SQL contract 0.6.10, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION taskq._check_admission_owner(
    p_queue text, p_workflow_id uuid DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_role text;
BEGIN
    -- Holding this queue row lock through the caller transaction closes
    -- check/insert races with binding without changing published migrations.
    SELECT admission_owner_role INTO v_role
    FROM taskq.queues WHERE name = p_queue FOR SHARE;
    IF v_role IS NULL THEN RETURN; END IF;
    IF NOT pg_has_role(session_user, v_role, 'member') THEN
        RAISE EXCEPTION 'queue admission owner does not permit caller'
            USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
    END IF;
END $$;
ALTER FUNCTION taskq._check_admission_owner(text,uuid) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._check_admission_owner(text,uuid) FROM PUBLIC;

-- Correct 0045 in place. CREATE OR REPLACE preserves its public function OID,
-- dependencies and ACLs while checking ownership before payload lookup/locking.
CREATE OR REPLACE FUNCTION taskq.lock_terminal_effect_job(
    p_job_id uuid,
    p_queue text,
    p_job_type text,
    p_expected_environment text,
    p_expected_installation_id uuid,
    p_allow_production boolean DEFAULT false
) RETURNS TABLE(status text, outcome text, finished_at timestamptz, payload jsonb, workflow_id uuid)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF p_job_id IS NULL OR p_expected_installation_id IS NULL THEN
        RAISE EXCEPTION 'job_id and expected_installation_id are required'
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(p_queue, '') !~ '^[a-z0-9][a-z0-9_]{0,56}$'
       OR COALESCE(p_job_type, '') = '' OR length(p_job_type) > 120 THEN
        RAISE EXCEPTION 'valid queue and job_type are required'
            USING ERRCODE = 'TQ422';
    END IF;
    -- Check bound ownership before target attestation or any payload-bearing
    -- job lookup. This direct call has no continuation provenance bypass.
    PERFORM taskq._check_admission_owner(p_queue);
    PERFORM taskq.attest_target(
        p_expected_environment, p_expected_installation_id, p_allow_production
    );
    -- READ COMMITTED rechecks this predicate after waiting for a redrive lock.
    -- Holding the returned lock prevents redrive/retention until caller commit.
    RETURN QUERY SELECT j.status, j.outcome, j.finished_at, j.payload, j.workflow_id
      FROM taskq.jobs AS j
     WHERE j.id = p_job_id AND j.queue = p_queue AND j.job_type = p_job_type
       AND j.status IN ('succeeded', 'failed', 'cancelled')
     FOR UPDATE OF j;
END $$;

CREATE OR REPLACE FUNCTION taskq._admission_owner_guard()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_target_owner text;
    v_parent_queue text;
    v_parent_status text;
    v_parent_workflow_id uuid;
    v_parent_policy_hash text;
    v_parent_owner text;
    v_chain_prefix text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Direct admission remains role-bound. The only runner path is the
        -- private continuation inserter, whose parent and reserved key are
        -- verified from rows, never from caller state.
        SELECT admission_owner_role INTO v_target_owner
        FROM taskq.queues WHERE name = NEW.queue FOR SHARE;
        IF v_target_owner IS NULL
           OR pg_has_role(session_user, v_target_owner, 'member') THEN
            RETURN NEW;
        END IF;

        IF NEW.parent_job_id IS NULL OR NEW.idempotency_key IS NULL THEN
            RAISE EXCEPTION 'queue admission owner does not permit caller'
                USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
        END IF;
        v_chain_prefix := 'chain:' || lower(NEW.parent_job_id::text) || ':';
        IF NEW.idempotency_key NOT LIKE v_chain_prefix || '%'
           OR substring(NEW.idempotency_key FROM char_length(v_chain_prefix) + 1)
                !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' THEN
            RAISE EXCEPTION 'queue admission owner does not permit caller'
                USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
        END IF;

        SELECT j.queue, j.status, j.workflow_id, j.continuation_policy_hash,
               q.admission_owner_role
        INTO v_parent_queue, v_parent_status, v_parent_workflow_id,
             v_parent_policy_hash, v_parent_owner
        FROM taskq.jobs AS j
        JOIN taskq.queues AS q ON q.name = j.queue
        WHERE j.id = NEW.parent_job_id
        FOR SHARE OF j;
        IF NOT FOUND OR v_parent_status IS DISTINCT FROM 'succeeded'
           OR v_parent_owner IS NULL
           OR v_parent_owner IS DISTINCT FROM v_target_owner THEN
            RAISE EXCEPTION 'queue admission owner does not permit caller'
                USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
        END IF;
        -- Detached continuation keeps all workflow identity null. A member
        -- continuation must carry exactly identity derived from its parent.
        IF (NEW.workflow_id IS NULL AND NEW.continuation_policy_hash IS NULL
            AND NEW.step_key IS NULL)
           OR (NEW.workflow_id IS NOT DISTINCT FROM v_parent_workflow_id
               AND NEW.continuation_policy_hash IS NOT DISTINCT FROM v_parent_policy_hash
               AND NEW.workflow_id IS NOT NULL
               AND NEW.continuation_policy_hash IS NOT NULL
               AND NEW.step_key LIKE 'c:' || lower(NEW.parent_job_id::text) || ':%'
               AND substring(NEW.step_key FROM
                   char_length('c:' || lower(NEW.parent_job_id::text) || ':') + 1
               ) ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'queue admission owner does not permit caller'
            USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
    ELSIF OLD.status IN ('succeeded','failed','cancelled')
          AND NEW.status NOT IN ('succeeded','failed','cancelled') THEN
        IF EXISTS (SELECT 1 FROM taskq.queues
                   WHERE name = OLD.queue AND admission_owner_role IS NOT NULL) THEN
            RAISE EXCEPTION 'terminal jobs on bound queues cannot be redriven'
                USING ERRCODE = 'TQ409', DETAIL = '{"reason":"bound_queue_redrive"}';
        END IF;
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION taskq._admission_owner_guard() OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._admission_owner_guard() FROM PUBLIC;

-- Preserve bind API identity and ACL while closing admission-state binding gaps.
CREATE OR REPLACE FUNCTION taskq.bind_queue_admission_owner(
    p_queue text, p_owner_role text,
    p_expected_environment text, p_expected_installation_id uuid,
    p_allow_production boolean DEFAULT false
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_bound text; v_count bigint;
BEGIN
    IF NOT pg_has_role(session_user, 'taskq_operator', 'member')
       AND session_user <> 'taskq_owner' THEN
        RAISE EXCEPTION 'queue admission binding requires operator role' USING ERRCODE = 'TQ403';
    END IF;
    IF p_owner_role IS NULL OR p_owner_role !~ '^[a-z_][a-z0-9_$]{0,62}$'
       OR p_owner_role IN ('taskq_owner','taskq_producer','taskq_runner','taskq_observer',
                           'taskq_operator','taskq_housekeeper')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = p_owner_role)
       OR NOT pg_has_role(p_owner_role, 'taskq_producer', 'member') THEN
        RAISE EXCEPTION 'owner role must be an existing producer member' USING ERRCODE = 'TQ422';
    END IF;
    PERFORM taskq.attest_target(
        p_expected_environment, p_expected_installation_id, p_allow_production);
    PERFORM pg_advisory_xact_lock(hashtextextended(p_queue, 0));
    SELECT admission_owner_role INTO v_bound FROM taskq.queues
    WHERE name = p_queue FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown queue %', p_queue USING ERRCODE = 'TQ001'; END IF;
    IF v_bound IS NOT NULL THEN
        RAISE EXCEPTION 'queue admission owner is immutable' USING ERRCODE = 'TQ409';
    END IF;
    SELECT count(*) INTO v_count FROM taskq.jobs WHERE queue = p_queue;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'queue must be empty before binding' USING ERRCODE = 'TQ409';
    END IF;
    SELECT count(*) INTO v_count FROM taskq.admissions WHERE queue = p_queue;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'queue must have no admission state before binding' USING ERRCODE = 'TQ409';
    END IF;
    UPDATE taskq.queues SET admission_owner_role = p_owner_role, updated_at = now()
    WHERE name = p_queue;
    RETURN p_owner_role;
END $$;
ALTER FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean) TO taskq_operator;

-- Add the shared owner guard to published admission bodies in place. This
-- preserves their function OIDs, dependencies and ACLs without editing 0007.
DO $$
DECLARE
    v_identity regprocedure;
    v_definition text;
    v_begin integer;
BEGIN
    FOR v_identity IN
        SELECT unnest(ARRAY[
            'taskq.reserve_admission(text,text,text,uuid,integer,integer)'::regprocedure,
            'taskq.finish_admission(text,text,uuid,jsonb,jsonb)'::regprocedure,
            'taskq.cancel_admission(text,text,uuid)'::regprocedure
        ])
    LOOP
        SELECT pg_get_functiondef(v_identity::oid) INTO v_definition;
        v_begin := strpos(v_definition, E'\nBEGIN');
        IF v_definition IS NULL OR v_begin IS NULL OR v_begin = 0
           OR substr(v_definition, v_begin + 1, 5) <> 'BEGIN' THEN
            RAISE EXCEPTION '0047 admission function has no executable BEGIN anchor: %',
                v_identity USING ERRCODE = 'TQ500';
        END IF;
        v_definition := left(v_definition, v_begin + 5)
            || E'\n    PERFORM taskq._check_admission_owner(p_queue);'
            || substr(v_definition, v_begin + 6);
        EXECUTE v_definition;
    END LOOP;
END $$;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.6.11"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
