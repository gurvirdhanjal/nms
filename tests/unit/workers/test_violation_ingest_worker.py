from datetime import datetime

import pytest

from extensions import db
from models.alert_fanout_task import AlertFanoutTask
from models.restricted_site_policy import RestrictedSiteEvent, RestrictedSitePolicy
from models.tracked_device import TrackedDevice
from models.tracking_sync_envelope import TrackingSyncEnvelope
from workers import violation_ingest_worker


pytestmark = pytest.mark.unit


def _envelope(tracked_device_id=None, payload_json=None):
    row = TrackingSyncEnvelope(
        normalized_mac='AA:BB:CC:DD:EE:85',
        unique_client_id='client-85',
        tracked_device_id=tracked_device_id,
        payload_json=payload_json or {'restricted_site_events': [{'domain': 'example.com', 'source': 'window_title'}]},
        received_at=datetime.utcnow(),
        core_status='pending',
        violation_status='pending',
        shadow_status='pending',
        core_next_run_at=datetime.utcnow(),
        violation_next_run_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_violation_ingest_worker_completes_without_device():
    envelope = _envelope(tracked_device_id=None)
    db.session.commit()

    processed = violation_ingest_worker.run_once()

    db.session.refresh(envelope)
    assert processed.id == envelope.id
    assert envelope.violation_status == 'completed'


def test_violation_ingest_worker_shadow_mode_only_updates_shadow_summary(monkeypatch):
    device = TrackedDevice(mac_address='AA:BB:CC:DD:EE:86', device_name='Shadow Device', availability_status='online')
    db.session.add(device)
    db.session.flush()
    envelope = _envelope(tracked_device_id=device.id)
    db.session.commit()

    monkeypatch.setattr(violation_ingest_worker, 'current_sync_mode', lambda: 'shadow')
    violation_ingest_worker.run_once()

    db.session.refresh(envelope)
    assert envelope.violation_status == 'completed'
    assert envelope.shadow_status == 'completed'
    assert envelope.shadow_summary_json['restricted_event_count_shadow'] == 0
    assert RestrictedSiteEvent.query.count() == 0


def test_violation_ingest_worker_queues_fanout_in_non_shadow_mode():
    device = TrackedDevice(mac_address='AA:BB:CC:DD:EE:87', device_name='Queued Device', availability_status='online')
    db.session.add(device)
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['example.com'])
    db.session.flush()
    envelope = _envelope(tracked_device_id=device.id)
    db.session.commit()

    violation_ingest_worker.run_once()

    db.session.refresh(envelope)
    assert envelope.violation_status == 'completed'
    assert RestrictedSiteEvent.query.filter_by(device_id=device.id).count() == 1
    assert AlertFanoutTask.query.filter_by(tracked_device_id=device.id).count() == 2


def test_violation_ingest_worker_retries_on_failure(monkeypatch):
    device = TrackedDevice(mac_address='AA:BB:CC:DD:EE:88', device_name='Retry Device', availability_status='online')
    db.session.add(device)
    db.session.flush()
    envelope = _envelope(tracked_device_id=device.id)
    db.session.commit()

    monkeypatch.setattr(violation_ingest_worker, 'plan_restricted_site_ingest', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(RuntimeError):
        violation_ingest_worker.run_once()

    db.session.refresh(envelope)
    assert envelope.violation_status == 'pending'
    assert envelope.violation_retry_count == 1
    assert envelope.violation_error_code == 'TRACKING_SYNC_VIOLATION_FAILED'
