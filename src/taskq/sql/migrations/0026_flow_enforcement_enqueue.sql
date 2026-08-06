-- outlabs-taskq — migration 0026: flow enforcement, enqueue family + profile
-- SQL contract 0.4.3 — Wave 2b slice 3 per the 0.5 Flow Enforcement
-- Specification. The enqueue body is byte-derived from the 0016 final body
-- with exactly these changes: trailing p_ttl_seconds / p_flow_key parameters
-- (old 18-arg identity dropped; named and defaulted callers keep working),
-- expires_at/flow_key stamping (TTL from parameter or queue default), the
-- notify_mode on_idle_transition gate at the single-enqueue NOTIFY site, and
-- workflow intent hashes that include the new fields ONLY when set so
-- pre-0.5 workflow steps replay with identical hashes.
--
-- Recorded amendment: notify_mode governs the single-enqueue site in 0.5.0;
-- enqueue_many (already one NOTIFY per call), followup inserts, and workflow
-- promotes keep 'always' — batch-coalesced or workflow-internal emissions;
-- extending the gate there is a 0.5.1 candidate. try_enqueue is recreated as
-- a pure pass-through (enqueue stamps TTL itself now), and the profile
-- surface (composite + ensure_queue/get_queue_profile) exposes every 0024
-- enforcement column.

DO $$
DECLARE
    v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.4.2"'::jsonb THEN
        RAISE EXCEPTION '0026 requires SQL contract 0.4.2, found %', v_contract;
    END IF;
END $$;

DROP FUNCTION taskq.enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb);
DROP FUNCTION taskq.try_enqueue(
    text,text,jsonb,smallint,timestamptz,text,text,text,smallint,integer,
    text,integer,integer,uuid[],uuid,text,uuid,jsonb,integer);

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


-- ============================================================================
-- try_enqueue: recreate as a pass-through over the 20-arg enqueue
-- ============================================================================

CREATE FUNCTION taskq.try_enqueue(
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
) RETURNS TABLE (outcome text, job_id uuid, retry_after_seconds integer)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    q taskq.queues%ROWTYPE;
    v_existing uuid;
    v_id uuid;
    v_created boolean;
    v_stats jsonb;
    v_rate numeric;
    v_ready numeric;
    v_hint integer;
BEGIN
    IF NOT taskq.has_capability('flow_control') THEN
        RAISE EXCEPTION 'flow_control capability is not active'
            USING ERRCODE = 'TQ501', DETAIL = '{"reason":"flow_control_inactive"}';
    END IF;
    SELECT * INTO q FROM taskq.queues WHERE name = p_queue;
    -- Check order fix (harness finding, 2026-08-05): an already-admitted key
    -- must resolve to 'existed' BEFORE the depth gate can reject it — an
    -- existed result adds no depth, so reconciliation replay works at cap.
    IF p_idempotency_key IS NOT NULL THEN
        SELECT j.id INTO v_existing FROM taskq.jobs j
         WHERE j.queue = p_queue AND j.idempotency_key = p_idempotency_key
           AND j.status IN ('blocked', 'queued', 'running');
        IF FOUND THEN
            RETURN QUERY SELECT 'existed'::text, v_existing, NULL::integer;
            RETURN;
        END IF;
    END IF;
    BEGIN
        SELECT e.job_id, e.created INTO v_id, v_created
          FROM taskq.enqueue(
              p_queue, p_job_type, p_payload, p_priority, p_scheduled_at,
              p_idempotency_key, p_concurrency_key, p_affinity_key,
              p_max_attempts, p_lease_seconds, p_backoff_mode, p_backoff_base,
              p_backoff_cap, p_depends_on, p_workflow_id, p_step_key,
              p_parent_job_id, p_headers, p_ttl_seconds, p_flow_key
          ) e;
    EXCEPTION WHEN SQLSTATE 'TQ429' THEN
        SELECT c.data -> 'queues' -> p_queue INTO v_stats
          FROM taskq.control_state c WHERE c.key = 'stats_snapshot';
        v_rate := (v_stats -> 'rates' ->> 'settled_per_s')::numeric;
        v_ready := COALESCE((v_stats ->> 'ready')::numeric, 0);
        IF v_rate IS NOT NULL AND v_rate > 0 THEN
            v_hint := least(60, greatest(1, ceil(v_ready / v_rate)))::integer;
        ELSE
            v_hint := COALESCE(q.backpressure_retry_seconds, 5);
        END IF;
        RETURN QUERY SELECT 'rejected_depth'::text, NULL::uuid, v_hint;
        RETURN;
    END;
    RETURN QUERY SELECT
        CASE WHEN v_created THEN 'accepted' ELSE 'existed' END::text, v_id, NULL::integer;
