from __future__ import annotations

import logging

from sqlalchemy import or_

from extensions import db, redis_client
from models.dashboard import DashboardSnapshot

logger = logging.getLogger(__name__)


def _normalized_prefixes(prefixes=None) -> list[str]:
    values = prefixes or []
    normalized = []
    for prefix in values:
        text = str(prefix or "").strip()
        if text:
            normalized.append(text)
    return normalized


def invalidate_dashboard_namespace(namespace="dashboard", scope_fragments=None, prefixes=None) -> dict:
    from routes import dashboard as dashboard_route_module

    normalized_namespace = str(namespace or "dashboard").strip() or "dashboard"
    normalized_prefixes = _normalized_prefixes(prefixes)
    scope_fragments = [str(fragment).strip() for fragment in (scope_fragments or []) if str(fragment).strip()]

    removed_local = 0
    removed_snapshots = 0
    removed_redis = 0

    def key_matches(cache_key: str) -> bool:
        if not str(cache_key or "").startswith(f"{normalized_namespace}:"):
            return False
        if not normalized_prefixes:
            return True
        for prefix in normalized_prefixes:
            if cache_key.startswith(f"{normalized_namespace}:{prefix}:"):
                if not scope_fragments:
                    return True
                if any(fragment in cache_key for fragment in scope_fragments):
                    return True
        return False

    local_keys = [key for key in list(dashboard_route_module._cache.keys()) if key_matches(key)]
    for key in local_keys:
        dashboard_route_module._cache.pop(key, None)
        dashboard_route_module._cache_ttl.pop(key, None)
        removed_local += 1

    if redis_client:
        try:
            redis_keys: list[str] = []
            for prefix in normalized_prefixes or ["*"]:
                pattern = (
                    f"{normalized_namespace}:{prefix}:*"
                    if prefix != "*"
                    else f"{normalized_namespace}:*"
                )
                matched = redis_client.keys(pattern)
                for item in matched or []:
                    key_text = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                    if key_matches(key_text):
                        redis_keys.append(key_text)
            if redis_keys:
                removed_redis = int(redis_client.delete(*sorted(set(redis_keys))) or 0)
        except Exception as exc:
            logger.warning("[DashboardCache] Redis invalidation failed: %s", exc)

    snapshot_query = DashboardSnapshot.query
    if normalized_prefixes:
        snapshot_filters = [DashboardSnapshot.cache_key.like(f"{prefix}%") for prefix in normalized_prefixes]
        snapshot_query = snapshot_query.filter(or_(*snapshot_filters))
    if scope_fragments:
        scope_filters = [DashboardSnapshot.cache_key.like(f"%{fragment}%") for fragment in scope_fragments]
        snapshot_query = snapshot_query.filter(or_(*scope_filters))
    removed_snapshots = snapshot_query.delete(synchronize_session=False)

    return {
        "namespace": normalized_namespace,
        "prefixes": normalized_prefixes,
        "scope_fragments": scope_fragments,
        "local_removed": int(removed_local or 0),
        "redis_removed": int(removed_redis or 0),
        "snapshots_removed": int(removed_snapshots or 0),
    }


def invalidate_dashboard_server_views(device_id=None) -> dict:
    return invalidate_dashboard_namespace(
        namespace="dashboard",
        prefixes=["summary", "top-problems", "availability-details", "trends", "full_snapshot_"],
    )


def invalidate_dashboard_threshold_views() -> dict:
    return invalidate_dashboard_namespace(
        namespace="dashboard",
        prefixes=["summary", "top-problems", "availability-details", "trends", "full_snapshot_"],
    )
