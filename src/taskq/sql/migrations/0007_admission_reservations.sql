-- outlabs-taskq — migration 0007: durable two-phase admission (SQL contract 0.1.5)
-- Canonical authority: Function Manifest §14 / ADR-023 / Protocol v1.0.8.

DO $$
DECLARE
    v_contract text;
    v_capabilities jsonb;
BEGIN
    SELECT value #>> '{}' INTO v_contract
      FROM taskq.meta WHERE key = 'contract_version';
    SELECT value INTO v_capabilities
      FROM taskq.meta WHERE key = 'capabilities';
    IF v_contract IS DISTINCT FROM '0.1.4' THEN
        RAISE EXCEPTION '0007 requires SQL contract 0.1.4, found %', v_contract;
    END IF;
    IF v_capabilities IS DISTINCT FROM '{"active":["read_model_list_ready"]}'::jsonb THEN
        RAISE EXCEPTION '0007 requires the exact 0006 capability posture';
    END IF;
END $$;

CREATE TABLE taskq.admissions (
    id                          uuid PRIMARY KEY DEFAULT taskq.uuid7(),
    queue                       text NOT NULL REFERENCES taskq.queues(name),
    idempotency_key             text NOT NULL
        CHECK (char_length(idempotency_key) BETWEEN 1 AND 255),
    intent_hash                 text NOT NULL
        CHECK (intent_hash ~ '^[0-9a-f]{64}$'),
    handle                      uuid NOT NULL
        CHECK (handle <> '00000000-0000-0000-0000-000000000000'::uuid),
    state                       text NOT NULL
        CHECK (state IN ('reserved','admitted','cancelled')),
    reservation_expires_at      timestamptz NOT NULL,
    receipt_ttl_seconds         integer NOT NULL
        CHECK (receipt_ttl_seconds BETWEEN 3600 AND 31536000),
    finish_hash                 text CHECK (finish_hash IS NULL OR finish_hash ~ '^[0-9a-f]{64}$'),
    job_id                      uuid,
    receipt                     jsonb,
    receipt_expires_at          timestamptz,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    admitted_at                 timestamptz,
    cancelled_at                timestamptz,
    CONSTRAINT admissions_queue_key_uq UNIQUE (queue, idempotency_key),
    CONSTRAINT admissions_receipt_object_ck
        CHECK (receipt IS NULL OR jsonb_typeof(receipt) = 'object'),
    CONSTRAINT admissions_state_ck CHECK (
        (state = 'reserved' AND finish_hash IS NULL AND job_id IS NULL
            AND receipt IS NULL AND receipt_expires_at IS NULL
            AND admitted_at IS NULL AND cancelled_at IS NULL)
        OR
        (state = 'admitted' AND finish_hash IS NOT NULL AND job_id IS NOT NULL
            AND receipt IS NOT NULL AND receipt_expires_at IS NOT NULL
            AND admitted_at IS NOT NULL AND cancelled_at IS NULL)
        OR
        (state = 'cancelled' AND finish_hash IS NULL AND job_id IS NULL
            AND receipt IS NULL AND receipt_expires_at IS NULL
            AND admitted_at IS NULL AND cancelled_at IS NOT NULL)
    )
);
ALTER TABLE taskq.admissions OWNER TO taskq_owner;
REVOKE ALL ON TABLE taskq.admissions FROM PUBLIC;

CREATE INDEX admissions_receipt_cleanup_idx
    ON taskq.admissions (receipt_expires_at, id)
    WHERE state = 'admitted';
CREATE INDEX admissions_reservation_cleanup_idx
    ON taskq.admissions (reservation_expires_at, id)
    WHERE state = 'reserved';
CREATE INDEX admissions_cancelled_cleanup_idx
    ON taskq.admissions (updated_at, id)
    WHERE state = 'cancelled';

ALTER TABLE taskq.jobs
    ADD COLUMN admission_id uuid REFERENCES taskq.admissions(id);
ALTER TABLE taskq.jobs
    ADD CONSTRAINT jobs_admission_id_uq UNIQUE (admission_id);

CREATE TYPE taskq.admission_reservation AS (
    outcome text,
    handle uuid,
    job_id uuid,
    reservation_expires_at timestamptz,
    retry_after_seconds integer,
    receipt jsonb,
    receipt_expires_at timestamptz
);
ALTER TYPE taskq.admission_reservation OWNER TO taskq_owner;

