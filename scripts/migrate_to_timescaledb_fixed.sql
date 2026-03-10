-- TimescaleDB Migration Script (Fixed for existing primary keys)
-- Device Monitoring System - Time-Clustered Database Implementation

\echo '=== Phase 1: Enabling TimescaleDB Extension ==='
CREATE EXTENSION IF NOT EXISTS timescaledb;

SELECT default_version, installed_version 
FROM pg_available_extensions 
WHERE name = 'timescaledb';

\echo '=== Phase 2: Preparing Tables for Hypertable Conversion ==='

-- Drop primary keys that don't include timestamp
\echo 'Dropping incompatible primary keys...'
ALTER TABLE server_health_logs DROP CONSTRAINT IF EXISTS server_health_logs_pkey CASCADE;
ALTER TABLE tracking_samples DROP CONSTRAINT IF EXISTS tracking_samples_pkey CASCADE;
ALTER TABLE device_resource_logs DROP CONSTRAINT IF EXISTS device_resource_logs_pkey CASCADE;
ALTER TABLE device_activity_logs DROP CONSTRAINT IF EXISTS device_activity_logs_pkey CASCADE;
ALTER TABLE device_application_logs DROP CONSTRAINT IF EXISTS device_application_logs_pkey CASCADE;

-- Add unique indexes instead (without primary key constraint)
\echo 'Creating unique indexes...'
CREATE UNIQUE INDEX IF NOT EXISTS idx_server_health_logs_id ON server_health_logs(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracking_samples_id ON tracking_samples(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_resource_logs_id ON device_resource_logs(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_activity_logs_id ON device_activity_logs(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_device_application_logs_id ON device_application_logs(id);

\echo '=== Phase 3: Converting Tables to Hypertables ==='

-- Convert server_health_logs
\echo 'Converting server_health_logs to hypertable...'
SELECT create_hypertable(
    'server_health_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert tracking_samples
\echo 'Converting tracking_samples to hypertable...'
SELECT create_hypertable(
    'tracking_samples',
    'received_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert device_resource_logs
\echo 'Converting device_resource_logs to hypertable...'
SELECT create_hypertable(
    'device_resource_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert device_activity_logs
\echo 'Converting device_activity_logs to hypertable...'
SELECT create_hypertable(
    'device_activity_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Convert device_application_logs
\echo 'Converting device_application_logs to hypertable...'
SELECT create_hypertable(
    'device_application_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Verify hypertables created
\echo 'Verifying hypertables...'
SELECT hypertable_schema, hypertable_name, num_chunks 
FROM timescaledb_information.hypertables;

\echo '=== Phase 4: Configuring Compression Policies ==='

-- Enable compression on server_health_logs
\echo 'Enabling compression on server_health_logs...'
ALTER TABLE server_health_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id,source',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy(
    'server_health_logs',
    INTERVAL '7 days'
);

-- Enable compression on tracking_samples
\echo 'Enabling compression on tracking_samples...'
ALTER TABLE tracking_samples SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'received_at DESC'
);
SELECT add_compression_policy('tracking_samples', INTERVAL '30 days');

-- Enable compression on device_resource_logs
\echo 'Enabling compression on device_resource_logs...'
ALTER TABLE device_resource_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('device_resource_logs', INTERVAL '30 days');

-- Enable compression on device_activity_logs
\echo 'Enabling compression on device_activity_logs...'
ALTER TABLE device_activity_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('device_activity_logs', INTERVAL '30 days');

-- Enable compression on device_application_logs
\echo 'Enabling compression on device_application_logs...'
ALTER TABLE device_application_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
SELECT add_compression_policy('device_application_logs', INTERVAL '30 days');

\echo '=== Phase 5: Configuring Retention Policies ==='

-- Automatically drop old chunks
SELECT add_retention_policy('server_health_logs', INTERVAL '30 days');
SELECT add_retention_policy('tracking_samples', INTERVAL '60 days');
SELECT add_retention_policy('device_resource_logs', INTERVAL '60 days');
SELECT add_retention_policy('device_activity_logs', INTERVAL '60 days');
SELECT add_retention_policy('device_application_logs', INTERVAL '60 days');

\echo '=== Phase 6: Creating Continuous Aggregates ==='

-- Hourly server health aggregate
\echo 'Creating hourly continuous aggregate...'
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

SELECT add_continuous_aggregate_policy('server_health_hourly_cagg',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '5 minutes'
);

-- Daily server health aggregate
\echo 'Creating daily continuous aggregate...'
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
    AVG(avg_network_in_bps) AS avg_network_in_bps,
    AVG(avg_network_out_bps) AS avg_network_out_bps,
    SUM(sample_count) AS sample_count,
    SUM(online_samples) AS online_samples,
    AVG(avg_ping_latency_ms) AS avg_ping_latency_ms,
    MAX(max_ping_latency_ms) AS max_ping_latency_ms,
    AVG(avg_packet_loss_pct) AS avg_packet_loss_pct,
    MAX(max_packet_loss_pct) AS max_packet_loss_pct
FROM server_health_hourly_cagg
GROUP BY device_id, source, bucket_day;

SELECT add_continuous_aggregate_policy('server_health_daily_cagg',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- Tracking hourly aggregate
\echo 'Creating tracking hourly continuous aggregate...'
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

\echo '=== Phase 7: Creating Optimized Indexes ==='

CREATE INDEX IF NOT EXISTS idx_server_health_device_source 
ON server_health_logs (device_id, source, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_tracking_device_time 
ON tracking_samples (device_id, received_at DESC);

\echo '=== Phase 8: Verification ==='

-- Show hypertables
\echo 'Hypertables:'
SELECT 
    hypertable_schema,
    hypertable_name,
    num_chunks
FROM timescaledb_information.hypertables;

-- Show compression settings
\echo 'Compression Settings:'
SELECT 
    hypertable_schema,
    hypertable_name,
    attname,
    segmentby_column_index,
    orderby_column_index
FROM timescaledb_information.compression_settings;

-- Show continuous aggregates
\echo 'Continuous Aggregates:'
SELECT 
    view_schema,
    view_name,
    materialization_hypertable_schema,
    materialization_hypertable_name
FROM timescaledb_information.continuous_aggregates;

-- Show scheduled jobs
\echo 'Scheduled Jobs:'
SELECT 
    job_id,
    application_name,
    schedule_interval,
    next_start
FROM timescaledb_information.jobs
ORDER BY next_start;

\echo '=== Migration Complete ==='
\echo 'TimescaleDB is now configured and ready to use!'
