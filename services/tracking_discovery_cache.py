import json
import logging

from config import Config
from extensions import redis_client

logger = logging.getLogger(__name__)

_DISCOVERY_CACHE_KEY_PREFIX = 'tracking:discovery-probe:ip:'


def _normalize_ip(ip_address):
    return str(ip_address or '').strip()


def get_cached_tracking_probe(ip_address):
    normalized_ip = _normalize_ip(ip_address)
    if not normalized_ip or not redis_client:
        return None

    try:
        raw_payload = redis_client.get(f'{_DISCOVERY_CACHE_KEY_PREFIX}{normalized_ip}')
    except Exception as exc:
        logger.debug('[TrackingDiscoveryCache] cache read failed ip=%s err=%s', normalized_ip, exc)
        return None

    if not raw_payload:
        return None

    try:
        payload = json.loads(raw_payload)
    except Exception as exc:
        logger.debug('[TrackingDiscoveryCache] cache decode failed ip=%s err=%s', normalized_ip, exc)
        return None

    return payload if isinstance(payload, dict) else None


def remember_tracking_probe(ip_address, probe_payload, ttl_seconds=None):
    normalized_ip = _normalize_ip(ip_address)
    if not normalized_ip or not redis_client or not isinstance(probe_payload, dict):
        return

    ttl = ttl_seconds
    try:
        ttl = int(ttl or getattr(Config, 'TRACKING_DISCOVERY_CACHE_TTL_SECONDS', 120) or 120)
    except (TypeError, ValueError):
        ttl = 120
    ttl = max(15, ttl)

    try:
        redis_client.setex(
            f'{_DISCOVERY_CACHE_KEY_PREFIX}{normalized_ip}',
            ttl,
            json.dumps(probe_payload),
        )
    except Exception as exc:
        logger.debug('[TrackingDiscoveryCache] cache write failed ip=%s err=%s', normalized_ip, exc)
