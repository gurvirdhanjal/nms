from datetime import datetime


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
        age_seconds = (datetime.utcnow() - log.timestamp).total_seconds()
        if age_seconds > OFFLINE_THRESHOLD_SECONDS:
            return "Offline"
    except Exception:
        return "Offline"

    cpu = log.cpu_usage
    ram = log.memory_usage
    disk = log.disk_usage

    if cpu is None or ram is None or disk is None:
        return "Offline"

    if cpu > 90 or disk > 95:
        return "Critical"
    if cpu > 80 or ram > 85:
        return "Warning"
    return "Healthy"
