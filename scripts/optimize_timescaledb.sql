-- Post-migration TimescaleDB optimization for the supported hypertables.
--
-- tracking_samples intentionally remains a regular PostgreSQL table and is not
-- part of this optimization pass.

\set ON_ERROR_STOP on

\echo '=== Phase 1: Verifying Supported Hypertables ==='

SELECT
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

\echo '=== Phase 2: Enforcing Chunk Intervals ==='

SELECT set_chunk_time_interval('server_health_logs', INTERVAL '1 day');
SELECT set_chunk_time_interval('device_resource_logs', INTERVAL '1 day');
SELECT set_chunk_time_interval('device_activity_logs', INTERVAL '3 days');
SELECT set_chunk_time_interval('device_application_logs', INTERVAL '3 days');

\echo '=== Phase 3: Ensuring Query Indexes ==='

CREATE INDEX IF NOT EXISTS idx_server_health_device_time
ON server_health_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_server_health_source_time
ON server_health_logs (source, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_server_health_device_source_time
ON server_health_logs (device_id, source, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_resource_device_time
ON device_resource_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_activity_device_time
ON device_activity_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_application_device_time
ON device_application_logs (device_id, timestamp DESC);

\echo '=== Phase 4: Continuous Aggregate Verification ==='

SELECT
    view_schema,
    view_name,
    materialization_hypertable_name
FROM timescaledb_information.continuous_aggregates
WHERE view_name IN ('server_health_hourly_cagg', 'server_health_daily_cagg')
ORDER BY view_name;

\echo '=== Phase 5: Compression Settings ==='

SELECT
    hypertable_schema,
    hypertable_name,
    attname,
    segmentby_column_index,
    orderby_column_index
FROM timescaledb_information.compression_settings
WHERE hypertable_name IN (
    'server_health_logs',
    'device_resource_logs',
    'device_activity_logs',
    'device_application_logs'
)
ORDER BY hypertable_name, segmentby_column_index, orderby_column_index;

\echo '=== Phase 6: Background Jobs ==='

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

\echo '=== Optimization Complete ==='
