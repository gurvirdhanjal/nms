# TimescaleDB Quick Start Guide

## Installation (5 minutes)

### Ubuntu/Debian
```bash
# Add TimescaleDB repository
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -

# Install
sudo apt update
sudo apt install timescaledb-2-postgresql-14

# Tune PostgreSQL for TimescaleDB
sudo timescaledb-tune --quiet --yes

# Restart PostgreSQL
sudo systemctl restart postgresql
```

### Windows
```powershell
# Download installer from: https://docs.timescale.com/install/latest/self-hosted/installation-windows/
# Run installer and follow prompts
# Restart PostgreSQL service
```

## Enable Extension (1 minute)

```bash
# Connect to database
psql -U your_user -d device_monitoring

# Enable extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

# Verify
SELECT default_version, installed_version 
FROM pg_available_extensions 
WHERE name = 'timescaledb';
```

## Run Migration (10 minutes)

```bash
# Backup first!
pg_dump device_monitoring > backup_$(date +%Y%m%d).sql

# Run migration script
psql -U your_user -d device_monitoring -f scripts/migrate_to_timescaledb.sql
```

## Verify Installation

```sql
-- Check hypertables
SELECT hypertable_name, num_chunks 
FROM timescaledb_information.hypertables;

-- Check compression policies
SELECT * FROM timescaledb_information.jobs 
WHERE application_name LIKE 'Compression%';

-- Check continuous aggregates
SELECT view_name FROM timescaledb_information.continuous_aggregates;
```

## Update Application Code

### Before (Slow)
```python
# routes/server_metrics.py
def get_metrics(device_id, hours=24):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    logs = ServerHealthLog.query.filter(
        ServerHealthLog.device_id == device_id,
        ServerHealthLog.timestamp >= cutoff
    ).all()  # Returns 10,000+ rows
```

### After (Fast)
```python
# routes/server_metrics.py
from services.timescaledb_service import TimescaleDBService

def get_metrics(device_id, hours=24):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    # Use continuous aggregate for hourly data
    if hours > 24:
        return TimescaleDBService.query_continuous_aggregate(
            'server_health_hourly_cagg',
            device_id=device_id,
            start_time=cutoff
        )
    else:
        # Use time_bucket for sub-hourly resolution
        return TimescaleDBService.query_time_bucket(
            'server_health_logs',
            'timestamp',
            '5 minutes',
            device_id=device_id,
            start_time=cutoff
        )
```

## Monitoring Commands

```sql
-- Compression ratio
SELECT
    hypertable_name,
    pg_size_pretty(before_compression_total_bytes) AS before,
    pg_size_pretty(after_compression_total_bytes) AS after,
    ROUND(100 - (after_compression_total_bytes::float / before_compression_total_bytes * 100), 2) AS saved_pct
FROM timescaledb_information.compression_settings;

-- Job status
SELECT 
    job_id,
    application_name,
    last_run_status,
    next_start
FROM timescaledb_information.jobs;

-- Chunk status
SELECT
    chunk_name,
    range_start,
    range_end,
    is_compressed,
    pg_size_pretty(total_bytes) AS size
FROM timescaledb_information.chunks
WHERE hypertable_name = 'server_health_logs'
ORDER BY range_start DESC
LIMIT 10;
```

## Common Operations

### Manual Compression
```sql
-- Compress specific chunk
SELECT compress_chunk('_timescaledb_internal._hyper_1_1_chunk');

-- Compress all uncompressed chunks older than 7 days
SELECT compress_chunk(chunk_schema || '.' || chunk_name)
FROM timescaledb_information.chunks
WHERE hypertable_name = 'server_health_logs'
  AND NOT is_compressed
  AND range_end < NOW() - INTERVAL '7 days';
```

### Manual Refresh
```sql
-- Refresh continuous aggregate
CALL refresh_continuous_aggregate('server_health_hourly_cagg', NULL, NULL);

-- Refresh specific time range
CALL refresh_continuous_aggregate(
    'server_health_hourly_cagg',
    NOW() - INTERVAL '24 hours',
    NOW()
);
```

### Drop Old Data
```sql
-- Drop chunks older than 90 days
SELECT drop_chunks('server_health_logs', INTERVAL '90 days');
```

## Troubleshooting

### Compression Not Working
```sql
-- Check compression policy exists
SELECT * FROM timescaledb_information.jobs 
WHERE application_name LIKE 'Compression%';

-- Manually trigger compression job
SELECT run_job(job_id) 
FROM timescaledb_information.jobs 
WHERE application_name LIKE 'Compression Policy%';
```

### Continuous Aggregate Not Refreshing
```sql
-- Check refresh policy
SELECT * FROM timescaledb_information.continuous_aggregate_stats;

-- Manually refresh
CALL refresh_continuous_aggregate('server_health_hourly_cagg', NULL, NULL);
```

### High Storage Usage
```sql
-- Check uncompressed chunks
SELECT
    chunk_name,
    pg_size_pretty(total_bytes) AS size,
    is_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name = 'server_health_logs'
  AND NOT is_compressed
ORDER BY total_bytes DESC;

-- Compress them
SELECT compress_chunk(chunk_schema || '.' || chunk_name)
FROM timescaledb_information.chunks
WHERE hypertable_name = 'server_health_logs'
  AND NOT is_compressed;
```

## Performance Tips

1. **Use continuous aggregates for queries > 24 hours**
2. **Use time_bucket for sub-hourly resolution**
3. **Always filter by device_id first (segmentby column)**
4. **Use time range filters (enables chunk exclusion)**
5. **Avoid SELECT * on large time ranges**

## Next Steps

1. Monitor compression ratio after 7 days
2. Update dashboard queries to use continuous aggregates
3. Remove manual rollup jobs from scheduler
4. Set up alerting for failed TimescaleDB jobs
5. Benchmark query performance (should be 10-100x faster)
