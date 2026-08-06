-- outlabs-taskq — migration 0029: surface schedule smear through the claim
-- SQL contract 0.5.1 — completes the schedule-firing smear started in 0024.
--
-- 0024 added schedules.smear_seconds but never surfaced it to the Python
-- scheduler, so it was inert. This appends smear_seconds to the schedule_claim
-- composite and projects it in the unattested claim body, so the evaluator can
-- apply the deterministic per-schedule offset. SQL stays lattice-agnostic —
-- it only carries the value; the offset math lives in the Python evaluator
-- (taskq/schedules.py), exactly as the 0.5 spec intends.
--
-- Additive and non-breaking: the composite gains a trailing attribute (the
-- claim_batch precedent, migration 0025), the claim function signature and
-- grants are unchanged, and a schedule with smear_seconds NULL (every existing
-- schedule) behaves byte-identically to today. No table rewrite; no worker,
-- producer, or non-schedule surface is touched.

DO $$
DECLARE
    v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.5.0"'::jsonb THEN
        RAISE EXCEPTION '0029 requires SQL contract 0.5.0, found %', v_contract;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'taskq' AND c.relname = 'schedules' AND a.attname = 'smear_seconds'
    ) THEN
        RAISE EXCEPTION '0029 requires the 0024 schedules.smear_seconds column';
    END IF;
END $$;

ALTER TYPE taskq.schedule_claim ADD ATTRIBUTE smear_seconds integer;

CREATE OR REPLACE FUNCTION taskq._claim_schedules_unattested(
    p_worker_id text,
    p_limit integer DEFAULT 10,
    p_lease_seconds integer DEFAULT 60
) RETURNS taskq.schedule_claim_batch
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_schedule taskq.schedules%ROWTYPE;
    v_claim taskq.schedule_claim;
    v_claims taskq.schedule_claim[] := '{}'::taskq.schedule_claim[];
    v_now timestamptz := now();
    v_token uuid;
BEGIN
    IF p_worker_id IS NULL OR octet_length(p_worker_id) NOT BETWEEN 1 AND 200
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 5 AND 300 THEN
        RAISE EXCEPTION 'invalid schedule claim arguments' USING ERRCODE = 'TQ422';
    END IF;
    FOR v_schedule IN
        SELECT * FROM taskq.schedules
        WHERE state = 'active'
          AND next_fire_at <= v_now
          AND (retry_not_before IS NULL OR retry_not_before <= v_now)
          AND (claim_token IS NULL OR claim_expires_at <= v_now)
        ORDER BY next_fire_at, id
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    LOOP
        v_token := taskq.uuid7();
        UPDATE taskq.schedules
        SET claim_token = v_token,
            claim_as_of = v_now,
            claimed_by = p_worker_id,
            claim_expires_at = v_now + make_interval(secs => p_lease_seconds),
            updated_at = v_now
        WHERE id = v_schedule.id;
        v_claim := (
            v_schedule.id, v_schedule.name, v_schedule.version, v_now,
            v_schedule.target, v_schedule.recurrence, v_schedule.catchup_policy,
            v_schedule.max_catchup, v_schedule.initialized, v_schedule.next_fire_at,
            v_token, p_lease_seconds, v_schedule.smear_seconds
        )::taskq.schedule_claim;
        v_claims := array_append(v_claims, v_claim);
    END LOOP;
    RETURN (
        CASE WHEN cardinality(v_claims) = 0 THEN 'empty' ELSE 'claimed' END,
        v_claims
    )::taskq.schedule_claim_batch;
END $$;
ALTER FUNCTION taskq._claim_schedules_unattested(text, integer, integer) OWNER TO taskq_owner;
REVOKE ALL ON FUNCTION taskq._claim_schedules_unattested(text, integer, integer) FROM PUBLIC;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.5.1"'::jsonb, now())
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
        RAISE EXCEPTION '0029 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
