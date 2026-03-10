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
