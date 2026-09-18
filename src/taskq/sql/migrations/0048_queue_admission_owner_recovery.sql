-- outlabs-taskq — queue admission ownership replay, scheduler and recovery closure
-- SQL contract 0.6.12. This is a forward-only correction over 0046/0047:
-- published migration bytes and their ledger rows remain immutable.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta
    WHERE key = 'contract_version' FOR UPDATE;
    IF v_contract IS DISTINCT FROM '"0.6.11"'::jsonb THEN
        RAISE EXCEPTION '0048 requires SQL contract 0.6.11, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

-- Role names are mutable. Keep the 0046 text column as a diagnostic snapshot,
-- but enforce and compare the PostgreSQL role OID from this migration onward.
ALTER TABLE taskq.queues ADD COLUMN admission_owner_oid oid;

UPDATE taskq.queues AS q
SET admission_owner_oid = r.oid
FROM pg_catalog.pg_roles AS r
WHERE q.admission_owner_role = r.rolname;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM taskq.queues
        WHERE admission_owner_role IS NOT NULL AND admission_owner_oid IS NULL
    ) THEN
        RAISE EXCEPTION '0048 cannot resolve an existing queue admission owner role'
            USING ERRCODE = 'TQ500',
                  DETAIL = '{"reason":"queue_admission_owner_role_missing"}';
    END IF;
END $$;

ALTER TABLE taskq.queues
    ADD CONSTRAINT queues_admission_owner_shape_ck CHECK (
        (admission_owner_role IS NULL) = (admission_owner_oid IS NULL)
    );

ALTER TABLE taskq.schedules ADD COLUMN admission_owner_oid oid;

UPDATE taskq.schedules AS s
SET admission_owner_oid = q.admission_owner_oid
FROM taskq.queues AS q
WHERE s.target->>'kind' = 'job'
  AND s.target->>'queue' = q.name;

CREATE FUNCTION taskq._resolve_admission_owner_role(p_owner_role text)
RETURNS oid
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_oid oid;
BEGIN
    IF p_owner_role IS NULL OR p_owner_role !~ '^[a-z_][a-z0-9_$]{0,62}$' THEN
        RAISE EXCEPTION 'owner role must be an existing producer member'
            USING ERRCODE = 'TQ422';
    END IF;
    SELECT oid INTO v_oid FROM pg_catalog.pg_roles WHERE rolname = p_owner_role;
    IF v_oid IS NULL
       OR v_oid = ANY(ARRAY[
            'taskq_owner'::regrole::oid,
            'taskq_producer'::regrole::oid,
            'taskq_runner'::regrole::oid,
            'taskq_observer'::regrole::oid,
            'taskq_operator'::regrole::oid,
            'taskq_housekeeper'::regrole::oid
       ])
       OR NOT pg_has_role(v_oid, 'taskq_producer', 'member') THEN
        RAISE EXCEPTION 'owner role must be an existing producer member'
            USING ERRCODE = 'TQ422';
    END IF;
    RETURN v_oid;
