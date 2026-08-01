-- outlabs-taskq — migration 0011: finite operator/workflow projections
-- SQL contract 0.2.3 / ADR-029 / Protocol document revision 1.0.13.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.2.2"'::jsonb THEN
        RAISE EXCEPTION '0011 requires SQL contract 0.2.2, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready","schedules"]}'::jsonb THEN
        RAISE EXCEPTION '0011 requires the exact 0010 capability set, found %', v_capabilities;
    END IF;
    IF to_regclass('taskq.workflow_member_counts') IS NOT NULL THEN
        RAISE EXCEPTION '0011 requires absent finite-projection backing';
    END IF;
END $$;

CREATE TYPE taskq.workflow_read_profile AS (
    workflow_id uuid,
    kind text,
    status text,
    sealed boolean,
    cancel_requested boolean,
    declared_queues text[],
    created_at timestamptz,
    updated_at timestamptz,
    finished_at timestamptz
);
ALTER TYPE taskq.workflow_read_profile OWNER TO taskq_owner;

CREATE TYPE taskq.workflow_state_counts AS (
    blocked bigint,
    queued bigint,
    running bigint,
    succeeded bigint,
    failed bigint,
    cancelled bigint
);
ALTER TYPE taskq.workflow_state_counts OWNER TO taskq_owner;

CREATE TYPE taskq.workflow_member_projection AS (
    job_id uuid,
    queue text,
    job_type text,
    step_key text,
    status text,
    outcome text,
    pending_deps integer,
    attempt_count integer,
    failure_count integer,
    created_at timestamptz,
    scheduled_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz
);
ALTER TYPE taskq.workflow_member_projection OWNER TO taskq_owner;

CREATE TYPE taskq.workflow_page AS (
    as_of timestamptz,
    profile taskq.workflow_read_profile,
    counts taskq.workflow_state_counts,
    items taskq.workflow_member_projection[],
    next_after uuid
);
ALTER TYPE taskq.workflow_page OWNER TO taskq_owner;

CREATE TABLE taskq.workflow_member_counts (
    workflow_id uuid PRIMARY KEY,
    blocked bigint NOT NULL DEFAULT 0 CHECK (blocked >= 0),
    queued bigint NOT NULL DEFAULT 0 CHECK (queued >= 0),
    running bigint NOT NULL DEFAULT 0 CHECK (running >= 0),
    succeeded bigint NOT NULL DEFAULT 0 CHECK (succeeded >= 0),
    failed bigint NOT NULL DEFAULT 0 CHECK (failed >= 0),
    cancelled bigint NOT NULL DEFAULT 0 CHECK (cancelled >= 0)
);
ALTER TABLE taskq.workflow_member_counts OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.workflow_member_counts FROM PUBLIC;

INSERT INTO taskq.workflow_member_counts (
    workflow_id, blocked, queued, running, succeeded, failed, cancelled
)
SELECT
    w.id,
    count(j.id) FILTER (WHERE j.status = 'blocked'),
    count(j.id) FILTER (WHERE j.status = 'queued'),
    count(j.id) FILTER (WHERE j.status = 'running'),
    count(j.id) FILTER (WHERE j.status = 'succeeded'),
    count(j.id) FILTER (WHERE j.status = 'failed'),
    count(j.id) FILTER (WHERE j.status = 'cancelled')
FROM taskq.workflows w
LEFT JOIN taskq.jobs j ON j.workflow_id = w.id
GROUP BY w.id;

CREATE OR REPLACE FUNCTION taskq.manage_workflow_member_counts()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO taskq.workflow_member_counts(workflow_id) VALUES (NEW.id);
        RETURN NEW;
    END IF;
    DELETE FROM taskq.workflow_member_counts WHERE workflow_id = OLD.id;
    RETURN OLD;
END $$;
ALTER FUNCTION taskq.manage_workflow_member_counts() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.manage_workflow_member_counts() FROM PUBLIC;

CREATE TRIGGER workflows_member_counts_lifecycle_trg
AFTER INSERT OR DELETE ON taskq.workflows
FOR EACH ROW EXECUTE FUNCTION taskq.manage_workflow_member_counts();

