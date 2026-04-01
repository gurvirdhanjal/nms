"""
alert_routing_service — decouples alert routing (who gets this alert?)
from alert transport (how is it delivered?).

Flow:
    alert_manager._trigger_alert()
        → alert_routing_service.route_alert(device, metric, value, msg, severity)
            → get_channels_for_alert(device, severity) → list[AlertChannel]
            → for each channel: notification_service.send_via_channel(channel, ...)
            → if no channels: fallback to notification_service.send_alert()

The AlertChannel table is created in Phase 7.  Until then, the query
safely returns an empty list and the fallback path fires — identical
to the previous behaviour.
"""
import logging
import time
import threading

logger = logging.getLogger(__name__)

# In-memory channel cache — avoids DB query on every alert.
_channel_cache: list | None = None
_channel_cache_expiry: float = 0.0
_channel_cache_lock = threading.Lock()
_CHANNEL_CACHE_TTL: float = 30.0


def invalidate_cache() -> None:
    """Invalidate the channel cache. Call after any AlertChannel CRUD."""
    global _channel_cache, _channel_cache_expiry
    with _channel_cache_lock:
        _channel_cache = None
        _channel_cache_expiry = 0.0


def _load_channels() -> list:
    """Load all enabled AlertChannel rows from DB (or return empty list on error)."""
    try:
        from models.alert_channel import AlertChannel  # noqa — created in Phase 7
        return AlertChannel.query.filter_by(is_enabled=True).all()
    except Exception:
        # Table may not exist yet (before Phase 7 migration runs) — safe fallback.
        return []


def get_channels_for_alert(device, severity: str) -> list:
    """Return enabled AlertChannel rows that match this alert.

    Filters by:
    - is_enabled = True
    - send_on_critical / send_on_warning matches severity
    - applicable_device_types is empty OR contains device.device_type

    Results are cached for 30 seconds to avoid DB queries on every alert.
    """
    global _channel_cache, _channel_cache_expiry

    now = time.time()
    with _channel_cache_lock:
        if _channel_cache is not None and now < _channel_cache_expiry:
            channels = list(_channel_cache)
        else:
            channels = _load_channels()
            _channel_cache = channels
            _channel_cache_expiry = now + _CHANNEL_CACHE_TTL

    sev_upper = (severity or '').upper()
    device_type = (getattr(device, 'device_type', '') or '').lower()

    matched = []
    for ch in channels:
        # Severity filter
        if sev_upper == 'CRITICAL' and not getattr(ch, 'send_on_critical', True):
            continue
        if sev_upper == 'WARNING' and not getattr(ch, 'send_on_warning', False):
            continue

        # Device type filter
        applicable = getattr(ch, 'applicable_device_types', None) or []
        if applicable:
            if device_type not in [t.lower() for t in applicable]:
                continue

        matched.append(ch)

    return matched


def route_alert(device, metric: str, value, message: str, severity: str) -> dict:
    """Route an alert through configured channels.

    Returns:
        {success: bool, channels_triggered: list[str]}

    Backward compatibility: if no AlertChannel rows exist, delegates to
    NotificationService.send_alert() exactly as before.  This preserves
    existing email delivery during the Phase 4→7 transition.
    """
    channels = get_channels_for_alert(device, severity)

    if not channels:
        # Fallback: use the original notification_service path.
        # Only fires for servers (matches existing alert_manager behaviour).
        try:
            from services.notification_service import NotificationService
            device_type = (getattr(device, 'device_type', '') or '').lower()
            if device_type == 'server' and severity in ('CRITICAL', 'WARNING'):
                NotificationService.send_alert(device, metric, value, message, severity=severity)
                return {'success': True, 'channels_triggered': ['email (fallback)']}
        except Exception:
            logger.exception("[routing] Fallback notification failed")
        return {'success': True, 'channels_triggered': []}

    from services.notification_service import NotificationService
    triggered = []
    for channel in channels:
        try:
            ok = NotificationService.send_via_channel(channel, device, message, severity)
            if ok:
                triggered.append(getattr(channel, 'name', str(channel)))
        except Exception:
            logger.exception("[routing] Channel '%s' delivery failed", getattr(channel, 'name', '?'))

    return {'success': True, 'channels_triggered': triggered}
