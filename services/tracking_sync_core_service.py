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


def plan_sync_core_mutations(payload: dict, normalized_mac: str, unique_client_id: str | None, now_utc: datetime | None = None) -> SyncCorePlan:
    now_utc = now_utc or datetime.utcnow()
    current_stats = payload.get('current_stats')
    if current_stats is None and any(key in payload for key in ('current_activity', 'today_stats', 'system_metrics', 'device_info', 'meta')):
        current_stats = {
            'current_activity': payload.get('current_activity'),
            'today_stats': payload.get('today_stats'),
            'system_metrics': payload.get('system_metrics'),
            'device_info': payload.get('device_info'),
            'meta': payload.get('meta'),
        }

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