CREATE OR REPLACE FUNCTION taskq.update_workflow_member_counts()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.workflow_id IS NOT NULL
       AND (TG_OP = 'DELETE'
            OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id
            OR NEW.status IS DISTINCT FROM OLD.status) THEN
        UPDATE taskq.workflow_member_counts SET
            blocked = blocked - (OLD.status = 'blocked')::integer,
            queued = queued - (OLD.status = 'queued')::integer,
            running = running - (OLD.status = 'running')::integer,
            succeeded = succeeded - (OLD.status = 'succeeded')::integer,
            failed = failed - (OLD.status = 'failed')::integer,
            cancelled = cancelled - (OLD.status = 'cancelled')::integer
        WHERE workflow_id = OLD.workflow_id;
        IF NOT FOUND AND EXISTS (
            SELECT 1 FROM taskq.workflows WHERE id = OLD.workflow_id
        ) THEN
            RAISE EXCEPTION 'workflow counter invariant missing'
                USING ERRCODE = 'TQ500';
        END IF;
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.workflow_id IS NOT NULL
       AND (TG_OP = 'INSERT'
            OR NEW.workflow_id IS DISTINCT FROM OLD.workflow_id
            OR NEW.status IS DISTINCT FROM OLD.status) THEN
        UPDATE taskq.workflow_member_counts SET
            blocked = blocked + (NEW.status = 'blocked')::integer,
            queued = queued + (NEW.status = 'queued')::integer,
            running = running + (NEW.status = 'running')::integer,
            succeeded = succeeded + (NEW.status = 'succeeded')::integer,
            failed = failed + (NEW.status = 'failed')::integer,
            cancelled = cancelled + (NEW.status = 'cancelled')::integer
        WHERE workflow_id = NEW.workflow_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'workflow counter invariant missing'
                USING ERRCODE = 'TQ500';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
ALTER FUNCTION taskq.update_workflow_member_counts() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.update_workflow_member_counts() FROM PUBLIC;

CREATE TRIGGER jobs_workflow_member_counts_trg
AFTER INSERT OR DELETE OR UPDATE OF workflow_id, status ON taskq.jobs
FOR EACH ROW EXECUTE FUNCTION taskq.update_workflow_member_counts();

CREATE INDEX taskq_jobs_running_page_idx
    ON taskq.jobs (queue, started_at DESC, id DESC)
    WHERE status = 'running';
CREATE INDEX taskq_jobs_finished_page_idx
    ON taskq.jobs (queue, finished_at DESC, id DESC)
    WHERE status IN ('succeeded', 'failed', 'cancelled');
CREATE INDEX taskq_jobs_workflow_page_idx
    ON taskq.jobs (workflow_id, id)
    WHERE workflow_id IS NOT NULL;

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
    v_profile := ROW(
        v_workflow.id,
        v_workflow.kind,
        v_workflow.status,
        v_workflow.sealed_at IS NOT NULL,
        v_workflow.cancel_requested_at IS NOT NULL,
        v_workflow.declared_queues,
        v_workflow.created_at,
        v_workflow.updated_at,
        v_workflow.finished_at
    )::taskq.workflow_read_profile;

    SELECT ROW(c.blocked, c.queued, c.running, c.succeeded, c.failed, c.cancelled)
           ::taskq.workflow_state_counts
    INTO v_counts
    FROM taskq.workflow_member_counts c
    WHERE c.workflow_id = p_workflow_id;
    v_counts := COALESCE(
        v_counts,
        ROW(0, 0, 0, 0, 0, 0)::taskq.workflow_state_counts
    );

    SELECT ARRAY(
        SELECT ROW(
            j.id, j.queue, j.job_type, j.step_key, j.status, j.outcome,
            j.pending_deps, j.attempt_count::integer, j.failure_count::integer,
            j.created_at, j.scheduled_at, j.started_at, j.finished_at, j.updated_at
        )::taskq.workflow_member_projection
        FROM taskq.jobs j
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

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('contract_version','"0.2.3"'::jsonb,now()),
    ('capabilities','{"active":["admission_reservations","dependencies_workflows","followups","read_model_list_ready","schedules"]}'::jsonb,now())
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=now();
