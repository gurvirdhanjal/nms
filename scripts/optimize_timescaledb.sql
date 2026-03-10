-- TimescaleDB Optimization Script
-- Implements production-grade optimizations for monitoring workload

\echo '=== Phase 1: Adding Device + Time Indexes ==='

-- Critical indexes for dashboard queries
CREATE INDEX IF NOT EXISTS idx_server_health_device_time 
ON server_health_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_tracking_device_time 
ON tracking_samples (device_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_resource_device_time 
ON device_resource_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_activity_device_time 
ON device_activity_logs (device_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_application_device_time 
ON device_application_logs (device_id, timestamp DESC);

\echo 'Device + Time indexes created'

\echo '=== Phase 2: Checking Chunk Intervals ==='

SELECT 
    hypertable_name,
    interval_length AS chunk_interval
FROM timescaledb_information.dimensions
WHERE dimension_type = 'Time'
ORDER BY hypertable_name;

\echo '=== Phase 3: Tuning Chunk Intervals ==='

-- Optimize chunk intervals based on ingestion rate
SELECT set_chunk_time_interval('server_health_logs', INTERVAL '1 day');
SELECT set_chunk_time_interval('tracking_samples', INTERVAL '1 day');
SELECT set_chunk_time_interval('device_resource_logs', INTERVAL '1 day');
SELECT set_chunk_time_interval('device_activity_logs', INTERVAL '3 days');
SELECT set_chunk_time_interval('device_application_logs', INTERVAL '3 days');

\echo 'Chunk intervals optimized'

\echo '=== Phase 4: Verifying Compression Settings ==='

-- Check current compression settings
SELECT 
    hypertable_schema,
    hypertable_name,
    attname,
    segmentby_column_index,
    orderby_column_index
FROM timescaledb_information.compression_settings
ORDER BY hypertable_name, segmentby_column_index;

\echo '=== Phase 5: Creating Extended Daily Aggregates ==='

-- Extended retention for daily aggregates (5 years)
-- These should never be deleted by retention policies

-- Create a separate daily aggregate with extended retention
CREATE MATERIALIZED VIEW IF NOT EXISTS server_health_daily_extended_cagg
WITH (timescaledb.continuous) AS
SELECT
    device_id,
    source,
    time_bucket('1 day', timestamp) AS bucket_day,
    AVG(cpu_usage) AS avg_cpu_usage,
    MAX(cpu_usage) AS max_cpu_usage,
    MIN(cpu_usage) AS min_cpu_usage,
    AVG(memory_usage) AS avg_memory_usage,
    MAX(memory_usage) AS max_memory_usage,
    MIN(memory_usage) AS min_memory_usage,
    AVG(disk_usage) AS avg_disk_usage,
    MAX(disk_usage) AS max_disk_usage,
    MIN(disk_usage) AS min_disk_usage,
    COUNT(*) AS sample_count
FROM server_health_logs
GROUP BY device_id, source, bucket_day;

-- Refresh daily at 3 AM
SELECT add_continuous_aggregate_policy('server_health_daily_extended_cagg',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- Add 5-year retention for daily aggregates
SELECT add_retention_policy('server_health_daily_extended_cagg', INTERVAL '5 years');

\echo 'Extended daily aggregates created'

\echo '=== Phase 6: Database Size Monitoring View ==='

-- Create view for easy database size monitoring
CREATE OR REPLACE VIEW v_database_size_monitor AS
SELECT
    'monitoring_db' AS database_name,
    pg_size_pretty(pg_database_size('monitoring_db')) AS total_size,
    pg_database_size('monitoring_db') AS total_bytes,
    CASE 
        WHEN pg_database_size('monitoring_db') > 107374182400 THEN 'CRITICAL: >100GB'
        WHEN pg_database_size('monitoring_db') > 53687091200 THEN 'WARNING: >50GB'
        ELSE 'OK'
    END AS status;

\echo 'Database size monitoring view created'

\echo '=== Phase 7: Job Health Monitoring View ==='

-- Create view for monitoring TimescaleDB background jobs
CREATE OR REPLACE VIEW v_timescaledb_job_health AS
SELECT
    job_id,
    application_name,
    schedule_interval,
    next_start,
    CASE 
        WHEN next_start < NOW() - schedule_interval * 2 THEN 'FAILED'
        WHEN next_start < NOW() THEN 'DELAYED'
        ELSE 'OK'
    END AS health_status
FROM timescaledb_information.jobs
WHERE job_id >= 1000
ORDER BY next_start;

\echo 'Job health monitoring view created'

\echo '=== Phase 8: Hypertable Statistics View ==='

-- Create view for hypertable statistics
CREATE OR REPLACE VIEW v_hypertable_stats AS
SELECT
    h.hypertable_schema,
    h.hypertable_name,
    h.num_chunks,
    COUNT(DISTINCT c.chunk_name) FILTER (WHERE c.is_compressed) AS compressed_chunks,
    COUNT(DISTINCT c.chunk_name) FILTER (WHERE NOT c.is_compressed) AS uncompressed_chunks,
    pg_size_pretty(SUM(c.total_bytes)) AS total_size,
    pg_size_pretty(SUM(c.compressed_total_bytes)) AS compressed_size,
    CASE 
        WHEN SUM(c.total_bytes) > 0 THEN
            ROUND(100 - (SUM(c.compressed_total_bytes)::float / SUM(c.total_bytes) * 100), 2)
        ELSE 0
    END AS compression_ratio_pct
FROM timescaledb_information.hypertables h
LEFT JOIN timescaledb_information.chunks c ON h.hypertable_name = c.hypertable_name
GROUP BY h.hypertable_schema, h.hypertable_name, h.num_chunks
ORDER BY h.hypertable_name;

\echo 'Hypertable statistics view created'

\echo '=== Phase 9: Query Performance Indexes ==='

-- Additional indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_server_health_source_time 
ON server_health_logs (source, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_server_health_device_source_time 
ON server_health_logs (device_id, source, timestamp DESC);

-- Index for alert queries
CREATE INDEX IF NOT EXISTS idx_server_health_cpu_high 
ON server_health_logs (device_id, timestamp DESC) 
WHERE cpu_usage > 80;

CREATE INDEX IF NOT EXISTS idx_server_health_memory_high 
ON server_health_logs (device_id, timestamp DESC) 
WHERE memory_usage > 80;

\echo 'Performance indexes created'

\echo '=== Phase 10: Verification ==='

-- Show all hypertables with chunk info
\echo 'Hypertables:'
SELECT 
    hypertable_schema,
    hypertable_name,
    num_chunks
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;

-- Show all indexes on hypertables
\echo 'Indexes on server_health_logs:'
SELECT 
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'server_health_logs'
ORDER BY indexname;

-- Show compression policies
\echo 'Compression Policies:'
SELECT 
    job_id,
    application_name,
    schedule_interval
FROM timescaledb_information.jobs
WHERE application_name LIKE '%Columnstore%'
ORDER BY job_id;

-- Show retention policies
\echo 'Retention Policies:'
SELECT 
    job_id,
    application_name,
    schedule_interval
FROM timescaledb_information.jobs
WHERE application_name LIKE '%Retention%'
ORDER BY job_id;

-- Show continuous aggregates
\echo 'Continuous Aggregates:'
SELECT 
    view_schema,
    view_name
FROM timescaledb_information.continuous_aggregates
ORDER BY view_name;

-- Show database size
\echo 'Database Size:'
SELECT * FROM v_database_size_monitor;

-- Show job health
\echo 'Job Health:'
SELECT * FROM v_timescaledb_job_health;

-- Show hypertable stats
\echo 'Hypertable Statistics:'
SELECT * FROM v_hypertable_stats;

\echo '=== Optimization Complete ==='
\echo 'All production-grade optimizations applied!'
