-- outlabs-taskq — migration 0016: inactive native workflow continuations
-- SQL contract 0.2.5 / ADR-035 / Protocol document revision 1.0.15.
-- This definition migration deliberately leaves workflow_continuations absent.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.4"'::jsonb THEN
        RAISE EXCEPTION '0016 requires SQL contract 0.2.4, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules","worker_presence"]}'::jsonb THEN
        RAISE EXCEPTION '0016 requires the exact activated 0015 capability set, found %',
            v_capabilities;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM taskq.jobs AS j
        WHERE j.idempotency_key LIKE 'chain:%'
          AND (
              j.parent_job_id IS NULL
              OR j.idempotency_key NOT LIKE
                    'chain:' || j.parent_job_id::text || ':%'
              OR substring(
                    j.idempotency_key
                    FROM length('chain:' || j.parent_job_id::text || ':') + 1
                 ) !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
          )
    ) THEN
        RAISE EXCEPTION '0016 found a producer-shaped retained chain: idempotency key'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"reserved_idempotency_namespace"}';
    END IF;
    IF to_regprocedure(
           'taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])'
       ) IS NOT NULL
       OR to_regprocedure(
           'taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)'
       ) IS NOT NULL
       OR to_regprocedure(
           'taskq.create_workflow(text,text,jsonb,text[],text,integer,text)'
       ) IS NOT NULL
       OR to_regprocedure(
           'taskq._reserve_workflow_members(uuid,integer,text)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION '0016 requires absent workflow-continuation overloads';
    END IF;
END $$;

ALTER TABLE taskq.workflows
    ADD COLUMN member_limit integer,
    ADD COLUMN continuation_policy_hash text,
    ADD CONSTRAINT workflows_member_limit_ck
        CHECK (member_limit BETWEEN 1 AND 1000000),
    ADD CONSTRAINT workflows_continuation_policy_hash_ck
        CHECK (continuation_policy_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT workflows_continuation_policy_shape_ck
        CHECK (continuation_policy_hash IS NULL OR member_limit IS NOT NULL);

ALTER TABLE taskq.workflow_member_counts
    ADD COLUMN admitted_total bigint,
    ADD CONSTRAINT workflow_member_counts_admitted_total_ck
        CHECK (admitted_total >= 0);

ALTER TABLE taskq.jobs
    ADD COLUMN continuation_policy_hash text,
    ADD CONSTRAINT jobs_continuation_policy_hash_ck
        CHECK (continuation_policy_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT jobs_continuation_policy_shape_ck
        CHECK (continuation_policy_hash IS NULL OR workflow_id IS NOT NULL);

ALTER TYPE taskq.claimed_job
    ADD ATTRIBUTE continuation_policy_hash text CASCADE;
ALTER TYPE taskq.workflow_read_profile
    ADD ATTRIBUTE member_limit integer,
    ADD ATTRIBUTE admitted_total bigint,
    ADD ATTRIBUTE remaining_capacity bigint,
    ADD ATTRIBUTE continuation_policy_hash text CASCADE;

DROP INDEX taskq.jobs_affinity_idx;
CREATE INDEX jobs_claim_policy_idx
    ON taskq.jobs (
        queue, continuation_policy_hash, priority, scheduled_at, id
    )
    WHERE status = 'queued' AND cancel_requested_at IS NULL;
CREATE INDEX jobs_affinity_policy_idx
    ON taskq.jobs (
        queue, affinity_key, continuation_policy_hash,
        priority, scheduled_at, id
    )
    WHERE status = 'queued' AND cancel_requested_at IS NULL
      AND affinity_key IS NOT NULL;
CREATE INDEX jobs_workflow_cancel_idx
    ON taskq.jobs (workflow_id, id)
    WHERE workflow_id IS NOT NULL
      AND (
          status IN ('blocked','queued')
          OR (status = 'running' AND cancel_requested_at IS NULL)
      );

CREATE OR REPLACE FUNCTION taskq.manage_workflow_member_counts()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO taskq.workflow_member_counts(workflow_id, admitted_total)
        VALUES (
            NEW.id,
            CASE WHEN NEW.member_limit IS NULL THEN NULL ELSE 0 END
        );
        RETURN NEW;
    END IF;
    DELETE FROM taskq.workflow_member_counts WHERE workflow_id = OLD.id;
    RETURN OLD;
END $$;
ALTER FUNCTION taskq.manage_workflow_member_counts() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.manage_workflow_member_counts() FROM PUBLIC;

CREATE FUNCTION taskq._reserve_workflow_members(
    p_workflow_id uuid,
    p_count integer,
    p_continuation_policy_hash text
) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_limit integer;
    v_policy text;
    v_remaining bigint;
BEGIN
    IF p_workflow_id IS NULL
       OR p_count IS NULL
       OR p_count NOT BETWEEN 1 AND 1000000 THEN
        RAISE EXCEPTION 'invalid workflow member reservation'
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT w.member_limit, w.continuation_policy_hash
    INTO v_limit, v_policy
    FROM taskq.workflows AS w
    WHERE w.id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workflow reservation identity is missing'
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_policy IS DISTINCT FROM p_continuation_policy_hash THEN
        RAISE EXCEPTION 'workflow continuation policy mismatch'
            USING ERRCODE = 'TQ409',
                  DETAIL = '{"reason":"continuation_policy_mismatch"}';
    END IF;
    IF v_limit IS NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM taskq.workflow_member_counts AS c
            WHERE c.workflow_id = p_workflow_id
              AND c.admitted_total IS NULL
        ) THEN
            RAISE EXCEPTION 'legacy workflow counter invariant is invalid'
                USING ERRCODE = 'TQ500';
        END IF;
        RETURN NULL;
    END IF;

    UPDATE taskq.workflow_member_counts AS c
    SET admitted_total = c.admitted_total + p_count
    WHERE c.workflow_id = p_workflow_id
      AND c.admitted_total IS NOT NULL
      AND c.admitted_total + p_count <= v_limit
    RETURNING v_limit - c.admitted_total INTO v_remaining;
    IF FOUND THEN
        RETURN v_remaining;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM taskq.workflow_member_counts AS c
        WHERE c.workflow_id = p_workflow_id
          AND c.admitted_total IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'workflow lifetime counter invariant is missing'
            USING ERRCODE = 'TQ500';
    END IF;
    RAISE EXCEPTION 'workflow member lifetime limit exceeded'
        USING ERRCODE = 'TQ409',
              DETAIL = '{"reason":"workflow_member_limit_exceeded"}';
END $$;
ALTER FUNCTION taskq._reserve_workflow_members(uuid,integer,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq._reserve_workflow_members(uuid,integer,text) FROM PUBLIC;

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
        declared_queues, member_limit, continuation_policy_hash
    ) VALUES (
        v_id, p_workflow_key, p_kind, 'running', p_params, '{}'::jsonb,
        p_actor, v_queues, 10000, NULL
    )
    ON CONFLICT (workflow_key) DO NOTHING;
    IF FOUND THEN
        RETURN ('created', v_id, 'running')::taskq.workflow_result;
    END IF;

    SELECT * INTO v_existing
    FROM taskq.workflows
    WHERE workflow_key = p_workflow_key;
    IF v_existing.kind IS DISTINCT FROM p_kind
       OR v_existing.params IS DISTINCT FROM p_params
       OR v_existing.declared_queues IS DISTINCT FROM v_queues
       OR v_existing.continuation_policy_hash IS NOT NULL
       OR (
           v_existing.member_limit IS NOT NULL
           AND v_existing.member_limit IS DISTINCT FROM 10000
       ) THEN
        RAISE EXCEPTION 'workflow idempotency identity mismatch'
            USING ERRCODE = 'TQ409',
                  DETAIL = '{"reason":"workflow_mismatch"}';
    END IF;
    RETURN ('existed', v_existing.id, v_existing.status)::taskq.workflow_result;
END $$;
ALTER FUNCTION taskq.create_workflow(text,text,jsonb,text[],text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.create_workflow(text,text,jsonb,text[],text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.create_workflow(text,text,jsonb,text[],text) TO taskq_producer;

CREATE FUNCTION taskq.create_workflow(
    p_workflow_key text,
    p_kind text,
    p_params jsonb,
    p_declared_queues text[],
    p_actor text,
    p_member_limit integer,
    p_continuation_policy_hash text
) RETURNS taskq.workflow_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_id uuid;
    v_existing taskq.workflows%ROWTYPE;
    v_queues text[];
BEGIN
    IF NOT taskq.has_capability('workflow_continuations') THEN
        RAISE EXCEPTION 'workflow continuations are inactive'
            USING ERRCODE = 'TQ501';
    END IF;
    IF p_workflow_key IS NULL
       OR octet_length(p_workflow_key) NOT BETWEEN 1 AND 255
       OR p_kind IS NULL
       OR p_kind NOT IN ('dag', 'batch')
       OR p_params IS NULL
       OR jsonb_typeof(p_params) <> 'object'
       OR octet_length(p_params::text) > 65536
       OR p_actor IS NULL
       OR p_actor = ''
       OR p_member_limit IS NULL
       OR p_member_limit NOT BETWEEN 1 AND 1000000
       OR (
           p_continuation_policy_hash IS NOT NULL
           AND p_continuation_policy_hash !~ '^[0-9a-f]{64}$'
       ) THEN
        RAISE EXCEPTION 'invalid continuation workflow input'
            USING ERRCODE = 'TQ422';
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
        declared_queues, member_limit, continuation_policy_hash
    ) VALUES (
        v_id, p_workflow_key, p_kind, 'running', p_params, '{}'::jsonb,
        p_actor, v_queues, p_member_limit, p_continuation_policy_hash
    )
    ON CONFLICT (workflow_key) DO NOTHING;
    IF FOUND THEN
        RETURN ('created', v_id, 'running')::taskq.workflow_result;
    END IF;

    SELECT * INTO v_existing
    FROM taskq.workflows
    WHERE workflow_key = p_workflow_key;
    IF v_existing.kind IS DISTINCT FROM p_kind
       OR v_existing.params IS DISTINCT FROM p_params
       OR v_existing.declared_queues IS DISTINCT FROM v_queues
       OR v_existing.member_limit IS DISTINCT FROM p_member_limit
       OR v_existing.continuation_policy_hash IS DISTINCT FROM
            p_continuation_policy_hash THEN
        RAISE EXCEPTION 'workflow idempotency identity mismatch'
            USING ERRCODE = 'TQ409',
                  DETAIL = '{"reason":"workflow_mismatch"}';
    END IF;
    RETURN ('existed', v_existing.id, v_existing.status)::taskq.workflow_result;
END $$;
ALTER FUNCTION taskq.create_workflow(text,text,jsonb,text[],text,integer,text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION
    taskq.create_workflow(text,text,jsonb,text[],text,integer,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    taskq.create_workflow(text,text,jsonb,text[],text,integer,text)
    TO taskq_producer;

CREATE OR REPLACE FUNCTION taskq.get_workflow_page(
    p_workflow_id uuid,
    p_limit integer DEFAULT 50,
    p_after uuid DEFAULT NULL
) RETURNS taskq.workflow_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_as_of timestamptz;
    v_workflow taskq.workflows%ROWTYPE;
    v_profile taskq.workflow_read_profile;
    v_counts taskq.workflow_state_counts;
    v_items taskq.workflow_member_projection[];
    v_next uuid;
BEGIN
    IF p_workflow_id IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'invalid workflow page input' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_workflow
    FROM taskq.workflows
    WHERE id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
    END IF;
    IF NOT taskq.has_capability('read_model_workflow') THEN
        RAISE EXCEPTION 'workflow read model inactive'
            USING ERRCODE = 'TQ501',
                  DETAIL = 'reason=read_model_view_inactive view=workflow';
    END IF;

    v_as_of := now();
    SELECT
        c.blocked, c.queued, c.running, c.succeeded, c.failed, c.cancelled,
        c.admitted_total
    INTO
        v_counts.blocked, v_counts.queued, v_counts.running,
        v_counts.succeeded, v_counts.failed, v_counts.cancelled,
        v_profile.admitted_total
    FROM taskq.workflow_member_counts AS c
    WHERE c.workflow_id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workflow counter invariant missing'
            USING ERRCODE = 'TQ500';
    END IF;
    v_profile.workflow_id := v_workflow.id;
    v_profile.kind := v_workflow.kind;
    v_profile.status := v_workflow.status;
    v_profile.sealed := v_workflow.sealed_at IS NOT NULL;
    v_profile.cancel_requested := v_workflow.cancel_requested_at IS NOT NULL;
    v_profile.declared_queues := v_workflow.declared_queues;
    v_profile.created_at := v_workflow.created_at;
    v_profile.updated_at := v_workflow.updated_at;
    v_profile.finished_at := v_workflow.finished_at;
    v_profile.member_limit := v_workflow.member_limit;
    v_profile.remaining_capacity :=
        v_workflow.member_limit::bigint - v_profile.admitted_total;
    v_profile.continuation_policy_hash :=
        v_workflow.continuation_policy_hash;

    SELECT ARRAY(
        SELECT ROW(
            j.id, j.queue, j.job_type, j.step_key, j.status, j.outcome,
            j.pending_deps, j.attempt_count::integer, j.failure_count::integer,
            j.created_at, j.scheduled_at, j.started_at, j.finished_at, j.updated_at
        )::taskq.workflow_member_projection
        FROM taskq.jobs AS j
        WHERE j.workflow_id = p_workflow_id
          AND (p_after IS NULL OR j.id > p_after)
        ORDER BY j.id
        LIMIT p_limit + 1
    ) INTO v_items;
    v_items := COALESCE(v_items, ARRAY[]::taskq.workflow_member_projection[]);
    IF cardinality(v_items) > p_limit THEN
        v_next := v_items[p_limit].job_id;
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of, v_profile, v_counts, v_items, v_next)::taskq.workflow_page;
END $$;
ALTER FUNCTION taskq.get_workflow_page(uuid,integer,uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_workflow_page(uuid,integer,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_workflow_page(uuid,integer,uuid) TO taskq_observer;

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
    IF p_idempotency_key LIKE 'chain:%' THEN
        RAISE EXCEPTION 'idempotency_key uses an engine-reserved namespace'
            USING ERRCODE = 'TQ422',
                  DETAIL = '{"reason":"reserved_idempotency_namespace"}';
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
       OR EXISTS (
           SELECT 1
           FROM unnest(COALESCE(p_depends_on, '{}')) AS d(id)
           WHERE id IS NULL
       )
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
        FOR NO KEY UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
        END IF;
        IF NOT p_queue = ANY(v_workflow.declared_queues) THEN
            RAISE EXCEPTION 'queue is outside workflow declaration'
                USING ERRCODE = 'TQ422',
                      DETAIL = '{"reason":"continuation_queue_undeclared"}';
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
            IF v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash
               OR v_existing.continuation_policy_hash IS DISTINCT FROM
                    v_workflow.continuation_policy_hash THEN
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
        BEGIN
            IF p_workflow_id IS NOT NULL THEN
                PERFORM taskq._reserve_workflow_members(
                    p_workflow_id, 1, v_workflow.continuation_policy_hash
                );
            END IF;
            INSERT INTO taskq.jobs (
                id, queue, job_type, status, priority, payload, headers,
                idempotency_key, concurrency_key, affinity_key,
                workflow_id, step_key, workflow_intent_hash,
                continuation_policy_hash, parent_job_id, pending_deps,
                scheduled_at, lease_seconds, max_attempts,
                backoff_mode, backoff_base_seconds, backoff_cap_seconds
            ) VALUES (
                v_id, p_queue, p_job_type,
                CASE WHEN cardinality(v_live_deps) > 0 THEN 'blocked' ELSE 'queued' END,
                COALESCE(p_priority, q.default_priority), p_payload, p_headers,
                p_idempotency_key, p_concurrency_key, p_affinity_key,
                p_workflow_id, p_step_key, v_intent_hash,
                v_workflow.continuation_policy_hash,
                p_parent_job_id, cardinality(v_live_deps),
                v_scheduled, COALESCE(p_lease_seconds, q.default_lease_seconds),
                COALESCE(p_max_attempts, q.default_max_attempts),
                v_mode, v_base, v_cap
            )
            ON CONFLICT (queue, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                  AND status IN ('blocked','queued','running')
                DO NOTHING;
            IF NOT FOUND THEN
                RAISE unique_violation;
            END IF;
            v_created := true;
        EXCEPTION WHEN unique_violation THEN
            v_created := false;
        END;
        EXIT WHEN v_created;

        IF p_workflow_id IS NOT NULL THEN
            SELECT j.* INTO v_existing
            FROM taskq.jobs AS j
            WHERE j.workflow_id = p_workflow_id AND j.step_key = p_step_key
            FOR UPDATE;
            IF FOUND THEN
                IF v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash
                   OR v_existing.continuation_policy_hash IS DISTINCT FROM
                        v_workflow.continuation_policy_hash THEN
                    RAISE EXCEPTION 'workflow step intent mismatch'
                        USING ERRCODE = 'TQ409',
                              DETAIL = '{"reason":"workflow_step_mismatch"}';
                END IF;
                RETURN QUERY SELECT v_existing.id, false;
                RETURN;
            END IF;
        END IF;
        IF p_idempotency_key IS NOT NULL THEN
            SELECT j.* INTO v_existing
            FROM taskq.jobs AS j
            WHERE j.queue = p_queue
              AND j.idempotency_key = p_idempotency_key
              AND j.status IN ('blocked','queued','running')
            ORDER BY j.created_at DESC
            LIMIT 1;
            IF FOUND THEN
                IF p_workflow_id IS NOT NULL
                   AND (
                       v_existing.workflow_id IS DISTINCT FROM p_workflow_id
                       OR v_existing.step_key IS DISTINCT FROM p_step_key
                       OR v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash
                       OR v_existing.continuation_policy_hash IS DISTINCT FROM
                            v_workflow.continuation_policy_hash
                   ) THEN
                    RAISE EXCEPTION 'workflow step intent mismatch'
                        USING ERRCODE = 'TQ409',
                              DETAIL = '{"reason":"workflow_step_mismatch"}';
                END IF;
                RETURN QUERY SELECT v_existing.id, false;
                RETURN;
            END IF;
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
ALTER FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb
) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb
) TO taskq_producer;

-- Owner-only continuation inserter. Membership is derived exclusively from
-- the fenced parent and the already-validated workflow_member request.
CREATE OR REPLACE FUNCTION taskq._enqueue_followup(
    p_parent_job_id uuid,
    p_parent_queue text,
    p_spec jsonb,
    p_spec_index integer
) RETURNS TABLE(job_id uuid, created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q taskq.queues%ROWTYPE;
    v_parent taskq.jobs%ROWTYPE;
    v_workflow taskq.workflows%ROWTYPE;
    v_queue text;
    v_local_step text;
    v_step text;
    v_job_type text;
    v_payload jsonb;
    v_headers jsonb;
    v_priority smallint;
    v_max_attempts smallint;
    v_lease_seconds integer;
    v_scheduled_at timestamptz;
    v_key text;
    v_member boolean := false;
    v_intent_hash text;
    v_id uuid;
    v_existing taskq.jobs%ROWTYPE;
    v_try integer;
BEGIN
    IF p_spec_index IS NULL OR p_spec_index < 1 OR p_spec_index > 20 THEN
        RAISE EXCEPTION 'followup index must be 1..20' USING ERRCODE = 'TQ422';
    END IF;
    IF p_spec IS NULL OR jsonb_typeof(p_spec) <> 'object' THEN
        RAISE EXCEPTION 'followup spec % must be an object', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_object_keys(p_spec) AS k(key)
        WHERE k.key NOT IN (
            'step','job_type','queue','payload','headers','priority',
            'max_attempts','lease_seconds','scheduled_at','workflow_member'
        )
    ) THEN
        RAISE EXCEPTION 'followup spec % has an unknown field', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_spec ? 'workflow_member' THEN
        IF jsonb_typeof(p_spec->'workflow_member') <> 'boolean'
           OR (p_spec->>'workflow_member')::boolean IS NOT TRUE THEN
            RAISE EXCEPTION 'workflow_member may only be true when present'
                USING ERRCODE = 'TQ422';
        END IF;
        v_member := true;
    END IF;

    SELECT * INTO v_parent FROM taskq.jobs WHERE id = p_parent_job_id;
    IF NOT FOUND OR v_parent.queue IS DISTINCT FROM p_parent_queue THEN
        RAISE EXCEPTION 'continuation parent is inconsistent'
            USING ERRCODE = 'TQ500';
    END IF;

    v_local_step := p_spec->>'step';
    v_job_type := p_spec->>'job_type';
    v_queue := COALESCE(p_spec->>'queue', p_parent_queue);
    IF v_local_step IS NULL OR octet_length(v_local_step) NOT BETWEEN 1 AND 64
       OR v_local_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
        RAISE EXCEPTION 'followup spec % has invalid step', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(v_job_type, '') = '' OR char_length(v_job_type) > 120 THEN
        RAISE EXCEPTION 'followup spec % requires job_type <= 120 chars', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    IF COALESCE(v_queue, '') = '' THEN
        RAISE EXCEPTION 'followup spec % has no queue', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    v_payload := COALESCE(p_spec->'payload', '{}'::jsonb);
    v_headers := COALESCE(p_spec->'headers', '{}'::jsonb);
    IF jsonb_typeof(v_payload) <> 'object' OR octet_length(v_payload::text) > 65536
       OR jsonb_typeof(v_headers) <> 'object' OR octet_length(v_headers::text) > 8192 THEN
        RAISE EXCEPTION 'followup spec % has invalid bounded JSON', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;
    BEGIN
        IF p_spec ? 'priority' THEN
            IF jsonb_typeof(p_spec->'priority') <> 'number'
               OR (p_spec->>'priority') !~ '^-?[0-9]+$' THEN RAISE data_exception; END IF;
            v_priority := (p_spec->>'priority')::smallint;
        END IF;
        IF p_spec ? 'max_attempts' THEN
            IF jsonb_typeof(p_spec->'max_attempts') <> 'number'
               OR (p_spec->>'max_attempts') !~ '^-?[0-9]+$' THEN RAISE data_exception; END IF;
            v_max_attempts := (p_spec->>'max_attempts')::smallint;
        END IF;
        IF p_spec ? 'lease_seconds' THEN
            IF jsonb_typeof(p_spec->'lease_seconds') <> 'number'
               OR (p_spec->>'lease_seconds') !~ '^-?[0-9]+$' THEN RAISE data_exception; END IF;
            v_lease_seconds := (p_spec->>'lease_seconds')::integer;
        END IF;
        IF p_spec ? 'scheduled_at' THEN
            IF jsonb_typeof(p_spec->'scheduled_at') <> 'string' THEN RAISE data_exception; END IF;
            v_scheduled_at := (p_spec->>'scheduled_at')::timestamptz;
        END IF;
    EXCEPTION WHEN data_exception OR invalid_text_representation OR datetime_field_overflow
                   OR numeric_value_out_of_range THEN
        RAISE EXCEPTION 'followup spec % has an invalid scalar field', p_spec_index
            USING ERRCODE = 'TQ422';
    END;
    IF v_priority IS NOT NULL AND v_priority NOT BETWEEN 0 AND 1000
       OR v_max_attempts IS NOT NULL AND v_max_attempts NOT BETWEEN 1 AND 100
       OR v_lease_seconds IS NOT NULL AND v_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'followup spec % has an out-of-range scalar field', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO q FROM taskq.queues WHERE name = v_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'followup spec % names unknown queue', p_spec_index
            USING ERRCODE = 'TQ422';
    END IF;

    v_key := 'chain:' || lower(p_parent_job_id::text) || ':' || v_local_step;
    v_step := NULL;
    IF v_member THEN
        IF v_parent.workflow_id IS NULL OR v_parent.continuation_policy_hash IS NULL THEN
            RAISE EXCEPTION 'continuation parent is not a policy workflow member'
                USING ERRCODE = 'TQ422',
                      DETAIL = '{"reason":"continuation_parent_not_member"}';
        END IF;
        SELECT * INTO v_workflow FROM taskq.workflows
        WHERE id = v_parent.workflow_id;
        IF NOT FOUND
           OR v_workflow.continuation_policy_hash IS DISTINCT FROM
              v_parent.continuation_policy_hash THEN
            RAISE EXCEPTION 'continuation policy identity is inconsistent'
                USING ERRCODE = 'TQ500';
        END IF;
        IF NOT (v_queue = ANY(v_workflow.declared_queues)) THEN
            RAISE EXCEPTION 'continuation queue is not declared by the workflow'
                USING ERRCODE = 'TQ422',
                      DETAIL = '{"reason":"continuation_queue_undeclared"}';
        END IF;
        v_step := 'c:' || lower(p_parent_job_id::text) || ':' || v_local_step;
        v_intent_hash := encode(sha256(convert_to(
            jsonb_build_object(
                'queue',v_queue,'job_type',v_job_type,'payload',v_payload,
                'headers',v_headers,'priority',COALESCE(v_priority,q.default_priority),
                'scheduled_at',CASE WHEN p_spec ? 'scheduled_at' THEN v_scheduled_at ELSE NULL END,
                'max_attempts',COALESCE(v_max_attempts,q.default_max_attempts),
                'lease_seconds',COALESCE(v_lease_seconds,q.default_lease_seconds),
                'parent_job_id',p_parent_job_id
            )::text, 'UTF8'
        )), 'hex');
    END IF;

    v_scheduled_at := COALESCE(v_scheduled_at, now());
    FOR v_try IN 1..3 LOOP
        v_id := taskq.uuid7();
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, parent_job_id, pending_deps, scheduled_at,
            lease_seconds, max_attempts, backoff_mode,
            backoff_base_seconds, backoff_cap_seconds,
            workflow_id, step_key, workflow_intent_hash,
            continuation_policy_hash
        ) VALUES (
            v_id, v_queue, v_job_type, 'queued',
            COALESCE(v_priority, q.default_priority), v_payload, v_headers,
            v_key, p_parent_job_id, 0, v_scheduled_at,
            COALESCE(v_lease_seconds, q.default_lease_seconds),
            COALESCE(v_max_attempts, q.default_max_attempts),
            q.default_backoff_mode, q.default_backoff_base, q.default_backoff_cap,
            CASE WHEN v_member THEN v_parent.workflow_id END,
            v_step, v_intent_hash,
            CASE WHEN v_member THEN v_parent.continuation_policy_hash END
        )
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL AND status IN ('blocked','queued','running')
            DO NOTHING;
        IF FOUND THEN
            PERFORM taskq.emit_event(v_id, NULL, 'enqueued', 'system', NULL,
                jsonb_build_object('status','queued','scheduled_at',v_scheduled_at));
            IF v_scheduled_at <= now() AND q.notify_enabled THEN
                PERFORM pg_notify('taskq_' || v_queue, '');
            END IF;
            RETURN QUERY SELECT v_id, true;
            RETURN;
        END IF;
        SELECT j.* INTO v_existing FROM taskq.jobs AS j
        WHERE j.queue = v_queue AND j.idempotency_key = v_key
          AND j.status IN ('blocked','queued','running')
        ORDER BY j.created_at DESC LIMIT 1;
        IF FOUND THEN
            IF v_existing.parent_job_id IS DISTINCT FROM p_parent_job_id
               OR v_existing.job_type IS DISTINCT FROM v_job_type
               OR v_existing.payload IS DISTINCT FROM v_payload
               OR v_existing.headers IS DISTINCT FROM v_headers
               OR v_existing.priority IS DISTINCT FROM COALESCE(v_priority,q.default_priority)
               OR v_existing.max_attempts IS DISTINCT FROM
                  COALESCE(v_max_attempts,q.default_max_attempts)
               OR v_existing.lease_seconds IS DISTINCT FROM
                  COALESCE(v_lease_seconds,q.default_lease_seconds)
               OR v_existing.workflow_id IS DISTINCT FROM
                  (CASE WHEN v_member THEN v_parent.workflow_id END)
               OR v_existing.step_key IS DISTINCT FROM v_step
               OR v_existing.workflow_intent_hash IS DISTINCT FROM v_intent_hash
               OR v_existing.continuation_policy_hash IS DISTINCT FROM
                  (CASE WHEN v_member THEN v_parent.continuation_policy_hash END)
               OR (p_spec ? 'scheduled_at'
                   AND v_existing.scheduled_at IS DISTINCT FROM v_scheduled_at) THEN
                RAISE EXCEPTION 'followup idempotency key has an inconsistent holder'
                    USING ERRCODE = 'TQ500';
            END IF;
            RETURN QUERY SELECT v_existing.id, false;
            RETURN;
        END IF;
    END LOOP;
    RAISE EXCEPTION 'followup idempotency insert did not converge' USING ERRCODE = 'TQ500';
END $$;
ALTER FUNCTION taskq._enqueue_followup(uuid,text,jsonb,integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq._enqueue_followup(uuid,text,jsonb,integer) FROM PUBLIC;

-- Protocol-1.0.14 compatibility claim: old workers are confined to the
-- null-policy cohort even after the 0.2.5 catalog is installed.
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
    IF p_lease_seconds IS NOT NULL AND p_lease_seconds NOT BETWEEN 15 AND 86400 THEN
        RAISE EXCEPTION 'lease override must be 15..86400 seconds'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_types IS NOT NULL AND cardinality(p_job_types) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'job type filter must have 1..20 entries'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_affinity_key IS NOT NULL AND char_length(p_affinity_key) > 120 THEN
        RAISE EXCEPTION 'affinity_key exceeds 120 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;

    SELECT q.paused_at INTO v_paused_at FROM taskq.queues AS q WHERE q.name = p_queue;
    IF NOT FOUND THEN
        RETURN ROW('unknown_queue','{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    IF v_paused_at IS NOT NULL THEN
        RETURN ROW('paused','{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    SELECT COALESCE(array_agg(k.key),'{}') INTO v_saturated
    FROM (
        SELECT r.concurrency_key AS key, count(*) AS c
        FROM taskq.jobs AS r
        WHERE r.status = 'running' AND r.concurrency_key IS NOT NULL
        GROUP BY r.concurrency_key
    ) AS k
    WHERE k.c >= COALESCE(
        (SELECT l.max_running FROM taskq.concurrency_limits AS l WHERE l.key = k.key),1
    );

    WHILE v_claimed < v_batch AND v_scans < v_batch + 20 LOOP
        v_scans := v_scans + 1;
        v_job := NULL;
        IF v_affinity IS NOT NULL AND p_job_id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs AS j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.continuation_policy_hash IS NULL
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND (j.workflow_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taskq.workflows AS w
                    WHERE w.id = j.workflow_id AND w.cancel_requested_at IS NOT NULL))
              AND j.affinity_key = v_affinity
              AND (p_job_types IS NULL OR j.job_type = ANY(p_job_types))
              AND NOT (j.id = ANY(v_skip))
              AND (j.concurrency_key IS NULL
                   OR NOT (j.concurrency_key = ANY(v_saturated)))
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED;
            IF v_job.id IS NULL THEN v_affinity := NULL; END IF;
        END IF;
        IF v_job.id IS NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs AS j
            WHERE j.queue = p_queue AND j.status = 'queued'
              AND j.continuation_policy_hash IS NULL
              AND j.scheduled_at <= now() AND j.cancel_requested_at IS NULL
              AND (j.workflow_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taskq.workflows AS w
                    WHERE w.id = j.workflow_id AND w.cancel_requested_at IS NOT NULL))
              AND (p_job_id IS NULL OR j.id = p_job_id)
              AND (p_job_types IS NULL OR j.job_type = ANY(p_job_types))
              AND NOT (j.id = ANY(v_skip))
              AND (j.concurrency_key IS NULL
                   OR NOT (j.concurrency_key = ANY(v_saturated)))
            ORDER BY j.priority, j.scheduled_at, j.id
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED;
        END IF;
        EXIT WHEN v_job.id IS NULL;
        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended('taskq.ck:' || v_job.concurrency_key,0)
            ) THEN
                v_skip := v_skip || v_job.id; CONTINUE;
            END IF;
            SELECT COALESCE((SELECT l.max_running FROM taskq.concurrency_limits AS l
                             WHERE l.key = v_job.concurrency_key),1) INTO v_cap;
            SELECT count(*) INTO v_running FROM taskq.jobs AS r
            WHERE r.status = 'running' AND r.concurrency_key = v_job.concurrency_key;
            IF v_running >= v_cap THEN v_skip := v_skip || v_job.id; CONTINUE; END IF;
        END IF;
        v_attempt_id := taskq.uuid7();
        v_lease := COALESCE(p_lease_seconds,v_job.lease_seconds);
        UPDATE taskq.jobs AS j
        SET status='running',worker_id=p_worker_id,current_attempt_id=v_attempt_id,
            attempt_count=j.attempt_count+1,
            lease_expires_at=now()+make_interval(secs=>v_lease),
            started_at=COALESCE(j.started_at,now()),updated_at=now()
        WHERE j.id=v_job.id;
        INSERT INTO taskq.job_attempts(id,job_id,worker_id,lease_seconds)
        VALUES(v_attempt_id,v_job.id,p_worker_id,v_lease);
        PERFORM taskq.emit_event(v_job.id,v_attempt_id,'claimed',p_worker_id,NULL,
            jsonb_build_object('attempt',v_job.attempt_count+1));
        v_claimed := v_claimed + 1;
        v_jobs := v_jobs || ROW(
            v_job.id,v_job.queue,v_job.job_type,v_job.priority,v_job.payload,
            v_job.headers,v_job.progress,v_attempt_id,
            (v_job.attempt_count+1)::integer,v_job.failure_count,
            v_job.max_attempts,now()+make_interval(secs=>v_lease),
            v_job.workflow_id,v_job.step_key,v_lease,NULL
        )::taskq.claimed_job;
    END LOOP;
    IF v_claimed = 0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            RETURN ROW('unavailable','{}'::taskq.claimed_job[])::taskq.claim_batch;
        END IF;
        RETURN ROW('empty','{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed',v_jobs)::taskq.claim_batch;
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)
    TO taskq_runner;

-- Policy-aware claim. Each policy cohort contributes at most batch rows to the
-- candidate frontier, bounding inspection at (1 + supported hashes) * batch.
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
    v_hashes text[] := COALESCE(p_continuation_policy_hashes,'{}');
    v_saturated text[] := '{}';
    v_paused_at timestamptz;
    v_jobs taskq.claimed_job[] := '{}';
BEGIN
    IF NOT taskq.has_capability('workflow_continuations') THEN
        RAISE EXCEPTION 'workflow continuations are not enabled by this contract version'
            USING ERRCODE = 'TQ501';
    END IF;
    IF COALESCE(p_worker_id,'') = '' OR length(p_worker_id) > 200
       OR p_batch IS NULL OR p_batch NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'invalid worker or claim batch' USING ERRCODE = 'TQ422';
    END IF;
    IF p_lease_seconds IS NOT NULL AND p_lease_seconds NOT BETWEEN 15 AND 86400
       OR p_job_types IS NOT NULL AND cardinality(p_job_types) NOT BETWEEN 1 AND 20
       OR p_affinity_key IS NOT NULL AND char_length(p_affinity_key) > 120 THEN
        RAISE EXCEPTION 'invalid claim filter' USING ERRCODE = 'TQ422';
    END IF;
    IF cardinality(v_hashes) > 32
       OR cardinality(v_hashes) <> cardinality(ARRAY(SELECT DISTINCT unnest(v_hashes)))
       OR EXISTS (SELECT 1 FROM unnest(v_hashes) AS h
                  WHERE h IS NULL OR h !~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'supported continuation policies must be 0..32 distinct hashes'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_job_id IS NOT NULL THEN v_batch := 1; END IF;
    SELECT q.paused_at INTO v_paused_at FROM taskq.queues AS q WHERE q.name=p_queue;
    IF NOT FOUND THEN
        RETURN ROW('unknown_queue','{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    IF v_paused_at IS NOT NULL THEN
        RETURN ROW('paused','{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    SELECT COALESCE(array_agg(k.key),'{}') INTO v_saturated
    FROM (SELECT r.concurrency_key AS key,count(*) AS c FROM taskq.jobs AS r
          WHERE r.status='running' AND r.concurrency_key IS NOT NULL
          GROUP BY r.concurrency_key) AS k
    WHERE k.c >= COALESCE((SELECT l.max_running FROM taskq.concurrency_limits AS l
                           WHERE l.key=k.key),1);

    WHILE v_claimed < v_batch AND v_scans < v_batch + 20 LOOP
        v_scans := v_scans + 1;
        v_job := NULL;
        IF p_job_id IS NOT NULL THEN
            SELECT j.* INTO v_job FROM taskq.jobs AS j
            WHERE j.id=p_job_id AND j.queue=p_queue AND j.status='queued'
              AND (j.continuation_policy_hash IS NULL
                   OR j.continuation_policy_hash=ANY(v_hashes))
              AND j.scheduled_at<=now() AND j.cancel_requested_at IS NULL
              AND (p_job_types IS NULL OR j.job_type=ANY(p_job_types))
              AND (j.workflow_id IS NULL OR NOT EXISTS(
                    SELECT 1 FROM taskq.workflows AS w
                    WHERE w.id=j.workflow_id AND w.cancel_requested_at IS NOT NULL))
            FOR UPDATE OF j SKIP LOCKED;
        ELSIF v_affinity IS NOT NULL THEN
            SELECT frontier.* INTO v_job
            FROM unnest(array_prepend(NULL::text,v_hashes)) AS policy(hash)
            CROSS JOIN LATERAL (
                SELECT j.* FROM taskq.jobs AS j
                WHERE j.queue=p_queue AND j.status='queued'
                  AND j.continuation_policy_hash IS NOT DISTINCT FROM policy.hash
                  AND j.scheduled_at<=now() AND j.cancel_requested_at IS NULL
                  AND j.affinity_key=v_affinity
                  AND (p_job_types IS NULL OR j.job_type=ANY(p_job_types))
                  AND NOT (j.id=ANY(v_skip))
                  AND (j.concurrency_key IS NULL
                       OR NOT (j.concurrency_key=ANY(v_saturated)))
                  AND (j.workflow_id IS NULL OR NOT EXISTS(
                        SELECT 1 FROM taskq.workflows AS w
                        WHERE w.id=j.workflow_id AND w.cancel_requested_at IS NOT NULL))
                ORDER BY j.continuation_policy_hash,j.priority,j.scheduled_at,j.id
                LIMIT v_batch
            ) AS frontier
            ORDER BY frontier.priority,frontier.scheduled_at,frontier.id
            LIMIT 1 FOR UPDATE OF frontier SKIP LOCKED;
            IF v_job.id IS NULL THEN v_affinity := NULL; END IF;
        END IF;
        IF v_job.id IS NULL AND p_job_id IS NULL THEN
            SELECT frontier.* INTO v_job
            FROM unnest(array_prepend(NULL::text,v_hashes)) AS policy(hash)
            CROSS JOIN LATERAL (
                SELECT j.* FROM taskq.jobs AS j
                WHERE j.queue=p_queue AND j.status='queued'
                  AND j.continuation_policy_hash IS NOT DISTINCT FROM policy.hash
                  AND j.scheduled_at<=now() AND j.cancel_requested_at IS NULL
                  AND (p_job_types IS NULL OR j.job_type=ANY(p_job_types))
                  AND NOT (j.id=ANY(v_skip))
                  AND (j.concurrency_key IS NULL
                       OR NOT (j.concurrency_key=ANY(v_saturated)))
                  AND (j.workflow_id IS NULL OR NOT EXISTS(
                        SELECT 1 FROM taskq.workflows AS w
                        WHERE w.id=j.workflow_id AND w.cancel_requested_at IS NOT NULL))
                ORDER BY j.continuation_policy_hash,j.priority,j.scheduled_at,j.id
                LIMIT v_batch
            ) AS frontier
            ORDER BY frontier.priority,frontier.scheduled_at,frontier.id
            LIMIT 1 FOR UPDATE OF frontier SKIP LOCKED;
        END IF;
        EXIT WHEN v_job.id IS NULL;
        IF v_job.concurrency_key IS NOT NULL THEN
            IF NOT pg_try_advisory_xact_lock(
                hashtextextended('taskq.ck:'||v_job.concurrency_key,0)
            ) THEN v_skip:=v_skip||v_job.id; CONTINUE; END IF;
            SELECT COALESCE((SELECT l.max_running FROM taskq.concurrency_limits AS l
                             WHERE l.key=v_job.concurrency_key),1) INTO v_cap;
            SELECT count(*) INTO v_running FROM taskq.jobs AS r
            WHERE r.status='running' AND r.concurrency_key=v_job.concurrency_key;
            IF v_running>=v_cap THEN v_skip:=v_skip||v_job.id; CONTINUE; END IF;
        END IF;
        v_attempt_id:=taskq.uuid7();
        v_lease:=COALESCE(p_lease_seconds,v_job.lease_seconds);
        UPDATE taskq.jobs AS j
        SET status='running',worker_id=p_worker_id,current_attempt_id=v_attempt_id,
            attempt_count=j.attempt_count+1,
            lease_expires_at=now()+make_interval(secs=>v_lease),
            started_at=COALESCE(j.started_at,now()),updated_at=now()
        WHERE j.id=v_job.id;
        INSERT INTO taskq.job_attempts(id,job_id,worker_id,lease_seconds)
        VALUES(v_attempt_id,v_job.id,p_worker_id,v_lease);
        PERFORM taskq.emit_event(v_job.id,v_attempt_id,'claimed',p_worker_id,NULL,
            jsonb_build_object('attempt',v_job.attempt_count+1));
        v_claimed:=v_claimed+1;
        v_jobs:=v_jobs||ROW(
            v_job.id,v_job.queue,v_job.job_type,v_job.priority,v_job.payload,
            v_job.headers,v_job.progress,v_attempt_id,
            (v_job.attempt_count+1)::integer,v_job.failure_count,
            v_job.max_attempts,now()+make_interval(secs=>v_lease),
            v_job.workflow_id,v_job.step_key,v_lease,v_job.continuation_policy_hash
        )::taskq.claimed_job;
    END LOOP;
    IF v_claimed=0 THEN
        PERFORM taskq.reap_expired(5);
        IF p_job_id IS NOT NULL THEN
            RETURN ROW('unavailable','{}'::taskq.claimed_job[])::taskq.claim_batch;
        END IF;
        RETURN ROW('empty','{}'::taskq.claimed_job[])::taskq.claim_batch;
    END IF;
    RETURN ROW('claimed',v_jobs)::taskq.claim_batch;
END $$;
ALTER FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])
    TO taskq_runner;

-- Protocol-1.0.14 completion bridge. It can settle only null-policy parents
-- and its legacy JSON shape cannot request workflow membership.
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
    v_dep record;
    v_promoted record;
BEGIN
    IF COALESCE(p_worker_id,'')='' OR length(p_worker_id)>200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE='TQ422';
    END IF;
    IF p_result IS NOT NULL AND octet_length(p_result::text)>8192 THEN
        RAISE EXCEPTION 'result exceeds the 8KB limit' USING ERRCODE='TQ422';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_typeof(p_followups)<>'array' THEN
        RAISE EXCEPTION 'p_followups must be a jsonb array' USING ERRCODE='TQ422';
    END IF;
    SELECT j.status,j.current_attempt_id,j.finished_by_attempt_id,j.queue,
           j.continuation_policy_hash
    INTO v_job FROM taskq.jobs AS j WHERE j.id=p_job_id FOR UPDATE;
    IF NOT FOUND THEN RETURN ('lost',NULL,NULL)::taskq.settle_result; END IF;
    IF v_job.status<>'running' OR v_job.current_attempt_id IS DISTINCT FROM p_attempt_id THEN
        SELECT a.status INTO v_att FROM taskq.job_attempts AS a
        WHERE a.id=p_attempt_id AND a.job_id=p_job_id;
        IF v_att='succeeded' THEN
            RETURN ('already_settled',v_job.status,NULL)::taskq.settle_result;
        ELSIF v_att IN ('failed','released','snoozed','cancelled','expired') THEN
            RETURN ('settle_conflict',v_job.status,NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost',NULL,NULL)::taskq.settle_result;
    END IF;
    IF v_job.continuation_policy_hash IS NOT NULL THEN
        RAISE EXCEPTION 'policy-bearing jobs require policy-aware completion'
            USING ERRCODE='TQ422',
                  DETAIL='{"reason":"continuation_policy_required"}';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_array_length(p_followups)>0
       AND NOT taskq.has_capability('followups') THEN
        RAISE EXCEPTION 'followups are not enabled by this contract version'
            USING ERRCODE='TQ501';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_array_length(p_followups)>20 THEN
        RAISE EXCEPTION 'followup cap is 20 per settlement' USING ERRCODE='TQ422';
    END IF;
    FOR v_spec IN SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]')) LOOP
        v_index:=v_index+1;
        IF jsonb_typeof(v_spec)<>'object' OR EXISTS(
            SELECT 1 FROM jsonb_object_keys(v_spec) AS k(key)
            WHERE k.key NOT IN ('step','job_type','queue','payload','headers','priority',
                                'max_attempts','lease_seconds','scheduled_at')
        ) THEN
            RAISE EXCEPTION 'followup spec % has invalid shape',v_index
                USING ERRCODE='TQ422';
        END IF;
        v_step:=v_spec->>'step';
        IF v_step IS NULL OR octet_length(v_step) NOT BETWEEN 1 AND 64
           OR v_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
            RAISE EXCEPTION 'followup spec % has invalid step',v_index
                USING ERRCODE='TQ422';
        END IF;
        IF v_step=ANY(v_steps) THEN
            RAISE EXCEPTION 'duplicate followup step' USING ERRCODE='TQ422';
        END IF;
        v_steps:=array_append(v_steps,v_step);
    END LOOP;

    UPDATE taskq.jobs SET status='succeeded',outcome='success',worker_id=NULL,
        current_attempt_id=NULL,lease_expires_at=NULL,result=COALESCE(p_result,result),
        error=NULL,expiry_streak=0,finished_at=now(),
        finished_by_attempt_id=p_attempt_id,updated_at=now()
    WHERE id=p_job_id;
    UPDATE taskq.job_attempts SET status='succeeded',outcome='success',
        finished_at=now(),stats=COALESCE(p_stats,stats)
    WHERE id=p_attempt_id AND status='running';
    FOR v_dep IN
        SELECT d.job_id FROM taskq.job_deps AS d
        JOIN taskq.jobs AS child ON child.id=d.job_id
        WHERE d.depends_on=p_job_id AND child.status='blocked'
        ORDER BY d.job_id LIMIT 100 FOR UPDATE OF child SKIP LOCKED
    LOOP
        DELETE FROM taskq.job_deps WHERE job_id=v_dep.job_id AND depends_on=p_job_id;
        IF FOUND THEN
            UPDATE taskq.jobs
            SET pending_deps=pending_deps-1,
                status=CASE WHEN pending_deps=1 THEN 'queued' ELSE status END,
                updated_at=now()
            WHERE id=v_dep.job_id AND status='blocked'
            RETURNING queue,status,scheduled_at INTO v_promoted;
            IF v_promoted.status='queued' AND v_promoted.scheduled_at<=now()
               AND EXISTS(SELECT 1 FROM taskq.queues
                          WHERE name=v_promoted.queue AND notify_enabled) THEN
                PERFORM pg_notify('taskq_'||v_promoted.queue,'');
            END IF;
        END IF;
    END LOOP;
    PERFORM taskq.emit_event(p_job_id,p_attempt_id,'succeeded',p_worker_id,NULL,NULL);
    v_index:=0;
    FOR v_spec IN SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]')) LOOP
        v_index:=v_index+1;
        PERFORM * FROM taskq._enqueue_followup(p_job_id,v_job.queue,v_spec,v_index);
    END LOOP;
    RETURN ('ok','succeeded',NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)
    TO taskq_runner;

-- Policy-aware completion. The capability gate is deliberately after fencing
-- and replay classification but before any state mutation.
CREATE FUNCTION taskq.complete_job(
    p_job_id uuid,
    p_attempt_id uuid,
    p_worker_id text,
    p_result jsonb,
    p_stats jsonb,
    p_followups jsonb,
    p_continuation_policy_hash text
) RETURNS taskq.settle_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_job taskq.jobs%ROWTYPE;
    v_workflow taskq.workflows%ROWTYPE;
    v_att text;
    v_spec jsonb;
    v_index integer:=0;
    v_step text;
    v_steps text[]:='{}';
    v_member_count integer:=0;
    v_queue text;
    v_dep record;
    v_promoted record;
BEGIN
    IF COALESCE(p_worker_id,'')='' OR length(p_worker_id)>200 THEN
        RAISE EXCEPTION 'worker_id required (<=200 chars)' USING ERRCODE='TQ422';
    END IF;
    IF p_result IS NOT NULL AND octet_length(p_result::text)>8192 THEN
        RAISE EXCEPTION 'result exceeds the 8KB limit' USING ERRCODE='TQ422';
    END IF;
    IF p_followups IS NOT NULL AND jsonb_typeof(p_followups)<>'array' THEN
        RAISE EXCEPTION 'p_followups must be a jsonb array' USING ERRCODE='TQ422';
    END IF;
    SELECT * INTO v_job FROM taskq.jobs WHERE id=p_job_id FOR UPDATE;
    IF NOT FOUND THEN RETURN ('lost',NULL,NULL)::taskq.settle_result; END IF;
    IF v_job.status<>'running' OR v_job.current_attempt_id IS DISTINCT FROM p_attempt_id THEN
        SELECT a.status INTO v_att FROM taskq.job_attempts AS a
        WHERE a.id=p_attempt_id AND a.job_id=p_job_id;
        IF v_att='succeeded' THEN
            RETURN ('already_settled',v_job.status,NULL)::taskq.settle_result;
        ELSIF v_att IN ('failed','released','snoozed','cancelled','expired') THEN
            RETURN ('settle_conflict',v_job.status,NULL)::taskq.settle_result;
        END IF;
        RETURN ('lost',NULL,NULL)::taskq.settle_result;
    END IF;
    IF NOT taskq.has_capability('workflow_continuations') THEN
        RAISE EXCEPTION 'workflow continuations are not enabled by this contract version'
            USING ERRCODE='TQ501';
    END IF;
    IF v_job.continuation_policy_hash IS NULL THEN
        IF p_continuation_policy_hash IS NOT NULL THEN
            RAISE EXCEPTION 'continuation policy witness does not match'
                USING ERRCODE='TQ409',
                      DETAIL='{"reason":"continuation_policy_mismatch"}';
        END IF;
    ELSE
        IF p_continuation_policy_hash IS NULL THEN
            RAISE EXCEPTION 'continuation policy witness is required'
                USING ERRCODE='TQ422',
                      DETAIL='{"reason":"continuation_policy_required"}';
        END IF;
        IF p_continuation_policy_hash IS DISTINCT FROM v_job.continuation_policy_hash THEN
            RAISE EXCEPTION 'continuation policy witness does not match'
                USING ERRCODE='TQ409',
                      DETAIL='{"reason":"continuation_policy_mismatch"}';
        END IF;
        SELECT * INTO v_workflow FROM taskq.workflows WHERE id=v_job.workflow_id;
        IF NOT FOUND OR v_workflow.continuation_policy_hash IS DISTINCT FROM
                        v_job.continuation_policy_hash THEN
            RAISE EXCEPTION 'continuation policy identity is inconsistent'
                USING ERRCODE='TQ500';
        END IF;
        IF v_workflow.status <> 'running' THEN
            RAISE EXCEPTION 'continuation parent belongs to a terminal workflow'
                USING ERRCODE='TQ500';
        END IF;
    END IF;
    IF p_followups IS NOT NULL AND jsonb_array_length(p_followups)>20 THEN
        RAISE EXCEPTION 'followup cap is 20 per settlement' USING ERRCODE='TQ422';
    END IF;
    FOR v_spec IN SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]')) LOOP
        v_index:=v_index+1;
        IF jsonb_typeof(v_spec)<>'object' OR EXISTS(
            SELECT 1 FROM jsonb_object_keys(v_spec) AS k(key)
            WHERE k.key NOT IN ('step','job_type','queue','payload','headers','priority',
                                'max_attempts','lease_seconds','scheduled_at',
                                'workflow_member')
        ) THEN
            RAISE EXCEPTION 'followup spec % has invalid shape',v_index
                USING ERRCODE='TQ422';
        END IF;
        IF v_spec ? 'workflow_member'
           AND (jsonb_typeof(v_spec->'workflow_member')<>'boolean'
                OR (v_spec->>'workflow_member')::boolean IS NOT TRUE) THEN
            RAISE EXCEPTION 'workflow_member may only be true when present'
                USING ERRCODE='TQ422';
        END IF;
        v_step:=v_spec->>'step';
        IF v_step IS NULL OR octet_length(v_step) NOT BETWEEN 1 AND 64
           OR v_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$' THEN
            RAISE EXCEPTION 'followup spec % has invalid step',v_index
                USING ERRCODE='TQ422';
        END IF;
        IF v_step=ANY(v_steps) THEN
            RAISE EXCEPTION 'duplicate followup step' USING ERRCODE='TQ422';
        END IF;
        v_steps:=array_append(v_steps,v_step);
        IF v_spec ? 'workflow_member' THEN
            IF v_job.workflow_id IS NULL OR v_job.continuation_policy_hash IS NULL THEN
                RAISE EXCEPTION 'continuation parent is not a policy workflow member'
                    USING ERRCODE='TQ422',
                          DETAIL='{"reason":"continuation_parent_not_member"}';
            END IF;
            v_queue:=COALESCE(v_spec->>'queue',v_job.queue);
            IF NOT (v_queue=ANY(v_workflow.declared_queues)) THEN
                RAISE EXCEPTION 'continuation queue is not declared by the workflow'
                    USING ERRCODE='TQ422',
                          DETAIL='{"reason":"continuation_queue_undeclared"}';
            END IF;
            v_member_count:=v_member_count+1;
        END IF;
    END LOOP;
    IF v_member_count>0 THEN
        PERFORM taskq._reserve_workflow_members(
            v_job.workflow_id,v_member_count,v_job.continuation_policy_hash
        );
    END IF;

    UPDATE taskq.jobs SET status='succeeded',outcome='success',worker_id=NULL,
        current_attempt_id=NULL,lease_expires_at=NULL,result=COALESCE(p_result,result),
        error=NULL,expiry_streak=0,finished_at=now(),
        finished_by_attempt_id=p_attempt_id,updated_at=now()
    WHERE id=p_job_id;
    UPDATE taskq.job_attempts SET status='succeeded',outcome='success',
        finished_at=now(),stats=COALESCE(p_stats,stats)
    WHERE id=p_attempt_id AND status='running';
    FOR v_dep IN
        SELECT d.job_id FROM taskq.job_deps AS d
        JOIN taskq.jobs AS child ON child.id=d.job_id
        WHERE d.depends_on=p_job_id AND child.status='blocked'
        ORDER BY d.job_id LIMIT 100 FOR UPDATE OF child SKIP LOCKED
    LOOP
        DELETE FROM taskq.job_deps WHERE job_id=v_dep.job_id AND depends_on=p_job_id;
        IF FOUND THEN
            UPDATE taskq.jobs
            SET pending_deps=pending_deps-1,
                status=CASE WHEN pending_deps=1 THEN 'queued' ELSE status END,
                updated_at=now()
            WHERE id=v_dep.job_id AND status='blocked'
            RETURNING queue,status,scheduled_at INTO v_promoted;
            IF v_promoted.status='queued' AND v_promoted.scheduled_at<=now()
               AND EXISTS(SELECT 1 FROM taskq.queues
                          WHERE name=v_promoted.queue AND notify_enabled) THEN
                PERFORM pg_notify('taskq_'||v_promoted.queue,'');
            END IF;
        END IF;
    END LOOP;
    PERFORM taskq.emit_event(p_job_id,p_attempt_id,'succeeded',p_worker_id,NULL,NULL);
    v_index:=0;
    FOR v_spec IN SELECT value FROM jsonb_array_elements(COALESCE(p_followups,'[]')) LOOP
        v_index:=v_index+1;
        PERFORM * FROM taskq._enqueue_followup(p_job_id,v_job.queue,v_spec,v_index);
    END LOOP;
    RETURN ('ok','succeeded',NULL)::taskq.settle_result;
END $$;
ALTER FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)
    TO taskq_runner;

-- Workflow control uses the weakest row lock that still serializes control
-- mutations.  It remains compatible with the FK key-share lock acquired by
-- an internal continuation insert.
CREATE OR REPLACE FUNCTION taskq.advance_workflow_cancellations(
    p_limit integer DEFAULT 100
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_workflow_id uuid;
    v_job uuid;
    v_status text;
    v_n integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'limit must be 1..1000' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_workflow_id IN
        SELECT w.id FROM taskq.workflows AS w
        WHERE w.cancel_requested_at IS NOT NULL AND w.status = 'running'
        ORDER BY w.cancel_requested_at, w.id
        LIMIT p_limit
    LOOP
        FOR v_job IN
            SELECT j.id FROM taskq.jobs AS j
            WHERE j.workflow_id = v_workflow_id
              AND (
                  j.status IN ('blocked','queued')
                  OR (j.status = 'running' AND j.cancel_requested_at IS NULL)
              )
            ORDER BY j.id
            LIMIT (p_limit - v_n)
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
            IF FOUND THEN v_n := v_n + 1; END IF;
            EXIT WHEN v_n >= p_limit;
        END LOOP;
        EXIT WHEN v_n >= p_limit;
    END LOOP;
    RETURN v_n;
END $$;
ALTER FUNCTION taskq.advance_workflow_cancellations(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.advance_workflow_cancellations(integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.finalize_workflows(
    p_limit integer DEFAULT 100
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_workflow taskq.workflows%ROWTYPE;
    v_counts taskq.workflow_member_counts%ROWTYPE;
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
        FOR NO KEY UPDATE SKIP LOCKED
    LOOP
        SELECT * INTO v_counts FROM taskq.workflow_member_counts
        WHERE workflow_id = v_workflow.id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'workflow counter invariant missing'
                USING ERRCODE = 'TQ500';
        END IF;
        IF v_counts.blocked + v_counts.queued + v_counts.running > 0 THEN
            UPDATE taskq.workflows SET updated_at = now()
            WHERE id = v_workflow.id;
            CONTINUE;
        END IF;
        v_status := CASE
            WHEN v_workflow.cancel_requested_at IS NOT NULL THEN 'cancelled'
            WHEN v_counts.failed > 0 THEN 'failed'
            WHEN v_counts.cancelled > 0 THEN 'cancelled'
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
    v_counts taskq.workflow_member_counts%ROWTYPE;
BEGIN
    SELECT * INTO v_workflow FROM taskq.workflows
    WHERE id = p_workflow_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
    END IF;
    IF v_workflow.sealed_at IS NOT NULL THEN
        RETURN ('already_sealed', v_workflow.id, v_workflow.status)::taskq.workflow_result;
    END IF;
    UPDATE taskq.workflows
    SET sealed_at = now(), sealed_by = p_actor, updated_at = now()
    WHERE id = p_workflow_id;
    SELECT * INTO v_counts FROM taskq.workflow_member_counts
    WHERE workflow_id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workflow counter invariant missing'
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_counts.blocked + v_counts.queued + v_counts.running = 0 THEN
        UPDATE taskq.workflows
        SET status = CASE
                WHEN v_counts.failed > 0 THEN 'failed'
                WHEN v_counts.cancelled > 0 THEN 'cancelled'
                ELSE 'succeeded'
            END,
            finished_at = now(), updated_at = now()
        WHERE id = p_workflow_id;
    END IF;
    SELECT * INTO v_workflow FROM taskq.workflows WHERE id = p_workflow_id;
    RETURN ('sealed', v_workflow.id, v_workflow.status)::taskq.workflow_result;
END $$;
ALTER FUNCTION taskq.seal_workflow(uuid,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.seal_workflow(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.seal_workflow(uuid,text) TO taskq_producer;

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
    v_counts taskq.workflow_member_counts%ROWTYPE;
BEGIN
    IF p_actor IS NULL OR p_actor = '' THEN
        RAISE EXCEPTION 'workflow actor is required' USING ERRCODE = 'TQ422';
    END IF;
    IF p_reason IS NOT NULL AND octet_length(p_reason) > 2048 THEN
        RAISE EXCEPTION 'workflow cancel reason exceeds 2KB' USING ERRCODE = 'TQ422';
    END IF;
    SELECT * INTO v_workflow FROM taskq.workflows
    WHERE id = p_workflow_id FOR NO KEY UPDATE;
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
    SELECT * INTO v_counts FROM taskq.workflow_member_counts
    WHERE workflow_id = p_workflow_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'workflow counter invariant missing'
            USING ERRCODE = 'TQ500';
    END IF;
    IF v_counts.blocked + v_counts.queued + v_counts.running = 0 THEN
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

INSERT INTO taskq.meta(key,value,updated_at)
VALUES ('contract_version','"0.2.5"'::jsonb,now())
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now();
