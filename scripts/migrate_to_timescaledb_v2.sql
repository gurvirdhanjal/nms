-- TimescaleDB Migration Script V2
-- Drops all unique constraints and indexes before hypertable conversion

\echo '=== Phase 1: Enabling TimescaleDB Extension ==='
CREATE EXTENSION IF NOT EXISTS timescaledb;

\echo '=== Phase 2: Dropping Unique Indexes ==='

-- Drop unique indexes that prevent hypertable conversion
DROP INDEX IF EXISTS idx_server_health_logs_id CASCADE;
DROP INDEX IF EXISTS idx_tracking_samples_id CASCADE;
DROP INDEX IF EXISTS idx_device_resource_logs_id CASCADE;
DROP INDEX IF EXISTS idx_device_activity_logs_id CASCADE;
DROP INDEX IF EXISTS idx_device_application_logs_id CASCADE;

\echo '=== Phase 3: Converting Tables to Hypertables ==='

-- Convert server_health_logs
\echo 'Converting server_health_logs...'
SELECT create_hypertable(
    'server_health_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert tracking_samples
\echo 'Converting tracking_samples...'
SELECT create_hypertable(
    'tracking_samples',
    'received_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert device_resource_logs
\echo 'Converting device_resource_logs...'
SELECT create_hypertable(
    'device_resource_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert device_activity_logs
\echo 'Converting device_activity_logs...'
SELECT create_hypertable(
    'device_activity_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert device_application_logs
\echo 'Converting device_application_logs...'
SELECT create_hypertable(
    'device_application_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

\echo '=== Phase 4: Verifying Hypertables ==='
SELECT hypertable_schema, hypertable_name, num_chunks 
FROM timescaledb_information.hypertables;

\echo '=== Phase 5: Configuring Compression ==='

-- server_health_logs
ALTER TABLE server_health_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id,source',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('server_health_logs', INTERVAL '7 days');

-- tracking_samples
ALTER TABLE tracking_samples SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'received_at DESC'
);
SELECT add_compression_policy('tracking_samples', INTERVAL '30 days');

-- device_resource_logs
ALTER TABLE device_resource_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('device_resource_logs', INTERVAL '30 days');

-- device_activity_logs
ALTER TABLE device_activity_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('device_activity_logs', INTERVAL '30 days');

-- device_application_logs
ALTER TABLE device_application_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('device_application_logs', INTERVAL '30 days');

\echo '=== Phase 6: Configuring Retention Policies ==='
SELECT add_retention_policy('server_health_logs', INTERVAL '30 days');
SELECT add_retention_policy('tracking_samples', INTERVAL '60 days');
SELECT add_retention_policy('device_resource_logs', INTERVAL '60 days');
SELECT add_retention_policy('device_activity_logs', INTERVAL '60 days');
SELECT add_retention_policy('device_application_logs', INTERVAL '60 days');

\echo '=== Phase 7: Creating Continuous Aggregates ==='

-- Hourly aggregate
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
    COUNT(*) AS sample_count
FROM server_health_logs
GROUP BY device_id, source, bucket_hour;

SELECT add_continuous_aggregate_policy('server_health_hourly_cagg',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '5 minutes'
);

-- Daily aggregate
CREATE MATERIALIZED VIEW IF NOT EXISTS server_health_daily_cagg
WITH (timescaledb.continuous) AS
SELECT
    device_id,
    source,
    time_bucket('1 day', bucket_hour) AS bucket_day,
    AVG(avg_cpu_usage) AS avg_cpu_usage,
    MAX(max_cpu_usage) AS max_cpu_usage,
    AVG(avg_memory_usage) AS avg_memory_usage,
    MAX(max_memory_usage) AS max_memory_usage,
    AVG(avg_disk_usage) AS avg_disk_usage,
    SUM(sample_count) AS sample_count
FROM server_health_hourly_cagg
GROUP BY device_id, source, bucket_day;

SELECT add_continuous_aggregate_policy('server_health_daily_cagg',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- Tracking hourly
CREATE MATERIALIZED VIEW IF NOT EXISTS tracking_hourly_cagg
WITH (timescaledb.continuous) AS
SELECT
    device_id,
    time_bucket('1 hour', received_at) AS bucket_hour,
    COUNT(*) AS sample_count
FROM tracking_samples
GROUP BY device_id, bucket_hour;

SELECT add_continuous_aggregate_policy('tracking_hourly_cagg',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '10 minutes'
);

\echo '=== Phase 8: Final Verification ==='
SELECT 
    hypertable_schema,
    hypertable_name,
    num_chunks
FROM timescaledb_information.hypertables;

SELECT 
    view_schema,
    view_name
FROM timescaledb_information.continuous_aggregates;

SELECT 
    job_id,
    application_name,
    schedule_interval
FROM timescaledb_information.jobs
WHERE application_name LIKE '%Compression%' OR application_name LIKE '%Retention%' OR application_name LIKE '%Refresh%'
ORDER BY application_name;

\echo '=== Migration Complete! ==='
