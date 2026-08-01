-- outlabs-taskq — migration 0009: sealed workflows and dependencies
-- SQL contract 0.2.1 / ADR-026 / Protocol document revision 1.0.10.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.0"'::jsonb THEN
        RAISE EXCEPTION '0009 requires SQL contract 0.2.0, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","followups","read_model_list_ready"]}'::jsonb THEN
        RAISE EXCEPTION '0009 requires the exact 0008 capability set, found %', v_capabilities;
    END IF;
    IF EXISTS (SELECT 1 FROM taskq.workflows)
       OR EXISTS (SELECT 1 FROM taskq.job_deps)
       OR EXISTS (
           SELECT 1 FROM taskq.jobs
           WHERE workflow_id IS NOT NULL OR step_key IS NOT NULL OR pending_deps <> 0
       ) THEN
        RAISE EXCEPTION '0009 requires empty inactive workflow/dependency state';
    END IF;
END $$;

CREATE TYPE taskq.workflow_result AS (
    outcome text,
    workflow_id uuid,
    status text
);
ALTER TYPE taskq.workflow_result OWNER TO taskq_owner;

CREATE TYPE taskq.workflow_auth_projection AS (
    workflow_id uuid,
    declared_queues text[]
);
ALTER TYPE taskq.workflow_auth_projection OWNER TO taskq_owner;

ALTER TABLE taskq.workflows
    ADD COLUMN declared_queues text[] NOT NULL,
    ADD COLUMN sealed_at timestamptz,
    ADD COLUMN sealed_by text,
    ADD COLUMN cancel_requested_at timestamptz,
    ADD COLUMN cancel_requested_by text,
    ADD COLUMN cancel_reason text;

ALTER TABLE taskq.workflows
    ADD CONSTRAINT workflows_declared_queues_ck CHECK (
        cardinality(declared_queues) BETWEEN 1 AND 32
    ),
    ADD CONSTRAINT workflows_sealed_shape_ck CHECK (
        (sealed_at IS NULL) = (sealed_by IS NULL)
    ),
    ADD CONSTRAINT workflows_cancel_shape_ck CHECK (
        (cancel_requested_at IS NULL) = (cancel_requested_by IS NULL)
    ),
    ADD CONSTRAINT workflows_finished_shape_ck CHECK (
        (status = 'running') = (finished_at IS NULL)
    );

ALTER TABLE taskq.jobs
    ADD COLUMN workflow_intent_hash text,
    ADD CONSTRAINT jobs_workflow_member_shape_ck CHECK (
        (workflow_id IS NULL AND step_key IS NULL AND workflow_intent_hash IS NULL)
        OR (
            workflow_id IS NOT NULL
            AND step_key IS NOT NULL
            AND workflow_intent_hash ~ '^[0-9a-f]{64}$'
        )
    );

DROP INDEX taskq.jobs_workflow_idx;
DROP INDEX taskq.workflows_open_idx;
DROP INDEX taskq.job_deps_reverse_idx;
CREATE INDEX job_deps_reverse_idx
    ON taskq.job_deps (depends_on, job_id);
CREATE UNIQUE INDEX jobs_workflow_step_uq
    ON taskq.jobs (workflow_id, step_key)
    WHERE workflow_id IS NOT NULL;
CREATE INDEX jobs_workflow_state_idx
    ON taskq.jobs (workflow_id, status, id)
    WHERE workflow_id IS NOT NULL;
CREATE INDEX workflows_finalize_idx
    ON taskq.workflows (updated_at, id)
    WHERE sealed_at IS NOT NULL AND status = 'running';
CREATE INDEX workflows_cancel_idx
    ON taskq.workflows (cancel_requested_at, id)
    WHERE cancel_requested_at IS NOT NULL AND status = 'running';

CREATE OR REPLACE FUNCTION taskq.create_workflow(
    p_workflow_key text,
    p_kind text,
    p_params jsonb,
    p_declared_queues text[],
    p_actor text
) RETURNS taskq.workflow_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_id uuid;
    v_existing taskq.workflows%ROWTYPE;
    v_queues text[];
BEGIN
    IF p_workflow_key IS NULL
       OR octet_length(p_workflow_key) NOT BETWEEN 1 AND 255 THEN
        RAISE EXCEPTION 'workflow_key must be 1..255 UTF-8 bytes'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_kind IS NULL OR p_kind NOT IN ('dag', 'batch') THEN
        RAISE EXCEPTION 'workflow kind must be dag or batch'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_params IS NULL OR jsonb_typeof(p_params) <> 'object'
       OR octet_length(p_params::text) > 65536 THEN
        RAISE EXCEPTION 'workflow params must be an object of at most 64KB'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_actor IS NULL OR p_actor = '' THEN
        RAISE EXCEPTION 'workflow actor is required' USING ERRCODE = 'TQ422';
    END IF;
    IF p_declared_queues IS NULL
       OR cardinality(p_declared_queues) NOT BETWEEN 1 AND 32
       OR EXISTS (SELECT 1 FROM unnest(p_declared_queues) AS q(name) WHERE name IS NULL)
       OR (SELECT count(DISTINCT name) FROM unnest(p_declared_queues) AS q(name))
            <> cardinality(p_declared_queues) THEN
        RAISE EXCEPTION 'declared_queues must contain 1..32 distinct queues'
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT array_agg(name ORDER BY name) INTO v_queues
    FROM unnest(p_declared_queues) AS q(name);
    IF EXISTS (
        SELECT 1 FROM unnest(v_queues) AS q(name)
        WHERE NOT EXISTS (SELECT 1 FROM taskq.queues WHERE taskq.queues.name = q.name)
    ) THEN
        RAISE EXCEPTION 'workflow names an unknown queue' USING ERRCODE = 'TQ001';
    END IF;

    v_id := taskq.uuid7();
    INSERT INTO taskq.workflows (
        id, workflow_key, kind, status, params, stats, created_by,
        declared_queues
    ) VALUES (
        v_id, p_workflow_key, p_kind, 'running', p_params, '{}'::jsonb,
        p_actor, v_queues
    )
    ON CONFLICT (workflow_key) DO NOTHING;
    IF FOUND THEN
        RETURN ('created', v_id, 'running')::taskq.workflow_result;
    END IF;

    SELECT * INTO v_existing
    FROM taskq.workflows
    WHERE workflow_key = p_workflow_key
    FOR UPDATE;
    IF v_existing.kind IS DISTINCT FROM p_kind
       OR v_existing.params IS DISTINCT FROM p_params
       OR v_existing.declared_queues IS DISTINCT FROM v_queues THEN
        RAISE EXCEPTION 'workflow idempotency identity mismatch'
            USING ERRCODE = 'TQ409',
                  DETAIL = '{"reason":"workflow_mismatch"}';
    END IF;
    RETURN ('existed', v_existing.id, v_existing.status)::taskq.workflow_result;
