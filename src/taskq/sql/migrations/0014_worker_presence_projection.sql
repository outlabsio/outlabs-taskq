-- outlabs-taskq — migration 0014: bounded inactive worker-presence projection
-- SQL contract 0.2.4 / ADR-033 / Protocol document revision 1.0.14.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.3"'::jsonb THEN
        RAISE EXCEPTION '0014 requires SQL contract 0.2.3, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","schedules"]}'::jsonb THEN
        RAISE EXCEPTION '0014 requires the exact post-0012 capability set, found %',
            v_capabilities;
    END IF;
    IF to_regtype('taskq.worker_presence_item') IS NOT NULL
       OR to_regtype('taskq.worker_presence_page') IS NOT NULL
       OR to_regprocedure(
           'taskq.list_worker_presence(integer,timestamp with time zone,text)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION '0014 requires absent worker-presence projection';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM taskq.workers w
        WHERE w.worker_id = ''
           OR length(w.worker_id) > 200
           OR cardinality(w.queues) NOT BETWEEN 1 AND 32
           OR EXISTS (
               SELECT 1 FROM unnest(w.queues) AS q(name)
               WHERE q.name IS NULL OR q.name !~ '^[a-z0-9_]{1,57}$'
           )
           OR cardinality(w.queues) <> (
               SELECT count(DISTINCT q.name) FROM unnest(w.queues) AS q(name)
           )
           OR length(w.version) > 200
    ) THEN
        RAISE EXCEPTION '0014 existing worker presence violates bounded label domain';
    END IF;
END $$;

CREATE TYPE taskq.worker_presence_item AS (
    worker_id text,
    declared_queues text[],
    version text,
    started_at timestamptz,
    last_seen_at timestamptz,
    online boolean,
    running_jobs bigint,
    shutdown_requested boolean
);
ALTER TYPE taskq.worker_presence_item OWNER TO taskq_owner;

CREATE TYPE taskq.worker_presence_page AS (
    as_of timestamptz,
    items taskq.worker_presence_item[],
    next_last_seen_at timestamptz,
    next_worker_id text
);
ALTER TYPE taskq.worker_presence_page OWNER TO taskq_owner;

CREATE INDEX workers_presence_page_idx
    ON taskq.workers (last_seen_at DESC, worker_id DESC);
CREATE INDEX taskq_jobs_worker_running_idx
    ON taskq.jobs (worker_id)
    WHERE status = 'running';

CREATE OR REPLACE FUNCTION taskq.worker_heartbeat(
    p_worker_id text,
    p_queues text[],
    p_hostname text DEFAULT NULL,
    p_pid int DEFAULT NULL,
    p_version text DEFAULT NULL,
    p_meta jsonb DEFAULT NULL
) RETURNS TABLE (shutdown_requested boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF COALESCE(p_worker_id, '') = '' OR length(p_worker_id) > 200
       OR p_queues IS NULL OR cardinality(p_queues) NOT BETWEEN 1 AND 32
       OR EXISTS (
           SELECT 1 FROM unnest(p_queues) AS q(name)
           WHERE q.name IS NULL OR q.name !~ '^[a-z0-9_]{1,57}$'
       )
       OR cardinality(p_queues) <> (
           SELECT count(DISTINCT q.name) FROM unnest(p_queues) AS q(name)
       )
       OR length(p_version) > 200 THEN
        RAISE EXCEPTION 'invalid bounded worker presence labels'
            USING ERRCODE = 'TQ422';
    END IF;
    INSERT INTO taskq.workers AS w (
        worker_id, queues, hostname, pid, version, meta
    )
    VALUES (p_worker_id, p_queues, p_hostname, p_pid, p_version, p_meta)
    ON CONFLICT (worker_id) DO UPDATE
       SET queues = EXCLUDED.queues,
           hostname = EXCLUDED.hostname,
           pid = EXCLUDED.pid,
           version = EXCLUDED.version,
           meta = EXCLUDED.meta,
           last_seen_at = now();
    RETURN QUERY
    SELECT w.shutdown_requested_at IS NOT NULL
    FROM taskq.workers w
    WHERE w.worker_id = p_worker_id;
END $$;
ALTER FUNCTION taskq.worker_heartbeat(text, text[], text, int, text, jsonb)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION
    taskq.worker_heartbeat(text, text[], text, int, text, jsonb)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    taskq.worker_heartbeat(text, text[], text, int, text, jsonb)
    TO taskq_runner;

CREATE OR REPLACE FUNCTION taskq.list_worker_presence(
    p_limit integer DEFAULT 50,
    p_after_last_seen_at timestamptz DEFAULT NULL,
    p_after_worker_id text DEFAULT NULL
) RETURNS taskq.worker_presence_page
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_as_of timestamptz;
    v_items taskq.worker_presence_item[];
    v_next_last_seen_at timestamptz;
    v_next_worker_id text;
BEGIN
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR ((p_after_last_seen_at IS NULL) <> (p_after_worker_id IS NULL))
       OR (
           p_after_worker_id IS NOT NULL
           AND (p_after_worker_id = '' OR length(p_after_worker_id) > 200)
       ) THEN
        RAISE EXCEPTION 'invalid worker presence page input'
            USING ERRCODE = 'TQ422';
    END IF;
    IF NOT taskq.has_capability('worker_presence') THEN
        RAISE EXCEPTION 'worker presence inactive'
            USING ERRCODE = 'TQ501',
                  DETAIL = 'reason=worker_presence_inactive';
    END IF;

    v_as_of := now();
    SELECT ARRAY(
        SELECT ROW(
            w.worker_id,
            w.queues,
            w.version,
            w.started_at,
            w.last_seen_at,
            w.last_seen_at > v_as_of - interval '180 seconds',
            (
                SELECT count(*)
                FROM taskq.jobs j
                WHERE j.worker_id = w.worker_id
                  AND j.status = 'running'
            ),
            w.shutdown_requested_at IS NOT NULL
        )::taskq.worker_presence_item
        FROM taskq.workers w
        WHERE p_after_last_seen_at IS NULL
           OR (w.last_seen_at, w.worker_id)
              < (p_after_last_seen_at, p_after_worker_id)
        ORDER BY w.last_seen_at DESC, w.worker_id DESC
        LIMIT p_limit + 1
    ) INTO v_items;
    v_items := COALESCE(v_items, ARRAY[]::taskq.worker_presence_item[]);
    IF cardinality(v_items) > p_limit THEN
        v_next_last_seen_at := v_items[p_limit].last_seen_at;
        v_next_worker_id := v_items[p_limit].worker_id;
        v_items := v_items[1:p_limit];
    END IF;
    RETURN ROW(v_as_of, v_items, v_next_last_seen_at, v_next_worker_id)
           ::taskq.worker_presence_page;
END $$;
ALTER FUNCTION taskq.list_worker_presence(integer, timestamptz, text)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION
    taskq.list_worker_presence(integer, timestamptz, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    taskq.list_worker_presence(integer, timestamptz, text)
    TO taskq_observer;

INSERT INTO taskq.meta(key, value, updated_at)
VALUES ('contract_version', '"0.2.4"'::jsonb, now())
ON CONFLICT(key) DO UPDATE
SET value = excluded.value, updated_at = now();
