from datetime import datetime, timezone

from services.server_thresholds import summarize_health


OFFLINE_THRESHOLD_SECONDS = 120


def is_server_device(device_type: str) -> bool:
    return (device_type or "").strip().lower() == "server"


def compute_server_health(log) -> str:
    """
    Determine server health from the latest agent log.
    Rules:
      - Offline: last_seen > 120s OR missing metrics
      - Critical: CPU > 90% OR Disk > 95%
      - Warning: CPU > 80% OR RAM > 85%
      - Healthy: otherwise
    """
    if not log or not getattr(log, "timestamp", None):
        return "Offline"

    try:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        age_seconds = (now_utc - log.timestamp).total_seconds()
        if age_seconds > OFFLINE_THRESHOLD_SECONDS:
            return "Offline"
    except Exception:
        return "Offline"

    return summarize_health(log).state
