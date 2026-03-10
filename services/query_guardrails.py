"""
Query Guardrails for Monitoring System
Prevents heavy queries that could impact database performance
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple
from flask import abort
import logging

logger = logging.getLogger(__name__)


class QueryGuardrails:
    """Enforces query limits to prevent database overload"""
    
    # Maximum time ranges allowed for different query types
    MAX_RAW_QUERY_DAYS = 7  # Raw logs: max 7 days
    MAX_HOURLY_QUERY_DAYS = 90  # Hourly aggregates: max 90 days
    MAX_DAILY_QUERY_DAYS = 1825  # Daily aggregates: max 5 years
    
    # Maximum number of devices in a single query
    MAX_DEVICES_PER_QUERY = 100
    
    # Maximum rows to return
    MAX_ROWS_LIMIT = 10000
    
    @staticmethod
    def validate_time_range(
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        query_type: str = 'raw'
    ) -> Tuple[datetime, datetime]:
        """
        Validate and enforce time range limits
        
        Args:
            start_time: Query start time (None = auto-calculate)
            end_time: Query end time (None = now)
            query_type: Type of query ('raw', 'hourly', 'daily')
        
        Returns:
            Tuple of (validated_start_time, validated_end_time)
        
        Raises:
            ValueError: If time range exceeds limits
        """
        now = datetime.utcnow()
        
        # Set defaults
        if end_time is None:
            end_time = now
        
        if start_time is None:
            # Default to 24 hours for raw, 30 days for aggregates
            if query_type == 'raw':
                start_time = end_time - timedelta(days=1)
            elif query_type == 'hourly':
                start_time = end_time - timedelta(days=30)
            else:  # daily
                start_time = end_time - timedelta(days=365)
        
        # Validate end_time is not in the future
        if end_time > now:
            logger.warning(f"End time {end_time} is in the future, adjusting to now")
            end_time = now
        
        # Validate start_time is before end_time
        if start_time >= end_time:
            raise ValueError(f"Start time ({start_time}) must be before end time ({end_time})")
        
        # Calculate time range
        time_range = end_time - start_time
        
        # Enforce limits based on query type
        if query_type == 'raw':
            max_days = QueryGuardrails.MAX_RAW_QUERY_DAYS
            if time_range > timedelta(days=max_days):
                raise ValueError(
                    f"Raw query time range ({time_range.days} days) exceeds maximum "
                    f"allowed ({max_days} days). Use hourly aggregates for longer ranges."
                )
        
        elif query_type == 'hourly':
            max_days = QueryGuardrails.MAX_HOURLY_QUERY_DAYS
            if time_range > timedelta(days=max_days):
                raise ValueError(
                    f"Hourly query time range ({time_range.days} days) exceeds maximum "
                    f"allowed ({max_days} days). Use daily aggregates for longer ranges."
                )
        
        elif query_type == 'daily':
            max_days = QueryGuardrails.MAX_DAILY_QUERY_DAYS
            if time_range > timedelta(days=max_days):
                raise ValueError(
                    f"Daily query time range ({time_range.days} days) exceeds maximum "
                    f"allowed ({max_days} days)."
                )
        
        logger.info(f"Validated {query_type} query: {start_time} to {end_time} ({time_range.days} days)")
        return start_time, end_time
    
    @staticmethod
    def validate_device_list(device_ids: list) -> list:
        """
        Validate device list doesn't exceed limits
        
        Args:
            device_ids: List of device IDs to query
        
        Returns:
            Validated device ID list
        
        Raises:
            ValueError: If device list exceeds limits
        """
        if not device_ids:
            raise ValueError("Device list cannot be empty")
        
        if len(device_ids) > QueryGuardrails.MAX_DEVICES_PER_QUERY:
            raise ValueError(
                f"Device list ({len(device_ids)}) exceeds maximum "
                f"allowed ({QueryGuardrails.MAX_DEVICES_PER_QUERY})"
            )
        
        return device_ids
    
    @staticmethod
    def validate_limit(limit: Optional[int]) -> int:
        """
        Validate and enforce row limit
        
        Args:
            limit: Requested row limit (None = default)
        
        Returns:
            Validated limit
        """
        if limit is None:
            return 1000  # Default limit
        
        if limit <= 0:
            raise ValueError("Limit must be positive")
        
        if limit > QueryGuardrails.MAX_ROWS_LIMIT:
            logger.warning(
                f"Requested limit ({limit}) exceeds maximum ({QueryGuardrails.MAX_ROWS_LIMIT}), "
                f"capping to maximum"
            )
            return QueryGuardrails.MAX_ROWS_LIMIT
        
        return limit
    
    @staticmethod
    def recommend_query_type(start_time: datetime, end_time: datetime) -> str:
        """
        Recommend optimal query type based on time range
        
        Args:
            start_time: Query start time
            end_time: Query end time
        
        Returns:
            Recommended query type ('raw', 'hourly', 'daily')
        """
        time_range = end_time - start_time
        
        if time_range <= timedelta(hours=24):
            return 'raw'
        elif time_range <= timedelta(days=90):
            return 'hourly'
        else:
            return 'daily'
    
    @staticmethod
    def get_optimal_bucket_interval(start_time: datetime, end_time: datetime) -> str:
        """
        Get optimal time_bucket interval based on time range
        
        Args:
            start_time: Query start time
            end_time: Query end time
        
        Returns:
            Optimal bucket interval (e.g., '5 minutes', '1 hour', '1 day')
        """
        time_range = end_time - start_time
        
        if time_range <= timedelta(hours=6):
            return '1 minute'
        elif time_range <= timedelta(hours=24):
            return '5 minutes'
        elif time_range <= timedelta(days=7):
            return '1 hour'
        elif time_range <= timedelta(days=30):
            return '6 hours'
        elif time_range <= timedelta(days=90):
            return '1 day'
        else:
            return '1 week'
    
    @staticmethod
    def enforce_api_limits(
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        device_ids: Optional[list] = None,
        limit: Optional[int] = None,
        query_type: str = 'raw'
    ) -> dict:
        """
        Enforce all API limits and return validated parameters
        
        Args:
            start_time: Query start time
            end_time: Query end time
            device_ids: List of device IDs
            limit: Row limit
            query_type: Type of query
        
        Returns:
            Dict with validated parameters
        
        Raises:
            ValueError: If any validation fails
        """
        try:
            # Validate time range
            validated_start, validated_end = QueryGuardrails.validate_time_range(
                start_time, end_time, query_type
            )
            
            # Validate device list if provided
            validated_devices = None
            if device_ids is not None:
                validated_devices = QueryGuardrails.validate_device_list(device_ids)
            
            # Validate limit
            validated_limit = QueryGuardrails.validate_limit(limit)
            
            # Get recommendations
            recommended_type = QueryGuardrails.recommend_query_type(validated_start, validated_end)
            optimal_bucket = QueryGuardrails.get_optimal_bucket_interval(validated_start, validated_end)
            
            return {
                'start_time': validated_start,
                'end_time': validated_end,
                'device_ids': validated_devices,
                'limit': validated_limit,
                'query_type': query_type,
                'recommended_query_type': recommended_type,
                'optimal_bucket_interval': optimal_bucket,
                'time_range_days': (validated_end - validated_start).days
            }
        
        except ValueError as e:
            logger.error(f"Query validation failed: {e}")
            raise


# Flask decorator for API endpoints
def enforce_query_limits(query_type='raw'):
    """
    Decorator to enforce query limits on API endpoints
    
    Usage:
        @app.route('/api/metrics')
        @enforce_query_limits(query_type='raw')
        def get_metrics():
            # Access validated params via request.validated_params
            pass
    """
    def decorator(f):
        from functools import wraps
        from flask import request
        
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                # Extract parameters from request
                start_time = request.args.get('start_time')
                end_time = request.args.get('end_time')
                device_ids = request.args.getlist('device_id')
                limit = request.args.get('limit', type=int)
                
                # Parse datetime strings if provided
                if start_time:
                    start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                if end_time:
                    end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                
                # Validate and enforce limits
                validated = QueryGuardrails.enforce_api_limits(
                    start_time=start_time,
                    end_time=end_time,
                    device_ids=device_ids if device_ids else None,
                    limit=limit,
                    query_type=query_type
                )
                
                # Attach validated params to request
                request.validated_params = validated
                
                # Warn if using suboptimal query type
                if validated['recommended_query_type'] != query_type:
                    logger.warning(
                        f"Query type '{query_type}' not optimal for time range. "
                        f"Recommend '{validated['recommended_query_type']}'"
                    )
                
                return f(*args, **kwargs)
            
            except ValueError as e:
                abort(400, description=str(e))
        
        return decorated_function
    return decorator
