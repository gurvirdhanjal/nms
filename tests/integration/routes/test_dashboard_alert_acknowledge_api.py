import pytest

from extensions import db
from models.dashboard import DashboardEvent


pytestmark = pytest.mark.integration


def test_dashboard_alert_acknowledge_route_updates_event(admin_client):
    event = DashboardEvent(
        event_id='evt-dashboard-ack',
        device_ip='10.0.0.100',
        event_type='restricted_site',
        severity='WARNING',
        metric_name='restricted_site:tracked:1:example.com',
        message='Example alert',
        resolved=False,
        is_acknowledged=False,
    )
    db.session.add(event)
    db.session.commit()

    response = admin_client.post('/api/dashboard/alerts/evt-dashboard-ack/acknowledge')

    assert response.status_code == 200
    db.session.refresh(event)
    assert event.is_acknowledged is True
    assert event.acknowledged_at is not None
