# TimescaleDB Migration Checklist

## ✅ Completed Tasks

### Migration
- [x] Docker installed and running
- [x] TimescaleDB container created (port 5433)
- [x] PostgreSQL 18 database backed up (16.89 MB)
- [x] Data migrated to TimescaleDB
- [x] All data verified and preserved

### Hypertables
- [x] server_health_logs converted (5 chunks)
- [x] tracking_samples converted (9 chunks)
- [x] device_resource_logs converted (8 chunks)
- [x] device_activity_logs converted (8 chunks)
- [x] device_application_logs converted (8 chunks)

### Compression
- [x] Compression enabled on all hypertables
- [x] Segment-by columns configured (device_id)
- [x] Compression policies created (7-30 days)
- [x] 5 Columnstore policies active

### Retention
- [x] Retention policies created
- [x] Raw data: 30-60 days
- [x] Hourly aggregates: 1 year
- [x] Daily aggregates: 5 years
- [x] 6 Retention policies active

### Continuous Aggregates
- [x] server_health_hourly_cagg created
- [x] server_health_daily_cagg created
- [x] server_health_daily_extended_cagg created (5-year retention)
- [x] tracking_hourly_cagg created
- [x] 4 Refresh policies active

### Indexes
- [x] Device + Time indexes (5)
- [x] Source + Time indexes (2)
- [x] Alert-specific indexes (2)
- [x] Composite indexes (3)
- [x] 20+ total indexes created

### Optimization
- [x] PostgreSQL tuned for monitoring workload
- [x] shared_buffers increased to 2GB
- [x] work_mem increased to 64MB
- [x] WAL compression enabled
- [x] SSD optimizations applied
- [x] Chunk intervals optimized

### Monitoring
- [x] Database size monitoring view created
- [x] Job health monitoring view created
- [x] Hypertable statistics view created
- [x] All 15 background jobs healthy

### Query Guardrails
- [x] Query validation module created
- [x] Time range limits enforced
- [x] Device list limits enforced
- [x] Row limits enforced
- [x] Query type recommendations implemented

### Configuration
- [x] .env file updated (port 5433)
- [x] docker-compose.yml optimized
- [x] Verification script updated

### Documentation
- [x] Migration complete document
- [x] Production optimizations document
- [x] Final status document
- [x] Quick start guide
- [x] This checklist

---

## ⏳ Pending Tasks

### Immediate (Today)
- [ ] Start application: `python web_main.py`
- [ ] Open browser: http://localhost:5000
- [ ] Verify dashboards load
- [ ] Check metrics are displaying
- [ ] Test device details page
- [ ] Verify no errors in console

### This Week
- [ ] Add query guardrails to API endpoints
- [ ] Update dashboard queries to use aggregates
- [ ] Test query performance
- [ ] Monitor job health daily
- [ ] Set up automated backups

### After 7 Days
- [ ] Check compression ratio
- [ ] Verify storage savings
- [ ] Confirm compression is 85-95%
- [ ] Review background job logs
- [ ] Benchmark query performance

### After 30 Days
- [ ] Stop PostgreSQL 18 service
- [ ] Remove PostgreSQL 18 backup (if confident)
- [ ] Document lessons learned
- [ ] Train team on TimescaleDB

---

## 🧪 Testing Checklist

### Database Tests
- [x] Database connection works
- [ ] Application connects successfully
- [ ] Queries execute without errors
- [ ] Data is being inserted
- [ ] Aggregates are refreshing

### Performance Tests
- [ ] Dashboard loads in < 2 seconds
- [ ] 24h metrics query < 0.5 seconds
- [ ] 7d metrics query < 1 second
- [ ] Device list loads quickly
- [ ] No timeout errors

### Functionality Tests
- [ ] Server dashboard displays metrics
- [ ] Device details page works
- [ ] Historical data is accessible
- [ ] Alerts are working
- [ ] Reports can be generated

### Background Job Tests
- [ ] Compression jobs running
- [ ] Retention jobs running
- [ ] Refresh jobs running
- [ ] No failed jobs
- [ ] Jobs completing on schedule

---

## 📊 Monitoring Checklist

### Daily Checks
```powershell
# Check job health
docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "SELECT * FROM v_timescaledb_job_health WHERE health_status != 'OK';"

# Check database size
docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "SELECT * FROM v_database_size_monitor;"

# Check container status
docker ps | Select-String "monitoring_timescaledb"
```

### Weekly Checks
```powershell
# Check hypertable stats
docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "SELECT * FROM v_hypertable_stats;"

# Check for slow queries
docker exec -i monitoring_timescaledb psql -U monitoring_man -d monitoring_db -c "SELECT query, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"

# Verify backups exist
Get-ChildItem ./backups/ | Sort-Object LastWriteTime -Descending | Select-Object -First 5
```

### Monthly Checks
```sql
-- Check compression ratio
SELECT 
    hypertable_name,
    COUNT(*) FILTER (WHERE is_compressed) AS compressed_chunks,
    COUNT(*) FILTER (WHERE NOT is_compressed) AS uncompressed_chunks,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_compressed) / COUNT(*), 2) AS compression_pct
FROM timescaledb_information.chunks
GROUP BY hypertable_name;

-- Check retention is working
SELECT 
    hypertable_name,
    MIN(range_start) AS oldest_data,
    MAX(range_end) AS newest_data,
    AGE(NOW(), MIN(range_start)) AS data_age
FROM timescaledb_information.chunks
GROUP BY hypertable_name;

-- Check continuous aggregate freshness
SELECT 
    view_name,
    last_successful_finish,
    AGE(NOW(), last_successful_finish) AS time_since_refresh
FROM timescaledb_information.continuous_aggregate_stats;
```

