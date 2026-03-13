"""
TimescaleDB Service for Device Monitoring System
Provides helper functions for time-series queries and maintenance
"""
from datetime import datetime, timedelta
import re
from typing import Dict, List, Optional
from sqlalchemy import text
from extensions import db
import logging

logger = logging.getLogger(__name__)


class TimescaleDBService:
    """Service for TimescaleDB-specific operations"""

    _IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    @staticmethod
    def _is_safe_identifier(value: str) -> bool:
        return bool(value) and bool(TimescaleDBService._IDENTIFIER_RE.match(value))

    @staticmethod
    def _rows_to_dicts(result) -> List[Dict]:
        return [dict(row._mapping) for row in result]
    
    @staticmethod
    def is_timescaledb_enabled() -> bool:
        """Check if TimescaleDB extension is installed and enabled"""
        try:
            result = db.session.execute(text("""
                SELECT COUNT(*) 
                FROM pg_extension 
                WHERE extname = 'timescaledb'
            """)).scalar()
            return result > 0
        except Exception as e:
            logger.warning(f"Failed to check TimescaleDB status: {e}")
            return False
    
    @staticmethod
    def get_hypertable_info() -> List[Dict]:
        """Get information about all hypertables"""
        if not TimescaleDBService.is_timescaledb_enabled():
            return []
        
        try:
            result = db.session.execute(text("""
                SELECT 
                    h.hypertable_schema,
                    h.hypertable_name,
                    h.num_chunks,
                    h.num_dimensions,
                    h.compression_enabled,
                    pg_size_pretty(s.total_bytes) AS total_size,
                    pg_size_pretty(s.table_bytes) AS table_size,
                    pg_size_pretty(s.index_bytes) AS index_size
                FROM timescaledb_information.hypertables h
                CROSS JOIN LATERAL hypertable_detailed_size(
                    format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass
                ) s
                ORDER BY s.total_bytes DESC, h.hypertable_name ASC
            """))
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to get hypertable info: {e}")
            return []
    
    @staticmethod
    def get_compression_stats() -> List[Dict]:
        """Get compression statistics for all hypertables"""
        if not TimescaleDBService.is_timescaledb_enabled():
            return []
        
        try:
            result = db.session.execute(text("""
                SELECT
                    h.hypertable_name,
                    COUNT(c.chunk_name) AS total_chunks,
                    COUNT(*) FILTER (WHERE c.is_compressed) AS compressed_chunks,
                    STRING_AGG(
                        CASE WHEN cs.segmentby_column_index IS NOT NULL THEN cs.attname END,
                        ', ' ORDER BY cs.segmentby_column_index
                    ) AS segment_by,
                    STRING_AGG(
                        CASE WHEN cs.orderby_column_index IS NOT NULL THEN cs.attname END,
                        ', ' ORDER BY cs.orderby_column_index
                    ) AS order_by
                FROM timescaledb_information.hypertables h
                LEFT JOIN timescaledb_information.chunks c
                    ON c.hypertable_schema = h.hypertable_schema
                   AND c.hypertable_name = h.hypertable_name
                LEFT JOIN timescaledb_information.compression_settings cs
                    ON cs.hypertable_schema = h.hypertable_schema
                   AND cs.hypertable_name = h.hypertable_name
                WHERE h.compression_enabled
                GROUP BY h.hypertable_name
                ORDER BY h.hypertable_name
            """))
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to get compression stats: {e}")
            return []
    
    @staticmethod
    def get_chunk_status(hypertable_name: str, limit: int = 20) -> List[Dict]:
        """Get chunk information for a specific hypertable"""
        if not TimescaleDBService.is_timescaledb_enabled():
            return []
        
        try:
            result = db.session.execute(text("""
                SELECT
                    c.chunk_schema,
                    c.chunk_name,
                    c.range_start,
                    c.range_end,
                    c.is_compressed,
                    pg_size_pretty(
                        pg_total_relation_size(format('%I.%I', c.chunk_schema, c.chunk_name)::regclass)
                    ) AS size
                FROM timescaledb_information.chunks c
                WHERE hypertable_name = :hypertable_name
                ORDER BY c.range_start DESC
                LIMIT :limit
            """), {'hypertable_name': hypertable_name, 'limit': limit})
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to get chunk status: {e}")
            return []
    
    @staticmethod
    def get_continuous_aggregate_stats() -> List[Dict]:
        """Get statistics for continuous aggregates"""
        if not TimescaleDBService.is_timescaledb_enabled():
            return []
        
        try:
            result = db.session.execute(text("""
                SELECT
                    ca.view_name,
                    ca.materialization_hypertable_name,
                    js.last_run_started_at,
                    js.last_successful_finish,
                    js.total_runs,
                    js.total_failures,
                    js.total_successes,
                    js.last_run_status,
                    js.job_status
                FROM timescaledb_information.continuous_aggregates ca
                LEFT JOIN timescaledb_information.jobs j
                    ON j.hypertable_name = ca.view_name
                   AND j.proc_name = 'policy_refresh_continuous_aggregate'
                LEFT JOIN timescaledb_information.job_stats js
                    ON js.job_id = j.job_id
                ORDER BY ca.view_name
            """))
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to get continuous aggregate stats: {e}")
            return []
    
    @staticmethod
    def get_job_stats() -> List[Dict]:
        """Get status of TimescaleDB background jobs"""
        if not TimescaleDBService.is_timescaledb_enabled():
            return []
        
        try:
            result = db.session.execute(text("""
                SELECT
                    j.job_id,
                    j.application_name,
                    j.hypertable_name,
                    j.proc_name,
                    j.schedule_interval,
                    js.job_status,
                    js.last_run_status,
                    js.last_run_started_at,
                    js.last_successful_finish,
                    COALESCE(js.next_start, j.next_start) AS next_start,
                    js.total_runs,
                    js.total_failures
                FROM timescaledb_information.jobs j
                LEFT JOIN timescaledb_information.job_stats js
                    ON js.job_id = j.job_id
                ORDER BY COALESCE(js.next_start, j.next_start), j.job_id
            """))
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to get job stats: {e}")
            return []
    
    @staticmethod
    def query_time_bucket(
        table_name: str,
        time_column: str,
        bucket_interval: str,
        device_id: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        metrics: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Query data using time_bucket aggregation
        
        Args:
            table_name: Name of the hypertable
            time_column: Name of the timestamp column
            bucket_interval: Interval for time_bucket (e.g., '5 minutes', '1 hour')
            device_id: Optional device filter
            start_time: Optional start time filter
            end_time: Optional end time filter
            metrics: List of metric columns to aggregate (default: ['cpu_usage', 'memory_usage'])
        
        Returns:
            List of aggregated results
        """
        if not TimescaleDBService.is_timescaledb_enabled():
            logger.warning("TimescaleDB not enabled, falling back to standard query")
            return []
        if not TimescaleDBService._is_safe_identifier(table_name) or not TimescaleDBService._is_safe_identifier(time_column):
            logger.error("Unsafe table or column name for time bucket query")
            return []
        
        metrics = metrics or ['cpu_usage', 'memory_usage', 'disk_usage']
        
        # Build metric aggregations
        metric_aggs = []
        for metric in metrics:
            if not TimescaleDBService._is_safe_identifier(metric):
                logger.error("Unsafe metric name for time bucket query: %s", metric)
                return []
            metric_aggs.append(f"AVG({metric}) AS avg_{metric}")
            metric_aggs.append(f"MAX({metric}) AS max_{metric}")
            metric_aggs.append(f"MIN({metric}) AS min_{metric}")
        
        metric_sql = ', '.join(metric_aggs)
        
        # Build WHERE clause
        where_clauses = []
        params = {'bucket_interval': bucket_interval}
        
        if device_id is not None:
            where_clauses.append("device_id = :device_id")
            params['device_id'] = device_id
        
        if start_time:
            where_clauses.append(f"{time_column} >= :start_time")
            params['start_time'] = start_time
        
        if end_time:
            where_clauses.append(f"{time_column} < :end_time")
            params['end_time'] = end_time
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        query = text(f"""
            SELECT
                time_bucket(:bucket_interval, {time_column}) AS bucket,
                COUNT(*) AS sample_count,
                {metric_sql}
            FROM {table_name}
            {where_sql}
            GROUP BY bucket
            ORDER BY bucket
        """)
        
        try:
            result = db.session.execute(query, params)
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to query time_bucket: {e}")
            return []
    
    @staticmethod
    def query_continuous_aggregate(
        view_name: str,
        device_id: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Query a continuous aggregate view
        
        Args:
            view_name: Name of the continuous aggregate view
            device_id: Optional device filter
            start_time: Optional start time filter
            end_time: Optional end time filter
        
        Returns:
            List of aggregated results
        """
        if not TimescaleDBService.is_timescaledb_enabled():
            logger.warning("TimescaleDB not enabled, falling back to standard query")
            return []
        if not TimescaleDBService._is_safe_identifier(view_name):
            logger.error("Unsafe view name for continuous aggregate query")
            return []

        where_clauses = []
        params = {}
        time_col = 'bucket_hour' if 'hourly' in view_name else 'bucket_day'
        
        if device_id is not None:
            where_clauses.append("device_id = :device_id")
            params['device_id'] = device_id
        
        if start_time:
            where_clauses.append(f"{time_col} >= :start_time")
            params['start_time'] = start_time
        
        if end_time:
            where_clauses.append(f"{time_col} < :end_time")
            params['end_time'] = end_time
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        query = text(f"""
            SELECT * FROM {view_name}
            {where_sql}
            ORDER BY {time_col}
        """)
        
        try:
            result = db.session.execute(query, params)
            return TimescaleDBService._rows_to_dicts(result)
        except Exception as e:
            logger.error(f"Failed to query continuous aggregate: {e}")
            return []
    
    @staticmethod
    def compress_chunks_manually(hypertable_name: str, older_than: timedelta) -> Dict:
        """
        Manually compress chunks older than specified duration
        
        Args:
            hypertable_name: Name of the hypertable
            older_than: Compress chunks older than this duration
        
        Returns:
            Dict with compression results
        """
        if not TimescaleDBService.is_timescaledb_enabled():
            return {'success': False, 'error': 'TimescaleDB not enabled'}
        
        try:
            cutoff = datetime.utcnow() - older_than
            result = db.session.execute(text("""
                SELECT compress_chunk(chunk_schema || '.' || chunk_name)
                FROM timescaledb_information.chunks
                WHERE hypertable_name = :hypertable_name
                  AND NOT is_compressed
                  AND range_end < :cutoff
            """), {'hypertable_name': hypertable_name, 'cutoff': cutoff})
            
            compressed_count = result.rowcount
            db.session.commit()
            
            return {
                'success': True,
                'compressed_chunks': compressed_count,
                'hypertable': hypertable_name,
                'cutoff': cutoff.isoformat()
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to compress chunks: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def refresh_continuous_aggregate(view_name: str, start_time: Optional[datetime] = None, 
                                     end_time: Optional[datetime] = None) -> Dict:
        """
        Manually refresh a continuous aggregate
        
        Args:
            view_name: Name of the continuous aggregate view
            start_time: Optional start of refresh window
            end_time: Optional end of refresh window
        
        Returns:
            Dict with refresh results
        """
        if not TimescaleDBService.is_timescaledb_enabled():
            return {'success': False, 'error': 'TimescaleDB not enabled'}
        
        try:
            if start_time and end_time:
                db.session.execute(text("""
                    CALL refresh_continuous_aggregate(
                        CAST(:view_name AS regclass),
                        :start_time,
                        :end_time,
                        FALSE,
                        NULL
                    )
                """), {'view_name': view_name, 'start_time': start_time, 'end_time': end_time})
            else:
                db.session.execute(text("""
                    CALL refresh_continuous_aggregate(CAST(:view_name AS regclass), NULL, NULL, FALSE, NULL)
                """), {'view_name': view_name})
            
            db.session.commit()
            
            return {
                'success': True,
                'view_name': view_name,
                'refreshed_at': datetime.utcnow().isoformat()
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to refresh continuous aggregate: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def get_health_report() -> Dict:
        """
        Get comprehensive health report for TimescaleDB
        
        Returns:
            Dict with health metrics
        """
        if not TimescaleDBService.is_timescaledb_enabled():
            return {
                'enabled': False,
                'message': 'TimescaleDB extension not installed'
            }
        
        return {
            'enabled': True,
            'hypertables': TimescaleDBService.get_hypertable_info(),
            'compression_stats': TimescaleDBService.get_compression_stats(),
            'continuous_aggregates': TimescaleDBService.get_continuous_aggregate_stats(),
            'jobs': TimescaleDBService.get_job_stats(),
            'generated_at': datetime.utcnow().isoformat()
        }
