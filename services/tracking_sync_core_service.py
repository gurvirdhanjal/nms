from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from services.restricted_site_ingest_service import coerce_restricted_events


@dataclass(frozen=True)
class SyncCorePlan:
    normalized_mac: str
    unique_client_id: str | None
    dedupe_key: str
    current_stats_valid: bool
    has_current_activity: bool
    has_system_metrics: bool
    has_network_metrics: bool
    restricted_event_count: int
    policy_version_seen: str
    hostname: str | None
    generated_at: datetime

    def to_summary(self) -> dict:
        return {
            'normalized_mac': self.normalized_mac,
            'unique_client_id': self.unique_client_id,
            'dedupe_key': self.dedupe_key,
            'current_stats_valid': bool(self.current_stats_valid),
            'has_current_activity': bool(self.has_current_activity),
            'has_system_metrics': bool(self.has_system_metrics),
            'has_network_metrics': bool(self.has_network_metrics),
            'restricted_event_count': int(self.restricted_event_count),
            'policy_version_seen': self.policy_version_seen,
            'hostname': self.hostname,
        }


@dataclass(frozen=True)
class SyncCoreResult:
    applied: bool
    summary: dict


def build_sync_dedupe_key(payload: dict, normalized_mac: str, unique_client_id: str | None) -> str:
    canonical = json.dumps(
        {
            'mac_address': normalized_mac,
            'unique_client_id': unique_client_id,
            'hostname': payload.get('hostname'),
            'restricted_sites_policy_version': payload.get('restricted_sites_policy_version'),
            'current_stats': payload.get('current_stats'),
            'restricted_site_events': payload.get('restricted_site_events'),
        },
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _clean_string(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def normalize_current_stats_payload(current_stats: dict | None, *, hostname: str | None = None) -> dict | None:
    if current_stats is None:
        return None
    if not isinstance(current_stats, dict):
        return None

    current_activity = dict(current_stats.get('current_activity') or {}) if isinstance(current_stats.get('current_activity'), dict) else {}
    today_stats = dict(current_stats.get('today_stats') or {}) if isinstance(current_stats.get('today_stats'), dict) else {}
    system_metrics = dict(current_stats.get('system_metrics') or {}) if isinstance(current_stats.get('system_metrics'), dict) else {}
    network_metrics = dict(current_stats.get('network') or {}) if isinstance(current_stats.get('network'), dict) else {}
    device_info = dict(current_stats.get('device_info') or {}) if isinstance(current_stats.get('device_info'), dict) else {}
    meta = dict(current_stats.get('meta') or {}) if isinstance(current_stats.get('meta'), dict) else {}

    legacy_activity = current_stats.get('activity') if isinstance(current_stats.get('activity'), dict) else {}
    legacy_system = current_stats.get('system') if isinstance(current_stats.get('system'), dict) else {}

    if legacy_activity:
        current_activity.setdefault('keyboard_active', bool(legacy_activity.get('keyboard_active')))
        current_activity.setdefault('mouse_active', bool(legacy_activity.get('mouse_active')))
        idle_seconds = _coerce_float(legacy_activity.get('idle_seconds'))
        if idle_seconds is not None:
            current_activity.setdefault('idle_seconds', round(idle_seconds, 2))

        total_active_seconds = _coerce_float(legacy_activity.get('total_active_today'))
        if total_active_seconds is not None and 'total_active_hours' not in today_stats:
            today_stats['total_active_hours'] = round(max(total_active_seconds, 0.0) / 3600.0, 2)

        for source_key, target_key in (
            ('keyboard_count', 'keyboard_events'),
            ('mouse_count', 'mouse_events'),
            ('keyboard_events', 'keyboard_events'),
            ('mouse_events', 'mouse_events'),
        ):
            if target_key in today_stats:
                continue
            numeric = _coerce_float(legacy_activity.get(source_key))
            if numeric is not None:
                today_stats[target_key] = max(0, int(numeric))

    if legacy_system:
        cpu = _coerce_float(legacy_system.get('cpu'))
        memory = _coerce_float(legacy_system.get('memory'))
        if cpu is not None:
            system_metrics.setdefault('cpu_percent', cpu)
        if memory is not None:
            system_metrics.setdefault('memory_percent', memory)

        current_app = _clean_string(legacy_system.get('current_app'))
        if current_app and 'current_application' not in current_activity:
            current_activity['current_application'] = current_app

    if network_metrics and not isinstance(system_metrics.get('network_speed'), dict):
        system_metrics['network_speed'] = dict(network_metrics)

    resolved_hostname = _clean_string(device_info.get('hostname')) or _clean_string(hostname)
    if resolved_hostname:
        device_info['hostname'] = resolved_hostname

    normalized = {}
    if current_activity:
        normalized['current_activity'] = current_activity
    if today_stats:
        normalized['today_stats'] = today_stats
    if system_metrics:
        normalized['system_metrics'] = system_metrics
    if network_metrics:
        normalized['network'] = network_metrics
    if device_info:
        normalized['device_info'] = device_info
    if meta:
        normalized['meta'] = meta
    return normalized


def extract_current_stats_payload(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None

    current_stats = payload.get('current_stats')
    if current_stats is None and any(
        key in payload for key in (
            'current_activity',
            'today_stats',
            'system_metrics',
            'device_info',
            'meta',
            'activity',
            'system',
            'network',
        )
    ):
        current_stats = {
            'current_activity': payload.get('current_activity'),
            'today_stats': payload.get('today_stats'),
            'system_metrics': payload.get('system_metrics'),
            'device_info': payload.get('device_info'),
            'meta': payload.get('meta'),
            'activity': payload.get('activity'),
            'system': payload.get('system'),
            'network': payload.get('network'),
        }

    return normalize_current_stats_payload(
        current_stats,
        hostname=str(payload.get('hostname') or '').strip() or None,
    )


def plan_sync_core_mutations(payload: dict, normalized_mac: str, unique_client_id: str | None, now_utc: datetime | None = None) -> SyncCorePlan:
    now_utc = now_utc or datetime.utcnow()
    current_stats = extract_current_stats_payload(payload)

    current_stats_valid = isinstance(current_stats, dict)
    system_metrics = current_stats.get('system_metrics') if current_stats_valid else None
    current_activity = current_stats.get('current_activity') if current_stats_valid else None
    network_metrics = current_stats.get('network') if current_stats_valid else None

    return SyncCorePlan(
        normalized_mac=normalized_mac,
        unique_client_id=unique_client_id,
        dedupe_key=build_sync_dedupe_key(payload, normalized_mac, unique_client_id),
        current_stats_valid=current_stats_valid,
        has_current_activity=bool(current_activity),
        has_system_metrics=bool(system_metrics),
        has_network_metrics=bool(network_metrics),
        restricted_event_count=len(coerce_restricted_events(payload.get('restricted_site_events'))),
        policy_version_seen=str(payload.get('restricted_sites_policy_version') or '').strip(),
        hostname=str(payload.get('hostname') or '').strip() or None,
        generated_at=now_utc,
    )


def apply_sync_core_plan(plan: SyncCorePlan) -> SyncCoreResult:
    return SyncCoreResult(applied=True, summary=plan.to_summary())
