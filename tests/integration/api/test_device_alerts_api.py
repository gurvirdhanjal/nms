import uuid
from datetime import datetime, timedelta

import pytest

from extensions import db
from models.dashboard import DashboardEvent
from models.restricted_site_policy import RestrictedSiteAlertState, RestrictedSiteEvent
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def _seed_alert_device():
    device = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:20',
        device_name='Tracked-Alert-Device',
        employee_name='gurvir',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()
    return device


def _seed_telemetry_device(status='online', sync_delta_seconds=None):
    last_sync = None
    if sync_delta_seconds is not None:
        last_sync = datetime.utcnow() - timedelta(seconds=sync_delta_seconds)
    device = TrackedDevice(
        mac_address=str(uuid.uuid4())[:17],
        device_name='Telemetry-Device',
        employee_name='telemetry-user',
        availability_status=status,
        last_agent_sync_at=last_sync,
    )
    db.session.add(device)
    db.session.commit()
    return device


def test_device_alerts_endpoint_returns_normalized_cards(admin_client):
    device = _seed_alert_device()
    event_id = str(uuid.uuid4())

    dashboard_event = DashboardEvent(
        event_id=event_id,
        event_type='restricted_site',
        severity='WARNING',
        metric_name=f'restricted_site:tracked:{device.id}:youtube.com',
        message='Restricted domain detected',
        resolved=False,
        is_acknowledged=False,
        timestamp=datetime.utcnow(),
    )
    state = RestrictedSiteAlertState(
        device_id=device.id,
        domain='youtube.com',
        hit_count=4,
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        active_dashboard_event_id=event_id,
    )
    event = RestrictedSiteEvent(
        device_id=device.id,
        domain='youtube.com',
        matched_rule='youtube.com',
        source='dns_cache',
        confidence='MEDIUM',
        policy_version='v1',
        observed_at_utc=datetime.utcnow(),
    )
    db.session.add(dashboard_event)
    db.session.add(state)
    db.session.add(event)
    db.session.commit()

    response = admin_client.get(f'/api/devices/{device.id}/alerts')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['success'] is True
    assert payload['active_alert_count'] == 1
    assert payload['risk_level'] in {'low', 'medium', 'high'}
    assert len(payload['alerts']) == 1
    alert = payload['alerts'][0]
    assert alert['title'] == 'Policy Violation'
    assert alert['domain'] == 'youtube.com'
    assert alert['status'] == 'active'


def test_device_alerts_status_resolution_and_high_severity(admin_client):
    device = _seed_alert_device()
    resolved_id = str(uuid.uuid4())
    acked_id = str(uuid.uuid4())

    db.session.add_all(
        [
            DashboardEvent(
                event_id=resolved_id,
                event_type='restricted_site',
                severity='LOW',
                metric_name=f'restricted_site:tracked:{device.id}:resolved.com',
                message='Resolved violation',
                resolved=True,
                is_acknowledged=False,
                timestamp=datetime.utcnow(),
            ),
            DashboardEvent(
                event_id=acked_id,
                event_type='restricted_site',
                severity='HIGH',
                metric_name=f'restricted_site:tracked:{device.id}:acked.com',
                message='Acknowledged violation',
                resolved=False,
                is_acknowledged=True,
                timestamp=datetime.utcnow(),
            ),
            RestrictedSiteAlertState(
                device_id=device.id,
                domain='orphaned.com',
                hit_count=1,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                active_dashboard_event_id=None,
            ),
            RestrictedSiteAlertState(
                device_id=device.id,
                domain='resolved.com',
                hit_count=2,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                active_dashboard_event_id=resolved_id,
            ),
            RestrictedSiteAlertState(
                device_id=device.id,
                domain='acked.com',
                hit_count=3,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                active_dashboard_event_id=acked_id,
            ),
        ]
    )
    db.session.commit()

    response = admin_client.get(f'/api/devices/{device.id}/alerts')
    assert response.status_code == 200
    payload = response.get_json()

    by_domain = {row['domain']: row for row in payload['alerts']}
    assert by_domain['orphaned.com']['status'] == 'resolved'
    assert by_domain['resolved.com']['status'] == 'resolved'
    assert by_domain['acked.com']['status'] == 'acknowledged'
    assert payload['highest_severity'] == 'High'


@pytest.mark.parametrize(
    'availability,sync_delta,expected',
    [
        ('offline', None, 'offline'),
        ('online', 30, 'healthy'),
        ('online', 120, 'degraded'),
        ('online', 400, 'stale'),
    ],
)
def test_device_alerts_telemetry_state_mapping(admin_client, availability, sync_delta, expected):
    device = _seed_telemetry_device(status=availability, sync_delta_seconds=sync_delta)

    response = admin_client.get(f'/api/devices/{device.id}/alerts')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['device_state']['telemetry'] == expected
