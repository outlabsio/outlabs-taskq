-- outlabs-taskq — migration 0022: queue counters, rates, and health (inactive)
-- SQL contract 0.4.0 — Wave 2a signals per
-- "Task Queue 0.4 Queue Counters and Health Specification" (docs-first S3).
--
-- Installs the per-queue counter table, the maintenance trigger (DISABLED —
-- 0023 enables it and re-backfills atomically under the trigger's own
-- SHARE ROW EXCLUSIVE lock, so counters are consistent with concurrent
-- writers by construction and no per-row capability guard is ever paid),
-- the snapshot v2 with cumulative totals -> rates/drain-ETA, the
-- capability-gated queue_health verdicts, and additive metrics rows.
--
-- Level counters clamp with greatest(0, ...) and carry NO check constraints:
-- counter drift must never abort a production settle. The L6 harness scenario
-- asserts exact equality against ground truth instead. Cumulative totals are
-- since-activation, seeded from surviving rows at 0023 backfill; rates use
-- deltas only, so the seed baseline is irrelevant.

DO $$
DECLARE
    v_contract jsonb;
    v_capabilities jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '"0.3.1"'::jsonb THEN
        RAISE EXCEPTION '0022 requires SQL contract 0.3.1, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM
       '{"active":["admission_reservations","dependencies_workflows","followups","operator_schedule_list","read_model_job_events","read_model_job_views_v2","read_model_list_finished","read_model_list_ready","read_model_list_running","read_model_workflow","read_model_workflow_list","scheduler_v2","schedules","target_attestation","worker_presence","workflow_continuations"]}'::jsonb THEN
        RAISE EXCEPTION '0022 requires the exact 0021 capability set, found %', v_capabilities;
    END IF;
END $$;

-- ============================================================================
-- 1. Counter table — one row per queue; upsert-maintained so late-created
--    queues self-heal. Levels are status-derived only (the ready/scheduled
--    due-time split stays in the tick snapshot, which owns clock-derived
--    numbers).
-- ============================================================================

