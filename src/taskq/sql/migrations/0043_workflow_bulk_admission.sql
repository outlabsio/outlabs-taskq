-- outlabs-taskq — migration 0043: workflow-safe bulk admission
-- SQL contract 0.6.7 / Protocol document revision 1.0.18.
--
-- enqueue_many keeps its existing identity and non-workflow behavior, but may
-- now admit one planning workflow's dependency-free members.  The workflow is
-- locked once, membership is reserved once, rows/events are inserted set-wise,
-- and the queue is notified once.  The existing 1000-item wire bound remains.
-- Depth checks use the active queue counter instead of the historical OFFSET
-- scan, with the old probe retained only for pre-counter installations.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.6.6"'::jsonb THEN
        RAISE EXCEPTION '0043 requires SQL contract 0.6.6, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","circuit_breaker","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0043 requires the exact 0.6.6 capability set, found %',
            v_capabilities;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION taskq.enqueue_many(p_queue text, p_jobs jsonb)
RETURNS TABLE (input_index int, job_id uuid, outcome text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q                  taskq.queues%ROWTYPE;
    v_workflow         taskq.workflows%ROWTYPE;
    v_existing_job     taskq.jobs%ROWTYPE;
    v_n                integer;
    v_i                integer;
    v_try              integer;
    v_spec             jsonb;
    v_field            text;
    v_key              text;
    v_step             text;
    v_existing         uuid;
    v_workflow_id      uuid;
    v_item_workflow_id uuid;
    v_membership_mode  boolean;
    v_intent_hash      text;
    v_intent_hashes    text[] := '{}';
    v_ids              uuid[] := '{}';
    v_out              uuid[];
    v_outcome          text[];
    v_created_set      uuid[];
    v_all_created      uuid[];
    v_new_count        integer;
    v_depth            bigint;
BEGIN
    IF p_jobs IS NULL OR jsonb_typeof(p_jobs) <> 'array'
       OR jsonb_array_length(p_jobs) NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'p_jobs must be an array of 1..1000 specs'
            USING ERRCODE = 'TQ422';
    END IF;
    v_n := jsonb_array_length(p_jobs);

    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;

    -- Validate every item before locking/reserving/inserting.  Workflow bulk
    -- deliberately remains dependency-free; DAG edges keep using enqueue.
    FOR v_i IN 1..v_n LOOP
        v_spec := p_jobs -> (v_i - 1);
        IF jsonb_typeof(v_spec) <> 'object' THEN
            RAISE EXCEPTION 'taskq: spec % must be a json object', v_i
                USING ERRCODE = 'TQ422';
        END IF;
        BEGIN
            FOR v_field IN SELECT jsonb_object_keys(v_spec) LOOP
                IF v_field = 'depends_on' THEN
                    RAISE EXCEPTION 'dependencies are not available in bulk admission';
                ELSIF v_field NOT IN (
                    'job_type','payload','headers','priority','scheduled_at',
                    'idempotency_key','concurrency_key','affinity_key',
                    'max_attempts','lease_seconds','backoff_mode','backoff_base',
                    'backoff_cap','parent_job_id','workflow_id','step_key',
                    'ttl_seconds','flow_key'
                ) THEN
                    RAISE EXCEPTION 'unknown field "%"', v_field;
                END IF;
            END LOOP;
            IF COALESCE(v_spec->>'job_type', '') = ''
               OR char_length(v_spec->>'job_type') > 120 THEN
                RAISE EXCEPTION 'job_type is required (<= 120 chars)';
            END IF;
            IF v_spec ? 'payload' AND jsonb_typeof(v_spec->'payload') <> 'object' THEN
                RAISE EXCEPTION 'payload must be a json object';
            END IF;
            IF octet_length(COALESCE(v_spec->'payload', '{}'::jsonb)::text) > 65536 THEN
                RAISE EXCEPTION 'payload exceeds the 64KB limit';
            END IF;
            IF v_spec ? 'headers' AND jsonb_typeof(v_spec->'headers') <> 'object' THEN
                RAISE EXCEPTION 'headers must be a json object';
            END IF;
            IF octet_length((v_spec->'headers')::text) > 8192 THEN
                RAISE EXCEPTION 'headers exceed the 8KB limit';
            END IF;
            IF (v_spec->>'priority')::integer NOT BETWEEN 0 AND 1000 THEN
                RAISE EXCEPTION 'priority must be 0..1000';
            END IF;
            IF (v_spec->>'lease_seconds')::integer NOT BETWEEN 15 AND 86400 THEN
                RAISE EXCEPTION 'lease_seconds must be 15..86400';
            END IF;
            IF (v_spec->>'max_attempts')::integer NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'max_attempts must be 1..100';
            END IF;
            IF v_spec ? 'backoff_mode'
               AND (v_spec->>'backoff_mode') NOT IN ('fixed','exponential') THEN
                RAISE EXCEPTION 'backoff_mode must be fixed|exponential';
            END IF;
            IF (v_spec->>'backoff_base')::integer NOT BETWEEN 1 AND 86400 THEN
                RAISE EXCEPTION 'backoff_base must be 1..86400';
            END IF;
            IF COALESCE((v_spec->>'backoff_cap')::integer, q.default_backoff_cap)
               < COALESCE((v_spec->>'backoff_base')::integer, q.default_backoff_base) THEN
                RAISE EXCEPTION 'backoff_cap below backoff_base';
            END IF;
            IF v_spec ? 'idempotency_key'
               AND (COALESCE(v_spec->>'idempotency_key','') = ''
                    OR char_length(v_spec->>'idempotency_key') > 255) THEN
                RAISE EXCEPTION 'idempotency_key must be 1..255 chars';
            END IF;
            IF (v_spec->>'idempotency_key') LIKE 'chain:%' THEN
                RAISE EXCEPTION 'idempotency_key uses an engine-reserved namespace';
            END IF;
            IF v_spec ? 'concurrency_key'
               AND (COALESCE(v_spec->>'concurrency_key','') = ''
                    OR char_length(v_spec->>'concurrency_key') > 120) THEN
                RAISE EXCEPTION 'concurrency_key must be 1..120 chars';
            END IF;
            IF v_spec ? 'affinity_key'
               AND (COALESCE(v_spec->>'affinity_key','') = ''
                    OR char_length(v_spec->>'affinity_key') > 120) THEN
                RAISE EXCEPTION 'affinity_key must be 1..120 chars';
            END IF;
            IF (v_spec->>'ttl_seconds')::integer NOT BETWEEN 1 AND 31536000 THEN
                RAISE EXCEPTION 'ttl_seconds must be 1..31536000';
            END IF;
            IF v_spec ? 'flow_key'
               AND (COALESCE(v_spec->>'flow_key','') = ''
                    OR char_length(v_spec->>'flow_key') > 120) THEN
                RAISE EXCEPTION 'flow_key must be 1..120 chars';
            END IF;
            IF ((v_spec->>'workflow_id') IS NULL)
               <> ((v_spec->>'step_key') IS NULL) THEN
                RAISE EXCEPTION 'workflow_id and step_key must be supplied together';
            END IF;
            v_item_workflow_id := (v_spec->>'workflow_id')::uuid;
            IF v_item_workflow_id IS NOT NULL
               AND v_item_workflow_id = '00000000-0000-0000-0000-000000000000'::uuid THEN
                RAISE EXCEPTION 'workflow_id must be non-nil';
            END IF;
            v_step := v_spec->>'step_key';
            IF v_step IS NOT NULL
               AND (octet_length(v_step) NOT BETWEEN 1 AND 64
                    OR v_step !~ '^[A-Za-z0-9][A-Za-z0-9._-]*$') THEN
                RAISE EXCEPTION 'invalid workflow step_key';
            END IF;
            PERFORM (v_spec->>'scheduled_at')::timestamptz;
            PERFORM (v_spec->>'parent_job_id')::uuid;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'taskq: spec % invalid: %', v_i, SQLERRM
                USING ERRCODE = 'TQ422';
        END;

        IF v_membership_mode IS NULL THEN
            v_membership_mode := v_item_workflow_id IS NOT NULL;
        ELSIF v_membership_mode IS DISTINCT FROM (v_item_workflow_id IS NOT NULL) THEN
            RAISE EXCEPTION 'bulk admission cannot mix workflow and ordinary jobs'
                USING ERRCODE = 'TQ422';
        END IF;
        IF v_item_workflow_id IS NOT NULL THEN
            IF v_workflow_id IS NULL THEN
                v_workflow_id := v_item_workflow_id;
            ELSIF v_workflow_id IS DISTINCT FROM v_item_workflow_id THEN
                RAISE EXCEPTION 'bulk admission must target one workflow'
                    USING ERRCODE = 'TQ422';
            END IF;
            v_intent_hash := encode(
                sha256(convert_to((jsonb_build_object(
                    'queue', p_queue,
                    'job_type', v_spec->>'job_type',
                    'payload', COALESCE(v_spec->'payload', '{}'::jsonb),
                    'priority', (v_spec->>'priority')::smallint,
                    'scheduled_at', (v_spec->>'scheduled_at')::timestamptz,
                    'idempotency_key', v_spec->>'idempotency_key',
                    'concurrency_key', v_spec->>'concurrency_key',
                    'affinity_key', v_spec->>'affinity_key',
                    'max_attempts', (v_spec->>'max_attempts')::smallint,
                    'lease_seconds', (v_spec->>'lease_seconds')::integer,
                    'backoff_mode', v_spec->>'backoff_mode',
                    'backoff_base', (v_spec->>'backoff_base')::integer,
                    'backoff_cap', (v_spec->>'backoff_cap')::integer,
                    'depends_on', '[]'::jsonb,
                    'parent_job_id', (v_spec->>'parent_job_id')::uuid,
                    'headers', v_spec->'headers'
                ) || CASE
                    WHEN (v_spec->>'ttl_seconds') IS NOT NULL
                         OR (v_spec->>'flow_key') IS NOT NULL THEN
                        jsonb_build_object(
                            'ttl_seconds', (v_spec->>'ttl_seconds')::integer,
                            'flow_key', v_spec->>'flow_key')
                    ELSE '{}'::jsonb
                END)::text, 'UTF8')),
                'hex'
            );
        ELSE
            v_intent_hash := NULL;
        END IF;
        v_intent_hashes := array_append(v_intent_hashes, v_intent_hash);
        v_ids := array_append(v_ids, taskq.uuid7());
    END LOOP;

    v_out := array_fill(NULL::uuid, ARRAY[v_n]);
    v_outcome := array_fill(NULL::text, ARRAY[v_n]);

    IF v_workflow_id IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM jsonb_array_elements(p_jobs) AS item(spec)
            GROUP BY spec->>'step_key'
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION 'workflow bulk step_keys must be distinct'
                USING ERRCODE = 'TQ422';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM jsonb_array_elements(p_jobs) AS item(spec)
            WHERE spec->>'idempotency_key' IS NOT NULL
            GROUP BY spec->>'idempotency_key'
            HAVING count(*) > 1
        ) THEN
            RAISE EXCEPTION 'workflow bulk idempotency keys must be distinct'
                USING ERRCODE = 'TQ409',
                      DETAIL = '{"reason":"workflow_step_mismatch"}';
        END IF;

        SELECT * INTO v_workflow
        FROM taskq.workflows
        WHERE id = v_workflow_id
        FOR NO KEY UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'taskq: no such workflow' USING ERRCODE = 'TQ001';
        END IF;
        IF NOT p_queue = ANY(v_workflow.declared_queues) THEN
            RAISE EXCEPTION 'queue is outside workflow declaration'
                USING ERRCODE = 'TQ422',
                      DETAIL = '{"reason":"continuation_queue_undeclared"}';
        END IF;

        -- Resolve exact step replays before checking sealed/depth state.  An
        -- all-replay call is therefore safe after sealing and at max depth.
        FOR v_i IN 1..v_n LOOP
            v_spec := p_jobs -> (v_i - 1);
            SELECT * INTO v_existing_job
            FROM taskq.jobs
            WHERE workflow_id = v_workflow_id
              AND step_key = v_spec->>'step_key'
            FOR UPDATE;
            IF FOUND THEN
                IF v_existing_job.workflow_intent_hash IS DISTINCT FROM v_intent_hashes[v_i]
                   OR v_existing_job.continuation_policy_hash IS DISTINCT FROM
                        v_workflow.continuation_policy_hash THEN
                    RAISE EXCEPTION 'workflow step intent mismatch'
                        USING ERRCODE = 'TQ409',
                              DETAIL = '{"reason":"workflow_step_mismatch"}';
                END IF;
                v_out[v_i] := v_existing_job.id;
                v_outcome[v_i] := 'existed';
            ELSIF v_workflow.sealed_at IS NOT NULL THEN
                RAISE EXCEPTION 'workflow membership is sealed'
                    USING ERRCODE = 'TQ409',
                          DETAIL = '{"reason":"workflow_sealed"}';
            ELSIF (v_spec->>'idempotency_key') IS NOT NULL THEN
                SELECT * INTO v_existing_job
                FROM taskq.jobs
                WHERE queue = p_queue
                  AND idempotency_key = v_spec->>'idempotency_key'
                  AND status IN ('blocked','queued','running')
                ORDER BY created_at DESC
                LIMIT 1;
                IF FOUND THEN
                    RAISE EXCEPTION 'workflow step intent mismatch'
                        USING ERRCODE = 'TQ409',
                              DETAIL = '{"reason":"workflow_step_mismatch"}';
                END IF;
            END IF;
        END LOOP;
    END IF;

    SELECT count(*) INTO v_new_count
    FROM generate_series(1, v_n) AS g(i)
    WHERE v_out[g.i] IS NULL;

    -- Counter-backed O(1) depth check when available.  The fallback preserves
    -- compatibility for an upgraded package inspecting a pre-counter schema.
    IF v_new_count > 0 AND q.max_depth IS NOT NULL THEN
        IF taskq.has_capability('queue_counters') THEN
            SELECT COALESCE(blocked, 0) + COALESCE(queued, 0)
            INTO v_depth
            FROM taskq.queue_counters
            WHERE queue = p_queue;
            v_depth := COALESCE(v_depth, 0);
            IF v_depth >= q.max_depth THEN
                RAISE EXCEPTION 'queue % at max_depth %', p_queue, q.max_depth
                    USING ERRCODE = 'TQ429';
            END IF;
        ELSIF EXISTS (
            SELECT 1 FROM taskq.jobs
            WHERE queue = p_queue AND status IN ('blocked','queued')
            OFFSET greatest(q.max_depth - 1, 0) LIMIT 1
        ) THEN
            RAISE EXCEPTION 'queue % at max_depth %', p_queue, q.max_depth
                USING ERRCODE = 'TQ429';
        END IF;
    END IF;

    IF v_workflow_id IS NOT NULL AND v_new_count > 0 THEN
        PERFORM taskq._reserve_workflow_members(
            v_workflow_id, v_new_count, v_workflow.continuation_policy_hash
        );
    END IF;

    -- One set-based insert.  Workflow rows are dependency-free and remain
    -- unclaimable until seal_workflow promotes the planning workflow.
    WITH specs AS (
        SELECT a.ord::integer AS i, a.spec
        FROM jsonb_array_elements(p_jobs) WITH ORDINALITY AS a(spec, ord)
    ), prepared AS (
        SELECT
            s.i,
            s.spec,
            COALESCE((s.spec->>'scheduled_at')::timestamptz, now()) AS scheduled,
            COALESCE(
                (s.spec->>'ttl_seconds')::integer,
                q.default_ttl_seconds
            ) AS ttl
        FROM specs AS s
        WHERE v_out[s.i] IS NULL
    ), ins AS (
        INSERT INTO taskq.jobs (
            id, queue, job_type, status, priority, payload, headers,
            idempotency_key, concurrency_key, affinity_key,
            workflow_id, step_key, workflow_intent_hash,
            continuation_policy_hash, parent_job_id, pending_deps,
            scheduled_at, lease_seconds, max_attempts,
            backoff_mode, backoff_base_seconds, backoff_cap_seconds,
            expires_at, flow_key
        )
        SELECT
            v_ids[p.i], p_queue, p.spec->>'job_type', 'queued',
            COALESCE((p.spec->>'priority')::smallint, q.default_priority),
            COALESCE(p.spec->'payload', '{}'::jsonb),
            p.spec->'headers',
            p.spec->>'idempotency_key',
            p.spec->>'concurrency_key',
            p.spec->>'affinity_key',
            v_workflow_id,
            p.spec->>'step_key',
            v_intent_hashes[p.i],
            CASE WHEN v_workflow_id IS NULL
                 THEN NULL ELSE v_workflow.continuation_policy_hash END,
            (p.spec->>'parent_job_id')::uuid,
            0,
            p.scheduled,
            COALESCE((p.spec->>'lease_seconds')::integer, q.default_lease_seconds),
            COALESCE((p.spec->>'max_attempts')::smallint, q.default_max_attempts),
            COALESCE(p.spec->>'backoff_mode', q.default_backoff_mode),
            COALESCE((p.spec->>'backoff_base')::integer, q.default_backoff_base),
            COALESCE((p.spec->>'backoff_cap')::integer, q.default_backoff_cap),
            CASE WHEN p.ttl IS NULL
                 THEN NULL ELSE p.scheduled + make_interval(secs => p.ttl) END,
            p.spec->>'flow_key'
        FROM prepared AS p
        ORDER BY p.i
        ON CONFLICT (queue, idempotency_key)
            WHERE idempotency_key IS NOT NULL
              AND status IN ('blocked','queued','running')
            DO NOTHING
        RETURNING id
    )
    SELECT COALESCE(array_agg(id), '{}') INTO v_created_set FROM ins;

    v_all_created := v_created_set;

    -- Resolve conflicts in later snapshots.  Ordinary bulk retains its
    -- established duplicate-key behavior; workflow conflicts must prove the
    -- same step intent or fail the whole call.
    FOR v_i IN 1..v_n LOOP
        CONTINUE WHEN v_out[v_i] IS NOT NULL;
        IF v_ids[v_i] = ANY(v_created_set) THEN
            v_out[v_i] := v_ids[v_i];
            v_outcome[v_i] := 'created';
            CONTINUE;
        END IF;
        v_spec := p_jobs -> (v_i - 1);
        v_key := v_spec->>'idempotency_key';
        FOR v_try IN 1..3 LOOP
            IF v_workflow_id IS NOT NULL THEN
                SELECT * INTO v_existing_job
                FROM taskq.jobs
                WHERE workflow_id = v_workflow_id
                  AND step_key = v_spec->>'step_key'
                FOR UPDATE;
                IF FOUND THEN
                    IF v_existing_job.workflow_intent_hash IS DISTINCT FROM
                           v_intent_hashes[v_i]
                       OR v_existing_job.continuation_policy_hash IS DISTINCT FROM
                           v_workflow.continuation_policy_hash THEN
                        RAISE EXCEPTION 'workflow step intent mismatch'
                            USING ERRCODE = 'TQ409',
                                  DETAIL = '{"reason":"workflow_step_mismatch"}';
                    END IF;
                    v_out[v_i] := v_existing_job.id;
                    v_outcome[v_i] := 'existed';
                    EXIT;
                END IF;
            END IF;
            IF v_key IS NOT NULL THEN
                v_existing := NULL;
                SELECT j.id INTO v_existing
                FROM taskq.jobs AS j
                WHERE j.queue = p_queue
                  AND j.idempotency_key = v_key
                  AND j.status IN ('blocked','queued','running')
                ORDER BY j.created_at DESC
                LIMIT 1;
                IF v_existing IS NOT NULL THEN
                    IF v_workflow_id IS NOT NULL THEN
                        RAISE EXCEPTION 'workflow step intent mismatch'
                            USING ERRCODE = 'TQ409',
                                  DETAIL = '{"reason":"workflow_step_mismatch"}';
                    END IF;
                    v_out[v_i] := v_existing;
                    v_outcome[v_i] := 'existed';
                    EXIT;
                END IF;
            END IF;

            INSERT INTO taskq.jobs (
                id, queue, job_type, status, priority, payload, headers,
                idempotency_key, concurrency_key, affinity_key,
                workflow_id, step_key, workflow_intent_hash,
                continuation_policy_hash, parent_job_id, pending_deps,
                scheduled_at, lease_seconds, max_attempts,
                backoff_mode, backoff_base_seconds, backoff_cap_seconds,
                expires_at, flow_key
            )
            SELECT
                v_ids[v_i], p_queue, v_spec->>'job_type', 'queued',
                COALESCE((v_spec->>'priority')::smallint, q.default_priority),
                COALESCE(v_spec->'payload', '{}'::jsonb),
                v_spec->'headers',
                v_spec->>'idempotency_key',
                v_spec->>'concurrency_key',
                v_spec->>'affinity_key',
                v_workflow_id,
                v_spec->>'step_key',
                v_intent_hashes[v_i],
                CASE WHEN v_workflow_id IS NULL
                     THEN NULL ELSE v_workflow.continuation_policy_hash END,
                (v_spec->>'parent_job_id')::uuid,
                0,
                COALESCE((v_spec->>'scheduled_at')::timestamptz, now()),
                COALESCE((v_spec->>'lease_seconds')::integer, q.default_lease_seconds),
                COALESCE((v_spec->>'max_attempts')::smallint, q.default_max_attempts),
                COALESCE(v_spec->>'backoff_mode', q.default_backoff_mode),
                COALESCE((v_spec->>'backoff_base')::integer, q.default_backoff_base),
                COALESCE((v_spec->>'backoff_cap')::integer, q.default_backoff_cap),
                CASE
                    WHEN COALESCE(
                        (v_spec->>'ttl_seconds')::integer,
                        q.default_ttl_seconds
                    ) IS NULL THEN NULL
                    ELSE COALESCE((v_spec->>'scheduled_at')::timestamptz, now())
                         + make_interval(secs => COALESCE(
                             (v_spec->>'ttl_seconds')::integer,
                             q.default_ttl_seconds
                         ))
                END,
                v_spec->>'flow_key'
            ON CONFLICT (queue, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                  AND status IN ('blocked','queued','running')
                DO NOTHING;
            IF FOUND THEN
                v_out[v_i] := v_ids[v_i];
                v_outcome[v_i] := 'created';
                v_all_created := v_all_created || v_ids[v_i];
                EXIT;
            END IF;
        END LOOP;
        IF v_out[v_i] IS NULL THEN
            RAISE EXCEPTION
                'taskq: bulk insert did not converge for key % on queue % (spec %)',
                v_key, p_queue, v_i USING ERRCODE = 'TQ500';
        END IF;
    END LOOP;

    IF cardinality(v_all_created) > 0 THEN
        INSERT INTO taskq.job_events (
            job_id, attempt_id, event_type, actor, message, data
        )
        SELECT
            j.id, NULL, 'enqueued', 'system', NULL,
            jsonb_build_object('status', j.status, 'scheduled_at', j.scheduled_at)
        FROM taskq.jobs AS j
        WHERE j.id = ANY(v_all_created);
    END IF;

    IF q.notify_enabled AND EXISTS (
        SELECT 1
        FROM taskq.jobs AS j
        WHERE j.id = ANY(v_all_created)
          AND j.status = 'queued'
          AND j.scheduled_at <= now()
    ) THEN
        PERFORM pg_notify('taskq_' || p_queue, '');
    END IF;

    RETURN QUERY
    SELECT g.i, v_out[g.i], v_outcome[g.i]
    FROM generate_series(1, v_n) AS g(i)
    ORDER BY g.i;
END $$;
ALTER FUNCTION taskq.enqueue_many(text, jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.enqueue_many(text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.enqueue_many(text, jsonb) TO taskq_producer;

-- Single admission uses the same O(1) depth source.  This closes the
-- implementation drift from the queue-counter contract while preserving the
-- historical OFFSET fallback for pre-0.4 schemas.
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
    p_headers jsonb DEFAULT NULL,
    p_ttl_seconds integer DEFAULT NULL,
    p_flow_key text DEFAULT NULL
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
    v_ttl integer;
    v_depth bigint;
BEGIN
    IF p_ttl_seconds IS NOT NULL AND p_ttl_seconds NOT BETWEEN 1 AND 31536000 THEN
        RAISE EXCEPTION 'ttl_seconds must be 1..31536000' USING ERRCODE = 'TQ422';
    END IF;
    IF p_flow_key IS NOT NULL
       AND (p_flow_key = '' OR char_length(p_flow_key) > 120) THEN
        RAISE EXCEPTION 'flow_key must be 1..120 chars' USING ERRCODE = 'TQ422';
    END IF;
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
    v_ttl := COALESCE(p_ttl_seconds, q.default_ttl_seconds);
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
            sha256(convert_to((jsonb_build_object(
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
            ) || CASE
                WHEN p_ttl_seconds IS NOT NULL OR p_flow_key IS NOT NULL THEN
                    jsonb_build_object(
                        'ttl_seconds', p_ttl_seconds, 'flow_key', p_flow_key)
                ELSE '{}'::jsonb
            END)::text, 'UTF8')),
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

    IF q.max_depth IS NOT NULL THEN
        IF taskq.has_capability('queue_counters') THEN
            SELECT COALESCE(c.blocked, 0) + COALESCE(c.queued, 0)
            INTO v_depth
            FROM taskq.queue_counters AS c
            WHERE c.queue = p_queue;
            IF COALESCE(v_depth, 0) >= q.max_depth THEN
                RAISE EXCEPTION 'queue is at max_depth' USING ERRCODE = 'TQ429';
            END IF;
        ELSIF EXISTS (
            SELECT 1 FROM taskq.jobs
            WHERE queue = p_queue AND status IN ('blocked','queued')
            OFFSET greatest(q.max_depth - 1, 0) LIMIT 1
        ) THEN
            RAISE EXCEPTION 'queue is at max_depth' USING ERRCODE = 'TQ429';
        END IF;
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
                backoff_mode, backoff_base_seconds, backoff_cap_seconds,
                expires_at, flow_key
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
                v_mode, v_base, v_cap,
                CASE WHEN v_ttl IS NOT NULL
                     THEN v_scheduled + make_interval(secs => v_ttl) END,
                p_flow_key
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
       AND v_scheduled <= now() AND q.notify_enabled
       AND (q.notify_mode = 'always' OR COALESCE((
                SELECT c.blocked + c.queued FROM taskq.queue_counters c
                WHERE c.queue = p_queue), 0) <= 1) THEN
        -- on_idle_transition: the just-inserted row is already in the
        -- counters, so <= 1 means the queue was idle before this insert.
        PERFORM pg_notify('taskq_' || p_queue, '');
    END IF;
    RETURN QUERY SELECT v_id, true;
END $$;
ALTER FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text
) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer,text
) TO taskq_producer;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.7"'::jsonb, now()),
    ('capabilities', '{"active":["admission_reservations","circuit_breaker","dependencies_workflows","flow_control","followups","operator_schedule_list","queue_counters","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_bulk_admission","workflow_continuations"]}'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();
