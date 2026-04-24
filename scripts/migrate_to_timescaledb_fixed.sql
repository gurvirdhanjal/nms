-- Proper TimescaleDB migration for the current monitoring schema.
--
-- Important design choice:
-- - tracking_samples remains a regular PostgreSQL table because it carries
--   relational/idempotency semantics that are not a good fit for a Timescale
--   hypertable in the current app schema.
-- - The raw append-only history tables become hypertables:
--     * server_health_logs
--     * device_resource_logs
--     * device_activity_logs
--     * device_application_logs
-- This script is also safe to run after the older broken migration attempt:
-- it restores the tracking_samples primary key / sample foreign keys and then
-- performs the supported hypertable conversion.

\set ON_ERROR_STOP on

\echo '=== Phase 1: Enabling TimescaleDB Extension ==='
CREATE EXTENSION IF NOT EXISTS timescaledb;

SELECT default_version, installed_version
FROM pg_available_extensions
WHERE name = 'timescaledb';

\echo '=== Phase 2: Restoring Relational Constraints ==='

DROP INDEX IF EXISTS idx_tracking_samples_id;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tracking_samples'::regclass
          AND conname = 'tracking_samples_pkey'
          AND contype = 'p'
    ) THEN
        ALTER TABLE tracking_samples
            ADD CONSTRAINT tracking_samples_pkey PRIMARY KEY (id);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'tracking_samples'
          AND column_name = 'previous_sample_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tracking_samples'::regclass
          AND conname = 'tracking_samples_previous_sample_id_fkey'
    ) THEN
        ALTER TABLE tracking_samples
            ADD CONSTRAINT tracking_samples_previous_sample_id_fkey
            FOREIGN KEY (previous_sample_id) REFERENCES tracking_samples(id);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'tracked_device_availability_events'
          AND column_name = 'sample_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tracked_device_availability_events'::regclass
          AND conname = 'tracked_device_availability_events_sample_id_fkey'
    ) THEN
        ALTER TABLE tracked_device_availability_events
            ADD CONSTRAINT tracked_device_availability_events_sample_id_fkey
            FOREIGN KEY (sample_id) REFERENCES tracking_samples(id);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'device_activity_logs'
          AND column_name = 'sample_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'device_activity_logs'::regclass
          AND conname = 'device_activity_logs_sample_id_fkey'
    ) THEN
        ALTER TABLE device_activity_logs
            ADD CONSTRAINT device_activity_logs_sample_id_fkey
            FOREIGN KEY (sample_id) REFERENCES tracking_samples(id);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'device_resource_logs'
          AND column_name = 'sample_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'device_resource_logs'::regclass
          AND conname = 'device_resource_logs_sample_id_fkey'
    ) THEN
        ALTER TABLE device_resource_logs
            ADD CONSTRAINT device_resource_logs_sample_id_fkey
            FOREIGN KEY (sample_id) REFERENCES tracking_samples(id);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'device_application_logs'
          AND column_name = 'sample_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'device_application_logs'::regclass
          AND conname = 'device_application_logs_sample_id_fkey'
    ) THEN
        ALTER TABLE device_application_logs
            ADD CONSTRAINT device_application_logs_sample_id_fkey
            FOREIGN KEY (sample_id) REFERENCES tracking_samples(id);
    END IF;
END $$;

\echo '=== Phase 3: Preparing Hypertable Candidates ==='

DROP INDEX IF EXISTS idx_server_health_logs_id;
DROP INDEX IF EXISTS idx_device_resource_logs_id;
DROP INDEX IF EXISTS idx_device_activity_logs_id;
DROP INDEX IF EXISTS idx_device_application_logs_id;

UPDATE server_health_logs
SET timestamp = NOW()
WHERE timestamp IS NULL;

UPDATE device_resource_logs
SET timestamp = NOW()
WHERE timestamp IS NULL;

UPDATE device_activity_logs
SET timestamp = NOW()
WHERE timestamp IS NULL;

UPDATE device_application_logs
SET timestamp = NOW()
WHERE timestamp IS NULL;

ALTER TABLE server_health_logs
    ALTER COLUMN id SET NOT NULL,
    ALTER COLUMN timestamp SET NOT NULL;

ALTER TABLE device_resource_logs
    ALTER COLUMN id SET NOT NULL,
    ALTER COLUMN timestamp SET NOT NULL;

ALTER TABLE device_activity_logs
    ALTER COLUMN id SET NOT NULL,
    ALTER COLUMN timestamp SET NOT NULL;

ALTER TABLE device_application_logs
    ALTER COLUMN id SET NOT NULL,
    ALTER COLUMN timestamp SET NOT NULL;