END $$;
ALTER FUNCTION taskq.try_enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, integer, text, integer, integer, uuid[], uuid, text, uuid, jsonb, integer, text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.try_enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, integer, text, integer, integer, uuid[], uuid, text, uuid, jsonb, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.try_enqueue(text, text, jsonb, smallint, timestamptz, text, text, text, smallint, integer, text, integer, integer, uuid[], uuid, text, uuid, jsonb, integer, text) TO taskq_producer;

-- ============================================================================
-- Profile surface: composite + CRUD expose the enforcement columns
-- ============================================================================

ALTER TYPE taskq.queue_profile ADD ATTRIBUTE max_running integer;
ALTER TYPE taskq.queue_profile ADD ATTRIBUTE claim_rate_per_minute integer;
ALTER TYPE taskq.queue_profile ADD ATTRIBUTE claim_burst integer;
ALTER TYPE taskq.queue_profile ADD ATTRIBUTE ramp_seconds integer;
ALTER TYPE taskq.queue_profile ADD ATTRIBUTE default_ttl_seconds integer;
ALTER TYPE taskq.queue_profile ADD ATTRIBUTE backpressure_retry_seconds integer;
ALTER TYPE taskq.queue_profile ADD ATTRIBUTE notify_mode text;

CREATE OR REPLACE FUNCTION taskq.get_queue_profile(p_queue text)
RETURNS taskq.queue_profile LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp AS $$
    SELECT ROW(q.name,q.profile_version,q.default_priority,q.default_lease_seconds,q.default_max_attempts,
        q.default_backoff_mode,q.default_backoff_base,q.default_backoff_cap,q.retention_hours,
        q.failed_retention_hours,q.max_depth,q.notify_enabled,q.paused_at IS NOT NULL,
        q.max_running,q.claim_rate_per_minute,q.claim_burst,q.ramp_seconds,
        q.default_ttl_seconds,q.backpressure_retry_seconds,q.notify_mode)::taskq.queue_profile
    FROM taskq.queues q WHERE q.name = p_queue
