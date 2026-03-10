from datetime import datetime, timedelta

import pytest

from extensions import db
from models.alert_fanout_task import AlertFanoutTask
from models.tracked_device import TrackedDevice
from workers import alert_fanout_worker


pytestmark = pytest.mark.unit


def _tracked_device():
    row = TrackedDevice(mac_address='AA:BB:CC:DD:EE:81', device_name='Alert Worker Device')
    db.session.add(row)
    db.session.flush()
    return row


def test_run_once_delivers_sse_task():
    tracked = _tracked_device()
    task = AlertFanoutTask(
        dashboard_event_id='evt-sse',
        tracked_device_id=tracked.id,
        channel='sse',
        delivery_key='delivery-sse',
        payload_json={'message': 'hello'},
        status='pending',
        next_run_at=datetime.utcnow(),
    )
    db.session.add(task)
    db.session.commit()

    seen = []
    processed = alert_fanout_worker.run_once(sse_sender=lambda payload: seen.append(payload))

    db.session.refresh(task)
    assert processed.id == task.id
    assert seen[0]['delivery_key'] == 'delivery-sse'
    assert task.status == 'completed'


def test_run_once_delivers_email_task_and_sets_message_id():
    tracked = _tracked_device()
    task = AlertFanoutTask(
        dashboard_event_id='evt-email',
        tracked_device_id=tracked.id,
        channel='email',
        delivery_key='delivery-email',
        payload_json={'metric_name': 'restricted_site'},
        status='pending',
        next_run_at=datetime.utcnow(),
    )
    db.session.add(task)
    db.session.commit()

    seen = []
    alert_fanout_worker.run_once(email_sender=lambda task_obj, payload: seen.append((task_obj.id, payload['message_id'])))

    db.session.refresh(task)
    assert seen[0][0] == task.id
    assert task.provider_message_id == '<delivery-email@device-monitoring-tactical.local>'
    assert task.status == 'completed'


def test_run_once_retries_failed_task_and_reclaims_stale_claim():
    tracked = _tracked_device()
    stale = AlertFanoutTask(
        dashboard_event_id='evt-stale',
        tracked_device_id=tracked.id,
        channel='sse',
        delivery_key='delivery-stale',
        payload_json={'message': 'stale'},
        status='running',
        claim_token='expired',
        claim_expires_at=datetime.utcnow() - timedelta(seconds=1),
        next_run_at=datetime.utcnow() - timedelta(seconds=5),
    )
    pending = AlertFanoutTask(
        dashboard_event_id='evt-pending',
        tracked_device_id=tracked.id,
        channel='sse',
        delivery_key='delivery-pending',
        payload_json={'message': 'pending'},
        status='pending',
        next_run_at=datetime.utcnow() - timedelta(seconds=5),
    )
    db.session.add_all([stale, pending])
    db.session.commit()

    with pytest.raises(RuntimeError):
        alert_fanout_worker.run_once(sse_sender=lambda payload: (_ for _ in ()).throw(RuntimeError('send failed')))

    db.session.refresh(stale)
    db.session.refresh(pending)
    assert stale.status == 'pending'
    assert stale.retry_count >= 1
    assert pending.error_code == 'ALERT_FANOUT_FAILED'


def test_default_sse_sender_and_default_email_sender(monkeypatch):
    tracked = _tracked_device()
    task = AlertFanoutTask(
        dashboard_event_id='evt-default-email',
        tracked_device_id=tracked.id,
        channel='email',
        delivery_key='delivery-default-email',
        payload_json={'metric_name': 'restricted_site', 'message': 'hello'},
        status='pending',
        next_run_at=datetime.utcnow(),
    )
    db.session.add(task)
    db.session.commit()

    events = []
    emails = []

    class NotificationStub:
        @staticmethod
        def send_warning_alert(device_arg, metric=None, value=None, message=None):
            emails.append((device_arg.id, metric, value, message))

    monkeypatch.setattr('services.sse_broadcaster.broadcast_event', lambda event_name, payload: events.append((event_name, payload)))
    monkeypatch.setattr('services.notification_service.NotificationService', NotificationStub)

    alert_fanout_worker._default_sse_sender({'message': 'broadcast'})
    alert_fanout_worker._default_email_sender(task, {'metric_name': 'restricted_site', 'value': 2, 'message': 'hello'})

    assert events == [('alert_created', {'message': 'broadcast'})]
    assert emails == [(tracked.id, 'restricted_site', 2, 'hello')]


def test_default_email_sender_raises_for_missing_tracked_device():
    task = AlertFanoutTask(
        dashboard_event_id='evt-missing-device',
        tracked_device_id=999999,
        channel='email',
        delivery_key='delivery-missing-device',
        payload_json={},
        status='pending',
        next_run_at=datetime.utcnow(),
    )

    with pytest.raises(ValueError, match='tracked device not found'):
        alert_fanout_worker._default_email_sender(task, {})


def test_deliver_alert_fanout_task_rejects_unsupported_channel():
    task = AlertFanoutTask(
        dashboard_event_id='evt-sms',
        tracked_device_id=123,
        channel='sms',
        delivery_key='delivery-sms',
        payload_json={},
        status='pending',
        next_run_at=datetime.utcnow(),
    )

    with pytest.raises(ValueError, match='unsupported fanout channel'):
        alert_fanout_worker.deliver_alert_fanout_task(task)


def test_run_once_returns_none_when_queue_is_empty():
    assert alert_fanout_worker.run_once() is None