CREATE TYPE taskq.admission_finish_result AS (
    outcome text,
    job_id uuid,
    receipt jsonb,
    receipt_expires_at timestamptz
);
ALTER TYPE taskq.admission_finish_result OWNER TO taskq_owner;

CREATE TYPE taskq.admission_cancel_result AS (
    outcome text,
    job_id uuid,
    receipt jsonb,
    receipt_expires_at timestamptz
);
ALTER TYPE taskq.admission_cancel_result OWNER TO taskq_owner;

CREATE FUNCTION taskq.reserve_admission(
    p_queue text,
    p_idempotency_key text,
    p_intent_hash text,
    p_handle uuid,
    p_reservation_ttl_seconds integer DEFAULT 300,
    p_receipt_ttl_seconds integer DEFAULT 2592000
) RETURNS taskq.admission_reservation
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $reserve$
DECLARE
    v_admission taskq.admissions%ROWTYPE;
    v_now timestamptz := now();
    v_retry integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM taskq.queues q WHERE q.name = p_queue) THEN
        RAISE EXCEPTION 'taskq: unknown queue' USING ERRCODE = 'TQ001';
    END IF;
    IF p_idempotency_key IS NULL OR p_idempotency_key = ''
       OR char_length(p_idempotency_key) > 255 THEN
        RAISE EXCEPTION 'idempotency_key must be 1..255 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_intent_hash IS NULL OR p_intent_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'intent_hash must be lowercase SHA-256 hex' USING ERRCODE = 'TQ422';
    END IF;
    IF p_handle IS NULL OR p_handle = '00000000-0000-0000-0000-000000000000'::uuid THEN
        RAISE EXCEPTION 'handle must be a non-nil UUID' USING ERRCODE = 'TQ422';
    END IF;
    IF p_reservation_ttl_seconds IS NULL
       OR p_reservation_ttl_seconds NOT BETWEEN 15 AND 3600 THEN
        RAISE EXCEPTION 'reservation_ttl_seconds must be 15..3600'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_receipt_ttl_seconds IS NULL
       OR p_receipt_ttl_seconds NOT BETWEEN 3600 AND 31536000 THEN
        RAISE EXCEPTION 'receipt_ttl_seconds must be 3600..31536000'
            USING ERRCODE = 'TQ422';
    END IF;

    LOOP
        SELECT * INTO v_admission
          FROM taskq.admissions a
         WHERE a.queue = p_queue AND a.idempotency_key = p_idempotency_key
         FOR UPDATE;

        IF NOT FOUND THEN
            INSERT INTO taskq.admissions (
                queue, idempotency_key, intent_hash, handle, state,
                reservation_expires_at, receipt_ttl_seconds
            ) VALUES (
                p_queue, p_idempotency_key, p_intent_hash, p_handle, 'reserved',
                v_now + make_interval(secs => p_reservation_ttl_seconds),
                p_receipt_ttl_seconds
            )
            ON CONFLICT (queue, idempotency_key) DO NOTHING
            RETURNING * INTO v_admission;
            IF FOUND THEN
                RETURN ROW(
                    'reserved', v_admission.handle, NULL::uuid,
                    v_admission.reservation_expires_at, NULL::integer,
                    NULL::jsonb, NULL::timestamptz
                )::taskq.admission_reservation;
            END IF;
            CONTINUE;
        END IF;

        IF v_admission.state = 'admitted' THEN
            IF v_admission.receipt_expires_at <= v_now
               AND NOT EXISTS (
                   SELECT 1 FROM taskq.jobs j WHERE j.admission_id = v_admission.id
               ) THEN
                NULL; -- cleanup-eligible; reacquire below, including a new intent
            ELSIF v_admission.intent_hash <> p_intent_hash THEN
                RAISE EXCEPTION 'taskq: admission intent mismatch'
                    USING ERRCODE = 'TQ409', DETAIL = '{"reason":"idempotency_mismatch"}';
            ELSE
                RETURN ROW(
                    'admitted', NULL::uuid, v_admission.job_id,
                    NULL::timestamptz, NULL::integer,
                    v_admission.receipt, v_admission.receipt_expires_at
                )::taskq.admission_reservation;
            END IF;
        ELSIF v_admission.state = 'reserved'
              AND v_admission.reservation_expires_at > v_now THEN
            IF v_admission.intent_hash <> p_intent_hash THEN
                RAISE EXCEPTION 'taskq: admission intent mismatch'
                    USING ERRCODE = 'TQ409', DETAIL = '{"reason":"idempotency_mismatch"}';
            END IF;
            IF v_admission.handle = p_handle THEN
                RETURN ROW(
                    'reserved', v_admission.handle, NULL::uuid,
                    v_admission.reservation_expires_at, NULL::integer,
                    NULL::jsonb, NULL::timestamptz
                )::taskq.admission_reservation;
            END IF;
            v_retry := greatest(
                1,
                ceil(extract(epoch FROM (v_admission.reservation_expires_at - v_now)))::integer
            );
            RETURN ROW(
                'pending', NULL::uuid, NULL::uuid,
                v_admission.reservation_expires_at, v_retry,
                NULL::jsonb, NULL::timestamptz
            )::taskq.admission_reservation;
        END IF;

        UPDATE taskq.admissions
           SET intent_hash = p_intent_hash,
               handle = p_handle,
               state = 'reserved',
               reservation_expires_at = v_now + make_interval(secs => p_reservation_ttl_seconds),
               receipt_ttl_seconds = p_receipt_ttl_seconds,
               finish_hash = NULL,
               job_id = NULL,
               receipt = NULL,
               receipt_expires_at = NULL,
               updated_at = v_now,
               admitted_at = NULL,
               cancelled_at = NULL
         WHERE id = v_admission.id
         RETURNING * INTO v_admission;
        RETURN ROW(
            'reserved', v_admission.handle, NULL::uuid,
            v_admission.reservation_expires_at, NULL::integer,
            NULL::jsonb, NULL::timestamptz
        )::taskq.admission_reservation;
    END LOOP;
