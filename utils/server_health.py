from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select

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


def query_latest_server_health_logs(*, device_ids=None, source=None, cutoff=None):
    from extensions import db
    from models.server_health import ServerHealthLog

    if device_ids is not None:
        device_ids = [int(device_id) for device_id in device_ids if device_id is not None]
        if not device_ids:
            return []

    table = ServerHealthLog.__table__
    ranked_query = select(
        *table.c,
        func.row_number().over(
            partition_by=table.c.device_id,
            order_by=(table.c.timestamp.desc(), table.c.id.desc()),
        ).label("row_num"),
    )

    if source:
        ranked_query = ranked_query.where(table.c.source == source)
    if cutoff is not None:
        ranked_query = ranked_query.where(table.c.timestamp >= cutoff)
    if device_ids is not None:
        ranked_query = ranked_query.where(table.c.device_id.in_(device_ids))

    ranked_subq = ranked_query.subquery()
    rows = db.session.execute(
        select(ranked_subq).where(ranked_subq.c.row_num == 1)
    ).mappings()

    return [
        SimpleNamespace(**{key: value for key, value in row.items() if key != "row_num"})
        for row in rows
    ]
