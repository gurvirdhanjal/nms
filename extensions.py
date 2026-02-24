from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import redis
import logging
from config import Config

logger = logging.getLogger(__name__)

# Initialize extensions without app
db = SQLAlchemy()
bcrypt = Bcrypt()

# Global Event Manager
from events.event_manager import EventManager
event_manager = EventManager()

# Global Redis Client
# Graceful fallback: Test the connection on module load
redis_client = None

try:
    redis_client = redis.Redis.from_url(
        Config.REDIS_URL,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
        retry_on_timeout=True,
        decode_responses=True # We will store/read strings/JSON
    )
    redis_client.ping()
    logger.info("[Redis] Successfully connected to global Redis instance.")
except redis.exceptions.ConnectionError:
    logger.warning("[Redis] Connection failed or missing. Reverting to graceful multi-worker fallback modes.")
    redis_client = None
except Exception as e:
    logger.warning(f"[Redis] Unexpected error during initialization: {e}")
    redis_client = None

def is_redis_available():
    """Returns True if the global redis client is initialized and reachable."""
    if redis_client is None:
        return False
    try:
        return redis_client.ping()
    except redis.exceptions.ConnectionError:
        return False