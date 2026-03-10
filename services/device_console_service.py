from __future__ import annotations

from datetime import datetime
from typing import Iterable

from models.restricted_site_policy import normalize_domain

_ALLOWED_POLICY_CATEGORIES = {'productivity': 'Productivity', 'security': 'Security', 'custom': 'Custom'}
_SEVERITY_WEIGHTS = {'low': 8, 'medium': 22, 'high': 45}


def normalize_domain_input(value: object) -> str | None:
    """Normalize inbound domains for policy mutations."""
    return normalize_domain(value)


def normalize_policy_category(value: object) -> str:
    key = str(value or '').strip().lower()
    return _ALLOWED_POLICY_CATEGORIES.get(key, 'Custom')


def normalize_policy_reason(value: object) -> str:
    text = str(value or '').strip()
    return text[:500]


def normalize_violation_severity(value: object) -> str:
    raw = str(value or '').strip().upper()
    if raw in {'CRITICAL', 'HIGH'}:
        return 'High'
    if raw in {'WARNING', 'MEDIUM'}:
        return 'Medium'
    return 'Low'


def normalize_violation_status(value: object) -> str:
    raw = str(value or '').strip().lower()
    if raw in {'ack', 'acked', 'acknowledged'}:
        return 'acknowledged'
    if raw in {'resolved', 'closed'}:
        return 'resolved'
    return 'active'


def build_policy_payload(*, mode: str, domains: Iterable[dict], violations_today: int, recent_violations: Iterable[dict]) -> dict:
    rows = list(domains or [])
    recent = list(recent_violations or [])
    restricted_sites = [str(row.get('domain') or '').strip() for row in rows if str(row.get('domain') or '').strip()]
    return {
        'mode': str(mode or 'active').strip().lower() or 'active',
        'restricted_sites': restricted_sites,
        'restricted_site_meta': rows,
        'violations_today': max(0, int(violations_today or 0)),
        'recent_violations': recent,
    }


def normalize_alert_record(*, domain: str, user: str | None, severity: str, timestamp: datetime | None, event_id: str | None,
                           status: str = 'active', action: str = 'Blocked', device_name: str | None = None,
                           source: str | None = None, hit_count: int | None = None) -> dict:
    severity_label = normalize_violation_severity(severity)
    status_label = normalize_violation_status(status)
    return {
        'event_id': str(event_id or ''),
        'type': 'policy_violation',
        'title': 'Policy Violation',
        'device': str(device_name or '').strip() or None,
        'domain': str(domain or '').strip() or 'unknown',
        'site': str(domain or '').strip() or 'unknown',
        'user': str(user or '').strip() or 'unknown',
        'severity': severity_label,
        'status': status_label,
        'source': str(source or '').strip() or None,
        'hit_count': max(0, int(hit_count or 0)),
        'action': str(action or 'Blocked').strip() or 'Blocked',
        'time': timestamp.isoformat() if isinstance(timestamp, datetime) else None,
    }


def calculate_risk_score(*, alerts: Iterable[dict], suspicious_processes: int = 0, telemetry_state: str = 'healthy',
                         policy_violations: int = 0) -> int:
    score = 0
    for alert in list(alerts or []):
        severity_key = str(alert.get('severity') or '').strip().lower()
        score += _SEVERITY_WEIGHTS.get(severity_key, _SEVERITY_WEIGHTS['low'])

    score += max(0, int(policy_violations or 0)) * 7
    score += max(0, int(suspicious_processes or 0)) * 6

    telemetry_key = str(telemetry_state or '').strip().lower()
    if telemetry_key in {'critical', 'offline'}:
        score += 30
    elif telemetry_key in {'degraded', 'partial', 'stale'}:
        score += 15

    return min(100, max(0, int(score)))


def risk_level_from_score(score: int | float) -> str:
    value = max(0, min(100, int(score or 0)))
    if value >= 70:
        return 'high'
    if value >= 35:
        return 'medium'
    return 'low'


def derive_device_state(*, connectivity: str, telemetry: str, policy_violations: int, suspicious_processes: int,
                        alerts: Iterable[dict] | None = None) -> dict:
    alerts_list = list(alerts or [])
    risk_score = calculate_risk_score(
        alerts=alerts_list,
        suspicious_processes=suspicious_processes,
        telemetry_state=telemetry,
        policy_violations=policy_violations,
    )
    return {
        'connectivity': str(connectivity or 'offline').strip().lower() or 'offline',
        'telemetry': str(telemetry or 'stale').strip().lower() or 'stale',
        'policy': 'violations' if int(policy_violations or 0) > 0 else 'compliant',
        'risk': risk_level_from_score(risk_score),
        'risk_score': risk_score,
    }


def build_private_cache_headers(*, key_seed: str, ttl_seconds: int = 10) -> dict:
    safe_ttl = max(1, int(ttl_seconds or 10))
    etag = f'"{str(key_seed).strip()}"'
    return {
        'Cache-Control': f'private, max-age={safe_ttl}',
        'ETag': etag,
    }
