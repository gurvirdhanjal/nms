"""
TimescaleDB Service for Device Monitoring System
Provides helper functions for time-series queries and maintenance
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from sqlalchemy import text
from extensions import db
import logging

logger = logging.getLogger(__name__)


class TimescaleDBService:
    """Service for TimescaleDB-specific operations"""
    
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
                    hypertable_schema,
                    hypertable_name,
                    num_chunks,
                    num_dimensions,
                    pg_size_pretty(total_bytes) AS total_size,
                    pg_size_pretty(table_bytes) AS table_size,
                    pg_size_pretty(index_bytes) AS index_size
                FROM timescaledb_information.hypertables
                ORDER BY total_bytes DESC
            """))
            return [dict(row) for row in result]
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
                    hypertable_name,
                    pg_size_pretty(before_compression_total_bytes) AS uncompressed_size,
                    pg_size_pretty(after_compression_total_bytes) AS compressed_size,
                    ROUND(100 - (after_compression_total_bytes::float / 
                          NULLIF(before_compression_total_bytes, 0) * 100), 2) AS compression_ratio_pct
                FROM timescaledb_information.compression_settings
                WHERE before_compression_total_bytes > 0
                ORDER BY before_compression_total_bytes DESC
            """))
            return [dict(row) for row in result]
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
                    chunk_name,
                    range_start,
                    range_end,
                    is_compressed,
                    pg_size_pretty(total_bytes) AS size,
                    pg_size_pretty(compressed_total_bytes) AS compressed_size
                FROM timescaledb_information.chunks
                WHERE hypertable_name = :hypertable_name
                ORDER BY range_start DESC
                LIMIT :limit
            """), {'hypertable_name': hypertable_name, 'limit': limit})
            return [dict(row) for row in result]
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
                    view_name,
                    materialization_hypertable_name,
                    last_run_started_at,
                    last_successful_finish,
                    total_runs,
                    total_failures,
                    total_successes
                FROM timescaledb_information.continuous_aggregate_stats
                ORDER BY view_name
            """))
            return [dict(row) for row in result]
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
                    job_id,
                    application_name,
                    schedule_interval,
                    last_run_status,
                    last_run_started_at,
                    last_successful_finish,
                    next_start,
                    total_runs,
                    total_failures
                FROM timescaledb_information.jobs
                ORDER BY next_start
            """))
            return [dict(row) for row in result]
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
        
        metrics = metrics or ['cpu_usage', 'memory_usage', 'disk_usage']
        
        # Build metric aggregations
        metric_aggs = []
        for metric in metrics:
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
            return [dict(row) for row in result]
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
        where_clauses = []
        params = {}
        
        if device_id is not None:
            where_clauses.append("device_id = :device_id")
            params['device_id'] = device_id
        
        if start_time:
            # Determine time column based on view name
            time_col = 'bucket_hour' if 'hourly' in view_name else 'bucket_day'
            where_clauses.append(f"{time_col} >= :start_time")
            params['start_time'] = start_time
        
        if end_time:
            time_col = 'bucket_hour' if 'hourly' in view_name else 'bucket_day'
            where_clauses.append(f"{time_col} < :end_time")
            params['end_time'] = end_time
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        query = text(f"""
            SELECT * FROM {view_name}
            {where_sql}
            ORDER BY 
                CASE 
                    WHEN '{view_name}' LIKE '%hourly%' THEN bucket_hour
                    ELSE bucket_day::timestamp
                END
        """)
        
        try:
            result = db.session.execute(query, params)
            return [dict(row) for row in result]
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
                db.session.execute(text(f"""
                    CALL refresh_continuous_aggregate(
                        :view_name,
                        :start_time,
                        :end_time
                    )
                """), {'view_name': view_name, 'start_time': start_time, 'end_time': end_time})
            else:
                db.session.execute(text(f"""
                    CALL refresh_continuous_aggregate(:view_name, NULL, NULL)
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
