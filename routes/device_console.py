from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, redirect, request, session, url_for

from extensions import db
from middleware.rbac import create_audit_log, require_permission
from models.dashboard import DashboardEvent
from models.restricted_site_policy import (
    RestrictedSiteAlertState,
    RestrictedSiteDomainMeta,
    RestrictedSiteEvent,
)
from services.device_console_service import (
    build_policy_payload,
    build_private_cache_headers,
    calculate_risk_score,
    derive_device_state,
    normalize_alert_record,
    normalize_domain_input,
    normalize_policy_category,
    normalize_policy_reason,
    normalize_violation_severity,
    normalize_violation_status,
    risk_level_from_score,
)
from services.effective_policy_service import (
    EffectivePolicyUnavailable,
    enqueue_policy_rebuild,
    get_effective_policy,
)
from services.device_link_service import DeviceLinkService
from services.tracking_workstation import get_scoped_tracked_device_or_404


device_console_bp = Blueprint('device_console_bp', __name__)


def _apply_headers(response, headers: dict) -> None:
    for key, value in (headers or {}).items():
        response.headers[key] = value


def _recent_policy_violations(device_id: int, limit: int = 20) -> list[dict]:
    rows = (
        RestrictedSiteEvent.query.filter(RestrictedSiteEvent.device_id == int(device_id))
        .order_by(RestrictedSiteEvent.observed_at_utc.desc(), RestrictedSiteEvent.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    payload = []
    for row in rows:
        payload.append(
            {
                'domain': row.domain,
                'time': row.observed_at_utc.isoformat() if row.observed_at_utc else None,
                'severity': normalize_violation_severity(getattr(row, 'confidence', None)),
                'source': row.source,
                'user': None,
                'action': 'Blocked',
            }
        )
    return payload


def _policy_mode_for_device(device_id: int) -> str:
    # Device-scoped policy is considered active by default in console workflows.
    return 'active'


def _telemetry_state_for_device(device) -> str:
    status = str(getattr(device, 'availability_status', 'offline') or 'offline').strip().lower()
    if status == 'offline':
        return 'offline'

    last_sync = getattr(device, 'last_agent_sync_at', None)
    if not last_sync:
        return 'stale'

    age = max(0, int((datetime.utcnow() - last_sync).total_seconds()))
    if age <= 60:
        return 'healthy'
    if age <= 180:
        return 'degraded'
    return 'stale'


def _alert_status_from_dashboard_event(dashboard_event: DashboardEvent | None) -> str:
    if not dashboard_event:
        return 'resolved'
    if dashboard_event.resolved:
        return 'resolved'
    if dashboard_event.is_acknowledged:
        return 'acknowledged'
    return 'active'


def _severity_for_state(latest_event: RestrictedSiteEvent | None, dashboard_event: DashboardEvent | None) -> str:
    if latest_event is not None:
        return normalize_violation_severity(getattr(latest_event, 'confidence', None))
    return normalize_violation_severity(getattr(dashboard_event, 'severity', None))


@device_console_bp.route('/api/devices/<int:device_id>/website-policy', methods=['GET'])
@require_permission('tracking.history.view')
def get_device_website_policy(device_id: int):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)

    domain_rows = (
        RestrictedSiteDomainMeta.query.filter(RestrictedSiteDomainMeta.device_id == device.id)
        .order_by(RestrictedSiteDomainMeta.domain.asc())
        .all()
    )
    domain_payload = [row.to_dict() for row in domain_rows]

    today_utc = datetime.utcnow().date()
    violations_today = RestrictedSiteEvent.query.filter(
        RestrictedSiteEvent.device_id == device.id,
        db.func.date(RestrictedSiteEvent.observed_at_utc) == today_utc,
    ).count()

    recent_violations = _recent_policy_violations(device.id, limit=20)
    payload = build_policy_payload(
        mode=_policy_mode_for_device(device.id),
        domains=domain_payload,
        violations_today=violations_today,
        recent_violations=recent_violations,
    )
    try:
        effective_policy = get_effective_policy(int(device.id), allow_rebuild=True)
    except EffectivePolicyUnavailable:
        return jsonify({'success': False, 'error': 'effective_policy_unavailable'}), 503

    payload.update(
        {
            'global_restricted_sites': effective_policy.get('global_restricted_sites', []),
            'effective_restricted_sites': effective_policy.get('effective_restricted_sites', []),
            'effective_policy_version': effective_policy.get('effective_policy_version', ''),
            'agent_policy_version': effective_policy.get('agent_policy_version'),
            'agent_policy_last_seen_at': effective_policy.get('agent_policy_last_seen_at'),
            'policy_cache_state': effective_policy.get('policy_cache_state', 'fresh'),
            'policy_cache_age_seconds': effective_policy.get('policy_cache_age_seconds', 0),
            'policy_stale': bool(effective_policy.get('policy_stale', False)),
            'rebuild_enqueued': bool(effective_policy.get('rebuild_enqueued', False)),
            'identity_link_status': effective_policy.get('identity_link_status', 'unlinked'),
            'linked_inventory_device_id': effective_policy.get('linked_inventory_device_id'),
        }
    )

    response = jsonify({'success': True, 'device_id': int(device.id), **payload})
    latest_updated = domain_rows[-1].updated_at.isoformat() if domain_rows else 'none'
    headers = build_private_cache_headers(
        key_seed=f'device:{device.id}:policy:{latest_updated}:{violations_today}:{payload.get("effective_policy_version", "")}',
        ttl_seconds=10,
    )
    _apply_headers(response, headers)
    return response


@device_console_bp.route('/api/devices/<int:device_id>/website-policy', methods=['POST'])
@require_permission('devices.edit')
def add_device_website_policy(device_id: int):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    payload = request.get_json(silent=True) or {}

    domain = normalize_domain_input(payload.get('domain'))
    if not domain:
        return jsonify({'success': False, 'error': 'domain is required'}), 400

    actor = str(session.get('username') or 'system').strip() or 'system'
    category = normalize_policy_category(payload.get('category'))
    reason = normalize_policy_reason(payload.get('reason'))

    row = RestrictedSiteDomainMeta.query.filter_by(device_id=device.id, domain=domain).first()
    created = False
    if row is None:
        row = RestrictedSiteDomainMeta(
            device_id=device.id,
            domain=domain,
            category=category,
            reason=reason,
            created_by=actor,
            updated_by=actor,
        )
        db.session.add(row)
        created = True
    else:
        row.category = category
        row.reason = reason
        row.updated_by = actor

    enqueue_policy_rebuild(int(device.id))

    db.session.commit()

    create_audit_log(
        action='create' if created else 'update',
        entity_type='device_website_policy',
        entity_id=row.id,
        entity_name=domain,
        description=f'Website policy domain {"added" if created else "updated"} for device {device.id}',
        changes={
            'device_id': int(device.id),
            'domain': domain,
            'category': category,
            'reason': reason,
            'created': bool(created),
        },
    )

    status_code = 201 if created else 200
    return jsonify(
        {
            'success': True,
            'message': 'Domain added to policy' if created else 'Policy updated',
            'device_id': int(device.id),
            'domain': domain,
            'category': category,
            'reason': reason,
            'created': bool(created),
        }
    ), status_code


@device_console_bp.route('/api/devices/<int:device_id>/website-policy', methods=['DELETE'])
@require_permission('devices.edit')
def remove_device_website_policy(device_id: int):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    payload = request.get_json(silent=True) or {}

    raw_domains = payload.get('domains') if isinstance(payload.get('domains'), list) else []
    domains = sorted({domain for domain in (normalize_domain_input(item) for item in raw_domains) if domain})
    if not domains:
        return jsonify({'success': False, 'error': 'domains list is required'}), 400

    deleted = (
        RestrictedSiteDomainMeta.query.filter(
            RestrictedSiteDomainMeta.device_id == device.id,
            RestrictedSiteDomainMeta.domain.in_(domains),
        ).delete(synchronize_session=False)
    )
    enqueue_policy_rebuild(int(device.id))
    db.session.commit()

    create_audit_log(
        action='delete',
        entity_type='device_website_policy',
        entity_id=device.id,
        entity_name=f'device:{device.id}',
        description='Removed restricted domains from device policy',
        changes={'device_id': int(device.id), 'domains': domains, 'deleted': int(deleted)},
    )

    return jsonify({'success': True, 'device_id': int(device.id), 'deleted': int(deleted), 'domains': domains})


@device_console_bp.route('/api/devices/<int:device_id>/alerts', methods=['GET'])
@require_permission('tracking.history.view')
def get_device_alerts(device_id: int):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)

    states = (
        RestrictedSiteAlertState.query.filter(RestrictedSiteAlertState.device_id == device.id)
        .order_by(RestrictedSiteAlertState.last_seen_at.desc().nullslast(), RestrictedSiteAlertState.id.desc())
        .limit(100)
        .all()
    )

    dashboard_event_ids = sorted(
        {
            str(state.active_dashboard_event_id).strip()
            for state in states
            if state.active_dashboard_event_id
        }
    )
    dashboard_events = {}
    if dashboard_event_ids:
        rows = DashboardEvent.query.filter(DashboardEvent.event_id.in_(dashboard_event_ids)).all()
        dashboard_events = {str(row.event_id): row for row in rows}

    alerts = []
    active_count = 0

    for state in states:
        latest_event = (
            RestrictedSiteEvent.query.filter(
                RestrictedSiteEvent.device_id == device.id,
                RestrictedSiteEvent.domain == state.domain,
            )
            .order_by(RestrictedSiteEvent.observed_at_utc.desc(), RestrictedSiteEvent.id.desc())
            .first()
        )

        dashboard_event = dashboard_events.get(str(state.active_dashboard_event_id or '').strip())
        status = _alert_status_from_dashboard_event(dashboard_event)
        if status != 'resolved':
            active_count += 1

        observed = (
            latest_event.observed_at_utc if latest_event is not None else None
        ) or state.last_seen_at or (dashboard_event.timestamp if dashboard_event is not None else None)

        alerts.append(
            normalize_alert_record(
                domain=state.domain,
                user=getattr(device, 'employee_name', None),
                severity=_severity_for_state(latest_event, dashboard_event),
                timestamp=observed,
                event_id=(dashboard_event.event_id if dashboard_event is not None else state.active_dashboard_event_id),
                status=status,
                action='Blocked',
                device_name=getattr(device, 'device_name', None),
                source=(latest_event.source if latest_event is not None else None),
                hit_count=state.hit_count,
            )
        )

    alerts.sort(key=lambda item: item.get('time') or '', reverse=True)

    telemetry_state = _telemetry_state_for_device(device)
    risk_score = calculate_risk_score(
        alerts=alerts,
        telemetry_state=telemetry_state,
        policy_violations=active_count,
        suspicious_processes=0,
    )
    risk_level = risk_level_from_score(risk_score)
    device_state = derive_device_state(
        connectivity=getattr(device, 'availability_status', 'offline'),
        telemetry=telemetry_state,
        policy_violations=active_count,
        suspicious_processes=0,
        alerts=alerts,
    )

    highest_severity = 'Low'
    for alert in alerts:
        severity = str(alert.get('severity') or 'Low').lower()
        if severity == 'high':
            highest_severity = 'High'
            break
        if severity == 'medium' and highest_severity != 'High':
            highest_severity = 'Medium'

    response = jsonify(
        {
            'success': True,
            'device_id': int(device.id),
            'active_alert_count': int(active_count),
            'highest_severity': highest_severity,
            'risk_score': int(risk_score),
            'risk_level': risk_level,
            'device_state': device_state,
            'alerts': alerts,
        }
    )
    _apply_headers(
        response,
        build_private_cache_headers(
            key_seed=f'device:{device.id}:alerts:{active_count}:{risk_score}:{len(alerts)}',
            ttl_seconds=10,
        ),
    )
    return response


