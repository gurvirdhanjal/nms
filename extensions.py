from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import redis
import logging
from config import Config

_bcrypt_fallback_reason = None
try:
    from flask_bcrypt import Bcrypt
except Exception as bcrypt_import_error:  # pragma: no cover - environment fallback
    from werkzeug.security import check_password_hash as _wz_check_password_hash
    from werkzeug.security import generate_password_hash as _wz_generate_password_hash

    _bcrypt_fallback_reason = str(bcrypt_import_error)

    class Bcrypt:  # pragma: no cover - exercised only in incompatible runtimes
        def init_app(self, app):
            return self

        def generate_password_hash(self, password, rounds=None, prefix=None):
            if isinstance(password, bytes):
                password = password.decode('utf-8')
            generated = _wz_generate_password_hash(str(password))
            return generated.encode('utf-8')

        def check_password_hash(self, pw_hash, password):
            if isinstance(pw_hash, bytes):
                pw_hash = pw_hash.decode('utf-8')
            if isinstance(password, bytes):
                password = password.decode('utf-8')
            return _wz_check_password_hash(str(pw_hash), str(password))

logger = logging.getLogger(__name__)
if _bcrypt_fallback_reason:  # pragma: no cover - environment fallback
    logger.warning(
        "[Auth] Flask-Bcrypt unavailable; using Werkzeug fallback. reason=%s",
        _bcrypt_fallback_reason,
    )

# Initialize extensions without app
db = SQLAlchemy()
bcrypt = Bcrypt()
limiter = Limiter(key_func=get_remote_address, default_limits=[])

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
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError, OSError):
        return False
