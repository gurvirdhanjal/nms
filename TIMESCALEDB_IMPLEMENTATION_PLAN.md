# TimescaleDB Implementation Plan - Windows Environment

## Current Status

- **Database**: SQLite (default)
- **PostgreSQL**: Not installed
- **Environment**: Windows (win32)
- **Application**: Flask with SQLAlchemy ORM

## Implementation Phases

### Phase 1: PostgreSQL Installation (30 minutes)

#### Step 1.1: Download PostgreSQL

1. Download PostgreSQL 14 or 15 for Windows:
   - URL: https://www.postgresql.org/download/windows/
   - Or use EnterpriseDB installer: https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
   - Recommended: PostgreSQL 14.x (stable, well-tested with TimescaleDB)

2. Run installer:
   - Install location: `C:\Program Files\PostgreSQL\14`
   - Port: `5432` (default)
   - Locale: Default
   - **IMPORTANT**: Remember the superuser (postgres) password!

3. Add PostgreSQL to PATH:
   ```powershell
   # Add to system PATH
   $env:Path += ";C:\Program Files\PostgreSQL\14\bin"
   # Make permanent
   [Environment]::SetEnvironmentVariable("Path", $env:Path, [System.EnvironmentVariableTarget]::Machine)
   ```

4. Verify installation:
   ```powershell
   psql --version
   # Should output: psql (PostgreSQL) 14.x
   ```

#### Step 1.2: Create Database

```powershell
# Connect as postgres superuser
psql -U postgres

# In psql prompt:
CREATE DATABASE device_monitoring;
CREATE USER monitoring_user WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE device_monitoring TO monitoring_user;
\q
```

#### Step 1.3: Update Application Configuration

Update `.env` file:

```bash
# Database Configuration
DATABASE_URL=postgresql://monitoring_user:your_secure_password_here@localhost:5432/device_monitoring

# Enforce PostgreSQL (optional, for safety)
REQUIRE_POSTGRES=true

# Database Pool Settings (for PostgreSQL)
DB_POOL_SIZE=20
DB_POOL_MAX_OVERFLOW=20
DB_POOL_TIMEOUT_SECONDS=30
DB_POOL_RECYCLE_SECONDS=1800
```

#### Step 1.4: Migrate Data from SQLite to PostgreSQL

```powershell
# Install pgloader (data migration tool)
# Download from: https://github.com/dimitri/pgloader/releases

# Or use Python script (safer for your schema)
python scripts/migrate_sqlite_to_postgres.py
```

---

### Phase 2: TimescaleDB Installation (15 minutes)

#### Step 2.1: Download TimescaleDB

1. Download TimescaleDB for Windows:
   - URL: https://docs.timescale.com/install/latest/self-hosted/installation-windows/
   - Choose version matching your PostgreSQL (14.x)

2. Run TimescaleDB installer:
   - It will detect your PostgreSQL installation
   - Follow prompts to install extension

#### Step 2.2: Enable TimescaleDB Extension

```powershell
# Connect to database
psql -U monitoring_user -d device_monitoring

# Enable extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

# Verify
SELECT default_version, installed_version 
FROM pg_available_extensions 
WHERE name = 'timescaledb';

# Should show version 2.x
```

---

### Phase 3: Database Migration (20 minutes)

#### Step 3.1: Backup Current Database

```powershell
# Backup SQLite (if migrating)
Copy-Item "instance\device_monitoring.db" "instance\device_monitoring_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss').db"

# Backup PostgreSQL (after migration)
pg_dump -U monitoring_user -d device_monitoring -F c -f "backup_$(Get-Date -Format 'yyyyMMdd').dump"
```

#### Step 3.2: Run TimescaleDB Migration

```powershell
# Run migration script
psql -U monitoring_user -d device_monitoring -f scripts/migrate_to_timescaledb.sql
```

This script will:
- Convert tables to hypertables
- Add compression policies
- Add retention policies
- Create continuous aggregates
- Set up automatic refresh schedules

#### Step 3.3: Verify Migration

```powershell
psql -U monitoring_user -d device_monitoring

# Check hypertables
SELECT hypertable_name, num_chunks, pg_size_pretty(total_bytes) AS size
FROM timescaledb_information.hypertables;

# Check compression policies
SELECT * FROM timescaledb_information.jobs 
WHERE application_name LIKE '%Compression%';

# Check continuous aggregates
SELECT view_name, refresh_interval 
FROM timescaledb_information.continuous_aggregates;
```

