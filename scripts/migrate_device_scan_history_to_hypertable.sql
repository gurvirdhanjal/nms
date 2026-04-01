-- migrate_device_scan_history_to_hypertable.sql
-- One-time migration: convert device_scan_history to a TimescaleDB hypertable.
-- Fully idempotent: safe to re-run after partial failure.
--
-- Run from the DB container:
--   docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db \
--     -f /scripts/migrate_device_scan_history_to_hypertable.sql
--
-- Expected duration: ~30s for ~92K rows.
-- No application downtime required; inserts queue normally during migration.

-- ────────────────────────────────────────────────────────────────────────────
-- STEP 1: Guard — skip entirely if already a hypertable
-- ────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'device_scan_history'
    ) THEN
        RAISE NOTICE 'device_scan_history is already a hypertable — skipping conversion.';
    ELSE

        -- ────────────────────────────────────────────────────────────────────
        -- STEP 2: Drop plain B-tree indexes that would block hypertable creation
        -- (TimescaleDB re-creates them per-chunk after conversion)
        -- ────────────────────────────────────────────────────────────────────
        DROP INDEX IF EXISTS idx_device_scan_history_ts;
        DROP INDEX IF EXISTS idx_device_scan_history_ip_time;
        DROP INDEX IF EXISTS idx_device_scan_history_status_time;

        -- ────────────────────────────────────────────────────────────────────
        -- STEP 3: Convert to hypertable (1-day chunks, migrate existing data)
        -- ────────────────────────────────────────────────────────────────────
        PERFORM create_hypertable(
            'device_scan_history',
            'scan_timestamp',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE,
            migrate_data        => TRUE
        );

        RAISE NOTICE 'device_scan_history converted to hypertable.';
    END IF;
END;
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- STEP 4: Re-add query indexes (idempotent — IF NOT EXISTS)
-- TimescaleDB applies these to each new chunk automatically.
-- ────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_device_scan_history_ip_time
    ON device_scan_history (device_ip, scan_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_device_scan_history_status_time
    ON device_scan_history (status, scan_timestamp);

-- ────────────────────────────────────────────────────────────────────────────
-- STEP 5: Compression policy (compress chunks older than 7 days)
-- Guard: skip if compression is already configured.
-- ────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    -- Enable compression settings on the table (idempotent via ALTER TABLE ... SET)
    ALTER TABLE device_scan_history SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'device_ip',
        timescaledb.compress_orderby   = 'scan_timestamp DESC'
    );

    -- Add compression policy only if none exists yet
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_compression'
          AND hypertable_name = 'device_scan_history'
    ) THEN
        PERFORM add_compression_policy('device_scan_history', INTERVAL '7 days');
        RAISE NOTICE 'Compression policy added (7-day threshold).';
    ELSE
        RAISE NOTICE 'Compression policy already exists — skipped.';
    END IF;
END;
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- STEP 6: Retention policy (drop chunks older than 30 days)
-- The Python scheduler's purge_old_scan_history() job becomes redundant for
-- PostgreSQL after this runs (TimescaleDB drops whole chunks instantly).
-- Keep the Python job as a fallback for SQLite dev environments.
-- Guard: skip if retention policy already exists.
-- ────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_retention'
          AND hypertable_name = 'device_scan_history'
    ) THEN
        PERFORM add_retention_policy('device_scan_history', INTERVAL '30 days');
        RAISE NOTICE 'Retention policy added (30-day window).';
    ELSE
        RAISE NOTICE 'Retention policy already exists — skipped.';
    END IF;
END;
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Verification queries (run after migration)
-- ────────────────────────────────────────────────────────────────────────────
-- SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables
--   WHERE hypertable_name = 'device_scan_history';
--
-- SELECT job_id, proc_name, schedule_interval, config
--   FROM timescaledb_information.jobs
--   WHERE hypertable_name = 'device_scan_history';