---

## 🚨 Alert Thresholds

### Critical Alerts
- [ ] Database size > 100 GB
- [ ] Any job failed for > 24 hours
- [ ] Container stopped
- [ ] Disk space < 10%
- [ ] Query time > 10 seconds

### Warning Alerts
- [ ] Database size > 50 GB
- [ ] Any job delayed > 2 hours
- [ ] Compression ratio < 70%
- [ ] Query time > 5 seconds
- [ ] Disk space < 20%

### Info Alerts
- [ ] Database size > 20 GB
- [ ] Compression completed
- [ ] Retention policy executed
- [ ] Backup completed

---

## 🔧 Troubleshooting Checklist

### Container Won't Start
- [ ] Check Docker is running
- [ ] Check port 5433 is available
- [ ] Check Docker logs: `docker logs monitoring_timescaledb`
- [ ] Try recreating container

### Application Can't Connect
- [ ] Verify .env has correct DATABASE_URL
- [ ] Check container is running: `docker ps`
- [ ] Test connection: `docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db`
- [ ] Check firewall/network

### Slow Queries
- [ ] Check if using aggregates (not raw data)
- [ ] Verify indexes exist
- [ ] Check query plan: `EXPLAIN ANALYZE`
- [ ] Review time range (too large?)
- [ ] Check if compression is working

### Compression Not Working
- [ ] Check policies exist: `SELECT * FROM timescaledb_information.jobs WHERE application_name LIKE '%Columnstore%';`
- [ ] Check data age (needs to be > 7 days)
- [ ] Manually trigger: `SELECT run_job(job_id) FROM timescaledb_information.jobs WHERE application_name LIKE '%Columnstore%' LIMIT 1;`
- [ ] Check logs for errors

### High Memory Usage
- [ ] Check active connections: `SELECT count(*) FROM pg_stat_activity;`
- [ ] Review work_mem setting
- [ ] Check for long-running queries
- [ ] Consider reducing shared_buffers

---

## 📝 Application Integration Checklist

### API Endpoints
- [ ] Add `@enforce_query_limits` decorator
- [ ] Use continuous aggregates for > 24h queries
- [ ] Use time_bucket for < 24h queries
- [ ] Return query metadata (time range, bucket size)
- [ ] Handle validation errors gracefully

### Dashboard Queries
- [ ] Replace raw queries with aggregate queries
- [ ] Use optimal bucket intervals
- [ ] Add loading indicators
- [ ] Cache results when appropriate
- [ ] Show query performance metrics

### Background Jobs
- [ ] Remove manual rollup jobs (if any)
- [ ] Update data retention logic
- [ ] Add TimescaleDB job monitoring
- [ ] Alert on job failures

---

## 🎯 Success Criteria

### Week 1
- [ ] Application running without errors
- [ ] All dashboards working
- [ ] No performance degradation
- [ ] Background jobs healthy
- [ ] Team trained on basics

### Month 1
- [ ] Compression working (85-95%)
- [ ] Storage reduced by 80-90%
- [ ] Queries 10-100x faster
- [ ] No failed jobs
- [ ] Team comfortable with TimescaleDB

### Month 3
- [ ] PostgreSQL 18 decommissioned
- [ ] Automated monitoring in place
- [ ] Performance benchmarks documented
- [ ] Runbook created
- [ ] Best practices documented

---

## 📚 Knowledge Transfer Checklist

### Team Training
- [ ] Overview of TimescaleDB architecture
- [ ] How to query continuous aggregates
- [ ] How to use query guardrails
- [ ] How to monitor background jobs
- [ ] How to troubleshoot common issues

### Documentation
- [ ] Architecture diagram created
- [ ] Query patterns documented
- [ ] Runbook for common tasks
- [ ] Troubleshooting guide
- [ ] Performance tuning guide

### Handoff
- [ ] Admin access documented
- [ ] Backup procedures documented
- [ ] Monitoring setup documented
- [ ] Escalation procedures defined
- [ ] On-call runbook created

---

## 🔄 Rollback Checklist

### If Rollback Needed
- [ ] Stop application
- [ ] Update .env to port 5432
- [ ] Verify PostgreSQL 18 is running
- [ ] Restore from backup if needed
- [ ] Start application
- [ ] Verify functionality
- [ ] Document issues encountered

### Rollback Commands
```powershell
# Update .env
(Get-Content .env) -replace 'DATABASE_URL=postgresql\+psycopg2://monitoring_man:admin123@127.0.0.1:5433/monitoring_db', 'DATABASE_URL=postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5432/monitoring_db' | Set-Content .env

# Restart application
python web_main.py
```

---

## ✅ Sign-Off

### Technical Lead
- [ ] Migration reviewed
- [ ] Optimizations verified
- [ ] Documentation approved
- [ ] Team trained
- [ ] Production ready

### Operations
- [ ] Monitoring configured
- [ ] Backups automated
- [ ] Alerts configured
- [ ] Runbook reviewed
- [ ] On-call trained

### Development
- [ ] Code updated
- [ ] Tests passing
- [ ] Performance verified
- [ ] Documentation updated
- [ ] Ready for production

---

**Checklist created**: 2026-03-09
**Migration status**: ✅ Complete
**Next review**: 2026-03-16 (7 days)