---

### Phase 4: Application Code Updates (2 hours)

#### Step 4.1: Update Maintenance Service

Replace manual rollup logic with TimescaleDB monitoring:

```python
# services/maintenance_service.py

from services.timescaledb_service import TimescaleDBService

class MaintenanceService:
    def __init__(self):
        # Simplified retention (TimescaleDB handles automatically)
        self.server_health_raw_retention_days = 30  # Increased from 7
        self.use_timescaledb = TimescaleDBService.is_timescaledb_enabled()
    
    def run_server_health_retention(self) -> Dict:
        """Run retention - delegates to TimescaleDB if available"""
        if self.use_timescaledb:
            # TimescaleDB handles retention automatically via policies
            return TimescaleDBService.get_health_report()
        else:
            # Fallback to manual cleanup (SQLite)
            return self._manual_cleanup()
    
    # REMOVE: rollup_server_health_hourly() - replaced by continuous aggregates
    # REMOVE: rollup_server_health_daily() - replaced by continuous aggregates
```

#### Step 4.2: Update Query Routes

Optimize queries to use continuous aggregates:

```python
# routes/server_metrics.py

from services.timescaledb_service import TimescaleDBService

@server_metrics_bp.route('/api/server/health', methods=['GET'])
def get_server_health():
    hours = request.args.get('hours', 24, type=int)
    device_id = request.args.get('device_id', type=int)
    
    # Use TimescaleDB optimized queries if available
    if TimescaleDBService.is_timescaledb_enabled():
        if hours > 24:
            # Use continuous aggregate for longer ranges
            data = TimescaleDBService.query_continuous_aggregate(
                'server_health_hourly_cagg',
                device_id=device_id,
                start_time=datetime.utcnow() - timedelta(hours=hours)
            )
        else:
            # Use time_bucket for sub-hourly resolution
            data = TimescaleDBService.query_time_bucket(
                'server_health_logs',
                'timestamp',
                '5 minutes',
                device_id=device_id,
                start_time=datetime.utcnow() - timedelta(hours=hours)
            )
        return jsonify(data)
    else:
        # Fallback to standard query (SQLite)
        return _standard_query(device_id, hours)
```

#### Step 4.3: Add Health Monitoring Endpoint

```python
# routes/monitoring.py

from services.timescaledb_service import TimescaleDBService

@monitoring_bp.route('/api/timescaledb/health', methods=['GET'])
@login_required
def timescaledb_health():
    """Get TimescaleDB health metrics"""
    if not TimescaleDBService.is_timescaledb_enabled():
        return jsonify({'enabled': False, 'message': 'TimescaleDB not installed'}), 200
    
    health = TimescaleDBService.get_health_report()
    return jsonify(health)
```

---

### Phase 5: Testing & Validation (1 hour)

#### Step 5.1: Functional Testing

```powershell
# Start application
python web_main.py

# Test endpoints
curl http://localhost:5000/api/server/health
curl http://localhost:5000/api/timescaledb/health
```

#### Step 5.2: Performance Benchmarking

```python
# scripts/benchmark_queries.py

import time
from datetime import datetime, timedelta
from models.server_health import ServerHealthLog
from services.timescaledb_service import TimescaleDBService

def benchmark_query(device_id, hours):
    # Test standard query
    start = time.time()
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    logs = ServerHealthLog.query.filter(
        ServerHealthLog.device_id == device_id,
        ServerHealthLog.timestamp >= cutoff
    ).all()
    standard_time = time.time() - start
    
    # Test TimescaleDB query
    start = time.time()
    data = TimescaleDBService.query_time_bucket(
        'server_health_logs',
        'timestamp',
        '5 minutes',
        device_id=device_id,
        start_time=cutoff
    )
    timescale_time = time.time() - start
    
    print(f"Standard query: {standard_time:.3f}s")
    print(f"TimescaleDB query: {timescale_time:.3f}s")
    print(f"Speedup: {standard_time / timescale_time:.1f}x")

# Run benchmarks
benchmark_query(device_id=1, hours=24)
benchmark_query(device_id=1, hours=168)  # 7 days
```

#### Step 5.3: Compression Verification

Wait 7 days, then check compression:

