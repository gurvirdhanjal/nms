"""
Database Maintenance Service for Network Monitoring System.
Handles data retention, cleanup, and aggregation rollups.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Dict
from sqlalchemy import and_, func, text
from extensions import db
from services.timescaledb_service import TimescaleDBService


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
        self.tracking_raw_retention_days = 30
        self.tracking_hourly_retention_days = 365
        self.tracking_daily_retention_days = 1095

    @staticmethod
    def _floor_to_hour(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(minute=0, second=0, microsecond=0)

    @staticmethod
    def _floor_to_day(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return datetime.combine(value.date(), time.min)

    @staticmethod
    def _iter_dates(start_date: date, end_date: date):
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)

    @staticmethod
    def _min_datetime(*values: datetime | None) -> datetime | None:
        present = [value for value in values if value is not None]
        return min(present) if present else None

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

    def _timescaledb_enabled(self) -> bool:
        if self._backend_name() != 'postgresql':
            return False
        return bool(TimescaleDBService.is_timescaledb_enabled())

    def _timescaledb_managed_result(self, task_name: str, detail: str) -> Dict:
        return {
            'success': True,
            'skipped': True,
            'policy_managed': True,
            'task': task_name,
            'backend': self._backend_name(),
            'reason': 'Managed by TimescaleDB',
            'detail': detail,
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
        if self._backend_name() != 'postgresql':
            return self._postgres_required_result('rollup_server_health_hourly')
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'rollup_server_health_hourly',
                'Server health reports use TimescaleDB continuous aggregates instead of legacy hourly rollup tables.',
            )

        now_utc = datetime.utcnow()
        window_end = self._floor_to_hour(now_utc)
        from models.server_health import ServerHealthLog

        oldest_raw = db.session.query(func.min(ServerHealthLog.timestamp)).scalar()
        default_start = self._floor_to_hour(oldest_raw) if oldest_raw and oldest_raw < window_end else window_end

        try:
            state = self._get_or_create_rollup_state('raw_to_hourly', default_start)
            window_start = self._floor_to_hour(state.rolled_until) or default_start

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
        if self._backend_name() != 'postgresql':
            return self._postgres_required_result('rollup_server_health_daily')
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'rollup_server_health_daily',
                'Server health reports use TimescaleDB continuous aggregates instead of legacy daily rollup tables.',
            )

        now_utc = datetime.utcnow()
        window_end = self._floor_to_day(now_utc)
        from models.server_health_rollups import ServerHealthHourlyRollup

        oldest_hourly = db.session.query(func.min(ServerHealthHourlyRollup.bucket_hour)).scalar()
        default_start = self._floor_to_day(oldest_hourly) if oldest_hourly and oldest_hourly < window_end else window_end

        try:
            state = self._get_or_create_rollup_state('hourly_to_daily', default_start)
            window_start = self._floor_to_day(state.rolled_until) or default_start

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
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'cleanup_old_server_health_logs',
                f"Retention is enforced by TimescaleDB policies for the server_health_logs hypertable (requested cutoff {cutoff.isoformat()}).",
            )

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
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'cleanup_old_server_health_hourly_rollups',
                f"Legacy server_health_hourly_rollups cleanup is disabled because TimescaleDB summaries are used instead (requested cutoff {cutoff.isoformat()}).",
            )

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
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'cleanup_old_server_health_daily_rollups',
                f"Legacy server_health_daily_rollups cleanup is disabled because TimescaleDB summaries are used instead (requested cutoff {cutoff.isoformat()}).",
            )

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
        if self._timescaledb_enabled():
            tasks = {
                'hourly_rollup': self._timescaledb_managed_result(
                    'rollup_server_health_hourly',
                    'Server health rollups are computed from TimescaleDB continuous aggregates.',
                ),
                'daily_rollup': self._timescaledb_managed_result(
                    'rollup_server_health_daily',
                    'Server health rollups are computed from TimescaleDB continuous aggregates.',
                ),
                'raw_cleanup': self._timescaledb_managed_result(
                    'cleanup_old_server_health_logs',
                    'Server health raw retention is enforced by TimescaleDB retention policies.',
                ),
                'hourly_cleanup': self._timescaledb_managed_result(
                    'cleanup_old_server_health_hourly_rollups',
                    'Legacy hourly rollup tables are not maintained when TimescaleDB is enabled.',
                ),
                'daily_cleanup': self._timescaledb_managed_result(
                    'cleanup_old_server_health_daily_rollups',
                    'Legacy daily rollup tables are not maintained when TimescaleDB is enabled.',
                ),
            }
            return {'success': True, 'skipped': True, 'policy_managed': True, 'tasks': tasks}

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

    def validate_and_repair_server_health_rollups(self, lookback_days: int = 45) -> Dict:
        """
        Validate rollup completeness and backfill missing hourly/daily buckets.
        Runs only on PostgreSQL and uses idempotent INSERT ... ON CONFLICT DO NOTHING.
        """
        if self._backend_name() != 'postgresql':
            return self._postgres_required_result('validate_and_repair_server_health_rollups')
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'validate_and_repair_server_health_rollups',
                'Legacy server health rollup repair is disabled because TimescaleDB-backed reporting is active.',
            )

        lookback_days = max(1, int(lookback_days or 45))
        now_utc = datetime.utcnow()

        hourly_start = self._floor_to_hour(now_utc - timedelta(days=lookback_days))
        hourly_end = self._floor_to_hour(now_utc)
        daily_start = self._floor_to_day(now_utc - timedelta(days=lookback_days))
        daily_end = self._floor_to_day(now_utc)

        result = {
            'success': True,
            'hourly': {'missing': 0, 'repaired': 0, 'window_start': hourly_start.isoformat(), 'window_end': hourly_end.isoformat()},
            'daily': {'missing': 0, 'repaired': 0, 'window_start': daily_start.isoformat(), 'window_end': daily_end.isoformat()},
            'lookback_days': lookback_days,
        }

        try:
            # Repair missing hourly rollups from raw logs
            if hourly_start < hourly_end:
                missing_hourly_stmt = text("""
                    SELECT COUNT(*) FROM (
                        SELECT
                            shl.device_id,
                            COALESCE(shl.source, 'agent') AS source,
                            date_trunc('hour', shl.timestamp) AS bucket_hour
                        FROM server_health_logs shl
                        LEFT JOIN server_health_hourly_rollups h
                          ON h.device_id = shl.device_id
                         AND h.source = COALESCE(shl.source, 'agent')
                         AND h.bucket_hour = date_trunc('hour', shl.timestamp)
                        WHERE shl.timestamp >= :window_start
                          AND shl.timestamp < :window_end
                          AND h.id IS NULL
                        GROUP BY
                            shl.device_id,
                            COALESCE(shl.source, 'agent'),
                            date_trunc('hour', shl.timestamp)
                    ) missing
                """)
                missing_hourly = db.session.execute(
                    missing_hourly_stmt,
                    {'window_start': hourly_start, 'window_end': hourly_end}
                ).scalar() or 0
                result['hourly']['missing'] = int(missing_hourly)

                if missing_hourly > 0:
                    repair_hourly_stmt = text("""
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
                        LEFT JOIN server_health_hourly_rollups h
                          ON h.device_id = shl.device_id
                         AND h.source = COALESCE(shl.source, 'agent')
                         AND h.bucket_hour = date_trunc('hour', shl.timestamp)
                        WHERE shl.timestamp >= :window_start
                          AND shl.timestamp < :window_end
                          AND h.id IS NULL
                        GROUP BY
                            shl.device_id,
                            COALESCE(shl.source, 'agent'),
                            date_trunc('hour', shl.timestamp)
                        ON CONFLICT (device_id, source, bucket_hour) DO NOTHING
                    """)
                    db.session.execute(
                        repair_hourly_stmt,
                        {'window_start': hourly_start, 'window_end': hourly_end}
                    )
                    result['hourly']['repaired'] = int(missing_hourly)

            # Repair missing daily rollups from hourly rollups
            if daily_start < daily_end:
                missing_daily_stmt = text("""
                    SELECT COUNT(*) FROM (
                        SELECT
                            h.device_id,
                            h.source,
                            date_trunc('day', h.bucket_hour)::date AS bucket_day
                        FROM server_health_hourly_rollups h
                        LEFT JOIN server_health_daily_rollups d
                          ON d.device_id = h.device_id
                         AND d.source = h.source
                         AND d.bucket_day = date_trunc('day', h.bucket_hour)::date
                        WHERE h.bucket_hour >= :window_start
                          AND h.bucket_hour < :window_end
                          AND d.id IS NULL
                        GROUP BY
                            h.device_id,
                            h.source,
                            date_trunc('day', h.bucket_hour)::date
                    ) missing
                """)
                missing_daily = db.session.execute(
                    missing_daily_stmt,
                    {'window_start': daily_start, 'window_end': daily_end}
                ).scalar() or 0
                result['daily']['missing'] = int(missing_daily)

                if missing_daily > 0:
                    repair_daily_stmt = text("""
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
                        LEFT JOIN server_health_daily_rollups d
                          ON d.device_id = h.device_id
                         AND d.source = h.source
                         AND d.bucket_day = date_trunc('day', h.bucket_hour)::date
                        WHERE h.bucket_hour >= :window_start
                          AND h.bucket_hour < :window_end
                          AND d.id IS NULL
                        GROUP BY
                            h.device_id,
                            h.source,
                            date_trunc('day', h.bucket_hour)::date
                        ON CONFLICT (device_id, source, bucket_day) DO NOTHING
                    """)
                    db.session.execute(
                        repair_daily_stmt,
                        {'window_start': daily_start, 'window_end': daily_end}
                    )
                    result['daily']['repaired'] = int(missing_daily)

            db.session.commit()
            return result
        except Exception as exc:
            db.session.rollback()
            return {'success': False, 'error': str(exc)}
    
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

    def _rollup_tracking_hourly_python(self, window_start: datetime, window_end: datetime) -> int:
        from models.tracked_device import (
            DeviceActivityLog,
            DeviceApplicationLog,
            DeviceResourceLog,
            TrackingHourlyRollup,
            TrackingSample,
        )

        bucket_metrics = defaultdict(
            lambda: {
                'sample_count': 0,
                'active_seconds': 0,
                'keyboard_events': 0,
                'mouse_events': 0,
                'cpu_sum': 0.0,
                'cpu_count': 0,
                'memory_sum': 0.0,
                'memory_count': 0,
            }
        )

        for row in TrackingSample.query.filter(
            TrackingSample.received_at >= window_start,
            TrackingSample.received_at < window_end,
        ).all():
            bucket = self._floor_to_hour(row.received_at)
            if bucket is None:
                continue
            bucket_metrics[(row.device_id, bucket)]['sample_count'] += 1

        for row in DeviceResourceLog.query.filter(
            DeviceResourceLog.timestamp >= window_start,
            DeviceResourceLog.timestamp < window_end,
        ).all():
            bucket = self._floor_to_hour(row.timestamp)
            if bucket is None:
                continue
            entry = bucket_metrics[(row.device_id, bucket)]
            if row.cpu_usage is not None:
                entry['cpu_sum'] += float(row.cpu_usage)
                entry['cpu_count'] += 1
            if row.memory_usage is not None:
                entry['memory_sum'] += float(row.memory_usage)
                entry['memory_count'] += 1

        for row in DeviceActivityLog.query.filter(
            DeviceActivityLog.timestamp >= window_start,
            DeviceActivityLog.timestamp < window_end,
        ).all():
            bucket = self._floor_to_hour(row.timestamp)
            if bucket is None:
                continue
            entry = bucket_metrics[(row.device_id, bucket)]
            activity_type = str(row.activity_type or '').lower()
            if activity_type == 'keyboard':
                entry['keyboard_events'] += int(row.event_count or 0)
            elif activity_type == 'mouse':
                entry['mouse_events'] += int(row.event_count or 0)

        for row in DeviceApplicationLog.query.filter(
            DeviceApplicationLog.timestamp >= window_start,
            DeviceApplicationLog.timestamp < window_end,
        ).all():
            bucket = self._floor_to_hour(row.timestamp)
            if bucket is None:
                continue
            entry = bucket_metrics[(row.device_id, bucket)]
            entry['active_seconds'] += max(int(row.duration or 0), 0)

        rolled_buckets = 0
        for (device_id, bucket_hour), entry in bucket_metrics.items():
            rollup = TrackingHourlyRollup.query.filter_by(
                device_id=device_id,
                bucket_hour=bucket_hour,
            ).first()
            if not rollup:
                rollup = TrackingHourlyRollup(device_id=device_id, bucket_hour=bucket_hour)
                db.session.add(rollup)
            rollup.sample_count = int(entry['sample_count'])
            rollup.active_seconds = min(int(entry['active_seconds']), 3600)
            rollup.keyboard_events = int(entry['keyboard_events'])
            rollup.mouse_events = int(entry['mouse_events'])
            rollup.cpu_avg = (
                round(entry['cpu_sum'] / entry['cpu_count'], 3)
                if entry['cpu_count'] > 0
                else None
            )
            rollup.memory_avg = (
                round(entry['memory_sum'] / entry['memory_count'], 3)
                if entry['memory_count'] > 0
                else None
            )
            rolled_buckets += 1

        return rolled_buckets

    def rollup_tracking_hourly(self) -> Dict:
        """Roll up raw tracking samples/logs into hourly aggregates for closed hours."""
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'rollup_tracking_hourly',
                'Tracking/productivity reporting reads TimescaleDB hypertables directly; legacy hourly tracking rollups are disabled.',
            )
        now_utc = datetime.utcnow()
        window_end = self._floor_to_hour(now_utc)

        from models.server_health_rollups import ServerHealthRollupState
        from models.tracked_device import (
            DeviceActivityLog,
            DeviceApplicationLog,
            DeviceResourceLog,
            TrackingSample,
        )

        oldest_raw = self._min_datetime(
            db.session.query(func.min(TrackingSample.received_at)).scalar(),
            db.session.query(func.min(DeviceResourceLog.timestamp)).scalar(),
            db.session.query(func.min(DeviceActivityLog.timestamp)).scalar(),
            db.session.query(func.min(DeviceApplicationLog.timestamp)).scalar(),
        )
        default_start = self._floor_to_hour(oldest_raw) if oldest_raw and oldest_raw < window_end else window_end

        try:
            state = self._get_or_create_rollup_state('tracking_raw_to_hourly', default_start)
            window_start = self._floor_to_hour(state.rolled_until) or default_start

            if window_start >= window_end:
                state.updated_at = now_utc
                db.session.commit()
                return {
                    'success': True,
                    'rolled_buckets': 0,
                    'window_start': window_start.isoformat(),
                    'window_end': window_end.isoformat(),
                    'skipped': True,
                    'reason': 'No closed tracking hour to roll up',
                }

            if self._backend_name() == 'postgresql':
                params = {'window_start': window_start, 'window_end': window_end}
                count_stmt = text("""
                    WITH bucket_keys AS (
                        SELECT device_id, date_trunc('hour', received_at) AS bucket_hour
                        FROM tracking_samples
                        WHERE received_at >= :window_start AND received_at < :window_end
                        UNION
                        SELECT device_id, date_trunc('hour', timestamp) AS bucket_hour
                        FROM device_resource_logs
                        WHERE timestamp >= :window_start AND timestamp < :window_end
                        UNION
                        SELECT device_id, date_trunc('hour', timestamp) AS bucket_hour
                        FROM device_activity_logs
                        WHERE timestamp >= :window_start AND timestamp < :window_end
                        UNION
                        SELECT device_id, date_trunc('hour', timestamp) AS bucket_hour
                        FROM device_application_logs
                        WHERE timestamp >= :window_start AND timestamp < :window_end
                    )
                    SELECT COUNT(*) FROM bucket_keys
                """)
                rolled_buckets = db.session.execute(count_stmt, params).scalar() or 0

                if rolled_buckets > 0:
                    upsert_stmt = text("""
                        WITH bucket_keys AS (
                            SELECT device_id, date_trunc('hour', received_at) AS bucket_hour
                            FROM tracking_samples
                            WHERE received_at >= :window_start AND received_at < :window_end
                            UNION
                            SELECT device_id, date_trunc('hour', timestamp) AS bucket_hour
                            FROM device_resource_logs
                            WHERE timestamp >= :window_start AND timestamp < :window_end
                            UNION
                            SELECT device_id, date_trunc('hour', timestamp) AS bucket_hour
                            FROM device_activity_logs
                            WHERE timestamp >= :window_start AND timestamp < :window_end
                            UNION
                            SELECT device_id, date_trunc('hour', timestamp) AS bucket_hour
                            FROM device_application_logs
                            WHERE timestamp >= :window_start AND timestamp < :window_end
                        ),
                        sample_stats AS (
                            SELECT
                                device_id,
                                date_trunc('hour', received_at) AS bucket_hour,
                                COUNT(*)::INTEGER AS sample_count
                            FROM tracking_samples
                            WHERE received_at >= :window_start AND received_at < :window_end
                            GROUP BY device_id, date_trunc('hour', received_at)
                        ),
                        resource_stats AS (
                            SELECT
                                device_id,
                                date_trunc('hour', timestamp) AS bucket_hour,
                                AVG(cpu_usage) AS cpu_avg,
                                AVG(memory_usage) AS memory_avg
                            FROM device_resource_logs
                            WHERE timestamp >= :window_start AND timestamp < :window_end
                            GROUP BY device_id, date_trunc('hour', timestamp)
                        ),
                        activity_stats AS (
                            SELECT
                                device_id,
                                date_trunc('hour', timestamp) AS bucket_hour,
                                SUM(CASE WHEN LOWER(activity_type) = 'keyboard' THEN event_count ELSE 0 END)::INTEGER AS keyboard_events,
                                SUM(CASE WHEN LOWER(activity_type) = 'mouse' THEN event_count ELSE 0 END)::INTEGER AS mouse_events
                            FROM device_activity_logs
                            WHERE timestamp >= :window_start AND timestamp < :window_end
                            GROUP BY device_id, date_trunc('hour', timestamp)
                        ),
                        app_stats AS (
                            SELECT
                                device_id,
                                date_trunc('hour', timestamp) AS bucket_hour,
                                LEAST(COALESCE(SUM(GREATEST(COALESCE(duration, 0), 0)), 0), 3600)::INTEGER AS active_seconds
                            FROM device_application_logs
                            WHERE timestamp >= :window_start AND timestamp < :window_end
                            GROUP BY device_id, date_trunc('hour', timestamp)
                        )
                        INSERT INTO tracking_hourly_rollups (
                            device_id,
                            bucket_hour,
                            sample_count,
                            active_seconds,
                            keyboard_events,
                            mouse_events,
                            cpu_avg,
                            memory_avg,
                            created_at,
                            updated_at
                        )
                        SELECT
                            k.device_id,
                            k.bucket_hour,
                            COALESCE(s.sample_count, 0)::INTEGER AS sample_count,
                            COALESCE(a.active_seconds, 0)::INTEGER AS active_seconds,
                            COALESCE(act.keyboard_events, 0)::INTEGER AS keyboard_events,
                            COALESCE(act.mouse_events, 0)::INTEGER AS mouse_events,
                            r.cpu_avg,
                            r.memory_avg,
                            NOW() AS created_at,
                            NOW() AS updated_at
                        FROM bucket_keys k
                        LEFT JOIN sample_stats s
                          ON s.device_id = k.device_id
                         AND s.bucket_hour = k.bucket_hour
                        LEFT JOIN resource_stats r
                          ON r.device_id = k.device_id
                         AND r.bucket_hour = k.bucket_hour
                        LEFT JOIN activity_stats act
                          ON act.device_id = k.device_id
                         AND act.bucket_hour = k.bucket_hour
                        LEFT JOIN app_stats a
                          ON a.device_id = k.device_id
                         AND a.bucket_hour = k.bucket_hour
                        ON CONFLICT (device_id, bucket_hour)
                        DO UPDATE SET
                            sample_count = EXCLUDED.sample_count,
                            active_seconds = EXCLUDED.active_seconds,
                            keyboard_events = EXCLUDED.keyboard_events,
                            mouse_events = EXCLUDED.mouse_events,
                            cpu_avg = EXCLUDED.cpu_avg,
                            memory_avg = EXCLUDED.memory_avg,
                            updated_at = NOW()
                    """)
                    db.session.execute(upsert_stmt, params)
            else:
                rolled_buckets = self._rollup_tracking_hourly_python(window_start, window_end)

            state.rolled_until = window_end
            state.updated_at = now_utc
            db.session.commit()
            return {
                'success': True,
                'rolled_buckets': int(rolled_buckets),
                'window_start': window_start.isoformat(),
                'window_end': window_end.isoformat(),
            }
        except Exception as exc:
            db.session.rollback()
            return {'success': False, 'error': str(exc)}

    def _rollup_tracking_daily_python(self, window_start: datetime, window_end: datetime) -> int:
        from models.tracked_device import TrackingDailyRollup, TrackingHourlyRollup

        daily_metrics = defaultdict(
            lambda: {
                'sample_count': 0,
                'active_seconds': 0,
                'keyboard_events': 0,
                'mouse_events': 0,
                'cpu_weighted_sum': 0.0,
                'cpu_weight': 0,
                'memory_weighted_sum': 0.0,
                'memory_weight': 0,
            }
        )

        for row in TrackingHourlyRollup.query.filter(
            TrackingHourlyRollup.bucket_hour >= window_start,
            TrackingHourlyRollup.bucket_hour < window_end,
        ).all():
            bucket_day = row.bucket_hour.date()
            entry = daily_metrics[(row.device_id, bucket_day)]
            sample_count = int(row.sample_count or 0)
            entry['sample_count'] += sample_count
            entry['active_seconds'] += int(row.active_seconds or 0)
            entry['keyboard_events'] += int(row.keyboard_events or 0)
            entry['mouse_events'] += int(row.mouse_events or 0)
            if row.cpu_avg is not None:
                weight = sample_count or 1
                entry['cpu_weighted_sum'] += float(row.cpu_avg) * weight
                entry['cpu_weight'] += weight
            if row.memory_avg is not None:
                weight = sample_count or 1
                entry['memory_weighted_sum'] += float(row.memory_avg) * weight
                entry['memory_weight'] += weight

        rolled_buckets = 0
        for (device_id, bucket_day), entry in daily_metrics.items():
            rollup = TrackingDailyRollup.query.filter_by(
                device_id=device_id,
                bucket_day=bucket_day,
            ).first()
            if not rollup:
                rollup = TrackingDailyRollup(device_id=device_id, bucket_day=bucket_day)
                db.session.add(rollup)
            rollup.sample_count = int(entry['sample_count'])
            rollup.active_seconds = min(int(entry['active_seconds']), 86400)
            rollup.keyboard_events = int(entry['keyboard_events'])
            rollup.mouse_events = int(entry['mouse_events'])
            rollup.cpu_avg = (
                round(entry['cpu_weighted_sum'] / entry['cpu_weight'], 3)
                if entry['cpu_weight'] > 0
                else None
            )
            rollup.memory_avg = (
                round(entry['memory_weighted_sum'] / entry['memory_weight'], 3)
                if entry['memory_weight'] > 0
                else None
            )
            rolled_buckets += 1

        return rolled_buckets

    def rollup_tracking_daily(self) -> Dict:
        """Roll up hourly tracking aggregates into daily aggregates for closed days."""
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'rollup_tracking_daily',
                'Tracking/productivity reporting reads TimescaleDB hypertables directly; legacy daily tracking rollups are disabled.',
            )
        now_utc = datetime.utcnow()
        window_end = self._floor_to_day(now_utc)

        from models.tracked_device import TrackingHourlyRollup

        oldest_hourly = db.session.query(func.min(TrackingHourlyRollup.bucket_hour)).scalar()
        default_start = self._floor_to_day(oldest_hourly) if oldest_hourly and oldest_hourly < window_end else window_end

        try:
            state = self._get_or_create_rollup_state('tracking_hourly_to_daily', default_start)
            window_start = self._floor_to_day(state.rolled_until) or default_start

            if window_start >= window_end:
                state.updated_at = now_utc
                db.session.commit()
                return {
                    'success': True,
                    'rolled_buckets': 0,
                    'window_start': window_start.isoformat(),
                    'window_end': window_end.isoformat(),
                    'skipped': True,
                    'reason': 'No closed tracking day to roll up',
                }

            if self._backend_name() == 'postgresql':
                params = {'window_start': window_start, 'window_end': window_end}
                count_stmt = text("""
                    SELECT COUNT(*) FROM (
                        SELECT
                            device_id,
                            date_trunc('day', bucket_hour)::date AS bucket_day
                        FROM tracking_hourly_rollups
                        WHERE bucket_hour >= :window_start AND bucket_hour < :window_end
                        GROUP BY device_id, date_trunc('day', bucket_hour)::date
                    ) buckets
                """)
                rolled_buckets = db.session.execute(count_stmt, params).scalar() or 0

                if rolled_buckets > 0:
                    upsert_stmt = text("""
                        INSERT INTO tracking_daily_rollups (
                            device_id,
                            bucket_day,
                            sample_count,
                            active_seconds,
                            keyboard_events,
                            mouse_events,
                            cpu_avg,
                            memory_avg,
                            created_at,
                            updated_at
                        )
                        SELECT
                            device_id,
                            date_trunc('day', bucket_hour)::date AS bucket_day,
                            SUM(sample_count)::INTEGER AS sample_count,
                            LEAST(COALESCE(SUM(active_seconds), 0), 86400)::INTEGER AS active_seconds,
                            COALESCE(SUM(keyboard_events), 0)::INTEGER AS keyboard_events,
                            COALESCE(SUM(mouse_events), 0)::INTEGER AS mouse_events,
                            CASE
                                WHEN SUM(CASE WHEN cpu_avg IS NOT NULL THEN GREATEST(sample_count, 1) ELSE 0 END) > 0
                                THEN
                                    SUM(cpu_avg * GREATEST(sample_count, 1))
                                    / SUM(CASE WHEN cpu_avg IS NOT NULL THEN GREATEST(sample_count, 1) ELSE 0 END)
                                ELSE NULL
                            END AS cpu_avg,
                            CASE
                                WHEN SUM(CASE WHEN memory_avg IS NOT NULL THEN GREATEST(sample_count, 1) ELSE 0 END) > 0
                                THEN
                                    SUM(memory_avg * GREATEST(sample_count, 1))
                                    / SUM(CASE WHEN memory_avg IS NOT NULL THEN GREATEST(sample_count, 1) ELSE 0 END)
                                ELSE NULL
                            END AS memory_avg,
                            NOW() AS created_at,
                            NOW() AS updated_at
                        FROM tracking_hourly_rollups
                        WHERE bucket_hour >= :window_start AND bucket_hour < :window_end
                        GROUP BY device_id, date_trunc('day', bucket_hour)::date
                        ON CONFLICT (device_id, bucket_day)
                        DO UPDATE SET
                            sample_count = EXCLUDED.sample_count,
                            active_seconds = EXCLUDED.active_seconds,
                            keyboard_events = EXCLUDED.keyboard_events,
                            mouse_events = EXCLUDED.mouse_events,
                            cpu_avg = EXCLUDED.cpu_avg,
                            memory_avg = EXCLUDED.memory_avg,
                            updated_at = NOW()
                    """)
                    db.session.execute(upsert_stmt, params)
            else:
                rolled_buckets = self._rollup_tracking_daily_python(window_start, window_end)

            state.rolled_until = window_end
            state.updated_at = now_utc
            db.session.commit()
            return {
                'success': True,
                'rolled_buckets': int(rolled_buckets),
                'window_start': window_start.isoformat(),
                'window_end': window_end.isoformat(),
            }
        except Exception as exc:
            db.session.rollback()
            return {'success': False, 'error': str(exc)}

    def run_tracking_rollups(self) -> Dict:
        """Run tracking rollups in safe order for reporting windows."""
        if self._timescaledb_enabled():
            tasks = {
                'hourly_rollup': self._timescaledb_managed_result(
                    'rollup_tracking_hourly',
                    'Tracking/productivity reporting uses TimescaleDB hypertables directly.',
                ),
                'daily_rollup': self._timescaledb_managed_result(
                    'rollup_tracking_daily',
                    'Tracking/productivity reporting uses TimescaleDB hypertables directly.',
                ),
            }
            return {'success': True, 'skipped': True, 'policy_managed': True, 'tasks': tasks}
        tasks = {
            'hourly_rollup': self.rollup_tracking_hourly(),
        }
        if not tasks['hourly_rollup'].get('success', False):
            return {'success': False, 'tasks': tasks}

        tasks['daily_rollup'] = self.rollup_tracking_daily()
        success = all(task.get('success', False) for task in tasks.values())
        return {'success': success, 'tasks': tasks}

    def backfill_tracking_rollups(self, lookback_days: int = 90) -> Dict:
        """Backfill tracking rollups across a historical lookback window."""
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'backfill_tracking_rollups',
                'Tracking/productivity backfill no longer targets legacy rollup tables when TimescaleDB is enabled.',
            )
        lookback_days = max(1, int(lookback_days or 90))
        now_utc = datetime.utcnow()
        hourly_start = self._floor_to_hour(now_utc - timedelta(days=lookback_days))
        hourly_end = self._floor_to_hour(now_utc)
        daily_start = self._floor_to_day(now_utc - timedelta(days=lookback_days))
        daily_end = self._floor_to_day(now_utc)

        try:
            # Reuse the upsert-based rollups across the full requested window.
            state_hourly = self._get_or_create_rollup_state('tracking_raw_to_hourly', hourly_start)
            state_daily = self._get_or_create_rollup_state('tracking_hourly_to_daily', daily_start)
            state_hourly.rolled_until = min(state_hourly.rolled_until or hourly_start, hourly_start)
            state_daily.rolled_until = min(state_daily.rolled_until or daily_start, daily_start)
            db.session.commit()

            hourly_result = self.rollup_tracking_hourly()
            daily_result = self.rollup_tracking_daily()
            return {
                'success': bool(hourly_result.get('success')) and bool(daily_result.get('success')),
                'lookback_days': lookback_days,
                'tasks': {
                    'hourly_rollup': hourly_result,
                    'daily_rollup': daily_result,
                },
                'window_start': hourly_start.isoformat(),
                'window_end': hourly_end.isoformat(),
                'daily_window_start': daily_start.isoformat(),
                'daily_window_end': daily_end.isoformat(),
            }
        except Exception as exc:
            db.session.rollback()
            return {'success': False, 'error': str(exc)}

    def run_tracking_history_integrity_check(self, lookback_days: int = 7) -> Dict:
        """Run tracking history sample integrity checks and persist audit records."""
        try:
            from services.tracking_history import run_tracking_integrity_checks

            return run_tracking_integrity_checks(lookback_days=lookback_days)
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def run_tracking_history_retention(
        self,
        raw_days: int = 30,
        hourly_days: int = 365,
        daily_days: int = 1095,
    ) -> Dict:
        """Run tracking history retention for raw logs/samples and rollups."""
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'run_tracking_history_retention',
                'Tracking raw hypertables are retention-managed by TimescaleDB and legacy tracking rollup tables are not maintained.',
            )
        try:
            from services.tracking_history import run_tracking_retention

            return run_tracking_retention(
                raw_days=raw_days,
                hourly_days=hourly_days,
                daily_days=daily_days,
            )
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def backfill_server_health_rollups(self, lookback_days: int = 90) -> Dict:
        """Backfill and repair server health rollups for a historical lookback window."""
        if self._timescaledb_enabled():
            return self._timescaledb_managed_result(
                'backfill_server_health_rollups',
                'Server health backfill no longer targets legacy rollup tables when TimescaleDB is enabled.',
            )
        return self.validate_and_repair_server_health_rollups(lookback_days=lookback_days)

    def aggregate_daily_stats(self, target_date: date = None, rebuild_existing: bool = False) -> Dict:
        """
        Aggregate scan history into daily statistics.
        Should be run once per day for the previous day.
        
        Args:
            target_date: Date to aggregate (default: yesterday)
            rebuild_existing: Replace existing rows for the target date
            
        Returns:
            Dict with aggregation results
        """
        target_date = target_date or (datetime.utcnow().date() - timedelta(days=1))
        
        try:
            from models.device import Device
            from models.scan_history import DeviceScanHistory
            from models.dashboard import DailyDeviceStats, DashboardEvent
            
            if rebuild_existing:
                DailyDeviceStats.query.filter_by(date=target_date).delete(synchronize_session=False)

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
                'total_devices': len(devices),
                'rebuild_existing': bool(rebuild_existing),
            }
            
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': str(e)}

    def backfill_daily_stats(self, days: int = 90, rebuild_existing: bool = False) -> Dict:
        """Backfill daily device stats across a historical date window."""
        days = max(1, int(days or 90))
        end_date = datetime.utcnow().date() - timedelta(days=1)
        start_date = end_date - timedelta(days=days - 1)
        results = {
            'success': True,
            'days': days,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'dates_processed': 0,
            'devices_aggregated': 0,
            'tasks': [],
        }

        for target_date in self._iter_dates(start_date, end_date):
            task = self.aggregate_daily_stats(target_date=target_date, rebuild_existing=rebuild_existing)
            results['tasks'].append(task)
            if not task.get('success', False):
                results['success'] = False
                return results
            results['dates_processed'] += 1
            results['devices_aggregated'] += int(task.get('devices_aggregated') or 0)

        return results

    def backfill_reporting_rollups(
        self,
        *,
        days: int = 90,
        rebuild_daily_stats: bool = False,
    ) -> Dict:
        """Backfill report rollups across all currently supported monitoring domains."""
        days = max(1, int(days or 90))
        tasks = {
            'daily_device_stats': self.backfill_daily_stats(days=days, rebuild_existing=rebuild_daily_stats),
            'server_health_rollups': self.backfill_server_health_rollups(lookback_days=days),
            'tracking_rollups': self.backfill_tracking_rollups(lookback_days=days),
        }
        return {
            'success': all(task.get('success', False) for task in tasks.values()),
            'lookback_days': days,
            'tasks': tasks,
        }
    
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
        
        # 2. Roll up tracking telemetry before any retention work runs
        tracking_rollup_result = self.run_tracking_rollups()
        for task_name, task_result in tracking_rollup_result.get('tasks', {}).items():
            results['tasks'][f'tracking_{task_name}'] = task_result

        # 3. Server health rollups + retention (rollup first, cleanup second)
        retention_result = self.run_server_health_retention()
        for task_name, task_result in retention_result.get('tasks', {}).items():
            results['tasks'][f'server_health_{task_name}'] = task_result

        # 4. Tracking history integrity + retention
        results['tasks']['tracking_history_integrity'] = self.run_tracking_history_integrity_check()
        results['tasks']['tracking_history_retention'] = self.run_tracking_history_retention()

        # 5. Cleanup old scan history
        results['tasks']['cleanup_scans'] = self.cleanup_old_scan_history()
        
        # 6. Cleanup old interface metrics
        results['tasks']['cleanup_metrics'] = self.cleanup_old_interface_metrics()
        
        # 7. Cleanup old resolved events
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
