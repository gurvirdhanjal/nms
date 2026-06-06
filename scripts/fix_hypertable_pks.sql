-- fix_hypertable_pks.sql
-- Fix PKs on the 4 tables in _HYPERTABLE_SPECS so ensure_hypertables() can
-- convert them. TimescaleDB requires the time column in every unique index/PK.
-- Idempotent: safe to re-run. Each table: drop id-only PK, add (id, timestamp).
-- After this runs, a container restart triggers ensure_hypertables() which
-- calls create_hypertable() for each table automatically.

DO $$
DECLARE
    tbl TEXT;
    time_col TEXT;
BEGIN
    FOR tbl, time_col IN VALUES
        ('server_health_logs',      'timestamp'),
        ('device_resource_logs',    'timestamp'),
        ('device_activity_logs',    'timestamp'),
        ('device_application_logs', 'timestamp')
    LOOP
        -- Skip if already a hypertable (ensure_hypertables already ran)
        IF EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_name = tbl
        ) THEN
            RAISE NOTICE '% is already a hypertable — skipping.', tbl;
            CONTINUE;
        END IF;

        -- Skip if PK already includes the time column
        IF EXISTS (
            SELECT 1
            FROM pg_index idx
            JOIN pg_class c ON c.oid = idx.indrelid
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(idx.indkey)
            WHERE c.relname = tbl AND idx.indisprimary AND a.attname = time_col
        ) THEN
            RAISE NOTICE '% PK already includes % — skipping.', tbl, time_col;
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', tbl, tbl || '_pkey');
        EXECUTE format('ALTER TABLE %I ADD PRIMARY KEY (id, %I)', tbl, time_col);
        RAISE NOTICE 'Fixed PK on % to include %.', tbl, time_col;
    END LOOP;
END;
$$;