END $reserve$;
ALTER FUNCTION taskq.reserve_admission(text,text,text,uuid,integer,integer)
    OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.reserve_admission(text,text,text,uuid,integer,integer)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.reserve_admission(text,text,text,uuid,integer,integer)
    TO taskq_producer;

CREATE FUNCTION taskq.finish_admission(
    p_queue text,
    p_idempotency_key text,
    p_handle uuid,
    p_job jsonb,
    p_receipt jsonb DEFAULT '{}'::jsonb
) RETURNS taskq.admission_finish_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_admission taskq.admissions%ROWTYPE;
    v_now timestamptz := now();
    v_finish_hash text;
    v_job_id uuid;
    v_created boolean;
    v_job_type text;
    v_payload jsonb;
    v_priority smallint;
    v_scheduled_at timestamptz;
    v_concurrency_key text;
    v_affinity_key text;
    v_max_attempts smallint;
    v_lease_seconds integer;
    v_backoff_mode text;
    v_backoff_base integer;
    v_backoff_cap integer;
    v_headers jsonb;
    v_receipt_expires_at timestamptz;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM taskq.queues q WHERE q.name = p_queue) THEN
        RAISE EXCEPTION 'taskq: unknown queue' USING ERRCODE = 'TQ001';
    END IF;
    IF p_idempotency_key IS NULL OR p_idempotency_key = ''
       OR char_length(p_idempotency_key) > 255 THEN
        RAISE EXCEPTION 'idempotency_key must be 1..255 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_handle IS NULL OR p_handle = '00000000-0000-0000-0000-000000000000'::uuid THEN
        RAISE EXCEPTION 'handle must be a non-nil UUID' USING ERRCODE = 'TQ422';
    END IF;
    IF p_job IS NULL OR jsonb_typeof(p_job) <> 'object'
       OR NOT (p_job ? 'job_type') OR NOT (p_job ? 'payload') THEN
        RAISE EXCEPTION 'job must be an object with job_type and payload'
            USING ERRCODE = 'TQ422';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_object_keys(p_job) AS k(key)
         WHERE k.key NOT IN (
             'job_type','payload','priority','scheduled_at','concurrency_key','affinity_key',
             'max_attempts','lease_seconds','backoff_mode','backoff_base','backoff_cap','headers'
         )
    ) THEN
        RAISE EXCEPTION 'job contains an unsupported field' USING ERRCODE = 'TQ422';
    END IF;
    IF jsonb_typeof(p_job->'job_type') <> 'string'
       OR jsonb_typeof(p_job->'payload') <> 'object' THEN
        RAISE EXCEPTION 'job_type must be a string and payload an object'
            USING ERRCODE = 'TQ422';
    END IF;
    IF p_receipt IS NULL OR jsonb_typeof(p_receipt) <> 'object'
       OR octet_length(p_receipt::text) > 2048 THEN
        RAISE EXCEPTION 'receipt must be an object at most 2048 bytes'
            USING ERRCODE = 'TQ422';
    END IF;

    BEGIN
        IF p_job ? 'priority' AND jsonb_typeof(p_job->'priority') NOT IN ('number','null') THEN
            RAISE EXCEPTION 'invalid priority' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'scheduled_at'
           AND jsonb_typeof(p_job->'scheduled_at') NOT IN ('string','null') THEN
            RAISE EXCEPTION 'invalid scheduled_at' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'max_attempts'
           AND jsonb_typeof(p_job->'max_attempts') NOT IN ('number','null') THEN
            RAISE EXCEPTION 'invalid max_attempts' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'lease_seconds'
           AND jsonb_typeof(p_job->'lease_seconds') NOT IN ('number','null') THEN
            RAISE EXCEPTION 'invalid lease_seconds' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'backoff_base'
           AND jsonb_typeof(p_job->'backoff_base') NOT IN ('number','null') THEN
            RAISE EXCEPTION 'invalid backoff_base' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'backoff_cap'
           AND jsonb_typeof(p_job->'backoff_cap') NOT IN ('number','null') THEN
            RAISE EXCEPTION 'invalid backoff_cap' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'concurrency_key'
           AND jsonb_typeof(p_job->'concurrency_key') NOT IN ('string','null') THEN
            RAISE EXCEPTION 'invalid concurrency_key' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'affinity_key'
           AND jsonb_typeof(p_job->'affinity_key') NOT IN ('string','null') THEN
            RAISE EXCEPTION 'invalid affinity_key' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'backoff_mode'
           AND jsonb_typeof(p_job->'backoff_mode') NOT IN ('string','null') THEN
            RAISE EXCEPTION 'invalid backoff_mode' USING ERRCODE = 'TQ422';
        END IF;
        IF p_job ? 'headers' AND jsonb_typeof(p_job->'headers') NOT IN ('object','null') THEN
            RAISE EXCEPTION 'invalid headers' USING ERRCODE = 'TQ422';
        END IF;

        v_job_type := p_job->>'job_type';
        v_payload := p_job->'payload';
        v_priority := (p_job->>'priority')::smallint;
        v_scheduled_at := (p_job->>'scheduled_at')::timestamptz;
        v_concurrency_key := p_job->>'concurrency_key';
        v_affinity_key := p_job->>'affinity_key';
        v_max_attempts := (p_job->>'max_attempts')::smallint;
        v_lease_seconds := (p_job->>'lease_seconds')::integer;
        v_backoff_mode := p_job->>'backoff_mode';
        v_backoff_base := (p_job->>'backoff_base')::integer;
        v_backoff_cap := (p_job->>'backoff_cap')::integer;
        v_headers := p_job->'headers';
        IF v_headers = 'null'::jsonb THEN v_headers := NULL; END IF;
    EXCEPTION
        WHEN data_exception THEN
            RAISE EXCEPTION 'job contains an invalid typed value' USING ERRCODE = 'TQ422';
    END;

    v_finish_hash := encode(
        sha256(convert_to(jsonb_build_object('job', p_job, 'receipt', p_receipt)::text, 'UTF8')),
        'hex'
    );

    SELECT * INTO v_admission
      FROM taskq.admissions a
     WHERE a.queue = p_queue AND a.idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such admission' USING ERRCODE = 'TQ001';
    END IF;
    IF v_admission.handle <> p_handle THEN
        RAISE EXCEPTION 'taskq: reservation handle conflict'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"reservation_conflict"}';
    END IF;
    IF v_admission.state = 'admitted' THEN
        IF v_admission.finish_hash <> v_finish_hash THEN
            RAISE EXCEPTION 'taskq: finish content mismatch'
                USING ERRCODE = 'TQ409', DETAIL = '{"reason":"finish_mismatch"}';
        END IF;
        RETURN ROW(
            'existed', v_admission.job_id,
            v_admission.receipt, v_admission.receipt_expires_at
        )::taskq.admission_finish_result;
    END IF;
    IF v_admission.state = 'cancelled' THEN
        RAISE EXCEPTION 'taskq: reservation cancelled'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"reservation_cancelled"}';
    END IF;
    IF v_admission.reservation_expires_at <= v_now THEN
        RAISE EXCEPTION 'taskq: reservation expired'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"reservation_expired"}';
    END IF;

    SELECT e.job_id, e.created INTO v_job_id, v_created
      FROM taskq.enqueue(
          p_queue, v_job_type, v_payload, v_priority, v_scheduled_at,
          NULL, v_concurrency_key, v_affinity_key, v_max_attempts,
          v_lease_seconds, v_backoff_mode, v_backoff_base, v_backoff_cap,
          NULL, NULL, NULL, NULL, v_headers
      ) AS e;
    IF v_job_id IS NULL OR v_created IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'taskq: admission enqueue did not create a job' USING ERRCODE = 'TQ500';
    END IF;

    UPDATE taskq.jobs SET admission_id = v_admission.id WHERE id = v_job_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: admitted job disappeared' USING ERRCODE = 'TQ500';
    END IF;
    v_receipt_expires_at := v_now + make_interval(secs => v_admission.receipt_ttl_seconds);
    UPDATE taskq.admissions
       SET state = 'admitted',
           finish_hash = v_finish_hash,
           job_id = v_job_id,
           receipt = p_receipt,
           receipt_expires_at = v_receipt_expires_at,
           updated_at = v_now,
           admitted_at = v_now,
           cancelled_at = NULL
     WHERE id = v_admission.id;

    RETURN ROW(
        'created', v_job_id, p_receipt, v_receipt_expires_at
    )::taskq.admission_finish_result;
