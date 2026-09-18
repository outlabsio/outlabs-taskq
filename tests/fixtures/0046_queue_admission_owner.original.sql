-- outlabs-taskq — migration 0046: immutable queue admission ownership
-- SQL contract 0.6.10.  Queue binding is generic; applications supply role names.
-- The trigger uses session_user because enqueue/update entry points are
-- SECURITY DEFINER.  Binding is one-shot and requires an operator, while jobs
-- remain writable only through existing TaskQ functions.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta
    WHERE key = 'contract_version' FOR UPDATE;
    IF v_contract IS DISTINCT FROM '"0.6.9"'::jsonb THEN
        RAISE EXCEPTION '0046 requires SQL contract 0.6.9, found %', v_contract
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

ALTER TABLE taskq.queues
    ADD COLUMN admission_owner_role text
    CHECK (admission_owner_role IS NULL OR admission_owner_role ~ '^[a-z_][a-z0-9_$]{0,62}$');

CREATE OR REPLACE FUNCTION taskq._check_admission_owner(
    p_queue text, p_workflow_id uuid DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_role text;
BEGIN
    -- Trigger is shared by every historical and current admission function;
    -- locking here avoids edits to published migrations and closes bind races.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_queue, 0));
    SELECT admission_owner_role INTO v_role
    FROM taskq.queues WHERE name = p_queue;
    IF v_role IS NULL THEN RETURN; END IF;
    -- Every admission path must hold the queue row lock before this check.
    -- Continuations are API-owned and therefore use the same bound producer role.
    IF NOT pg_has_role(session_user, v_role, 'member') THEN
        RAISE EXCEPTION 'queue admission owner does not permit caller'
            USING ERRCODE = 'TQ425', DETAIL = '{"reason":"queue_admission_owner"}';
    END IF;
END $$;
ALTER FUNCTION taskq._check_admission_owner(text,uuid) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._check_admission_owner(text,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq._admission_owner_guard()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM taskq._check_admission_owner(NEW.queue, NEW.workflow_id);
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

CREATE TRIGGER jobs_admission_owner_guard
BEFORE INSERT OR UPDATE OF status ON taskq.jobs
FOR EACH ROW EXECUTE FUNCTION taskq._admission_owner_guard();

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
    UPDATE taskq.queues SET admission_owner_role = p_owner_role, updated_at = now()
    WHERE name = p_queue;
    RETURN p_owner_role;
END $$;
ALTER FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.bind_queue_admission_owner(text,text,text,uuid,boolean) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.get_queue_admission_owner(p_queue text)
RETURNS TABLE(queue text, owner_role text, max_depth bigint, depth bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE q taskq.queues%ROWTYPE; v_depth bigint;
BEGIN
    SELECT * INTO q FROM taskq.queues WHERE name = p_queue FOR SHARE;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT count(*) INTO v_depth FROM taskq.jobs j
    WHERE j.queue = q.name AND j.status IN ('blocked','queued','running');
    RETURN QUERY SELECT q.name, q.admission_owner_role, q.max_depth::bigint, v_depth;
END $$;
ALTER FUNCTION taskq.get_queue_admission_owner(text) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq.get_queue_admission_owner(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_admission_owner(text) TO taskq_observer;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.6.10"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