```sql
-- Check compression ratio
SELECT
    hypertable_name,
    pg_size_pretty(before_compression_total_bytes) AS before,
    pg_size_pretty(after_compression_total_bytes) AS after,
    ROUND(100 - (after_compression_total_bytes::float / before_compression_total_bytes * 100), 2) AS saved_pct
FROM timescaledb_information.compression_settings
WHERE before_compression_total_bytes > 0;
```

---

### Phase 6: Production Deployment (30 minutes)

#### Step 6.1: Update Scheduler

Remove manual rollup jobs from Windows Task Scheduler or cron:

```powershell
# Remove old rollup tasks (if using Task Scheduler)
# TimescaleDB handles this automatically now
```

#### Step 6.2: Monitor Background Jobs

```sql
-- Check job status daily
SELECT 
    job_id,
    application_name,
    last_run_status,
    last_successful_finish,
    next_start,
    total_failures
FROM timescaledb_information.jobs
WHERE last_run_status != 'Success' OR total_failures > 0;
```

#### Step 6.3: Set Up Alerting

Add monitoring for TimescaleDB jobs:

```python
# services/monitoring_service.py

def check_timescaledb_health():
    """Check TimescaleDB background jobs for failures"""
    jobs = TimescaleDBService.get_job_stats()
    
    failed_jobs = [j for j in jobs if j.get('total_failures', 0) > 0]
    
    if failed_jobs:
        # Send alert
        send_alert(
            title="TimescaleDB Job Failures",
            message=f"{len(failed_jobs)} background jobs have failures",
            severity="warning"
        )
```

---

## Rollback Plan

If issues occur, you can rollback:

### Option 1: Revert to SQLite

```bash
# Update .env
DATABASE_URL=sqlite:///instance/device_monitoring.db

# Restart application
python web_main.py
```

### Option 2: Revert to PostgreSQL without TimescaleDB

```sql
-- Disable TimescaleDB (keeps data)
DROP EXTENSION timescaledb CASCADE;

-- Application will fall back to standard PostgreSQL queries
```

### Option 3: Restore from Backup

```powershell
# Restore PostgreSQL backup
pg_restore -U monitoring_user -d device_monitoring -c backup_YYYYMMDD.dump
```

---

## Timeline Summary

| Phase | Duration | Can Run in Parallel |
|-------|----------|---------------------|
| PostgreSQL Installation | 30 min | No |
| TimescaleDB Installation | 15 min | No |
| Database Migration | 20 min | No |
| Code Updates | 2 hours | Yes (staging) |
| Testing | 1 hour | Yes (staging) |
| Production Deployment | 30 min | No |
| **Total** | **4.5 hours** | |

**Recommended Schedule:**
- Week 1: Install PostgreSQL + TimescaleDB on staging
- Week 2: Migrate data and test on staging
- Week 3: Update code and performance test
- Week 4: Production deployment (low-traffic window)

---

## Success Criteria

- [ ] PostgreSQL installed and accessible
- [ ] TimescaleDB extension enabled
- [ ] All tables converted to hypertables
- [ ] Compression policies active
- [ ] Continuous aggregates refreshing
- [ ] Queries 10-100x faster
- [ ] Storage reduced by 70-90% (after 7 days)
- [ ] No application errors
- [ ] Background jobs running successfully

---

## Support & Troubleshooting

### Common Issues

**Issue**: "Extension timescaledb not found"
**Solution**: Reinstall TimescaleDB, ensure it matches PostgreSQL version

**Issue**: "Permission denied for hypertable"
**Solution**: Grant privileges: `GRANT ALL ON ALL TABLES IN SCHEMA public TO monitoring_user;`

**Issue**: "Compression not working"
**Solution**: Check job status: `SELECT * FROM timescaledb_information.jobs;`
Manually trigger: `SELECT run_job(job_id) FROM timescaledb_information.jobs WHERE application_name LIKE '%Compression%';`

### Resources

- TimescaleDB Docs: https://docs.timescale.com/
- PostgreSQL Windows Guide: https://www.postgresql.org/docs/current/install-windows.html
- Community Slack: https://timescaledb.slack.com/

---

## Next Steps

1. **Install PostgreSQL** (see Phase 1)
2. **Create migration script** for SQLite → PostgreSQL
3. **Install TimescaleDB** (see Phase 2)
4. **Run migration** (see Phase 3)
5. **Update code** (see Phase 4)
6. **Test thoroughly** (see Phase 5)
7. **Deploy to production** (see Phase 6)

Ready to proceed? Start with Phase 1: PostgreSQL Installation.
