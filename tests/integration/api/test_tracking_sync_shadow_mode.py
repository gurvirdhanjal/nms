from datetime import datetime

import pytest

from extensions import db
from models.restricted_site_policy import RestrictedSiteEvent
from models.tracked_device import TrackingSample
from models.tracking_sync_envelope import TrackingSyncEnvelope
from workers import tracking_sync_worker, violation_ingest_worker


pytestmark = pytest.mark.integration


def test_shadow_mode_workers_only_update_shadow_fields(monkeypatch):
    envelope = TrackingSyncEnvelope(
        normalized_mac='AA:BB:CC:DD:EE:94',
        unique_client_id='shadow-client',
        tracked_device_id=None,
        payload_json={'hostname': 'shadow-host', 'restricted_site_events': [{'domain': 'example.com'}]},
        received_at=datetime.utcnow(),
        inline_summary_json={'hostname': 'inline-host'},
        shadow_status='pending',
        core_status='pending',
        violation_status='pending',
        core_next_run_at=datetime.utcnow(),
        violation_next_run_at=datetime.utcnow(),
    )
    db.session.add(envelope)
    db.session.commit()

    monkeypatch.setattr(tracking_sync_worker, 'current_sync_mode', lambda: 'shadow')
    monkeypatch.setattr(violation_ingest_worker, 'current_sync_mode', lambda: 'shadow')

    tracking_sync_worker.run_once()
    violation_ingest_worker.run_once()

    db.session.refresh(envelope)
    assert envelope.shadow_status == 'completed'
    assert envelope.core_status == 'completed'
    assert envelope.violation_status == 'completed'
    assert envelope.shadow_summary_json is not None
    assert envelope.shadow_mismatches_json is not None
    assert TrackingSample.query.count() == 0
    assert RestrictedSiteEvent.query.count() == 0