DO $$
DECLARE
    current_pk_name text;
    current_pk_cols text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname ORDER BY k.ordinality)
      INTO current_pk_name, current_pk_cols
    FROM pg_constraint c
    JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
    JOIN pg_attribute a
      ON a.attrelid = c.conrelid
     AND a.attnum = k.attnum
    WHERE c.conrelid = 'server_health_logs'::regclass
      AND c.contype = 'p'
    GROUP BY c.conname;

    IF current_pk_cols IS DISTINCT FROM ARRAY['id', 'timestamp'] THEN
        IF current_pk_name IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE server_health_logs DROP CONSTRAINT %I',
                current_pk_name
            );
        END IF;
        ALTER TABLE server_health_logs
            ADD CONSTRAINT server_health_logs_pkey PRIMARY KEY (id, timestamp);
    END IF;
END $$;

DO $$
DECLARE
    current_pk_name text;
    current_pk_cols text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname ORDER BY k.ordinality)
      INTO current_pk_name, current_pk_cols
    FROM pg_constraint c
    JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
    JOIN pg_attribute a
      ON a.attrelid = c.conrelid
     AND a.attnum = k.attnum
    WHERE c.conrelid = 'device_resource_logs'::regclass
      AND c.contype = 'p'
    GROUP BY c.conname;

    IF current_pk_cols IS DISTINCT FROM ARRAY['id', 'timestamp'] THEN
        IF current_pk_name IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE device_resource_logs DROP CONSTRAINT %I',
                current_pk_name
            );
        END IF;
        ALTER TABLE device_resource_logs
            ADD CONSTRAINT device_resource_logs_pkey PRIMARY KEY (id, timestamp);
    END IF;
END $$;

DO $$
DECLARE
    current_pk_name text;
    current_pk_cols text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname ORDER BY k.ordinality)
      INTO current_pk_name, current_pk_cols
    FROM pg_constraint c
    JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
    JOIN pg_attribute a
      ON a.attrelid = c.conrelid
     AND a.attnum = k.attnum
    WHERE c.conrelid = 'device_activity_logs'::regclass
      AND c.contype = 'p'
    GROUP BY c.conname;

    IF current_pk_cols IS DISTINCT FROM ARRAY['id', 'timestamp'] THEN
        IF current_pk_name IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE device_activity_logs DROP CONSTRAINT %I',
                current_pk_name
            );
        END IF;
        ALTER TABLE device_activity_logs
            ADD CONSTRAINT device_activity_logs_pkey PRIMARY KEY (id, timestamp);
    END IF;
END $$;

DO $$
DECLARE
    current_pk_name text;
    current_pk_cols text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname ORDER BY k.ordinality)
      INTO current_pk_name, current_pk_cols
    FROM pg_constraint c
    JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
    JOIN pg_attribute a
      ON a.attrelid = c.conrelid
     AND a.attnum = k.attnum
    WHERE c.conrelid = 'device_application_logs'::regclass
      AND c.contype = 'p'
    GROUP BY c.conname;

    IF current_pk_cols IS DISTINCT FROM ARRAY['id', 'timestamp'] THEN
        IF current_pk_name IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE device_application_logs DROP CONSTRAINT %I',
                current_pk_name
            );
        END IF;
        ALTER TABLE device_application_logs
            ADD CONSTRAINT device_application_logs_pkey PRIMARY KEY (id, timestamp);
    END IF;
END $$;

\echo '=== Phase 4: Converting Supported Tables to Hypertables ==='

