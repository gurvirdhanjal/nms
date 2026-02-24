"""
Redis-backed SSE Event Broadcaster for Real-Time Dashboard Updates.

Uses Redis Pub/Sub to fan out events across all Gunicorn workers simultaneously.
"""
import json
import logging
from extensions import redis_client, is_redis_available

logger = logging.getLogger(__name__)

SSE_CHANNEL = 'sse_events'

class RedisSSEBroadcaster:
    """
    Publishes events to the Redis SSE channel.
    Clients subscribe to this channel directly via the sse.py route generator.
    """
    def broadcast(self, event_type: str, payload: dict) -> bool:
        """
        Broadcast an event to all connected clients across all workers.
        """
        if not is_redis_available():
            return False
            
        try:
            event_data = {
                'event_type': event_type,
                'payload': payload
            }
            redis_client.publish(SSE_CHANNEL, json.dumps(event_data))
            return True
        except Exception as e:
            logger.error(f"[SSE] Failed to publish event to Redis: {e}")
            return False

def get_broadcaster():
    """Returns the Redis broadcaster if available, or None if multi-worker SSE is degraded."""
    if not is_redis_available():
        logger.warning("[SSE] Redis unavailable. SSE real-time events are degraded/disabled.")
        return None
    return RedisSSEBroadcaster()

def broadcast_event(event_type: str, payload: dict) -> bool:
    broadcaster = get_broadcaster()
    if broadcaster:
        return broadcaster.broadcast(event_type, payload)
    return False