END $$;
ALTER FUNCTION taskq._resolve_admission_owner_role(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._resolve_admission_owner_role(text) FROM PUBLIC;

-- Admissions take the weakest queue row lock. KEY SHARE does not block normal
-- queue configuration updates, but it does conflict with the explicit
-- FOR UPDATE used by bind/adopt/rotate and therefore closes the ownership race.
CREATE OR REPLACE FUNCTION taskq._check_admission_owner(
    p_queue text, p_workflow_id uuid DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_role_oid oid;
BEGIN
    SELECT admission_owner_oid INTO v_role_oid
    FROM taskq.queues WHERE name = p_queue FOR KEY SHARE;
    -- Some read/fence entry points intentionally return no row for a
    -- mismatched queue. Preserve that public contract here; admission entry
    -- points perform their own established TQ001 validation downstream.
    IF NOT FOUND THEN RETURN; END IF;
    IF v_role_oid IS NULL THEN RETURN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE oid = v_role_oid) THEN
        RAISE EXCEPTION 'queue admission owner role is unavailable'
            USING ERRCODE = 'TQ425',
                  DETAIL = '{"reason":"queue_admission_owner_role_missing"}';
    END IF;
    IF NOT pg_has_role(session_user, v_role_oid, 'member') THEN
        RAISE EXCEPTION 'queue admission owner does not permit caller'
            USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
    END IF;
END $$;
ALTER FUNCTION taskq._check_admission_owner(text,uuid) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._check_admission_owner(text,uuid) FROM PUBLIC;

-- Schedule ownership is derived from the target queue, never from the
-- housekeeper login or caller-supplied application identity.
CREATE FUNCTION taskq._schedule_admission_owner_guard()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF NEW.target->>'kind' = 'job' THEN
        SELECT q.admission_owner_oid INTO NEW.admission_owner_oid
        FROM taskq.queues AS q
        WHERE q.name = NEW.target->>'queue'
        FOR KEY SHARE;
    ELSE
        NEW.admission_owner_oid := NULL;
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION taskq._schedule_admission_owner_guard() OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._schedule_admission_owner_guard() FROM PUBLIC;

CREATE TRIGGER schedules_admission_owner_guard
BEFORE INSERT OR UPDATE OF target ON taskq.schedules
FOR EACH ROW EXECUTE FUNCTION taskq._schedule_admission_owner_guard();

CREATE OR REPLACE FUNCTION taskq._admission_owner_guard()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_target_owner oid;
    v_parent_status text;
    v_parent_workflow_id uuid;
    v_parent_policy_hash text;
    v_parent_owner oid;
    v_chain_prefix text;
    v_schedule_header jsonb;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT admission_owner_oid INTO v_target_owner
        FROM taskq.queues WHERE name = NEW.queue FOR KEY SHARE;
        IF v_target_owner IS NULL THEN RETURN NEW; END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE oid = v_target_owner) THEN
            RAISE EXCEPTION 'queue admission owner role is unavailable'
                USING ERRCODE = 'TQ425',
                      DETAIL = '{"reason":"queue_admission_owner_role_missing"}';
        END IF;
        IF pg_has_role(session_user, v_target_owner, 'member') THEN
            RETURN NEW;
        END IF;

        -- A standalone housekeeper may admit only the schedule occurrence row
        -- created in this same transaction by fire_schedule. Public producers
        -- cannot manufacture that uncommitted row or write the source tables.
        v_schedule_header := NEW.headers->'taskq_schedule';
        IF pg_has_role(session_user, 'taskq_housekeeper', 'member')
           AND jsonb_typeof(v_schedule_header) = 'object'
           AND COALESCE(v_schedule_header->>'occurrence_id', '') ~
               '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           AND EXISTS (
               SELECT 1
               FROM taskq.schedule_occurrences AS o
               JOIN taskq.schedules AS s ON s.id = o.schedule_id
               WHERE o.occurrence_id = (v_schedule_header->>'occurrence_id')::uuid
                 AND o.outcome = 'fired'
                 AND o.job_id IS NULL
                 AND o.xmin = pg_current_xact_id()::text::xid
                 AND s.id::text = v_schedule_header->>'schedule_id'
                 AND s.admission_owner_oid = v_target_owner
                 AND s.target->>'kind' = 'job'
                 AND s.target->>'queue' = NEW.queue
                 AND s.target->>'job_type' = NEW.job_type
           ) THEN
            RETURN NEW;
        END IF;

        -- Runner continuations retain their existing row-backed provenance.
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

        SELECT j.status, j.workflow_id, j.continuation_policy_hash,
               q.admission_owner_oid
        INTO v_parent_status, v_parent_workflow_id,
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
        IF EXISTS (
            SELECT 1 FROM taskq.queues
            WHERE name = OLD.queue AND admission_owner_oid IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'terminal jobs on bound queues cannot be redriven'
                USING ERRCODE = 'TQ409', DETAIL = '{"reason":"bound_queue_redrive"}';
        END IF;
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION taskq._admission_owner_guard() OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._admission_owner_guard() FROM PUBLIC;

-- The original pause/resume functions pre-locked the queue FOR UPDATE even
-- though they change no key column. Use one conditional UPDATE instead. Its
-- NO KEY UPDATE lock is compatible with admission's KEY SHARE lock, so an
-- operator can close the claim valve while a producer transaction is open.
CREATE OR REPLACE FUNCTION taskq.pause_queue(
    p_name text, p_actor text, p_reason text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    UPDATE taskq.queues
    SET paused_at = now(), pause_reason = p_reason, updated_at = now()
    WHERE name = p_name AND paused_at IS NULL;
    IF FOUND THEN RETURN 'paused'; END IF;
    IF EXISTS (SELECT 1 FROM taskq.queues WHERE name = p_name) THEN
        RETURN 'already_paused';
    END IF;
    RAISE EXCEPTION 'taskq: unknown queue %', p_name USING ERRCODE = 'TQ001';
END $$;
ALTER FUNCTION taskq.pause_queue(text,text,text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.pause_queue(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.pause_queue(text,text,text) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.resume_queue(p_name text, p_actor text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_ramp integer;
BEGIN
    UPDATE taskq.queues
    SET paused_at = NULL, pause_reason = NULL, updated_at = now()
    WHERE name = p_name AND paused_at IS NOT NULL
    RETURNING ramp_seconds INTO v_ramp;
    IF FOUND THEN
        IF v_ramp IS NOT NULL THEN
            INSERT INTO taskq.queue_flow (queue, ramp_started_at, updated_at)
            VALUES (p_name, now(), now())
            ON CONFLICT (queue) DO UPDATE
            SET ramp_started_at = now(), updated_at = now();
        END IF;
        RETURN 'resumed';
    END IF;
    IF EXISTS (SELECT 1 FROM taskq.queues WHERE name = p_name) THEN
        RETURN 'already_resumed';
    END IF;
    RAISE EXCEPTION 'taskq: unknown queue %', p_name USING ERRCODE = 'TQ001';
END $$;
ALTER FUNCTION taskq.resume_queue(text,text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.resume_queue(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.resume_queue(text,text) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.bind_queue_admission_owner(
    p_queue text, p_owner_role text,
    p_expected_environment text, p_expected_installation_id uuid,
    p_allow_production boolean DEFAULT false
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_bound oid; v_new oid; v_count bigint;
BEGIN
    IF NOT pg_has_role(session_user, 'taskq_operator', 'member')
       AND session_user <> 'taskq_owner' THEN
        RAISE EXCEPTION 'queue admission binding requires operator role' USING ERRCODE = 'TQ403';
    END IF;
    v_new := taskq._resolve_admission_owner_role(p_owner_role);
    PERFORM taskq.attest_target(
        p_expected_environment, p_expected_installation_id, p_allow_production);
    SELECT admission_owner_oid INTO v_bound FROM taskq.queues
    WHERE name = p_queue FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;
    IF v_bound IS NOT NULL THEN
        RAISE EXCEPTION 'queue admission owner is already set' USING ERRCODE = 'TQ409';
    END IF;
    SELECT count(*) INTO v_count FROM taskq.jobs WHERE queue = p_queue;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'queue must be empty before binding' USING ERRCODE = 'TQ409';
    END IF;
    SELECT count(*) INTO v_count FROM taskq.admissions WHERE queue = p_queue;
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'queue must have no admission state before binding' USING ERRCODE = 'TQ409';
    END IF;
    IF EXISTS (
        SELECT 1 FROM taskq.schedules
        WHERE target->>'kind' = 'job' AND target->>'queue' = p_queue AND state = 'active'
    ) THEN
        RAISE EXCEPTION 'active schedules must be paused before binding'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_active_schedule"}';
    END IF;
    UPDATE taskq.queues
    SET admission_owner_role = p_owner_role,
        admission_owner_oid = v_new,
        updated_at = now()
    WHERE name = p_queue;
    UPDATE taskq.schedules
    SET admission_owner_oid = v_new, updated_at = now()
    WHERE target->>'kind' = 'job' AND target->>'queue' = p_queue;
    PERFORM taskq._audit_queue(
        p_queue, 'admission_owner_bound', session_user,
        jsonb_build_object('new_owner_role',p_owner_role,'new_owner_oid',v_new::bigint)
    );
    RETURN p_owner_role;
END $$;
ALTER FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean)
    OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean)
    TO taskq_operator;

CREATE FUNCTION taskq.adopt_queue_admission_owner(
    p_queue text, p_owner_role text, p_actor text, p_reason text,
    p_expected_environment text, p_expected_installation_id uuid,
    p_allow_production boolean DEFAULT false
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_queue taskq.queues%ROWTYPE; v_new oid;
BEGIN
    IF NOT pg_has_role(session_user, 'taskq_operator', 'member')
       AND session_user <> 'taskq_owner' THEN
        RAISE EXCEPTION 'queue admission adoption requires operator role' USING ERRCODE = 'TQ403';
    END IF;
    IF p_actor IS NULL OR octet_length(p_actor) NOT BETWEEN 1 AND 255
       OR p_reason IS NULL OR octet_length(p_reason) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'adoption actor and reason are required' USING ERRCODE = 'TQ422';
    END IF;
    v_new := taskq._resolve_admission_owner_role(p_owner_role);
    PERFORM taskq.attest_target(
        p_expected_environment, p_expected_installation_id, p_allow_production);
    SELECT * INTO v_queue FROM taskq.queues WHERE name = p_queue FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;
    IF v_queue.admission_owner_oid IS NOT NULL THEN
        RAISE EXCEPTION 'queue admission owner is already set' USING ERRCODE = 'TQ409';
    END IF;
    IF v_queue.paused_at IS NULL THEN
        RAISE EXCEPTION 'queue must be paused for ownership adoption'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_not_paused"}';
    END IF;
    IF EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE queue = p_queue AND status IN ('blocked','queued','running')
    ) OR EXISTS (
        SELECT 1 FROM taskq.admissions WHERE queue = p_queue AND state = 'reserved'
    ) THEN
        RAISE EXCEPTION 'queue must have no active jobs or reservations for ownership adoption'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_not_quiesced"}';
    END IF;
    IF EXISTS (
        SELECT 1 FROM taskq.schedules
        WHERE target->>'kind' = 'job' AND target->>'queue' = p_queue AND state = 'active'
    ) THEN
        RAISE EXCEPTION 'active schedules must be paused for ownership adoption'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_active_schedule"}';
    END IF;
    UPDATE taskq.queues
    SET admission_owner_role = p_owner_role,
        admission_owner_oid = v_new,
        updated_at = now()
    WHERE name = p_queue;
    UPDATE taskq.schedules
    SET admission_owner_oid = v_new, updated_at = now()
    WHERE target->>'kind' = 'job' AND target->>'queue' = p_queue;
    PERFORM taskq._audit_queue(
        p_queue, 'admission_owner_adopted', p_actor,
        jsonb_build_object(
            'new_owner_role',p_owner_role,'new_owner_oid',v_new::bigint,'reason',p_reason
        )
    );
    RETURN p_owner_role;
END $$;
ALTER FUNCTION taskq.adopt_queue_admission_owner(text,text,text,text,text,uuid,boolean)
    OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.adopt_queue_admission_owner(text,text,text,text,text,uuid,boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.adopt_queue_admission_owner(text,text,text,text,text,uuid,boolean)
    TO taskq_operator;

CREATE FUNCTION taskq.rotate_queue_admission_owner(
    p_queue text, p_owner_role text, p_actor text, p_reason text,
    p_expected_environment text, p_expected_installation_id uuid,
    p_allow_production boolean DEFAULT false
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_queue taskq.queues%ROWTYPE; v_new oid; v_old_name text;
BEGIN
    IF NOT pg_has_role(session_user, 'taskq_operator', 'member')
       AND session_user <> 'taskq_owner' THEN
        RAISE EXCEPTION 'queue admission rotation requires operator role' USING ERRCODE = 'TQ403';
    END IF;
    IF p_actor IS NULL OR octet_length(p_actor) NOT BETWEEN 1 AND 255
       OR p_reason IS NULL OR octet_length(p_reason) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'rotation actor and reason are required' USING ERRCODE = 'TQ422';
    END IF;
    v_new := taskq._resolve_admission_owner_role(p_owner_role);
    PERFORM taskq.attest_target(
        p_expected_environment, p_expected_installation_id, p_allow_production);
    SELECT * INTO v_queue FROM taskq.queues WHERE name = p_queue FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;
    IF v_queue.admission_owner_oid IS NULL THEN
        RAISE EXCEPTION 'queue admission owner is not set' USING ERRCODE = 'TQ409';
    END IF;
    IF v_queue.admission_owner_oid = v_new THEN
        RAISE EXCEPTION 'queue admission owner is unchanged' USING ERRCODE = 'TQ409';
    END IF;
    IF v_queue.paused_at IS NULL THEN
        RAISE EXCEPTION 'queue must be paused for ownership rotation'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_not_paused"}';
    END IF;
    IF EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE queue = p_queue AND status IN ('blocked','queued','running')
    ) OR EXISTS (
        SELECT 1 FROM taskq.admissions WHERE queue = p_queue AND state = 'reserved'
    ) THEN
        RAISE EXCEPTION 'queue must have no active jobs or reservations for ownership rotation'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_not_quiesced"}';
    END IF;
    IF EXISTS (
        SELECT 1 FROM taskq.schedules
        WHERE target->>'kind' = 'job' AND target->>'queue' = p_queue AND state = 'active'
    ) THEN
        RAISE EXCEPTION 'active schedules must be paused for ownership rotation'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"queue_owner_active_schedule"}';
    END IF;
    SELECT rolname INTO v_old_name FROM pg_catalog.pg_roles
    WHERE oid = v_queue.admission_owner_oid;
    UPDATE taskq.queues
    SET admission_owner_role = p_owner_role,
        admission_owner_oid = v_new,
        updated_at = now()
    WHERE name = p_queue;
    UPDATE taskq.schedules
    SET admission_owner_oid = v_new, updated_at = now()
    WHERE target->>'kind' = 'job' AND target->>'queue' = p_queue;
    PERFORM taskq._audit_queue(
        p_queue, 'admission_owner_rotated', p_actor,
        jsonb_build_object(
            'old_owner_role',COALESCE(v_old_name,v_queue.admission_owner_role),
            'old_owner_oid',v_queue.admission_owner_oid::bigint,
            'new_owner_role',p_owner_role,
            'new_owner_oid',v_new::bigint,
            'reason',p_reason
        )
    );
    RETURN p_owner_role;
END $$;
ALTER FUNCTION taskq.rotate_queue_admission_owner(text,text,text,text,text,uuid,boolean)
    OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.rotate_queue_admission_owner(text,text,text,text,text,uuid,boolean)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.rotate_queue_admission_owner(text,text,text,text,text,uuid,boolean)
    TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.get_queue_admission_owner(p_queue text)
RETURNS TABLE(queue text, owner_role text, max_depth bigint, depth bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE q taskq.queues%ROWTYPE; v_depth bigint; v_current_name text;
BEGIN
    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT rolname INTO v_current_name FROM pg_catalog.pg_roles
    WHERE oid = q.admission_owner_oid;
    SELECT count(*) INTO v_depth FROM taskq.jobs AS j
    WHERE j.queue = q.name AND j.status IN ('blocked','queued','running');
    RETURN QUERY SELECT q.name, COALESCE(v_current_name,q.admission_owner_role)::text,
                        q.max_depth::bigint, v_depth;
END $$;
ALTER FUNCTION taskq.get_queue_admission_owner(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.get_queue_admission_owner(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_admission_owner(text) TO taskq_observer;

CREATE FUNCTION taskq.get_queue_admission_owner_identity(p_queue text)
RETURNS TABLE(
    queue text, owner_role text, owner_oid oid, owner_present boolean,
    max_depth bigint, depth bigint
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    RETURN QUERY
    SELECT q.name,
           COALESCE(r.rolname::text,q.admission_owner_role),
           q.admission_owner_oid,
           r.oid IS NOT NULL,
           q.max_depth::bigint,
           (SELECT count(*) FROM taskq.jobs AS j
            WHERE j.queue = q.name AND j.status IN ('blocked','queued','running'))
    FROM taskq.queues AS q
    LEFT JOIN pg_catalog.pg_roles AS r ON r.oid = q.admission_owner_oid
    WHERE q.name = p_queue;
END $$;
ALTER FUNCTION taskq.get_queue_admission_owner_identity(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.get_queue_admission_owner_identity(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_admission_owner_identity(text) TO taskq_observer;

-- try_enqueue can return an existing id before enqueue and its trigger run.
-- Guard the function before that read. enqueue_many has workflow replay paths
-- with the same property, so guard it once before processing the batch.
DO $$
DECLARE v_identity regprocedure; v_definition text; v_begin integer;
BEGIN
    FOR v_identity IN
        SELECT unnest(ARRAY[
            'taskq.try_enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)'::regprocedure,
            'taskq.enqueue_many(text,jsonb)'::regprocedure
        ])
    LOOP
        SELECT pg_get_functiondef(v_identity::oid) INTO v_definition;
        v_begin := strpos(v_definition, E'\nBEGIN');
        IF v_definition IS NULL OR v_begin = 0 THEN
            RAISE EXCEPTION '0048 admission function has no executable BEGIN anchor: %',
                v_identity USING ERRCODE = 'TQ500';
        END IF;
        v_definition := left(v_definition, v_begin + 5)
            || E'\n    PERFORM taskq._check_admission_owner(p_queue);'
            || substr(v_definition, v_begin + 6);
        EXECUTE v_definition;
    END LOOP;
END $$;

-- enqueue's insert trigger already fences ordinary idempotency conflicts. Its
-- pre-insert workflow step replay needs an explicit guard before returning the
-- existing job, while the scheduler's non-workflow enqueue remains available
-- to the row-backed schedule provenance below.
DO $$
DECLARE
    v_identity regprocedure :=
        'taskq.enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text)'::regprocedure;
    v_definition text;
    v_anchor text := E'    IF p_workflow_id IS NOT NULL THEN\n        SELECT * INTO v_workflow';
BEGIN
    SELECT pg_get_functiondef(v_identity::oid) INTO v_definition;
    IF strpos(v_definition, v_anchor) = 0 THEN
        RAISE EXCEPTION '0048 enqueue workflow replay anchor is absent'
            USING ERRCODE = 'TQ500';
    END IF;
    v_definition := replace(
        v_definition,
        v_anchor,
        E'    IF p_workflow_id IS NOT NULL THEN\n        PERFORM taskq._check_admission_owner(p_queue, p_workflow_id);\n        SELECT * INTO v_workflow'
    );
    EXECUTE v_definition;
END $$;

-- fire_schedule now creates the occurrence row before enqueue and attaches the
-- job afterward. The uncommitted occurrence is the scheduler's non-forgeable
-- admission proof; the entire change still commits or rolls back atomically.
DO $$
DECLARE
    v_identity regprocedure :=
        'taskq.fire_schedule(uuid,uuid,bigint,timestamptz[],timestamptz)'::regprocedure;
    v_definition text;
    v_before text := E'        END IF;\n\n        IF v_occurrence_outcome = ''fired''\n           AND v_schedule.target->>''kind'' = ''maintenance'' THEN';
    v_after text := E'        END IF;\n\n        INSERT INTO taskq.schedule_occurrences(\n            schedule_id, due_at, occurrence_id, decision_id, outcome, job_id\n        ) VALUES (\n            v_schedule.id, v_due, v_occurrence_id, v_decision_id,\n            v_occurrence_outcome, NULL\n        );\n\n        IF v_occurrence_outcome = ''fired''\n           AND v_schedule.target->>''kind'' = ''maintenance'' THEN';
    v_insert text := E'        INSERT INTO taskq.schedule_occurrences(\n            schedule_id, due_at, occurrence_id, decision_id, outcome, job_id\n        ) VALUES (\n            v_schedule.id, v_due, v_occurrence_id, v_decision_id,\n            v_occurrence_outcome, v_job_id\n        );';
    v_update text := E'        UPDATE taskq.schedule_occurrences\n        SET job_id = v_job_id\n        WHERE occurrence_id = v_occurrence_id;';
BEGIN
    SELECT pg_get_functiondef(v_identity::oid) INTO v_definition;
    IF strpos(v_definition, v_before) = 0 OR strpos(v_definition, v_insert) = 0 THEN
        RAISE EXCEPTION '0048 fire_schedule provenance anchor is absent'
            USING ERRCODE = 'TQ500';
    END IF;
    v_definition := replace(v_definition, v_before, v_after);
    v_definition := replace(v_definition, v_insert, v_update);
    EXECUTE v_definition;
END $$;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.6.12"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
