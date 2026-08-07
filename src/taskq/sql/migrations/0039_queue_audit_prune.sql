-- outlabs-taskq — migration 0039: queue_audit retention/prune verb (SQL contract 0.6.6)
--
-- The 0.6.5 operator audit log (taskq.queue_audit) is append-only and was unbounded.
-- Operator actions are low-volume, so it grows slowly, but there was no cap. This
-- adds an explicit, bounded retention verb: taskq.prune_queue_audit(older_than_hours)
-- deletes audit rows older than the cutoff and returns how many it removed. It is a
-- maintenance operation (like janitor/tick) — granted to taskq_housekeeper and
-- taskq_operator — meant to be run on a schedule or by hand, not on any hot path.
-- The delete is a single range scan over the queue_audit_time_brin index.
--
-- COMPATIBILITY: additive; contract 0.6.5 -> 0.6.6.

DO $$
DECLARE v_contract jsonb;
BEGIN
    SELECT value INTO v_contract FROM taskq.meta WHERE key = 'contract_version';
    IF v_contract IS DISTINCT FROM '"0.6.5"'::jsonb THEN
        RAISE EXCEPTION '0039 requires SQL contract 0.6.5, found %', v_contract;
    END IF;
END $$;

CREATE FUNCTION taskq.prune_queue_audit(p_older_than_hours integer)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE v_deleted bigint;
BEGIN
    IF p_older_than_hours IS NULL OR p_older_than_hours < 1 THEN
        RAISE EXCEPTION 'older_than_hours must be >= 1' USING ERRCODE = 'TQ422';
    END IF;
    WITH d AS (
        DELETE FROM taskq.queue_audit
         WHERE created_at < now() - make_interval(hours => p_older_than_hours)
        RETURNING 1
    )
    SELECT count(*) INTO v_deleted FROM d;
    RETURN v_deleted;
END $$;
ALTER FUNCTION taskq.prune_queue_audit(integer) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.prune_queue_audit(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.prune_queue_audit(integer) TO taskq_housekeeper;
GRANT EXECUTE ON FUNCTION taskq.prune_queue_audit(integer) TO taskq_operator;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('contract_version', '"0.6.6"'::jsonb, now())
ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = now();

DO $$
DECLARE v_bad text[];
BEGIN
    SELECT array_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text)
      INTO v_bad FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = 'taskq'
       AND (NOT p.prosecdef OR p.proconfig IS NULL
            OR NOT p.proconfig @> ARRAY['search_path=pg_catalog, taskq, pg_temp']);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION '0039 function hardening self-check failed: %', v_bad
            USING ERRCODE = 'TQ500';
    END IF;
END $$;