CREATE TABLE IF NOT EXISTS taskq.queue_counters (
    queue            text PRIMARY KEY REFERENCES taskq.queues(name) ON DELETE CASCADE,
    blocked          bigint NOT NULL DEFAULT 0,
    queued           bigint NOT NULL DEFAULT 0,
    running          bigint NOT NULL DEFAULT 0,
    enqueued_total   bigint NOT NULL DEFAULT 0,
    requeued_total   bigint NOT NULL DEFAULT 0,
    succeeded_total  bigint NOT NULL DEFAULT 0,
    failed_total     bigint NOT NULL DEFAULT 0,
    cancelled_total  bigint NOT NULL DEFAULT 0,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE taskq.queue_counters OWNER TO taskq_owner;

-- ============================================================================
-- 2. Maintenance trigger (installed DISABLED; 0023 enables + backfills).
--    jobs.queue is immutable in this contract, so transitions never move a
--    row between counter rows.
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.update_queue_counters()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO taskq.queue_counters AS qc (
            queue, blocked, queued, running, enqueued_total
        ) VALUES (
            NEW.queue,
            (NEW.status = 'blocked')::integer,
            (NEW.status = 'queued')::integer,
            (NEW.status = 'running')::integer,
            1
        )
        ON CONFLICT (queue) DO UPDATE SET
            blocked = qc.blocked + (NEW.status = 'blocked')::integer,
            queued = qc.queued + (NEW.status = 'queued')::integer,
            running = qc.running + (NEW.status = 'running')::integer,
            enqueued_total = qc.enqueued_total + 1,
            updated_at = now();
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        UPDATE taskq.queue_counters AS qc SET
            blocked = greatest(0, qc.blocked - (OLD.status = 'blocked')::integer),
            queued = greatest(0, qc.queued - (OLD.status = 'queued')::integer),
            running = greatest(0, qc.running - (OLD.status = 'running')::integer),
            updated_at = now()
        WHERE qc.queue = OLD.queue;
        RETURN OLD;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        UPDATE taskq.queue_counters AS qc SET
            blocked = greatest(0, qc.blocked - (OLD.status = 'blocked')::integer)
                      + (NEW.status = 'blocked')::integer,
            queued = greatest(0, qc.queued - (OLD.status = 'queued')::integer)
                     + (NEW.status = 'queued')::integer,
            running = greatest(0, qc.running - (OLD.status = 'running')::integer)
                      + (NEW.status = 'running')::integer,
            requeued_total = qc.requeued_total
                             + (OLD.status = 'running' AND NEW.status = 'queued')::integer,
            succeeded_total = qc.succeeded_total + (NEW.status = 'succeeded')::integer,
            failed_total = qc.failed_total + (NEW.status = 'failed')::integer,
            cancelled_total = qc.cancelled_total + (NEW.status = 'cancelled')::integer,
            updated_at = now()
        WHERE qc.queue = NEW.queue;
        IF NOT FOUND THEN
            INSERT INTO taskq.queue_counters AS qc (queue) VALUES (NEW.queue)
            ON CONFLICT (queue) DO NOTHING;
            UPDATE taskq.queue_counters AS qc SET
                blocked = greatest(0, qc.blocked - (OLD.status = 'blocked')::integer)
                          + (NEW.status = 'blocked')::integer,
                queued = greatest(0, qc.queued - (OLD.status = 'queued')::integer)
                         + (NEW.status = 'queued')::integer,
                running = greatest(0, qc.running - (OLD.status = 'running')::integer)
                          + (NEW.status = 'running')::integer,
                requeued_total = qc.requeued_total
                                 + (OLD.status = 'running' AND NEW.status = 'queued')::integer,
                succeeded_total = qc.succeeded_total + (NEW.status = 'succeeded')::integer,
                failed_total = qc.failed_total + (NEW.status = 'failed')::integer,
                cancelled_total = qc.cancelled_total + (NEW.status = 'cancelled')::integer,
                updated_at = now()
            WHERE qc.queue = NEW.queue;
        END IF;
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION taskq.update_queue_counters() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.update_queue_counters() FROM PUBLIC;

CREATE TRIGGER jobs_queue_counters_trg
AFTER INSERT OR DELETE OR UPDATE OF status ON taskq.jobs
FOR EACH ROW EXECUTE FUNCTION taskq.update_queue_counters();
ALTER TABLE taskq.jobs DISABLE TRIGGER jobs_queue_counters_trg;

-- ============================================================================
-- 3. Snapshot v2 — legacy per-queue block unchanged; when the capability is
--    active, each queue object gains levels/rates/drain_eta_seconds and the
--    snapshot stores cumulative totals for the next delta.
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.refresh_stats_snapshot() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v jsonb;
    v_totals jsonb;
    v_prev_totals jsonb;
    v_prev_as_of timestamptz;
    v_window numeric;
    v_queue text;
    v_now jsonb;
    v_prev jsonb;
    v_rates jsonb;
    v_settled numeric;
    v_ready numeric;
    v_eta jsonb;
    v_data jsonb;
BEGIN
    SELECT jsonb_object_agg(q.name, jsonb_build_object(
        'ready',   (SELECT count(*) FROM taskq.jobs j WHERE j.queue=q.name AND j.status='queued'
                     AND j.cancel_requested_at IS NULL AND j.scheduled_at <= now()),
        'scheduled',(SELECT count(*) FROM taskq.jobs j WHERE j.queue=q.name AND j.status='queued'
                     AND j.cancel_requested_at IS NULL AND j.scheduled_at > now()),
        'running', (SELECT count(*) FROM taskq.jobs j WHERE j.queue=q.name AND j.status='running'),
        'oldest_ready_seconds', COALESCE((SELECT extract(epoch FROM now()-min(j.scheduled_at))::bigint
                     FROM taskq.jobs j WHERE j.queue=q.name AND j.status='queued'
                     AND j.cancel_requested_at IS NULL AND j.scheduled_at <= now()), 0),
        'paused',  q.paused_at IS NOT NULL))
      INTO v FROM taskq.queues q;
    v := COALESCE(v, '{}'::jsonb);
    IF taskq.has_capability('queue_counters') THEN
        SELECT jsonb_object_agg(c.queue, jsonb_build_object(
            'blocked', c.blocked, 'queued', c.queued, 'running', c.running,
            'enqueued_total', c.enqueued_total, 'requeued_total', c.requeued_total,
            'succeeded_total', c.succeeded_total, 'failed_total', c.failed_total,
            'cancelled_total', c.cancelled_total))
          INTO v_totals FROM taskq.queue_counters c;
        v_totals := COALESCE(v_totals, '{}'::jsonb);
        SELECT (c.data->>'as_of')::timestamptz, c.data->'totals'
          INTO v_prev_as_of, v_prev_totals
          FROM taskq.control_state c WHERE c.key = 'stats_snapshot';
        v_window := COALESCE(extract(epoch FROM now() - v_prev_as_of), 0);
        FOR v_queue, v_now IN SELECT key, value FROM jsonb_each(v_totals) LOOP
            CONTINUE WHEN NOT v ? v_queue;
            v_prev := CASE WHEN v_prev_totals IS NOT NULL
                           THEN v_prev_totals -> v_queue ELSE NULL END;
            v_rates := NULL;
            IF v_prev IS NOT NULL AND v_window >= 1 THEN
                v_settled :=
                    ((v_now->>'succeeded_total')::numeric - (v_prev->>'succeeded_total')::numeric)
                  + ((v_now->>'failed_total')::numeric - (v_prev->>'failed_total')::numeric)
                  + ((v_now->>'cancelled_total')::numeric - (v_prev->>'cancelled_total')::numeric);
                v_rates := jsonb_build_object(
                    'window_seconds', round(v_window, 3),
                    'enqueued_per_s', round(greatest(0,
                        (v_now->>'enqueued_total')::numeric
                        - (v_prev->>'enqueued_total')::numeric) / v_window, 4),
                    'settled_per_s', round(greatest(0, v_settled) / v_window, 4),
                    'failed_per_s', round(greatest(0,
                        (v_now->>'failed_total')::numeric
                        - (v_prev->>'failed_total')::numeric) / v_window, 4));
            END IF;
            v_ready := COALESCE(((v -> v_queue) ->> 'ready')::numeric, 0);
            v_eta := CASE
                WHEN v_rates IS NOT NULL AND (v_rates->>'settled_per_s')::numeric > 0
                THEN to_jsonb(round(v_ready / (v_rates->>'settled_per_s')::numeric, 1))
                ELSE 'null'::jsonb
            END;
            v := jsonb_set(v, ARRAY[v_queue], (v -> v_queue) || jsonb_build_object(
                'levels', v_now,
                'rates', COALESCE(v_rates, 'null'::jsonb),
                'drain_eta_seconds', v_eta));
        END LOOP;
        v_data := jsonb_build_object('as_of', now(), 'queues', v, 'totals', v_totals);
    ELSE
        v_data := jsonb_build_object('as_of', now(), 'queues', v);
    END IF;
    INSERT INTO taskq.control_state (key, data, last_finished_at)
    VALUES ('stats_snapshot', v_data, now())
    ON CONFLICT (key) DO UPDATE
      SET data = EXCLUDED.data, last_finished_at = now();
END $$;
ALTER FUNCTION taskq.refresh_stats_snapshot() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.refresh_stats_snapshot() FROM PUBLIC;

-- ============================================================================
-- 4. Health verdicts. Internal helper returns zero rows while the capability
--    is inactive (so metrics stays safe); the public function raises typed
--    TQ501, matching the read-model gate pattern. Fixed 600s lag constant in
--    0.4.0 (per-queue profile tuning is Wave 2b).
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.queue_health_internal()
RETURNS TABLE (queue text, verdict text, detail jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_lag constant numeric := 600;
    v_snapshot jsonb;
    q record;
    v_stats jsonb;
    v_levels jsonb;
    v_rates jsonb;
    v_ready numeric;
    v_oldest numeric;
    v_settled_rate numeric;
    v_eta numeric;
    v_depth numeric;
    v_online integer;
    v_verdict text;
BEGIN
    IF NOT taskq.has_capability('queue_counters') THEN
        RETURN;
    END IF;
    SELECT c.data INTO v_snapshot FROM taskq.control_state c WHERE c.key = 'stats_snapshot';
    FOR q IN
        SELECT qs.name, qs.paused_at, qs.max_depth,
               COALESCE(c.blocked, 0) AS blocked,
               COALESCE(c.queued, 0) AS queued,
               COALESCE(c.running, 0) AS running
          FROM taskq.queues qs
          LEFT JOIN taskq.queue_counters c ON c.queue = qs.name
         ORDER BY qs.name
    LOOP
        v_stats := COALESCE(v_snapshot -> 'queues' -> q.name, '{}'::jsonb);
        v_levels := jsonb_build_object(
            'blocked', q.blocked, 'queued', q.queued, 'running', q.running);
        v_rates := CASE WHEN jsonb_typeof(v_stats -> 'rates') = 'object'
                        THEN v_stats -> 'rates' ELSE NULL END;
        v_ready := COALESCE((v_stats ->> 'ready')::numeric, 0);
        v_oldest := COALESCE((v_stats ->> 'oldest_ready_seconds')::numeric, 0);
        v_settled_rate := COALESCE((v_rates ->> 'settled_per_s')::numeric, 0);
        v_eta := CASE WHEN jsonb_typeof(v_stats -> 'drain_eta_seconds') = 'number'
                      THEN (v_stats ->> 'drain_eta_seconds')::numeric ELSE NULL END;
        v_depth := q.blocked + q.queued;
        SELECT count(*)::integer INTO v_online
          FROM taskq.workers w
         WHERE w.last_seen_at > now() - interval '180 seconds'
           AND q.name = ANY(w.queues);
        v_verdict := CASE
            WHEN q.paused_at IS NOT NULL THEN 'paused'
            WHEN v_ready > 0 AND v_online = 0 THEN 'no_consumer'
            WHEN q.max_depth IS NOT NULL AND v_depth >= q.max_depth THEN 'choking'
            WHEN v_eta IS NOT NULL AND v_eta > v_lag THEN 'behind'
            WHEN v_settled_rate > 0 AND v_oldest > 2 * v_lag THEN 'starved'
            WHEN v_depth = 0 AND q.running = 0 AND v_settled_rate = 0 THEN 'inactive'
            ELSE 'ok'
        END;
        queue := q.name;
        verdict := v_verdict;
        detail := jsonb_build_object(
            'levels', v_levels,
            'ready', v_ready,
            'oldest_ready_seconds', v_oldest,
            'rates', COALESCE(v_rates, 'null'::jsonb),
            'drain_eta_seconds', COALESCE(to_jsonb(v_eta), 'null'::jsonb),
            'online_workers', v_online,
            'max_depth', COALESCE(to_jsonb(q.max_depth), 'null'::jsonb),
            'lag_seconds', v_lag);
        RETURN NEXT;
    END LOOP;
END $$;
ALTER FUNCTION taskq.queue_health_internal() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.queue_health_internal() FROM PUBLIC;

CREATE OR REPLACE FUNCTION taskq.queue_health(p_queue text DEFAULT NULL)
RETURNS TABLE (queue text, verdict text, detail jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
BEGIN
    IF NOT taskq.has_capability('queue_counters') THEN
        RAISE EXCEPTION 'queue_counters capability is not active'
            USING ERRCODE = 'TQ501',
                  DETAIL = '{"reason":"queue_counters_inactive"}';
    END IF;
    IF p_queue IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM taskq.queues qs WHERE qs.name = p_queue
    ) THEN
        RAISE EXCEPTION 'unknown queue %', p_queue USING ERRCODE = 'TQ001';
    END IF;
    RETURN QUERY
        SELECT h.queue, h.verdict, h.detail
          FROM taskq.queue_health_internal() h
         WHERE p_queue IS NULL OR h.queue = p_queue;
END $$;
ALTER FUNCTION taskq.queue_health(text) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.queue_health(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.queue_health(text) TO taskq_observer;

-- ============================================================================
-- 5. Metrics v2 — additive counter/health rows via the inactive-safe helper.
-- ============================================================================

CREATE OR REPLACE FUNCTION taskq.metrics()
RETURNS TABLE (name text, labels jsonb, value numeric)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
    SELECT 'taskq_ready'::text, jsonb_build_object('queue', s.queue),
           COALESCE((s.stats->>'ready')::numeric, 0)
      FROM taskq.get_queue_stats(NULL) s
    UNION ALL
    SELECT 'taskq_scheduled'::text, jsonb_build_object('queue', s.queue),
           COALESCE((s.stats->>'scheduled')::numeric, 0)
      FROM taskq.get_queue_stats(NULL) s
    UNION ALL
    SELECT 'taskq_oldest_ready_seconds'::text, jsonb_build_object('queue', s.queue),
           COALESCE((s.stats->>'oldest_ready_seconds')::numeric, 0)
      FROM taskq.get_queue_stats(NULL) s
    UNION ALL
    SELECT 'taskq_running'::text, jsonb_build_object('queue', j.queue), count(*)::numeric
      FROM taskq.jobs j WHERE j.status = 'running' GROUP BY j.queue
    UNION ALL
    SELECT 'taskq_dead_total'::text, jsonb_build_object('queue', j.queue), count(*)::numeric
      FROM taskq.jobs j WHERE j.status = 'failed' GROUP BY j.queue
    UNION ALL
    SELECT 'taskq_tick_age_seconds'::text, '{}'::jsonb,
           COALESCE(extract(epoch FROM now() - c.last_finished_at), 0)::numeric
      FROM taskq.control_state c WHERE c.key = 'tick'
    UNION ALL
    SELECT 'taskq_workers_online'::text, '{}'::jsonb, count(*)::numeric
      FROM taskq.workers w WHERE w.last_seen_at > now() - interval '180 seconds'
    UNION ALL
    SELECT 'taskq_index_bytes'::text, jsonb_build_object('index', ib.key), ib.value::numeric
      FROM taskq.control_state c2,
           LATERAL jsonb_each_text(c2.data->'index_bytes') ib(key, value)
     WHERE c2.key = 'janitor_daily'
    UNION ALL
    SELECT 'taskq_' || t.metric, jsonb_build_object('queue', c3.queue), t.metric_value
      FROM taskq.queue_counters c3,
           LATERAL (VALUES
               ('enqueued_total', c3.enqueued_total::numeric),
               ('requeued_total', c3.requeued_total::numeric),
               ('succeeded_total', c3.succeeded_total::numeric),
               ('failed_total', c3.failed_total::numeric),
               ('cancelled_total', c3.cancelled_total::numeric)
           ) AS t(metric, metric_value)
     WHERE taskq.has_capability('queue_counters')
    UNION ALL
    SELECT 'taskq_health'::text,
           jsonb_build_object('queue', h.queue, 'verdict', h.verdict), 1::numeric
      FROM taskq.queue_health_internal() h
$$;
ALTER FUNCTION taskq.metrics() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.metrics() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.metrics() TO taskq_observer;

-- ============================================================================
-- 6. Contract bump (capability stays inactive until 0023) + hardening check.
-- ============================================================================

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.4.0"'::jsonb, now())
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
        RAISE EXCEPTION '0022 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
