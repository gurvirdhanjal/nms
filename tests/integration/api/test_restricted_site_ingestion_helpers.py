import pytest
from datetime import datetime, timezone
import uuid
from extensions import db
from models.tracked_device import TrackedDevice
from models.user import User
from models.restricted_site_policy import (
    RestrictedSitePolicy,
    RestrictedSiteEvent,
    RestrictedSiteAlertState
)
from models.dashboard import DashboardEvent
from routes.tracking import (
    _coerce_restricted_events,
    _match_restricted_domain,
    _parse_observed_datetime,
    _maybe_uplift_confidence,
    _build_restricted_alert_message,
    _ingest_restricted_site_events_internal,
    RESTRICTED_SOURCE_WINDOW,
    RESTRICTED_SOURCE_DNS
)


def test_coerce_restricted_events():
    assert _coerce_restricted_events(None) == []
    assert _coerce_restricted_events([1, 2, "string"]) == []
    assert _coerce_restricted_events([{"domain": "foo.com"}, "bar"]) == [{"domain": "foo.com"}]


def test_match_restricted_domain():
    blocked = ["reddit.com", "facebook.com"]
    assert _match_restricted_domain("m.reddit.com", blocked) == "reddit.com"
    assert _match_restricted_domain("reddit.com", blocked) == "reddit.com"
    assert _match_restricted_domain("notreddit.com", blocked) is None
    assert _match_restricted_domain(None, blocked) is None
    assert _match_restricted_domain("reddit.com", []) is None


def test_parse_observed_datetime():
    # Pass datetime directly
    now = datetime.utcnow()
    assert _parse_observed_datetime(now) == now
    
    # Valid ISO string
    dt = _parse_observed_datetime("2026-01-01T12:00:00Z")
    assert dt.year == 2026
    assert dt.month == 1
    
    # Invalid string -> fallback to utcnow roughly
    dt_invalid = _parse_observed_datetime("not-a-date")
    assert abs((dt_invalid - datetime.utcnow()).total_seconds()) < 5
    
    # Empty string
    dt_empty = _parse_observed_datetime(None)
    assert abs((dt_empty - datetime.utcnow()).total_seconds()) < 5


def test_maybe_uplift_confidence():
    assert _maybe_uplift_confidence(1, "foo.com", RESTRICTED_SOURCE_WINDOW, datetime.utcnow()) == "HIGH"
    assert _maybe_uplift_confidence(1, "foo.com", RESTRICTED_SOURCE_DNS, datetime.utcnow()) == "LOW"


def test_build_restricted_alert_message():
    msg1 = _build_restricted_alert_message("reddit.com", RESTRICTED_SOURCE_WINDOW, "HIGH", 5)
    assert "window title" in msg1
    assert "hit_count=5" in msg1
    
    msg2 = _build_restricted_alert_message("reddit.com", RESTRICTED_SOURCE_DNS, "LOW", 1)
    assert "DNS" in msg2


@pytest.fixture
def mock_dependencies():
    # Create a device
    device = TrackedDevice(mac_address="AA:BB:CC:DD:EE:FF", device_name="Test Device")
    db.session.add(device)
    db.session.flush()
    
    # Set up policy
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(["reddit.com"])
    policy.enabled = True
    db.session.commit()
    
    yield {
        "device": device,
        "policy": policy
    }
    
    db.session.rollback()


def test_ingest_restricted_site_events_internal(mock_dependencies):
    device = mock_dependencies["device"]
    
    events = [
        {"domain": "reddit.com", "source": RESTRICTED_SOURCE_WINDOW, "raw_evidence": "Reddit - Chrome"},
        {"domain": "safe.com"}, # Should be ignored because not blocked
        "invalid_event"         # Should be ignored
    ]
    
    summary = _ingest_restricted_site_events_internal(device, events)
    
    assert summary["ingested_events"] == 1
    assert summary["alert_updates"] == 1
    assert summary["queued_fanout_tasks"] == 2
    
    # Check DB records
    state = RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain="reddit.com").first()
    assert state is not None
    assert state.hit_count == 1
    assert state.active_dashboard_event_id is not None
    
    event_row = RestrictedSiteEvent.query.filter_by(device_id=device.id).first()
    assert event_row is not None
    assert event_row.matched_rule == "reddit.com"
    assert event_row.confidence == "HIGH"
    
    db_event = DashboardEvent.query.filter_by(event_id=state.active_dashboard_event_id).first()
    assert db_event is not None
    assert db_event.event_type == 'restricted_site'


def test_ingest_restricted_site_events_internal_cooldown_and_update(mock_dependencies):
    device = mock_dependencies["device"]
    policy = mock_dependencies["policy"]
    policy.cooldown_seconds = 900
    
    # Pre-seed the state
    now = datetime.utcnow()
    state = RestrictedSiteAlertState(
        device_id=device.id,
        domain="reddit.com",
        hit_count=1,
        first_seen_at=now,
        last_seen_at=now,
        last_alerted_at=now,
        last_emailed_at=now,
        active_dashboard_event_id="test_event"
    )
    db.session.add(state)
    db.session.commit()
    
    # Ingest event immediately
    summary = _ingest_restricted_site_events_internal(device, [{"domain": "reddit.com", "source": RESTRICTED_SOURCE_DNS}])
    
    assert summary["ingested_events"] == 1
    assert summary["alert_updates"] == 0 # Caught by cooldown
    
    state = RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain="reddit.com").first()
    assert state.hit_count == 2
