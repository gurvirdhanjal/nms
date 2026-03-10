from datetime import datetime

import pytest

from extensions import db
from models.tracking_sync_envelope import TrackingSyncEnvelope
from workers import tracking_sync_worker


pytestmark = pytest.mark.unit


def _envelope(inline_summary_json=None):
    row = TrackingSyncEnvelope(
        normalized_mac='AA:BB:CC:DD:EE:84',
        unique_client_id='client-84',
        tracked_device_id=None,
        payload_json={'hostname': 'sync-worker', 'current_stats': {'system_metrics': {'cpu': 9}}},
        received_at=datetime.utcnow(),
        inline_summary_json=inline_summary_json or {'hostname': 'sync-worker'},
        core_status='pending',
        violation_status='pending',
        shadow_status='pending',
        core_next_run_at=datetime.utcnow(),
        violation_next_run_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_tracking_sync_worker_shadow_mode_records_comparison(monkeypatch):
    envelope = _envelope({'hostname': 'different-host'})
    db.session.commit()
    monkeypatch.setattr(tracking_sync_worker, 'current_sync_mode', lambda: 'shadow')

    processed = tracking_sync_worker.run_once()

    db.session.refresh(envelope)
    assert processed.id == envelope.id
    assert envelope.core_status == 'completed'
    assert envelope.shadow_status == 'completed'
    assert envelope.shadow_mismatches_json['hostname']['inline'] == 'different-host'


def test_tracking_sync_worker_retries_on_apply_failure(monkeypatch):
    envelope = _envelope()
    db.session.commit()
    monkeypatch.setattr(tracking_sync_worker, 'current_sync_mode', lambda: 'async')
    monkeypatch.setattr(tracking_sync_worker, 'apply_sync_core_plan', lambda plan: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(RuntimeError):
        tracking_sync_worker.run_once()

    db.session.refresh(envelope)
    assert envelope.core_status == 'pending'
    assert envelope.core_retry_count == 1
    assert envelope.core_error_code == 'TRACKING_SYNC_CORE_FAILED'


def test_tracking_sync_worker_async_mode_applies_plan(monkeypatch):
    envelope = _envelope()
    db.session.commit()
    monkeypatch.setattr(tracking_sync_worker, 'current_sync_mode', lambda: 'async')

    class Result:
        summary = {'hostname': 'sync-worker', 'applied': True}

    monkeypatch.setattr(tracking_sync_worker, 'apply_sync_core_plan', lambda plan: Result())

    processed = tracking_sync_worker.run_once()

    db.session.refresh(envelope)
    assert processed.id == envelope.id
    assert envelope.core_status == 'completed'
    assert envelope.shadow_status == 'completed'
    assert envelope.shadow_summary_json == {'hostname': 'sync-worker', 'applied': True}


def test_tracking_sync_worker_returns_none_when_queue_is_empty():
    assert tracking_sync_worker.run_once() is None