END $$;
ALTER FUNCTION taskq.finish_admission(text,text,uuid,jsonb,jsonb) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.finish_admission(text,text,uuid,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.finish_admission(text,text,uuid,jsonb,jsonb) TO taskq_producer;

CREATE FUNCTION taskq.cancel_admission(
    p_queue text,
    p_idempotency_key text,
    p_handle uuid
) RETURNS taskq.admission_cancel_result
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $$
DECLARE
    v_admission taskq.admissions%ROWTYPE;
    v_now timestamptz := now();
BEGIN
    IF NOT EXISTS (SELECT 1 FROM taskq.queues q WHERE q.name = p_queue) THEN
        RAISE EXCEPTION 'taskq: unknown queue' USING ERRCODE = 'TQ001';
    END IF;
    IF p_idempotency_key IS NULL OR p_idempotency_key = ''
       OR char_length(p_idempotency_key) > 255 THEN
        RAISE EXCEPTION 'idempotency_key must be 1..255 chars' USING ERRCODE = 'TQ422';
    END IF;
    IF p_handle IS NULL OR p_handle = '00000000-0000-0000-0000-000000000000'::uuid THEN
        RAISE EXCEPTION 'handle must be a non-nil UUID' USING ERRCODE = 'TQ422';
    END IF;

    SELECT * INTO v_admission
      FROM taskq.admissions a
     WHERE a.queue = p_queue AND a.idempotency_key = p_idempotency_key
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'taskq: no such admission' USING ERRCODE = 'TQ001';
    END IF;
    IF v_admission.handle <> p_handle THEN
        RAISE EXCEPTION 'taskq: reservation handle conflict'
            USING ERRCODE = 'TQ409', DETAIL = '{"reason":"reservation_conflict"}';
    END IF;
    IF v_admission.state = 'admitted' THEN
        RETURN ROW(
            'already_admitted', v_admission.job_id,
            v_admission.receipt, v_admission.receipt_expires_at
        )::taskq.admission_cancel_result;
    END IF;
    IF v_admission.state = 'cancelled' THEN
        RETURN ROW(
            'already_cancelled', NULL::uuid, NULL::jsonb, NULL::timestamptz
        )::taskq.admission_cancel_result;
    END IF;
    IF v_admission.reservation_expires_at <= v_now THEN
        RETURN ROW(
            'expired', NULL::uuid, NULL::jsonb, NULL::timestamptz
        )::taskq.admission_cancel_result;
    END IF;

    UPDATE taskq.admissions
       SET state = 'cancelled', updated_at = v_now, cancelled_at = v_now
     WHERE id = v_admission.id;
    RETURN ROW(
        'cancelled', NULL::uuid, NULL::jsonb, NULL::timestamptz
    )::taskq.admission_cancel_result;
END $$;
ALTER FUNCTION taskq.cancel_admission(text,text,uuid) OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.cancel_admission(text,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.cancel_admission(text,text,uuid) TO taskq_producer;

-- Preserve every existing janitor pass and add bounded admission cleanup after
-- terminal/dead job retention, so a job removed in this pass can release its
-- expired receipt without weakening job retention.
CREATE OR REPLACE FUNCTION taskq.janitor() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, taskq, pg_temp
AS $janitor$
DECLARE v jsonb := '{}'; v_n int;
BEGIN
    BEGIN
        WITH del AS (
            DELETE FROM taskq.jobs j USING taskq.queues q
             WHERE j.queue = q.name AND j.status IN ('succeeded','cancelled')
               AND j.finished_at < now() - make_interval(hours => q.retention_hours)
               AND j.id IN (SELECT id FROM taskq.jobs j2
                             WHERE j2.status IN ('succeeded','cancelled')
                             ORDER BY j2.finished_at LIMIT 5000)
             RETURNING j.id)
        SELECT count(*) INTO v_n FROM del;
        v := v || jsonb_build_object('terminal_deleted', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'retention: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN
        WITH del AS (
            DELETE FROM taskq.jobs j USING taskq.queues q
             WHERE j.queue = q.name AND j.status = 'failed'
               AND j.finished_at < now() - make_interval(hours => q.failed_retention_hours)
               AND j.id IN (SELECT id FROM taskq.jobs j2 WHERE j2.status = 'failed'
                             ORDER BY j2.finished_at LIMIT 2000)
             RETURNING j.id)
        SELECT count(*) INTO v_n FROM del;
        v := v || jsonb_build_object('failed_deleted', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'dead: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN
        DELETE FROM taskq.admissions a
         WHERE a.id IN (
             SELECT candidate.id
               FROM (
                   SELECT a2.id, a2.updated_at AS due_at
                     FROM taskq.admissions a2
                    WHERE a2.state = 'admitted'
                      AND a2.receipt_expires_at < now()
                      AND NOT EXISTS (
                          SELECT 1 FROM taskq.jobs j WHERE j.admission_id = a2.id
                      )
                   UNION ALL
                   SELECT a2.id, a2.reservation_expires_at AS due_at
                     FROM taskq.admissions a2
                    WHERE a2.state = 'reserved'
                      AND a2.reservation_expires_at < now() - interval '1 day'
                   UNION ALL
                   SELECT a2.id, a2.updated_at AS due_at
                     FROM taskq.admissions a2
                    WHERE a2.state = 'cancelled'
                      AND a2.updated_at < now() - interval '1 day'
               ) AS candidate
              ORDER BY candidate.due_at, candidate.id
              LIMIT 500
         );
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v := v || jsonb_build_object('admissions_pruned', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'admissions:'||SQLERRM
         WHERE key='janitor_daily';
    END;
    BEGIN
        DELETE FROM taskq.job_events WHERE id IN (
            SELECT id FROM taskq.job_events
             WHERE created_at < now() - interval '30 days' ORDER BY id LIMIT 20000);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v := v || jsonb_build_object('events_pruned', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'events: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN
        DELETE FROM taskq.workers WHERE last_seen_at < now() - interval '7 days';
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v := v || jsonb_build_object('workers_pruned', v_n);
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'workers: '||SQLERRM WHERE key='janitor_daily';
    END;
    BEGIN
        UPDATE taskq.control_state
           SET data = jsonb_set(COALESCE(data,'{}'), '{index_bytes}',
               (SELECT COALESCE(jsonb_object_agg(indexrelname, pg_relation_size(indexrelid)), '{}')
                  FROM pg_catalog.pg_stat_user_indexes
                 WHERE schemaname = 'taskq' AND relname = 'jobs')),
               last_finished_at = now()
         WHERE key = 'janitor_daily';
    EXCEPTION WHEN OTHERS THEN
        UPDATE taskq.control_state SET last_error = 'sizes: '||SQLERRM WHERE key='janitor_daily';
    END;
    RETURN v;
END $janitor$;
ALTER FUNCTION taskq.janitor() OWNER TO taskq_owner;
REVOKE EXECUTE ON FUNCTION taskq.janitor() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION taskq.janitor() TO taskq_housekeeper, taskq_operator;

INSERT INTO taskq.meta(key,value,updated_at) VALUES
    ('contract_version','"0.1.5"'::jsonb,now()),
    ('capabilities','{"active":["admission_reservations","read_model_list_ready"]}'::jsonb,now())
ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now();
