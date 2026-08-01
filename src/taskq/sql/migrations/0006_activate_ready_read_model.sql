-- outlabs-taskq — migration 0006: ready read-model activation (SQL contract 0.1.4)
-- DERIVED FROM: Function Manifest §13; B9 evidence commit 7fe2c6b.
-- Immutable metadata-only activation. No function, grant, index, or wire change.

DO $$
DECLARE
    v_contract text;
BEGIN
    SELECT value #>> '{}'
      INTO v_contract
      FROM taskq.meta
     WHERE key = 'contract_version'
     FOR UPDATE;
    IF v_contract IS DISTINCT FROM '0.1.4' THEN
        RAISE EXCEPTION 'ready read-model activation requires SQL contract 0.1.4'
            USING ERRCODE = 'TQ500';
    END IF;
END $$;

INSERT INTO taskq.meta(key, value, updated_at) VALUES
    ('capabilities', '{"active":["read_model_list_ready"]}'::jsonb, now())
ON CONFLICT (key) DO UPDATE
    SET value = EXCLUDED.value,
        updated_at = EXCLUDED.updated_at;