END $$;
ALTER FUNCTION taskq.create_workflow(text,text,jsonb,text[],text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.create_workflow(text,text,jsonb,text[],text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.create_workflow(text,text,jsonb,text[],text) TO taskq_producer;

CREATE OR REPLACE FUNCTION taskq.get_workflow_authorization_projection(
    p_workflow_id uuid
) RETURNS taskq.workflow_auth_projection
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_result taskq.workflow_auth_projection;
BEGIN
    SELECT w.id, w.declared_queues
    INTO v_result
    FROM taskq.workflows AS w
    WHERE w.id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such workflow'
            USING ERRCODE = 'TQ001';
    END IF;
    RETURN v_result;
END $$;
ALTER FUNCTION taskq.get_workflow_authorization_projection(uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_workflow_authorization_projection(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_workflow_authorization_projection(uuid) TO taskq_observer;

CREATE OR REPLACE FUNCTION taskq.finalize_workflows(
    p_limit integer DEFAULT 100
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_workflow taskq.workflows%ROWTYPE;
    v_status text;
    v_n integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'limit must be 1..1000' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_workflow IN
        SELECT * FROM taskq.workflows
        WHERE sealed_at IS NOT NULL AND status = 'running'
        ORDER BY updated_at, id
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    LOOP
        IF EXISTS (
            SELECT 1 FROM taskq.jobs
            WHERE workflow_id = v_workflow.id
              AND status NOT IN ('succeeded','failed','cancelled')
        ) THEN
            -- Rotate an unfinished workflow behind the bounded frontier.
            -- Without this monotonic cursor movement, the oldest p_limit
            -- active workflows could starve every later terminal candidate.
            UPDATE taskq.workflows
            SET updated_at = now()
            WHERE id = v_workflow.id;
            CONTINUE;
        END IF;
        v_status := CASE
            WHEN v_workflow.cancel_requested_at IS NOT NULL THEN 'cancelled'
            WHEN EXISTS (
                SELECT 1 FROM taskq.jobs
                WHERE workflow_id = v_workflow.id AND status = 'failed'
            ) THEN 'failed'
            WHEN EXISTS (
                SELECT 1 FROM taskq.jobs
                WHERE workflow_id = v_workflow.id AND status = 'cancelled'
            ) THEN 'cancelled'
            ELSE 'succeeded'
        END;
        UPDATE taskq.workflows
        SET status = v_status, finished_at = now(), updated_at = now()
        WHERE id = v_workflow.id;
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.finalize_workflows(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.finalize_workflows(integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.seal_workflow(
    p_workflow_id uuid,
    p_actor text
) RETURNS taskq.workflow_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_workflow taskq.workflows%ROWTYPE;
BEGIN
    SELECT * INTO v_workflow
    FROM taskq.workflows
    WHERE id = p_workflow_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
    END IF;
    IF v_workflow.sealed_at IS NOT NULL THEN
        RETURN ('already_sealed', v_workflow.id, v_workflow.status)::taskq.workflow_result;
    END IF;
    UPDATE taskq.workflows
    SET sealed_at = now(), sealed_by = p_actor, updated_at = now()
    WHERE id = p_workflow_id;
    IF NOT EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE workflow_id = p_workflow_id
          AND status NOT IN ('succeeded','failed','cancelled')
    ) THEN
        UPDATE taskq.workflows
        SET status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM taskq.jobs
                    WHERE workflow_id = p_workflow_id AND status = 'failed'
                ) THEN 'failed'
                WHEN EXISTS (
                    SELECT 1 FROM taskq.jobs
                    WHERE workflow_id = p_workflow_id AND status = 'cancelled'
                ) THEN 'cancelled'
                ELSE 'succeeded'
            END,
            finished_at = now(),
            updated_at = now()
        WHERE id = p_workflow_id;
    END IF;
    SELECT * INTO v_workflow FROM taskq.workflows WHERE id = p_workflow_id;
    RETURN ('sealed', v_workflow.id, v_workflow.status)::taskq.workflow_result;
END $$;
ALTER FUNCTION taskq.seal_workflow(uuid,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.seal_workflow(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.seal_workflow(uuid,text) TO taskq_producer;

CREATE OR REPLACE FUNCTION taskq.cancel_dependents(
    p_job_id uuid,
    p_reason text,
    p_limit integer DEFAULT 100
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_child uuid;
    v_n integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'limit must be 1..1000' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_child IN
        SELECT d.job_id
        FROM taskq.job_deps AS d
        JOIN taskq.jobs AS j ON j.id = d.job_id
        WHERE d.depends_on = p_job_id AND j.status = 'blocked'
        ORDER BY d.job_id
        LIMIT p_limit
        FOR UPDATE OF j SKIP LOCKED
    LOOP
        UPDATE taskq.jobs
        SET status = 'cancelled', outcome = 'dep_failed',
            error = taskq.truncate_utf8(p_reason, 2048),
            finished_at = now(), updated_at = now()
        WHERE id = v_child AND status = 'blocked';
        IF FOUND THEN
            DELETE FROM taskq.job_deps WHERE job_id = v_child;
            INSERT INTO taskq.job_events (
                job_id, attempt_id, event_type, actor, message, data
            ) VALUES (
                v_child, NULL, 'cancelled', 'system',
                taskq.truncate_utf8(p_reason, 500),
                jsonb_build_object('reason', 'dep_failed')
            );
            v_n := v_n + 1;
        END IF;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.cancel_dependents(uuid,text,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.cancel_dependents(uuid,text,integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.finalize_dep_stragglers(
    p_limit integer DEFAULT 100
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_edge record;
    v_promoted record;
    v_n integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'limit must be 1..1000' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_edge IN
        SELECT d.job_id, d.depends_on, p.status AS parent_status
        FROM taskq.job_deps AS d
        JOIN taskq.jobs AS p ON p.id = d.depends_on
        JOIN taskq.jobs AS c ON c.id = d.job_id
        WHERE c.status = 'blocked'
          AND p.status IN ('succeeded','failed','cancelled')
        ORDER BY d.depends_on, d.job_id
        LIMIT p_limit
        FOR UPDATE OF c SKIP LOCKED
    LOOP
        IF v_edge.parent_status = 'succeeded' THEN
            DELETE FROM taskq.job_deps
            WHERE job_id = v_edge.job_id AND depends_on = v_edge.depends_on;
            IF FOUND THEN
                UPDATE taskq.jobs
                SET pending_deps = pending_deps - 1,
                    status = CASE WHEN pending_deps = 1 THEN 'queued' ELSE status END,
                    updated_at = now()
                WHERE id = v_edge.job_id AND status = 'blocked'
                RETURNING queue, status, scheduled_at INTO v_promoted;
                IF v_promoted.status = 'queued'
                   AND v_promoted.scheduled_at <= now()
                   AND EXISTS (
                       SELECT 1 FROM taskq.queues
                       WHERE name = v_promoted.queue AND notify_enabled
                   ) THEN
                    PERFORM pg_notify('taskq_' || v_promoted.queue, '');
                END IF;
                v_n := v_n + 1;
            END IF;
        ELSE
            v_n := v_n + taskq.cancel_dependents(
                v_edge.depends_on, 'dependency terminal', 1
            );
        END IF;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.finalize_dep_stragglers(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.finalize_dep_stragglers(integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.advance_workflow_cancellations(
    p_limit integer DEFAULT 100
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_job uuid;
    v_status text;
    v_n integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'limit must be 1..1000' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_job IN
        SELECT j.id
        FROM taskq.jobs AS j
        JOIN taskq.workflows AS w ON w.id = j.workflow_id
        WHERE w.cancel_requested_at IS NOT NULL
          AND w.status = 'running'
          AND (
              j.status IN ('blocked','queued')
              OR (j.status = 'running' AND j.cancel_requested_at IS NULL)
          )
        ORDER BY w.cancel_requested_at, j.id
        LIMIT p_limit
        FOR UPDATE OF j SKIP LOCKED
    LOOP
        UPDATE taskq.jobs
        SET status = CASE WHEN status = 'running' THEN status ELSE 'cancelled' END,
            outcome = CASE WHEN status = 'running' THEN outcome ELSE 'canceled' END,
            cancel_requested_at = COALESCE(cancel_requested_at, now()),
            cancel_reason = COALESCE(cancel_reason, 'workflow cancelled'),
            error = CASE
                WHEN status = 'running' THEN error
                ELSE COALESCE(error, 'workflow cancelled')
            END,
            finished_at = CASE WHEN status = 'running' THEN finished_at ELSE now() END,
            updated_at = now()
        WHERE id = v_job
          AND (
              status IN ('blocked','queued')
              OR (status = 'running' AND cancel_requested_at IS NULL)
          )
        RETURNING status INTO v_status;
        IF FOUND AND v_status = 'cancelled' THEN
            DELETE FROM taskq.job_deps WHERE job_id = v_job;
            INSERT INTO taskq.job_events (
                job_id, attempt_id, event_type, actor, message, data
            ) VALUES (
                v_job, NULL, 'cancelled', 'system', 'workflow cancelled',
                jsonb_build_object('reason', 'workflow_cancelled')
            );
        ELSIF FOUND THEN
            INSERT INTO taskq.job_events (
                job_id, attempt_id, event_type, actor, message, data
            )
            SELECT
                id, current_attempt_id, 'cancel_requested', 'system',
                'workflow cancelled', jsonb_build_object('reason', 'workflow_cancelled')
            FROM taskq.jobs WHERE id = v_job;
        END IF;
        IF FOUND THEN
            v_n := v_n + 1;
        END IF;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.advance_workflow_cancellations(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.advance_workflow_cancellations(integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.cancel_workflow(
    p_workflow_id uuid,
    p_actor text,
    p_reason text
) RETURNS taskq.workflow_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_workflow taskq.workflows%ROWTYPE;
BEGIN
    IF p_actor IS NULL OR p_actor = '' THEN
        RAISE EXCEPTION 'workflow actor is required' USING ERRCODE = 'TQ422';
    END IF;
    IF p_reason IS NOT NULL AND octet_length(p_reason) > 2048 THEN
        RAISE EXCEPTION 'workflow cancel reason exceeds 2KB' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_workflow
    FROM taskq.workflows
    WHERE id = p_workflow_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
    END IF;
    IF v_workflow.status <> 'running' THEN
        RETURN ('already_terminal', v_workflow.id, v_workflow.status)::taskq.workflow_result;
    END IF;
    IF v_workflow.cancel_requested_at IS NOT NULL THEN
        RETURN ('already_requested', v_workflow.id, v_workflow.status)::taskq.workflow_result;
    END IF;
    UPDATE taskq.workflows
    SET sealed_at = COALESCE(sealed_at, now()),
        sealed_by = COALESCE(sealed_by, p_actor),
        cancel_requested_at = now(),
        cancel_requested_by = p_actor,
        cancel_reason = taskq.truncate_utf8(p_reason, 2048),
        updated_at = now()
    WHERE id = p_workflow_id;
    PERFORM taskq.advance_workflow_cancellations(100);
    IF NOT EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE workflow_id = p_workflow_id
          AND status NOT IN ('succeeded','failed','cancelled')
    ) THEN
        UPDATE taskq.workflows
        SET status = 'cancelled', finished_at = now(), updated_at = now()
        WHERE id = p_workflow_id;
    END IF;
    SELECT * INTO v_workflow FROM taskq.workflows WHERE id = p_workflow_id;
    RETURN ('cancel_requested', v_workflow.id, v_workflow.status)::taskq.workflow_result;
END $$;
ALTER FUNCTION taskq.cancel_workflow(uuid,text,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.cancel_workflow(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.cancel_workflow(uuid,text,text) TO taskq_operator;

-- The existing public enqueue, completion and tick bodies are replaced below
-- after the workflow primitives exist, keeping every graph mutation in one
-- transaction.

CREATE OR REPLACE FUNCTION taskq.enqueue(
    p_queue text,
    p_job_type text,
    p_payload jsonb DEFAULT '{}'::jsonb,
    p_priority smallint DEFAULT NULL,
    p_scheduled_at timestamptz DEFAULT NULL,
    p_idempotency_key text DEFAULT NULL,
    p_concurrency_key text DEFAULT NULL,
    p_affinity_key text DEFAULT NULL,
    p_max_attempts smallint DEFAULT NULL,
    p_lease_seconds integer DEFAULT NULL,
    p_backoff_mode text DEFAULT NULL,
    p_backoff_base integer DEFAULT NULL,
    p_backoff_cap integer DEFAULT NULL,
    p_depends_on uuid[] DEFAULT NULL,
    p_workflow_id uuid DEFAULT NULL,
    p_step_key text DEFAULT NULL,
    p_parent_job_id uuid DEFAULT NULL,
    p_headers jsonb DEFAULT NULL
) RETURNS TABLE (job_id uuid, created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q taskq.queues%ROWTYPE;
    v_workflow taskq.workflows%ROWTYPE;
    v_existing taskq.jobs%ROWTYPE;
    v_parent taskq.jobs%ROWTYPE;
    v_id uuid;
    v_created boolean := false;
    v_try integer;
    v_scheduled timestamptz := COALESCE(p_scheduled_at, now());
    v_mode text;
    v_base integer;
    v_cap integer;
    v_deps uuid[] := '{}';
    v_live_deps uuid[] := '{}';
    v_intent_hash text;
BEGIN
    IF COALESCE(p_job_type, '') = '' OR char_length(p_job_type) > 120 THEN
        RAISE EXCEPTION 'job_type is required (<= 120 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_priority IS NOT NULL AND p_priority NOT BETWEEN 0 AND 1000 THEN
        RAISE EXCEPTION 'priority must be 0..1000' USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND p_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'lease_seconds must be 15..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF p_max_attempts IS NOT NULL AND p_max_attempts NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'max_attempts must be 1..100' USING ERRCODE = 'TQ422';
    END IF;
    IF p_backoff_mode IS NOT NULL AND p_backoff_mode NOT IN ('fixed','exponential') THEN
        RAISE EXCEPTION 'backoff_mode must be fixed or exponential' USING ERRCODE = 'TQ422';
    END IF;
    IF p_backoff_base IS NOT NULL AND p_backoff_base NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'backoff_base must be 1..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF p_backoff_cap IS NOT NULL AND p_backoff_cap < 1 THEN
        RAISE EXCEPTION 'backoff_cap must be positive' USING ERRCODE = 'TQ422';
    END IF;
    IF p_idempotency_key IS NOT NULL
       AND (p_idempotency_key = '' OR char_length(p_idempotency_key) > 255) THEN
        RAISE EXCEPTION 'idempotency_key must be 1..255 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_concurrency_key IS NOT NULL
       AND (p_concurrency_key = '' OR char_length(p_concurrency_key) > 120) THEN
        RAISE EXCEPTION 'concurrency_key must be 1..120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_affinity_key IS NOT NULL
       AND (p_affinity_key = '' OR char_length(p_affinity_key) > 120) THEN
        RAISE EXCEPTION 'affinity_key must be 1..120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object'
       OR octet_length(p_payload::text) > 65536 THEN
        RAISE EXCEPTION 'payload must be an object of at most 64KB' USING ERRCODE = 'TQ422';
    END IF;
    IF p_headers IS NOT NULL
       AND (jsonb_typeof(p_headers) <> 'object' OR octet_length(p_headers::text) > 8192) THEN
        RAISE EXCEPTION 'headers must be an object of at most 8KB' USING ERRCODE = 'TQ422';
    END IF;
    IF (p_workflow_id IS NULL) <> (p_step_key IS NULL) THEN
        RAISE EXCEPTION 'workflow_id and step_key must be supplied together'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_workflow_id IS NULL AND COALESCE(cardinality(p_depends_on), 0) > 0 THEN
        RAISE EXCEPTION 'dependencies require a workflow' USING ERRCODE = 'TQ422';
    END IF;
    IF p_step_key IS NOT NULL
       AND (
           octet_length(p_step_key) NOT BETWEEN 1 AND 64
           OR p_step_key !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'
       ) THEN
        RAISE EXCEPTION 'invalid workflow step_key' USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(cardinality(p_depends_on), 0) > 100
       OR EXISTS (SELECT 1 FROM unnest(COALESCE(p_depends_on, '{}')) AS d(id) WHERE id IS NULL)
       OR (
           SELECT count(DISTINCT id)
           FROM unnest(COALESCE(p_depends_on, '{}')) AS d(id)
       ) <> COALESCE(cardinality(p_depends_on), 0) THEN
        RAISE EXCEPTION 'depends_on must contain at most 100 distinct non-null ids'
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;
    v_mode := COALESCE(p_backoff_mode, q.default_backoff_mode);
    v_base := COALESCE(p_backoff_base, q.default_backoff_base);
    v_cap := COALESCE(p_backoff_cap, q.default_backoff_cap);
    IF v_cap < v_base THEN
        RAISE EXCEPTION 'backoff_cap is below backoff_base' USING ERRCODE = 'TQ422';
    END IF;

    IF p_workflow_id IS NOT NULL THEN
        SELECT * INTO v_workflow
        FROM taskq.workflows
        WHERE id = p_workflow_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
        END IF;
        IF NOT p_queue = ANY(v_workflow.declared_queues) THEN
            RAISE EXCEPTION 'queue is outside workflow declaration' USING ERRCODE = 'TQ422';
        END IF;
        SELECT COALESCE(array_agg(id ORDER BY id), '{}') INTO v_deps
        FROM unnest(COALESCE(p_depends_on, '{}')) AS d(id);
        v_intent_hash := encode(
            sha256(convert_to(jsonb_build_object(
                'queue', p_queue,
                'job_type', p_job_type,
                'payload', p_payload,
                'priority', p_priority,
                'scheduled_at', p_scheduled_at,
                'idempotency_key', p_idempotency_key,
                'concurrency_key', p_concurrency_key,
                'affinity_key', p_affinity_key,
                'max_attempts', p_max_attempts,
                'lease_seconds', p_lease_seconds,
                'backoff_mode', p_backoff_mode,
                'backoff_base', p_backoff_base,
                'backoff_cap', p_backoff_cap,
                'depends_on', to_jsonb(v_deps),
                'parent_job_id', p_parent_job_id,
                'headers', p_headers
            )::text, 'UTF8')),
            'hex'
        );
        SELECT * INTO v_existing
        FROM taskq.jobs
        WHERE workflow_id = p_workflow_id AND step_key = p_step_key
        FOR UPDATE;
        IF FOUND THEN
            IF v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash THEN
                RAISE EXCEPTION 'workflow step intent mismatch'
                    USING ERRCODE = 'TQ409',
                          DETAIL = '{"reason":"workflow_step_mismatch"}';
            END IF;
            RETURN QUERY SELECT v_existing.id, false;
            RETURN;
        END IF;
        IF v_workflow.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'workflow membership is sealed'
                USING ERRCODE = 'TQ409',
                      DETAIL = '{"reason":"workflow_sealed"}';
        END IF;
        FOR v_parent IN
            SELECT j.*
            FROM unnest(v_deps) AS d(id)
            JOIN taskq.jobs AS j ON j.id = d.id
            ORDER BY j.id
            FOR UPDATE OF j
        LOOP
            IF v_parent.workflow_id IS DISTINCT FROM p_workflow_id THEN
                RAISE EXCEPTION 'taskq: dependency is outside workflow'
                    USING ERRCODE = 'TQ001';
            END IF;
            IF v_parent.status IN ('failed','cancelled') THEN
                RAISE EXCEPTION 'dependency is terminal'
                    USING ERRCODE = 'TQ409',
                          DETAIL = '{"reason":"dependency_terminal"}';
            END IF;
            IF v_parent.status <> 'succeeded' THEN
                v_live_deps := array_append(v_live_deps, v_parent.id);
            END IF;
        END LOOP;
        IF cardinality(v_deps) <> (
            SELECT count(*) FROM taskq.jobs WHERE id = ANY(v_deps)
        ) THEN
            RAISE EXCEPTION 'taskq: no such dependency' USING ERRCODE = 'TQ001';
        END IF;
    END IF;

    IF q.max_depth IS NOT NULL AND EXISTS (
        SELECT 1 FROM taskq.jobs
        WHERE queue = p_queue AND status IN ('blocked','queued')
        OFFSET greatest(q.max_depth - 1, 0) LIMIT 1
    ) THEN
        RAISE EXCEPTION 'queue is at max_depth' USING ERRCODE = 'TQ429';
    END IF;

    FOR v_try IN 1..3 LOOP
        v_id := taskq.uuid7();
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, concurrency_key, affinity_key,
            workflow_id, step_key, workflow_intent_hash,
            parent_job_id, pending_deps,
            scheduled_at, lease_seconds, max_attempts,
            backoff_mode, backoff_base_seconds, backoff_cap_seconds
        ) VALUES (
            v_id, p_queue, p_job_type,
            CASE WHEN cardinality(v_live_deps) > 0 THEN 'blocked' ELSE 'queued' END,
            COALESCE(p_priority, q.default_priority), p_payload, p_headers,
            p_idempotency_key, p_concurrency_key, p_affinity_key,
            p_workflow_id, p_step_key, v_intent_hash,
            p_parent_job_id, cardinality(v_live_deps),
            v_scheduled, COALESCE(p_lease_seconds, q.default_lease_seconds),
            COALESCE(p_max_attempts, q.default_max_attempts),
            v_mode, v_base, v_cap
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING;
        IF FOUND THEN
            v_created := true;
            EXIT;
        END IF;
        SELECT j.* INTO v_existing
        FROM taskq.jobs AS j
        WHERE j.queue = p_queue
          AND j.idempotency_key = p_idempotency_key
          AND j.status IN ('blocked','queued','running')
        ORDER BY j.created_at DESC
        LIMIT 1;
        IF v_existing.id IS NOT NULL THEN
            IF p_workflow_id IS NOT NULL
               AND (
                   v_existing.workflow_id IS DISTINCT FROM p_workflow_id
                   OR v_existing.step_key IS DISTINCT FROM p_step_key
                   OR v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash
               ) THEN
                RAISE EXCEPTION 'workflow step intent mismatch'
                    USING ERRCODE = 'TQ409',
                          DETAIL = '{"reason":"workflow_step_mismatch"}';
            END IF;
            RETURN QUERY SELECT v_existing.id, false;
            RETURN;
        END IF;
    END LOOP;
    IF NOT v_created THEN
        RAISE EXCEPTION 'taskq: idempotency insert did not converge'
            USING ERRCODE = 'TQ500';
    END IF;

    INSERT INTO taskq.job_deps(job_id, depends_on)
    SELECT v_id, id FROM unnest(v_live_deps) AS d(id);
    PERFORM taskq.emit_event(
        v_id, NULL, 'enqueued', 'system', NULL,
        jsonb_build_object(
            'status', CASE WHEN cardinality(v_live_deps) > 0 THEN 'blocked' ELSE 'queued' END,
            'scheduled_at', v_scheduled
        )
    );
    IF cardinality(v_live_deps) = 0
       AND v_scheduled <= now() AND q.notify_enabled THEN
        PERFORM pg_notify('taskq_' || p_queue, '');
    END IF;
    RETURN QUERY SELECT v_id, true;
END $$;
ALTER FUNCTION taskq.enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.enqueue(text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb) TO taskq_producer;

CREATE OR REPLACE FUNCTION taskq.claim_jobs(
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
DECLARE
    v_job taskq.jobs%ROWTYPE;
    v_attempt_id uuid;
    v_lease integer;
    v_skip uuid[] := '{}';
    v_claimed integer := 0;
    v_scans integer := 0;
    v_cap integer;
    v_running integer;
    v_affinity text := p_affinity_key;
    v_batch integer := p_batch;
    v_saturated text[] := '{}';
    v_paused_at timestamptz;
    v_jobs taskq.claimed_job[] := '{}';
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE = 'TQ422';
    END IF;
    IF p_batch IS NULL OR v_batch NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'claim batch must be 1..50' USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL
       AND p_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'lease override must be 15..86400 seconds'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_types IS NOT NULL
       AND cardinality(p_job_types) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'job type filter must have 1..20 entries'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_affinity_key IS NOT NULL AND char_length(p_affinity_key) > 120 THEN
        RAISE EXCEPTION 'affinity_key exceeds 120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_id IS NOT NULL THEN
        v_batch := 1;
    END IF;

    SELECT q.paused_at INTO v_paused_at
    FROM taskq.queues AS q
    WHERE q.name = p_queue;
    IF NOT FOUND THEN
        RETURN ROW('unknown_queue', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    IF v_paused_at IS NOT NULL THEN
        RETURN ROW('paused', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;

    SELECT COALESCE(array_agg(k.key), '{}') INTO v_saturated
    FROM (
        SELECT r.concurrency_key AS key, count(*) AS c
        FROM taskq.jobs AS r
        WHERE r.status = 'running' AND r.concurrency_key IS NOT NULL
        GROUP BY r.concurrency_key
    ) AS k
    WHERE k.c >= COALESCE(
        (SELECT l.max_running FROM taskq.concurrency_limits AS l WHERE l.key = k.key),
        1
    );

    WHILE v_claimed < v_batch AND v_scans < v_batch + 20 LOOP
        v_scans := v_scans + 1;
        v_job := NULL;

        IF v_affinity IS NOT NULL AND p_job_id IS NULL THEN
            SELECT j.* INTO v_job
            FROM taskq.jobs AS j
            WHERE j.queue = p_queue
              AND j.status = 'queued'
              AND j.scheduled_at <= now()
              AND j.cancel_requested_at IS NULL
              AND (
                  j.workflow_id IS NULL
                  OR NOT EXISTS (
                      SELECT 1 FROM taskq.workflows AS w
                      WHERE w.id = j.workflow_id
                        AND w.cancel_requested_at IS NOT NULL
                  )
              )
              AND j.affinity_key = v_affinity
              AND (p_job_types IS NULL OR j.job_type = ANY(p_job_types))
              AND NOT (j.id = ANY(v_skip))
              AND (
                  j.concurrency_key IS NULL
                  OR NOT (j.concurrency_key = ANY(v_saturated))
              )
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED;
            IF v_job.id IS NULL THEN
                v_affinity := NULL;
            END IF;
        END IF;

        IF v_job.id IS NULL THEN
            SELECT j.* INTO v_job
            FROM taskq.jobs AS j
            WHERE j.queue = p_queue
              AND j.status = 'queued'
              AND j.scheduled_at <= now()
              AND j.cancel_requested_at IS NULL
              AND (
                  j.workflow_id IS NULL
                  OR NOT EXISTS (
                      SELECT 1 FROM taskq.workflows AS w
                      WHERE w.id = j.workflow_id
                        AND w.cancel_requested_at IS NOT NULL
                  )
              )
              AND (p_job_id IS NULL OR j.id = p_job_id)
              AND (p_job_types IS NULL OR j.job_type = ANY(p_job_types))
              AND NOT (j.id = ANY(v_skip))
              AND (
                  j.concurrency_key IS NULL
                  OR NOT (j.concurrency_key = ANY(v_saturated))
              )
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED;
        END IF;
        EXIT WHEN v_job.id IS NULL;

        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended('taskq.ck:' || v_job.concurrency_key, 0)
            ) THEN
                v_skip := v_skip || v_job.id;
                CONTINUE;
            END IF;
            SELECT COALESCE(
                (
                    SELECT l.max_running
                    FROM taskq.concurrency_limits AS l
                    WHERE l.key = v_job.concurrency_key
                ),
                1
            ) INTO v_cap;
            SELECT count(*) INTO v_running
            FROM taskq.jobs AS r
            WHERE r.status = 'running'
              AND r.concurrency_key = v_job.concurrency_key;
            IF v_running >= v_cap THEN
                v_skip := v_skip || v_job.id;
                CONTINUE;
            END IF;
        END IF;

        v_attempt_id := taskq.uuid7();
        v_lease := COALESCE(p_lease_seconds, v_job.lease_seconds);
        UPDATE taskq.jobs AS j
        SET status = 'running',
            worker_id = p_worker_id,
            current_attempt_id = v_attempt_id,
            attempt_count = j.attempt_count + 1,
            lease_expires_at = now() + make_interval(secs => v_lease),
            started_at = COALESCE(j.started_at, now()),
            updated_at = now()
        WHERE j.id = v_job.id;
        INSERT INTO taskq.job_attempts(id,job_id,worker_id,lease_seconds)
        VALUES (v_attempt_id,v_job.id,p_worker_id,v_lease);
        PERFORM taskq.emit_event(
            v_job.id, v_attempt_id, 'claimed', p_worker_id, NULL,
            jsonb_build_object('attempt', v_job.attempt_count + 1)
        );
        v_claimed := v_claimed + 1;
        v_jobs := v_jobs || ROW(
            v_job.id, v_job.queue, v_job.job_type, v_job.priority,
            v_job.payload, v_job.headers, v_job.progress, v_attempt_id,
            (v_job.attempt_count + 1)::integer, v_job.failure_count,
            v_job.max_attempts, now() + make_interval(secs => v_lease),
            v_job.workflow_id, v_job.step_key, v_lease
        )::taskq.claimed_job;
    END LOOP;

    IF v_claimed = 0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            RETURN ROW('unavailable', '{}'::taskq.claimed_job[])::taskq.claim_batch;
        END IF;
        RETURN ROW('empty', '{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed', v_jobs)::taskq.claim_batch;
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid) TO taskq_runner;

CREATE OR REPLACE FUNCTION taskq.emit_event(
    p_job_id uuid,
    p_attempt_id uuid,
    p_event_type text,
    p_actor text,
    p_message text,
    p_data jsonb DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    INSERT INTO taskq.job_events (
        job_id, attempt_id, event_type, actor, message, data
    ) VALUES (
        p_job_id, p_attempt_id, p_event_type, p_actor,
        taskq.truncate_utf8(p_message, 500), p_data
    );
    IF p_event_type IN ('failed', 'cancelled') THEN
        PERFORM taskq.cancel_dependents(
            p_job_id,
            CASE
                WHEN p_event_type = 'failed' THEN 'dependency failed'
                ELSE 'dependency cancelled'
            END,
            100
        );
    END IF;
END $$;
ALTER FUNCTION taskq.emit_event(uuid,uuid,text,text,text,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.emit_event(uuid,uuid,text,text,text,jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.complete_job(
    p_job_id uuid,
    p_attempt_id uuid,
    p_worker_id text,
    p_result jsonb DEFAULT NULL,
    p_stats jsonb DEFAULT NULL,
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
    v_dep record;
    v_promoted record;
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
    INTO v_job
    FROM taskq.jobs AS j
    WHERE j.id = p_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN ('lost',NULL,NULL)::taskq.settle_result;
    END IF;
    IF v_job.status <> 'running'
       OR v_job.current_attempt_id IS DISTINCT FROM p_attempt_id THEN
        SELECT a.status INTO v_att
        FROM taskq.job_attempts AS a
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

    FOR v_spec IN
        SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]'::jsonb))
    LOOP
        v_index := v_index + 1;
        IF jsonb_typeof(v_spec) <> 'object' THEN
            RAISE EXCEPTION 'followup spec % must be an object', v_index
                USING ERRCODE = 'TQ422';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_object_keys(v_spec) AS k(key)
            WHERE k.key NOT IN (
                'step','job_type','queue','payload','headers','priority',
                'max_attempts','lease_seconds','scheduled_at'
            )
        ) THEN
            RAISE EXCEPTION 'followup spec % has an unknown field', v_index
                USING ERRCODE = 'TQ422';
        END IF;
        v_step := v_spec->>'step';
        IF v_step IS NULL
           OR octet_length(v_step) NOT BETWEEN 1 AND 64
           OR v_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
            RAISE EXCEPTION 'followup spec % has invalid step', v_index
                USING ERRCODE = 'TQ422';
        END IF;
        IF v_step = ANY(v_steps) THEN
            RAISE EXCEPTION 'duplicate followup step' USING ERRCODE = 'TQ422';
        END IF;
        v_steps := array_append(v_steps, v_step);
        IF COALESCE(v_spec->>'job_type','') = ''
           OR char_length(v_spec->>'job_type') > 120 THEN
            RAISE EXCEPTION 'followup spec % has invalid job_type', v_index
                USING ERRCODE = 'TQ422';
        END IF;
        v_queue := COALESCE(v_spec->>'queue', v_job.queue);
        IF NOT EXISTS (SELECT 1 FROM taskq.queues AS q WHERE q.name = v_queue) THEN
            RAISE EXCEPTION 'followup spec % names unknown queue', v_index
                USING ERRCODE = 'TQ422';
        END IF;
        IF jsonb_typeof(COALESCE(v_spec->'payload','{}'::jsonb)) <> 'object'
           OR octet_length(COALESCE(v_spec->'payload','{}'::jsonb)::text) > 65536
           OR jsonb_typeof(COALESCE(v_spec->'headers','{}'::jsonb)) <> 'object'
           OR octet_length(COALESCE(v_spec->'headers','{}'::jsonb)::text) > 8192 THEN
            RAISE EXCEPTION 'followup spec % has invalid bounded JSON', v_index
                USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            IF v_spec ? 'priority'
               AND (
                   jsonb_typeof(v_spec->'priority') <> 'number'
                   OR (v_spec->>'priority') !~ '^-?[0-9]+$'
                   OR (v_spec->>'priority')::integer NOT BETWEEN 0 AND 1000
               ) THEN
                RAISE data_exception;
            END IF;
            IF v_spec ? 'max_attempts'
               AND (
                   jsonb_typeof(v_spec->'max_attempts') <> 'number'
                   OR (v_spec->>'max_attempts') !~ '^-?[0-9]+$'
                   OR (v_spec->>'max_attempts')::integer NOT BETWEEN 1 AND 100
               ) THEN
                RAISE data_exception;
            END IF;
            IF v_spec ? 'lease_seconds'
               AND (
                   jsonb_typeof(v_spec->'lease_seconds') <> 'number'
                   OR (v_spec->>'lease_seconds') !~ '^-?[0-9]+$'
                   OR (v_spec->>'lease_seconds')::integer NOT BETWEEN 15 AND 86400
               ) THEN
                RAISE data_exception;
            END IF;
            IF v_spec ? 'scheduled_at' THEN
                IF jsonb_typeof(v_spec->'scheduled_at') <> 'string' THEN
                    RAISE data_exception;
                END IF;
                PERFORM (v_spec->>'scheduled_at')::timestamptz;
            END IF;
        EXCEPTION
            WHEN data_exception OR invalid_text_representation
                 OR datetime_field_overflow OR numeric_value_out_of_range THEN
                RAISE EXCEPTION 'followup spec % has an invalid scalar field', v_index
                    USING ERRCODE = 'TQ422';
        END;
    END LOOP;

    UPDATE taskq.jobs
    SET status = 'succeeded', outcome = 'success',
        worker_id = NULL, current_attempt_id = NULL, lease_expires_at = NULL,
        result = COALESCE(p_result,result), error = NULL, expiry_streak = 0,
        finished_at = now(), finished_by_attempt_id = p_attempt_id,
        updated_at = now()
    WHERE id = p_job_id;
    UPDATE taskq.job_attempts
    SET status = 'succeeded', outcome = 'success',
        finished_at = now(), stats = COALESCE(p_stats,stats)
    WHERE id = p_attempt_id AND status = 'running';

    FOR v_dep IN
        SELECT d.job_id
        FROM taskq.job_deps AS d
        JOIN taskq.jobs AS child ON child.id = d.job_id
        WHERE d.depends_on = p_job_id AND child.status = 'blocked'
        ORDER BY d.job_id
        LIMIT 100
        FOR UPDATE OF child SKIP LOCKED
    LOOP
        DELETE FROM taskq.job_deps
        WHERE job_id = v_dep.job_id AND depends_on = p_job_id;
        IF FOUND THEN
            UPDATE taskq.jobs
            SET pending_deps = pending_deps - 1,
                status = CASE WHEN pending_deps = 1 THEN 'queued' ELSE status END,
                updated_at = now()
            WHERE id = v_dep.job_id AND status = 'blocked'
            RETURNING queue, status, scheduled_at INTO v_promoted;
            IF v_promoted.status = 'queued'
               AND v_promoted.scheduled_at <= now()
               AND EXISTS (
                   SELECT 1 FROM taskq.queues
                   WHERE name = v_promoted.queue AND notify_enabled
               ) THEN
                PERFORM pg_notify('taskq_' || v_promoted.queue, '');
            END IF;
        END IF;
    END LOOP;

    PERFORM taskq.emit_event(
        p_job_id, p_attempt_id, 'succeeded', p_worker_id, NULL, NULL
    );
    v_index := 0;
    FOR v_spec IN
        SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]'::jsonb))
    LOOP
        v_index := v_index + 1;
        PERFORM * FROM taskq._enqueue_followup(
            p_job_id, v_job.queue, v_spec, v_index
        );
    END LOOP;
    RETURN ('ok','succeeded',NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb) TO taskq_runner;

CREATE OR REPLACE FUNCTION taskq.redrive_job(
    p_job_id uuid,
    p_actor text,
    p_reset_progress boolean DEFAULT false
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_queue text;
    v_status text;
    v_workflow_id uuid;
BEGIN
    SELECT status, workflow_id
    INTO v_status, v_workflow_id
    FROM taskq.jobs
    WHERE id = p_job_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such job %', p_job_id USING ERRCODE = 'TQ001';
    END IF;
    IF v_workflow_id IS NOT NULL THEN
        RAISE EXCEPTION 'workflow members cannot be individually redriven'
            USING ERRCODE = 'TQ409',
                  DETAIL = '{"reason":"workflow_member_redrive_forbidden"}';
    END IF;
    IF v_status <> 'failed' THEN
        RAISE EXCEPTION 'only failed jobs are redrivable'
            USING ERRCODE = 'TQ409', DETAIL = 'reason=not_redrivable';
    END IF;
    UPDATE taskq.jobs
    SET status = 'queued', scheduled_at = now(),
        failure_count = 0, expiry_streak = 0,
        outcome = NULL, finished_at = NULL, finished_by_attempt_id = NULL,
        cancel_requested_at = NULL, cancel_reason = NULL,
        progress = CASE WHEN p_reset_progress THEN NULL ELSE progress END,
        updated_at = now()
    WHERE id = p_job_id
    RETURNING queue INTO v_queue;
    PERFORM taskq.emit_event(
        p_job_id, NULL, 'redriven', p_actor, 'operator redrive from failed', NULL
    );
    PERFORM pg_notify('taskq_' || v_queue, '');
    RETURN true;
EXCEPTION
    WHEN unique_violation THEN
        RAISE EXCEPTION 'redrive idempotency collision'
            USING ERRCODE = 'TQ409', DETAIL = 'reason=idempotency_collision';
END $$;
ALTER FUNCTION taskq.redrive_job(uuid,text,boolean) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.redrive_job(uuid,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.redrive_job(uuid,text,boolean) TO taskq_operator;

CREATE OR REPLACE FUNCTION taskq.redrive_failed(
    p_queue text,
    p_limit integer,
    p_actor text
) RETURNS TABLE (redriven integer, skipped integer)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_id uuid;
    v_r integer := 0;
    v_s integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'limit must be 1..500' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_id IN
        SELECT id FROM taskq.jobs
        WHERE queue = p_queue
          AND status = 'failed'
          AND workflow_id IS NULL
        ORDER BY finished_at DESC
        LIMIT p_limit
    LOOP
        BEGIN
            PERFORM taskq.redrive_job(v_id, p_actor, false);
            v_r := v_r + 1;
        EXCEPTION WHEN SQLSTATE 'TQ409' THEN
            v_s := v_s + 1;
        END;
    END LOOP;
    RETURN QUERY SELECT v_r, v_s;
END $$;
ALTER FUNCTION taskq.redrive_failed(text,integer,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.redrive_failed(text,integer,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.redrive_failed(text,integer,text) TO taskq_operator;

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
    BEGIN
        IF taskq.claim_janitor_due() THEN
            v_out := v_out || jsonb_build_object('janitor', taskq.janitor());
        END IF;
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state
        SET last_error = 'janitor: ' || SQLERRM WHERE key = 'tick';
    END;
    UPDATE taskq.control_state SET last_finished_at = now() WHERE key = 'tick';
    RETURN v_out;
END $$;
ALTER FUNCTION taskq.tick(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.tick(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.tick(integer) TO taskq_housekeeper, taskq_operator;

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('contract_version','"0.2.1"'::jsonb,now()),
    ('capabilities','{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready"]}'::jsonb,now())
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now();