$$;
ALTER FUNCTION taskq.get_queue_profile(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.get_queue_profile(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.get_queue_profile(text) TO taskq_observer;

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
                           'retention_hours','failed_retention_hours','max_depth','notify_enabled',
                           'max_running','claim_rate_per_minute','claim_burst','ramp_seconds',
                           'default_ttl_seconds','backpressure_retry_seconds','notify_mode') THEN
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
        v_new.max_running := NULL; v_new.claim_rate_per_minute := NULL;
        v_new.claim_burst := NULL; v_new.ramp_seconds := NULL;
        v_new.default_ttl_seconds := NULL;
        v_new.backpressure_retry_seconds := 5; v_new.notify_mode := 'always';
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
    IF v_new.max_running IS NOT NULL AND v_new.max_running <= 0 THEN
        RAISE EXCEPTION 'max_running must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.claim_rate_per_minute IS NOT NULL AND v_new.claim_rate_per_minute <= 0
       OR v_new.claim_burst IS NOT NULL AND v_new.claim_burst <= 0 THEN
        RAISE EXCEPTION 'claim rate and burst must be NULL or > 0' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.ramp_seconds IS NOT NULL AND v_new.ramp_seconds NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'ramp_seconds must be NULL or 1..86400' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.default_ttl_seconds IS NOT NULL
       AND v_new.default_ttl_seconds NOT BETWEEN 1 AND 31536000 THEN
        RAISE EXCEPTION 'default_ttl_seconds must be NULL or 1..31536000' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.backpressure_retry_seconds IS NULL
       OR v_new.backpressure_retry_seconds NOT BETWEEN 1 AND 300 THEN
        RAISE EXCEPTION 'backpressure_retry_seconds must be 1..300' USING ERRCODE = 'TQ422';
    END IF;
    IF v_new.notify_mode IS NULL OR v_new.notify_mode NOT IN ('always','on_idle_transition') THEN
        RAISE EXCEPTION 'notify_mode must be always|on_idle_transition' USING ERRCODE = 'TQ422';
    END IF;

    IF v_old.name IS NULL THEN
        INSERT INTO taskq.queues (name, profile_version, default_priority, default_lease_seconds,
            default_max_attempts, default_backoff_mode, default_backoff_base, default_backoff_cap,
            retention_hours, failed_retention_hours, max_depth, notify_enabled,
            max_running, claim_rate_per_minute, claim_burst, ramp_seconds,
            default_ttl_seconds, backpressure_retry_seconds, notify_mode)
        VALUES (v_new.name, 1, v_new.default_priority, v_new.default_lease_seconds,
            v_new.default_max_attempts, v_new.default_backoff_mode, v_new.default_backoff_base,
            v_new.default_backoff_cap, v_new.retention_hours, v_new.failed_retention_hours,
            v_new.max_depth, v_new.notify_enabled,
            v_new.max_running, v_new.claim_rate_per_minute, v_new.claim_burst, v_new.ramp_seconds,
            v_new.default_ttl_seconds, v_new.backpressure_retry_seconds, v_new.notify_mode);
        v_result := 'created';
    ELSIF (v_new.default_priority, v_new.default_lease_seconds, v_new.default_max_attempts,
           v_new.default_backoff_mode, v_new.default_backoff_base, v_new.default_backoff_cap,
           v_new.retention_hours, v_new.failed_retention_hours, v_new.max_depth, v_new.notify_enabled,
           v_new.max_running, v_new.claim_rate_per_minute, v_new.claim_burst, v_new.ramp_seconds,
           v_new.default_ttl_seconds, v_new.backpressure_retry_seconds, v_new.notify_mode)
          IS NOT DISTINCT FROM
          (v_old.default_priority, v_old.default_lease_seconds, v_old.default_max_attempts,
           v_old.default_backoff_mode, v_old.default_backoff_base, v_old.default_backoff_cap,
           v_old.retention_hours, v_old.failed_retention_hours, v_old.max_depth, v_old.notify_enabled,
           v_old.max_running, v_old.claim_rate_per_minute, v_old.claim_burst, v_old.ramp_seconds,
           v_old.default_ttl_seconds, v_old.backpressure_retry_seconds, v_old.notify_mode) THEN
        v_result := 'unchanged';
    ELSE
        UPDATE taskq.queues SET default_priority=v_new.default_priority,
            default_lease_seconds=v_new.default_lease_seconds, default_max_attempts=v_new.default_max_attempts,
            default_backoff_mode=v_new.default_backoff_mode, default_backoff_base=v_new.default_backoff_base,
            default_backoff_cap=v_new.default_backoff_cap, retention_hours=v_new.retention_hours,
            failed_retention_hours=v_new.failed_retention_hours, max_depth=v_new.max_depth,
            notify_enabled=v_new.notify_enabled,
            max_running=v_new.max_running, claim_rate_per_minute=v_new.claim_rate_per_minute,
            claim_burst=v_new.claim_burst, ramp_seconds=v_new.ramp_seconds,
            default_ttl_seconds=v_new.default_ttl_seconds,
            backpressure_retry_seconds=v_new.backpressure_retry_seconds,
            notify_mode=v_new.notify_mode, profile_version=profile_version + 1, updated_at=now()
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
        'notify_enabled',v_new.notify_enabled,'max_running',v_new.max_running,
        'claim_rate_per_minute',v_new.claim_rate_per_minute,'claim_burst',v_new.claim_burst,
        'ramp_seconds',v_new.ramp_seconds,'default_ttl_seconds',v_new.default_ttl_seconds,
        'backpressure_retry_seconds',v_new.backpressure_retry_seconds,'notify_mode',v_new.notify_mode);
END $$;
ALTER FUNCTION taskq.ensure_queue(text,jsonb,text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.ensure_queue(text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.ensure_queue(text,jsonb,text) TO taskq_operator;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.4.3"'::jsonb, now())
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
        RAISE EXCEPTION '0026 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
