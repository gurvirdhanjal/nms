"""
Database Maintenance Service for Network Monitoring System.
Handles data retention, cleanup, and daily aggregation rollups.
"""
from datetime import datetime, timedelta, date
from typing import Dict
from sqlalchemy import and_, func, text
from extensions import db


class MaintenanceService:
    """
    Service for database maintenance tasks:
    - Cleanup old scan history
    - Aggregate daily statistics
    - Cleanup old interface metrics
    - Cleanup old events
    """
    
    def __init__(self):
        # Retention periods (days)
        self.scan_history_retention_days = 7
        self.interface_metrics_retention_days = 3
        self.events_retention_days = 30
        self.daily_stats_retention_days = 365
        self.server_health_raw_retention_days = 7
        self.server_health_hourly_retention_days = 30
        self.server_health_daily_retention_days = 365

    @staticmethod
    def _backend_name() -> str:
        return db.engine.url.get_backend_name()

    def _postgres_required_result(self, task_name: str) -> Dict:
        backend = self._backend_name()
        return {
            'success': True,
            'skipped': True,
            'task': task_name,
            'reason': f"PostgreSQL required, current backend is '{backend}'",
            'backend': backend
        }

    def _get_or_create_rollup_state(self, name: str, default_rolled_until: datetime):
        from models.server_health_rollups import ServerHealthRollupState

        state = ServerHealthRollupState.query.filter_by(name=name).first()
        if not state:
            state = ServerHealthRollupState(name=name, rolled_until=default_rolled_until)
            db.session.add(state)
            db.session.flush()
        return state

    def rollup_server_health_hourly(self, raw_days: int = None) -> Dict:
        """
        Roll up raw server_health_logs into hourly aggregates.
        Uses a closed window [start, end) and checkpoint cursor.
        """
        raw_days = raw_days or self.server_health_raw_retention_days
        if self._backend_name() != 'postgresql':
            return self._postgres_required_result('rollup_server_health_hourly')

        now_utc = datetime.utcnow()
        window_end = now_utc - timedelta(days=raw_days)
        from models.server_health import ServerHealthLog

        oldest_raw = db.session.query(func.min(ServerHealthLog.timestamp)).scalar()
        if oldest_raw and oldest_raw < window_end:
            default_start = oldest_raw
        else:
            default_start = window_end

        try:
            state = self._get_or_create_rollup_state('raw_to_hourly', default_start)
            window_start = state.rolled_until

            if window_start >= window_end:
                state.updated_at = now_utc
                db.session.commit()
                return {
                    'success': True,
                    'rolled_buckets': 0,
                    'window_start': window_start.isoformat(),
                    'window_end': window_end.isoformat(),
                    'skipped': True,
                    'reason': 'No new raw window to roll up'
                }

            count_stmt = text("""
                SELECT COUNT(*) FROM (
                    SELECT
                        shl.device_id,
                        COALESCE(shl.source, 'agent') AS source,
                        date_trunc('hour', shl.timestamp) AS bucket_hour
                    FROM server_health_logs shl
                    WHERE shl.timestamp >= :window_start
                      AND shl.timestamp < :window_end
                    GROUP BY
                        shl.device_id,
                        COALESCE(shl.source, 'agent'),
                        date_trunc('hour', shl.timestamp)
                ) buckets
            """)
            rolled_buckets = db.session.execute(
                count_stmt,
                {'window_start': window_start, 'window_end': window_end}
            ).scalar() or 0

            if rolled_buckets > 0:
                upsert_stmt = text("""
                    INSERT INTO server_health_hourly_rollups (
                        device_id,
                        source,
                        bucket_hour,
                        avg_cpu_usage,
                        max_cpu_usage,
                        avg_memory_usage,
                        max_memory_usage,
                        avg_disk_usage,
                        avg_network_in_bps,
                        avg_network_out_bps,
                        sample_count,
                        online_samples,
                        avg_ping_latency_ms,
                        max_ping_latency_ms,
                        avg_packet_loss_pct,
                        max_packet_loss_pct,
                        created_at,
                        updated_at
                    )
                    SELECT
                        shl.device_id,
                        COALESCE(shl.source, 'agent') AS source,
                        date_trunc('hour', shl.timestamp) AS bucket_hour,
                        AVG(shl.cpu_usage) AS avg_cpu_usage,
                        MAX(shl.cpu_usage) AS max_cpu_usage,
                        AVG(shl.memory_usage) AS avg_memory_usage,
                        MAX(shl.memory_usage) AS max_memory_usage,
                        AVG(shl.disk_usage) AS avg_disk_usage,
                        AVG(shl.network_in_bps) AS avg_network_in_bps,
                        AVG(shl.network_out_bps) AS avg_network_out_bps,
                        COUNT(*)::INTEGER AS sample_count,
                        COUNT(CASE WHEN shl.cpu_usage IS NOT NULL THEN 1 END)::INTEGER AS online_samples,
                        AVG(shl.ping_latency_ms) AS avg_ping_latency_ms,
                        MAX(shl.ping_latency_ms) AS max_ping_latency_ms,
                        AVG(shl.packet_loss_pct) AS avg_packet_loss_pct,
                        MAX(shl.packet_loss_pct) AS max_packet_loss_pct,
                        NOW() AS created_at,
                        NOW() AS updated_at
                    FROM server_health_logs shl
                    WHERE shl.timestamp >= :window_start
                      AND shl.timestamp < :window_end
                    GROUP BY
                        shl.device_id,
                        COALESCE(shl.source, 'agent'),
                        date_trunc('hour', shl.timestamp)
                    ON CONFLICT (device_id, source, bucket_hour)
                    DO UPDATE SET
                        avg_cpu_usage = EXCLUDED.avg_cpu_usage,
                        max_cpu_usage = EXCLUDED.max_cpu_usage,
                        avg_memory_usage = EXCLUDED.avg_memory_usage,
                        max_memory_usage = EXCLUDED.max_memory_usage,
                        avg_disk_usage = EXCLUDED.avg_disk_usage,
                        avg_network_in_bps = EXCLUDED.avg_network_in_bps,
                        avg_network_out_bps = EXCLUDED.avg_network_out_bps,
                        sample_count = EXCLUDED.sample_count,
                        online_samples = EXCLUDED.online_samples,
                        avg_ping_latency_ms = EXCLUDED.avg_ping_latency_ms,
                        max_ping_latency_ms = EXCLUDED.max_ping_latency_ms,
                        avg_packet_loss_pct = EXCLUDED.avg_packet_loss_pct,
                        max_packet_loss_pct = EXCLUDED.max_packet_loss_pct,
                        updated_at = NOW()
                """)
                db.session.execute(
                    upsert_stmt,
                    {'window_start': window_start, 'window_end': window_end}
                )

            state.rolled_until = window_end
            state.updated_at = now_utc
            db.session.commit()

            return {
                'success': True,
                'rolled_buckets': int(rolled_buckets),
                'window_start': window_start.isoformat(),
                'window_end': window_end.isoformat()
            }
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def rollup_server_health_daily(self, hourly_days: int = None) -> Dict:
        """
        Roll up hourly server health aggregates into daily aggregates.
        Uses a closed window [start, end) and checkpoint cursor.
        """
        hourly_days = hourly_days or self.server_health_hourly_retention_days
        if self._backend_name() != 'postgresql':
            return self._postgres_required_result('rollup_server_health_daily')

        now_utc = datetime.utcnow()
        window_end = now_utc - timedelta(days=hourly_days)
        from models.server_health_rollups import ServerHealthHourlyRollup

        oldest_hourly = db.session.query(func.min(ServerHealthHourlyRollup.bucket_hour)).scalar()
        if oldest_hourly and oldest_hourly < window_end:
            default_start = oldest_hourly
        else:
            default_start = window_end

        try:
            state = self._get_or_create_rollup_state('hourly_to_daily', default_start)
            window_start = state.rolled_until

            if window_start >= window_end:
                state.updated_at = now_utc
                db.session.commit()
                return {
                    'success': True,
                    'rolled_buckets': 0,
                    'window_start': window_start.isoformat(),
                    'window_end': window_end.isoformat(),
                    'skipped': True,
                    'reason': 'No new hourly window to roll up'
                }

            count_stmt = text("""
                SELECT COUNT(*) FROM (
                    SELECT
                        h.device_id,
                        h.source,
                        date_trunc('day', h.bucket_hour)::date AS bucket_day
                    FROM server_health_hourly_rollups h
                    WHERE h.bucket_hour >= :window_start
                      AND h.bucket_hour < :window_end
                    GROUP BY h.device_id, h.source, date_trunc('day', h.bucket_hour)::date
                ) buckets
            """)
            rolled_buckets = db.session.execute(
                count_stmt,
                {'window_start': window_start, 'window_end': window_end}
            ).scalar() or 0

            if rolled_buckets > 0:
                upsert_stmt = text("""
                    INSERT INTO server_health_daily_rollups (
                        device_id,
                        source,
                        bucket_day,
                        avg_cpu_usage,
                        max_cpu_usage,
                        avg_memory_usage,
                        max_memory_usage,
                        avg_disk_usage,
                        avg_network_in_bps,
                        avg_network_out_bps,
                        sample_count,
                        online_samples,
                        avg_ping_latency_ms,
                        max_ping_latency_ms,
                        avg_packet_loss_pct,
                        max_packet_loss_pct,
                        created_at,
                        updated_at
                    )
                    SELECT
                        h.device_id,
                        h.source,
                        date_trunc('day', h.bucket_hour)::date AS bucket_day,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_cpu_usage IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_cpu_usage * h.sample_count)
                                / SUM(CASE WHEN h.avg_cpu_usage IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_cpu_usage,
                        MAX(h.max_cpu_usage) AS max_cpu_usage,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_memory_usage IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_memory_usage * h.sample_count)
                                / SUM(CASE WHEN h.avg_memory_usage IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_memory_usage,
                        MAX(h.max_memory_usage) AS max_memory_usage,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_disk_usage IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_disk_usage * h.sample_count)
                                / SUM(CASE WHEN h.avg_disk_usage IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_disk_usage,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_network_in_bps IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_network_in_bps * h.sample_count)
                                / SUM(CASE WHEN h.avg_network_in_bps IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_network_in_bps,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_network_out_bps IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_network_out_bps * h.sample_count)
                                / SUM(CASE WHEN h.avg_network_out_bps IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_network_out_bps,
                        SUM(h.sample_count)::INTEGER AS sample_count,
                        COALESCE(SUM(h.online_samples), 0)::INTEGER AS online_samples,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_ping_latency_ms IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_ping_latency_ms * h.sample_count)
                                / SUM(CASE WHEN h.avg_ping_latency_ms IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_ping_latency_ms,
                        MAX(h.max_ping_latency_ms) AS max_ping_latency_ms,
                        CASE
                            WHEN SUM(CASE WHEN h.avg_packet_loss_pct IS NOT NULL THEN h.sample_count ELSE 0 END) > 0
                            THEN
                                SUM(h.avg_packet_loss_pct * h.sample_count)
                                / SUM(CASE WHEN h.avg_packet_loss_pct IS NOT NULL THEN h.sample_count ELSE 0 END)
                            ELSE NULL
                        END AS avg_packet_loss_pct,
                        MAX(h.max_packet_loss_pct) AS max_packet_loss_pct,
                        NOW() AS created_at,
                        NOW() AS updated_at
                    FROM server_health_hourly_rollups h
                    WHERE h.bucket_hour >= :window_start
                      AND h.bucket_hour < :window_end
                    GROUP BY h.device_id, h.source, date_trunc('day', h.bucket_hour)::date
                    ON CONFLICT (device_id, source, bucket_day)
                    DO UPDATE SET
                        avg_cpu_usage = EXCLUDED.avg_cpu_usage,
                        max_cpu_usage = EXCLUDED.max_cpu_usage,
                        avg_memory_usage = EXCLUDED.avg_memory_usage,
                        max_memory_usage = EXCLUDED.max_memory_usage,
                        avg_disk_usage = EXCLUDED.avg_disk_usage,
                        avg_network_in_bps = EXCLUDED.avg_network_in_bps,
                        avg_network_out_bps = EXCLUDED.avg_network_out_bps,
                        sample_count = EXCLUDED.sample_count,
                        online_samples = EXCLUDED.online_samples,
                        avg_ping_latency_ms = EXCLUDED.avg_ping_latency_ms,
                        max_ping_latency_ms = EXCLUDED.max_ping_latency_ms,
                        avg_packet_loss_pct = EXCLUDED.avg_packet_loss_pct,
                        max_packet_loss_pct = EXCLUDED.max_packet_loss_pct,
                        updated_at = NOW()
                """)
                db.session.execute(
                    upsert_stmt,
                    {'window_start': window_start, 'window_end': window_end}
                )

            state.rolled_until = window_end
            state.updated_at = now_utc
            db.session.commit()

            return {
                'success': True,
                'rolled_buckets': int(rolled_buckets),
                'window_start': window_start.isoformat(),
                'window_end': window_end.isoformat()
            }
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def cleanup_old_server_health_logs(self, days: int = None) -> Dict:
        """Delete raw server health logs older than retention window."""
        days = days or self.server_health_raw_retention_days
        cutoff = datetime.utcnow() - timedelta(days=days)

        try:
            from models.server_health import ServerHealthLog

            count = ServerHealthLog.query.filter(ServerHealthLog.timestamp < cutoff).count()
            if count > 0:
                ServerHealthLog.query.filter(ServerHealthLog.timestamp < cutoff).delete()
                db.session.commit()

            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def cleanup_old_server_health_hourly_rollups(self, days: int = None) -> Dict:
        """Delete hourly server health rollups older than retention window."""
        days = days or self.server_health_hourly_retention_days
        cutoff = datetime.utcnow() - timedelta(days=days)

        try:
            from models.server_health_rollups import ServerHealthHourlyRollup

            count = ServerHealthHourlyRollup.query.filter(
                ServerHealthHourlyRollup.bucket_hour < cutoff
            ).count()
            if count > 0:
                ServerHealthHourlyRollup.query.filter(
                    ServerHealthHourlyRollup.bucket_hour < cutoff
                ).delete()
                db.session.commit()

            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def cleanup_old_server_health_daily_rollups(self, days: int = None) -> Dict:
        """Delete daily server health rollups older than retention window."""
        days = days or self.server_health_daily_retention_days
        cutoff = (datetime.utcnow() - timedelta(days=days)).date()

        try:
            from models.server_health_rollups import ServerHealthDailyRollup

            count = ServerHealthDailyRollup.query.filter(
                ServerHealthDailyRollup.bucket_day < cutoff
            ).count()
            if count > 0:
                ServerHealthDailyRollup.query.filter(
                    ServerHealthDailyRollup.bucket_day < cutoff
                ).delete()
                db.session.commit()

            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def run_server_health_retention(self, raw_days: int = None, hourly_days: int = None, daily_days: int = None) -> Dict:
        """
        Run server health rollups and retention in safe order:
        rollup -> rollup -> cleanup -> cleanup -> cleanup.
        """
        raw_days = raw_days or self.server_health_raw_retention_days
        hourly_days = hourly_days or self.server_health_hourly_retention_days
        daily_days = daily_days or self.server_health_daily_retention_days

        if self._backend_name() != 'postgresql':
            return {
                'success': True,
                'skipped': True,
                'tasks': {
                    'hourly_rollup': self._postgres_required_result('rollup_server_health_hourly'),
                    'daily_rollup': self._postgres_required_result('rollup_server_health_daily'),
                    'raw_cleanup': self._postgres_required_result('cleanup_old_server_health_logs'),
                    'hourly_cleanup': self._postgres_required_result('cleanup_old_server_health_hourly_rollups'),
                    'daily_cleanup': self._postgres_required_result('cleanup_old_server_health_daily_rollups'),
                }
            }

        tasks = {}
        tasks['hourly_rollup'] = self.rollup_server_health_hourly(raw_days=raw_days)
        if not tasks['hourly_rollup'].get('success', False):
            return {'success': False, 'tasks': tasks}

        tasks['daily_rollup'] = self.rollup_server_health_daily(hourly_days=hourly_days)
        if not tasks['daily_rollup'].get('success', False):
            return {'success': False, 'tasks': tasks}

        tasks['raw_cleanup'] = self.cleanup_old_server_health_logs(raw_days)
        tasks['hourly_cleanup'] = self.cleanup_old_server_health_hourly_rollups(hourly_days)
        tasks['daily_cleanup'] = self.cleanup_old_server_health_daily_rollups(daily_days)

        success = all(task.get('success', False) for task in tasks.values())
        return {'success': success, 'tasks': tasks}
    
    def cleanup_old_scan_history(self, days: int = None) -> Dict:
        """
        Delete scan history older than specified days.
        
        Args:
            days: Retention period (default: 7 days)
            
        Returns:
            Dict with deletion count and status
        """
        days = days or self.scan_history_retention_days
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        try:
            from models.scan_history import DeviceScanHistory
            
            # Count before delete
            count = DeviceScanHistory.query.filter(
                DeviceScanHistory.scan_timestamp < cutoff
            ).count()
            
            if count > 0:
                DeviceScanHistory.query.filter(
                    DeviceScanHistory.scan_timestamp < cutoff
                ).delete()
                db.session.commit()
            
            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
            
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'error': str(e)
            }
    
    def cleanup_old_interface_metrics(self, days: int = None) -> Dict:
        """Delete interface metrics older than specified days."""
        days = days or self.interface_metrics_retention_days
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        try:
            from models.interfaces import InterfaceTrafficHistory
            
            count = InterfaceTrafficHistory.query.filter(
                InterfaceTrafficHistory.timestamp < cutoff
            ).count()
            
            if count > 0:
                InterfaceTrafficHistory.query.filter(
                    InterfaceTrafficHistory.timestamp < cutoff
                ).delete()
                db.session.commit()
            
            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
            
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def cleanup_old_events(self, days: int = None) -> Dict:
        """Delete resolved events older than specified days."""
        days = days or self.events_retention_days
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        try:
            from models.dashboard import DashboardEvent
            
            count = DashboardEvent.query.filter(
                and_(
                    DashboardEvent.timestamp < cutoff,
                    DashboardEvent.resolved == True
                )
            ).count()
            
            if count > 0:
                DashboardEvent.query.filter(
                    and_(
                        DashboardEvent.timestamp < cutoff,
                        DashboardEvent.resolved == True
                    )
                ).delete()
                db.session.commit()
            
            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
            
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def aggregate_daily_stats(self, target_date: date = None) -> Dict:
        """
        Aggregate scan history into daily statistics.
        Should be run once per day for the previous day.
        
        Args:
            target_date: Date to aggregate (default: yesterday)
            
        Returns:
            Dict with aggregation results
        """
        target_date = target_date or (datetime.utcnow().date() - timedelta(days=1))
        
        try:
            from models.device import Device
            from models.scan_history import DeviceScanHistory
            from models.dashboard import DailyDeviceStats, DashboardEvent
            
            # Get all devices for aggregation (User requested full visibility)
            devices = Device.query.all()
            
            start_dt = datetime.combine(target_date, datetime.min.time())
            end_dt = datetime.combine(target_date, datetime.max.time())
            
            aggregated = 0
            
            for device in devices:
                # Check if stats already exist for this date
                existing = DailyDeviceStats.query.filter_by(
                    device_id=device.device_id,
                    date=target_date
                ).first()
                
                if existing:
                    continue  # Skip if already aggregated
                
                # Get scans for this device on target date
                scans = DeviceScanHistory.query.filter(
                    and_(
                        DeviceScanHistory.device_ip == device.device_ip,
                        DeviceScanHistory.scan_timestamp >= start_dt,
                        DeviceScanHistory.scan_timestamp <= end_dt
                    )
                ).all()
                
                if not scans:
                    continue
                
                # Calculate aggregates
                total_scans = len(scans)
                online_scans = len([s for s in scans if s.status == 'Online'])
                uptime_percent = (online_scans / total_scans) * 100 if total_scans > 0 else 0
                
                latencies = [s.ping_time_ms for s in scans if s.ping_time_ms is not None]
                packet_losses = [s.packet_loss for s in scans if s.packet_loss is not None]
                
                avg_latency = sum(latencies) / len(latencies) if latencies else None
                max_latency = max(latencies) if latencies else None
                min_latency = min(latencies) if latencies else None
                avg_packet_loss = sum(packet_losses) / len(packet_losses) if packet_losses else 0
                
                # Count alerts for this device
                alert_count = DashboardEvent.query.filter(
                    and_(
                        DashboardEvent.device_id == device.device_id,
                        DashboardEvent.timestamp >= start_dt,
                        DashboardEvent.timestamp <= end_dt
                    )
                ).count()
                
                # Create daily stats record
                daily_stat = DailyDeviceStats(
                    device_id=device.device_id,
                    date=target_date,
                    uptime_percent=round(uptime_percent, 2),
                    avg_latency_ms=round(avg_latency, 2) if avg_latency else None,
                    max_latency_ms=round(max_latency, 2) if max_latency else None,
                    min_latency_ms=round(min_latency, 2) if min_latency else None,
                    avg_packet_loss_pct=round(avg_packet_loss, 2),
                    total_scans=total_scans,
                    online_scans=online_scans,
                    total_alerts=alert_count
                )
                
                db.session.add(daily_stat)
                aggregated += 1
            
            db.session.commit()
            
            return {
                'success': True,
                'target_date': target_date.isoformat(),
                'devices_aggregated': aggregated,
                'total_devices': len(devices)
            }
            
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    def run_all_maintenance(self) -> Dict:
        """
        Run all maintenance tasks.
        Suitable for a daily cron job.
        """
        results = {
            'timestamp': datetime.utcnow().isoformat(),
            'tasks': {}
        }
        
        # 1. Aggregate yesterday's stats (before cleanup)
        results['tasks']['aggregate_daily'] = self.aggregate_daily_stats()
        
        # 2. Server health rollups + retention (rollup first, cleanup second)
        retention_result = self.run_server_health_retention()
        for task_name, task_result in retention_result.get('tasks', {}).items():
            results['tasks'][f'server_health_{task_name}'] = task_result

        # 3. Cleanup old scan history
        results['tasks']['cleanup_scans'] = self.cleanup_old_scan_history()
        
        # 4. Cleanup old interface metrics
        results['tasks']['cleanup_metrics'] = self.cleanup_old_interface_metrics()
        
        # 5. Cleanup old resolved events
        results['tasks']['cleanup_events'] = self.cleanup_old_events()
        
        # Summary
        all_success = all(
            task.get('success', False) 
            for task in results['tasks'].values()
        )
        results['overall_success'] = all_success
        
        return results


# Singleton instance
maintenance_service = MaintenanceService()
