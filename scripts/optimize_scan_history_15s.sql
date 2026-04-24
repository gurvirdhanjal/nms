-- optimize_scan_history_15s.sql
-- Tunes device_scan_history for 15-second monitoring intervals.
-- Fully idempotent — safe to re-run.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Convert to hypertable (skips if already done)
-- ─────────────────────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS idx_device_scan_history_ts;
DROP INDEX IF EXISTS idx_device_scan_history_ip_time;
DROP INDEX IF EXISTS idx_device_scan_history_status_time;

-- TimescaleDB requires the partition column (scan_timestamp) to be part of any unique index/primary key.
-- Drop the plain scan_id primary key and recreate it as a composite key to satisfy this requirement.
ALTER TABLE device_scan_history DROP CONSTRAINT IF EXISTS device_scan_history_pkey;
ALTER TABLE device_scan_history ADD PRIMARY KEY (scan_id, scan_timestamp);

SELECT create_hypertable(
    'device_scan_history',
    'scan_timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Chunk interval: 1 day is correct for ~1.15M rows/day
-- ─────────────────────────────────────────────────────────────────────────────
SELECT set_chunk_time_interval('device_scan_history', INTERVAL '1 day');

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Compression — segment by device_ip, order by time DESC
--    Compress after 1 day (not 7): keeps only today uncompressed (~1.15M rows)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE device_scan_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_ip',
    timescaledb.compress_orderby   = 'scan_timestamp DESC'
);

SELECT remove_compression_policy('device_scan_history', if_exists => TRUE);
SELECT add_compression_policy('device_scan_history', INTERVAL '1 day');

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Retention — 30 days of raw scans
-- ─────────────────────────────────────────────────────────────────────────────
SELECT remove_retention_policy('device_scan_history', if_exists => TRUE);
SELECT add_retention_policy('device_scan_history', INTERVAL '30 days');

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Query indexes
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_dsh_ip_time
    ON device_scan_history (device_ip, scan_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_dsh_status_time
    ON device_scan_history (status, scan_timestamp DESC);

-- Covering index for the device detail "Recent Scan History" panel
CREATE INDEX IF NOT EXISTS idx_dsh_ip_time_covering
    ON device_scan_history (device_ip, scan_timestamp DESC)
    INCLUDE (status, ping_time_ms, packet_loss);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. 15-minute continuous aggregate
--    Collapses 900 raw rows per device per 15-min window into 1 row.
--    materialized_only=false: current partial bucket always included.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS device_scan_history_15m_cagg
WITH (
    timescaledb.continuous,
    timescaledb.materialized_only = false
) AS
SELECT
    device_ip,
    time_bucket('15 minutes', scan_timestamp)  AS bucket,
    COUNT(*)                                    AS probe_count,
    COUNT(*) FILTER (WHERE status = 'Online')   AS online_count,
    AVG(ping_time_ms)                           AS avg_rtt,
    MIN(ping_time_ms)                           AS min_rtt,
    MAX(ping_time_ms)                           AS max_rtt,
    AVG(packet_loss)                            AS avg_packet_loss,
    AVG(jitter)                                 AS avg_jitter
FROM device_scan_history
WHERE scan_type IS NULL OR scan_type != 'agent_push'
GROUP BY device_ip, bucket;

-- Refresh every 5 minutes
SELECT add_continuous_aggregate_policy(
    'device_scan_history_15m_cagg',
    start_offset      => INTERVAL '3 hours',
    end_offset        => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists     => TRUE
);

-- Compress the cagg's own materialization hypertable after 7 days
SELECT add_compression_policy(
    'device_scan_history_15m_cagg',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Verification
-- ─────────────────────────────────────────────────────────────────────────────
SELECT hypertable_name, num_chunks, compression_enabled
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'device_scan_history';

SELECT j.application_name, j.hypertable_name, j.schedule_interval,
       js.last_run_status, js.next_start
FROM timescaledb_information.jobs j
LEFT JOIN timescaledb_information.job_stats js ON js.job_id = j.job_id
WHERE j.hypertable_name IN ('device_scan_history', 'device_scan_history_15m_cagg')
ORDER BY j.application_name;

SELECT view_name, materialization_hypertable_name
FROM timescaledb_information.continuous_aggregates
WHERE view_name = 'device_scan_history_15m_cagg';

SELECT
    '15s / 200 devices'                    AS scenario,
    200 * (86400 / 15)                     AS rows_per_day,
    200 * (86400 / 15) * 30                AS rows_30_days,
    pg_size_pretty((200::bigint * (86400 / 15) * 30 * 120)) AS est_uncompressed,
    pg_size_pretty((200::bigint * (86400 / 15) * 30 * 8))   AS est_compressed;
