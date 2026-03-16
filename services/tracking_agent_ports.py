import logging

from config import Config
from extensions import redis_client

logger = logging.getLogger(__name__)

_PORT_CACHE_KEY_PREFIX = 'tracking:agent-port:ip:'


def _coerce_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def configured_tracking_agent_ports():
    configured = getattr(Config, 'TRACKING_AGENT_PORTS', None)
    ports = []

    if isinstance(configured, (list, tuple)):
        for item in configured:
            port = _coerce_port(item)
            if port and port not in ports:
                ports.append(port)

    fallback_port = _coerce_port(getattr(Config, 'TRACKING_AGENT_PORT', 5002)) or 5002
    if fallback_port not in ports:
        ports.append(fallback_port)

    return ports or [5002]


def get_cached_tracking_agent_port(ip_address):
    ip_text = str(ip_address or '').strip()
    if not ip_text or not redis_client:
        return None

    try:
        raw_value = redis_client.get(f'{_PORT_CACHE_KEY_PREFIX}{ip_text}')
    except Exception as exc:
        logger.debug('[TrackingAgentPort] cache read failed ip=%s err=%s', ip_text, exc)
        return None

    return _coerce_port(raw_value)


def remember_tracking_agent_port(ip_address, port, ttl_seconds=None):
    ip_text = str(ip_address or '').strip()
    normalized_port = _coerce_port(port)
    if not ip_text or not normalized_port or not redis_client:
        return

    ttl = _coerce_port(ttl_seconds) or int(
        getattr(Config, 'TRACKING_AGENT_PORT_CACHE_TTL_SECONDS', 43200) or 43200
    )
    ttl = max(60, ttl)

    try:
        redis_client.setex(
            f'{_PORT_CACHE_KEY_PREFIX}{ip_text}',
            ttl,
            str(normalized_port),
        )
    except Exception as exc:
        logger.debug('[TrackingAgentPort] cache write failed ip=%s port=%s err=%s', ip_text, normalized_port, exc)


def resolve_tracking_agent_ports(ip_address=None, explicit_port=None):
    ports = []

    cached_port = get_cached_tracking_agent_port(ip_address)
    if cached_port and cached_port not in ports:
        ports.append(cached_port)

    preferred_port = _coerce_port(explicit_port)
    if preferred_port and preferred_port not in ports:
        ports.append(preferred_port)

    for port in configured_tracking_agent_ports():
        if port not in ports:
            ports.append(port)

    return ports


def preferred_tracking_agent_port(ip_address=None, explicit_port=None):
    ports = resolve_tracking_agent_ports(ip_address=ip_address, explicit_port=explicit_port)
    return ports[0] if ports else 5002