SELECT create_hypertable(
    'server_health_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

SELECT create_hypertable(
    'device_resource_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

SELECT create_hypertable(
    'device_activity_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '3 days',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

SELECT create_hypertable(
    'device_application_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '3 days',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

\echo '=== Phase 5: Configuring Chunk Intervals ==='

SELECT set_chunk_time_interval('server_health_logs', INTERVAL '1 day');
SELECT set_chunk_time_interval('device_resource_logs', INTERVAL '1 day');
SELECT set_chunk_time_interval('device_activity_logs', INTERVAL '3 days');
SELECT set_chunk_time_interval('device_application_logs', INTERVAL '3 days');

\echo '=== Phase 6: Creating Indexes ==='

CREATE INDEX IF NOT EXISTS idx_server_health_source_device_id_id
ON server_health_logs (source, device_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_server_health_device_source_timestamp
ON server_health_logs (device_id, source, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_server_health_timestamp_brin
ON server_health_logs USING BRIN (timestamp);

CREATE INDEX IF NOT EXISTS idx_device_resource_logs_device_ts_id
ON device_resource_logs (device_id, timestamp DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_device_resource_logs_sample_id
ON device_resource_logs (sample_id);

CREATE INDEX IF NOT EXISTS idx_device_resource_logs_timestamp_brin
ON device_resource_logs USING BRIN (timestamp);

CREATE INDEX IF NOT EXISTS idx_device_activity_logs_device_ts_id
ON device_activity_logs (device_id, timestamp DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_device_activity_logs_sample_id
ON device_activity_logs (sample_id);

CREATE INDEX IF NOT EXISTS idx_device_activity_logs_timestamp_brin
ON device_activity_logs USING BRIN (timestamp);

CREATE INDEX IF NOT EXISTS idx_device_application_logs_device_ts_id
ON device_application_logs (device_id, timestamp DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_device_application_logs_sample_id
ON device_application_logs (sample_id);

CREATE INDEX IF NOT EXISTS idx_device_application_logs_timestamp_brin
ON device_application_logs USING BRIN (timestamp);

\echo '=== Phase 7: Configuring Compression Policies ==='

ALTER TABLE server_health_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id,source',
    timescaledb.compress_orderby = 'timestamp DESC'
);

ALTER TABLE device_resource_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

ALTER TABLE device_activity_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id,activity_type',
    timescaledb.compress_orderby = 'timestamp DESC'
);

ALTER TABLE device_application_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id,application_name',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy(
    'server_health_logs',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_compression_policy(
    'device_resource_logs',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

SELECT add_compression_policy(
    'device_activity_logs',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

SELECT add_compression_policy(
    'device_application_logs',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

\echo '=== Phase 8: Configuring Retention Policies ==='

SELECT add_retention_policy(
    'server_health_logs',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'device_resource_logs',
    INTERVAL '60 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'device_activity_logs',
    INTERVAL '60 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'device_application_logs',
    INTERVAL '60 days',
    if_not_exists => TRUE
);

\echo '=== Phase 9: Creating Continuous Aggregates ==='

CREATE MATERIALIZED VIEW IF NOT EXISTS server_health_hourly_cagg
WITH (timescaledb.continuous) AS
SELECT
    device_id,
    COALESCE(source, 'agent') AS source,
    time_bucket('1 hour', timestamp) AS bucket_hour,
    AVG(cpu_usage) AS avg_cpu_usage,
    MAX(cpu_usage) AS max_cpu_usage,
    AVG(memory_usage) AS avg_memory_usage,
    MAX(memory_usage) AS max_memory_usage,
    AVG(disk_usage) AS avg_disk_usage,
    AVG(network_in_bps) AS avg_network_in_bps,
    AVG(network_out_bps) AS avg_network_out_bps,
    COUNT(*) AS sample_count,
    COUNT(CASE WHEN cpu_usage IS NOT NULL THEN 1 END) AS online_samples,
    AVG(ping_latency_ms) AS avg_ping_latency_ms,
    MAX(ping_latency_ms) AS max_ping_latency_ms,
    AVG(packet_loss_pct) AS avg_packet_loss_pct,
    MAX(packet_loss_pct) AS max_packet_loss_pct
FROM server_health_logs
GROUP BY device_id, source, bucket_hour;

CREATE MATERIALIZED VIEW IF NOT EXISTS server_health_daily_cagg
WITH (timescaledb.continuous) AS
SELECT
    device_id,
    COALESCE(source, 'agent') AS source,
    time_bucket('1 day', timestamp) AS bucket_day,
    AVG(cpu_usage) AS avg_cpu_usage,
    MAX(cpu_usage) AS max_cpu_usage,
    AVG(memory_usage) AS avg_memory_usage,
    MAX(memory_usage) AS max_memory_usage,
    AVG(disk_usage) AS avg_disk_usage,
    AVG(network_in_bps) AS avg_network_in_bps,
    AVG(network_out_bps) AS avg_network_out_bps,
    COUNT(*) AS sample_count,
    COUNT(CASE WHEN cpu_usage IS NOT NULL THEN 1 END) AS online_samples,
    AVG(ping_latency_ms) AS avg_ping_latency_ms,
    MAX(ping_latency_ms) AS max_ping_latency_ms,
    AVG(packet_loss_pct) AS avg_packet_loss_pct,
    MAX(packet_loss_pct) AS max_packet_loss_pct
FROM server_health_logs
GROUP BY device_id, source, bucket_day;

SELECT add_continuous_aggregate_policy(
    'server_health_hourly_cagg',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy(
    'server_health_daily_cagg',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

\echo '=== Phase 10: Verification ==='

\echo 'Hypertables:'
SELECT
    hypertable_schema,
    hypertable_name,
    num_chunks
FROM timescaledb_information.hypertables
WHERE hypertable_name IN (
    'server_health_logs',
    'device_resource_logs',
    'device_activity_logs',
    'device_application_logs'
)
ORDER BY hypertable_name;

\echo 'Continuous Aggregates:'
SELECT
    view_schema,
    view_name,
    materialization_hypertable_name
FROM timescaledb_information.continuous_aggregates
WHERE view_name IN ('server_health_hourly_cagg', 'server_health_daily_cagg')
ORDER BY view_name;

\echo 'Scheduled Jobs:'
SELECT
    job_id,
    application_name,
    schedule_interval,
    next_start
FROM timescaledb_information.jobs
WHERE application_name LIKE '%Columnstore%'
   OR application_name LIKE '%Compression%'
   OR application_name LIKE '%Retention%'
   OR application_name LIKE '%Refresh%'
ORDER BY application_name, job_id;

\echo '=== Migration Complete ==='
\echo 'TimescaleDB is enabled. tracking_samples remains relational by design.'