@device_console_bp.route('/api/devices/<int:device_id>/alerts/<event_id>/acknowledge', methods=['POST'])
@require_permission('devices.edit')
def acknowledge_device_alert(device_id: int, event_id: str):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)

    event = DashboardEvent.query.filter(DashboardEvent.event_id == str(event_id)).first()
    if event is None:
        return jsonify({'success': False, 'error': 'Alert not found'}), 404

    linked_state = RestrictedSiteAlertState.query.filter(
        RestrictedSiteAlertState.device_id == device.id,
        RestrictedSiteAlertState.active_dashboard_event_id == str(event_id),
    ).first()
    metric_name = str(event.metric_name or '')
    expected_prefix = f'restricted_site:tracked:{device.id}:'
    if linked_state is None and not metric_name.startswith(expected_prefix):
        return jsonify({'success': False, 'error': 'Alert does not belong to this device'}), 404

    if not event.is_acknowledged:
        event.is_acknowledged = True
        event.acknowledged_at = datetime.utcnow()
        event.acknowledged_by = str(session.get('username') or 'system').strip() or 'system'
        db.session.commit()

    return jsonify(
        {
            'success': True,
            'device_id': int(device.id),
            'event_id': str(event_id),
            'status': normalize_violation_status('acknowledged'),
            'acknowledged_at': event.acknowledged_at.isoformat() if event.acknowledged_at else None,
            'acknowledged_by': event.acknowledged_by,
        }
    )


@device_console_bp.route('/devices/<int:device_id>/policy-history', methods=['GET'])
@require_permission('tracking.history.view')
def device_policy_history_redirect(device_id: int):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    return redirect(url_for('tracking_bp.device_history', device_id=device.id, focus='policy'))
