import uuid
from datetime import datetime

import pytest

from extensions import db
from models.dashboard import DashboardEvent
from models.restricted_site_policy import RestrictedSiteAlertState
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def test_acknowledge_alert_marks_dashboard_event(admin_client):
    device = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:30',
        device_name='Tracked-Ack-Device',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()

    event_id = str(uuid.uuid4())
    dashboard_event = DashboardEvent(
        event_id=event_id,
        event_type='restricted_site',
        severity='WARNING',
        metric_name=f'restricted_site:tracked:{device.id}:example.com',
        message='Restricted domain detected',
        resolved=False,
        is_acknowledged=False,
        timestamp=datetime.utcnow(),
    )
    state = RestrictedSiteAlertState(
        device_id=device.id,
        domain='example.com',
        hit_count=1,
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        active_dashboard_event_id=event_id,
    )
    db.session.add(dashboard_event)
    db.session.add(state)
    db.session.commit()

    response = admin_client.post(f'/api/devices/{device.id}/alerts/{event_id}/acknowledge')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['status'] == 'acknowledged'

    db.session.refresh(dashboard_event)
    assert dashboard_event.is_acknowledged is True
    assert dashboard_event.acknowledged_by == 'test-admin'


def test_acknowledge_alert_returns_404_for_missing_event(admin_client):
    device = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:31',
        device_name='Tracked-Ack-Missing',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()

    response = admin_client.post(f'/api/devices/{device.id}/alerts/missing-event/acknowledge')
    assert response.status_code == 404
    payload = response.get_json()
    assert payload['success'] is False


def test_acknowledge_alert_rejects_event_from_other_device(admin_client):
    target = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:32',
        device_name='Tracked-Ack-Target',
        availability_status='online',
    )
    other = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:33',
        device_name='Tracked-Ack-Other',
        availability_status='online',
    )
    db.session.add_all([target, other])
    db.session.commit()

    event_id = str(uuid.uuid4())
    dashboard_event = DashboardEvent(
        event_id=event_id,
        event_type='restricted_site',
        severity='HIGH',
        metric_name=f'restricted_site:tracked:{other.id}:foreign.com',
        message='Foreign device event',
        resolved=False,
        is_acknowledged=False,
        timestamp=datetime.utcnow(),
    )
    db.session.add(dashboard_event)
    db.session.commit()

    response = admin_client.post(f'/api/devices/{target.id}/alerts/{event_id}/acknowledge')
    assert response.status_code == 404
    payload = response.get_json()
    assert payload['success'] is False
