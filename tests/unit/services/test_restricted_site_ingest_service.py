import builtins
from datetime import datetime, timedelta, timezone

import pytest

from extensions import db
from models.alert_fanout_task import AlertFanoutTask
from models.dashboard import DashboardEvent
from models.restricted_site_policy import (
    RestrictedSiteAlertState,
    RestrictedSiteDomainMeta,
    RestrictedSiteEvent,
    RestrictedSitePolicy,
)
from models.tracked_device import TrackedDevice
from services import restricted_site_ingest_service as service


pytestmark = pytest.mark.unit


def _tracked_device(mac='AA:BB:CC:DD:EE:51', maintenance_mode=False):
    device = TrackedDevice(
        mac_address=mac,
        device_name='Ingest Device',
        availability_status='online',
        maintenance_mode=maintenance_mode,
    )
    db.session.add(device)
    db.session.flush()
    return device


def test_helper_functions_cover_parse_match_and_message_paths():
    assert service.coerce_restricted_events(None) == []
    assert service.coerce_restricted_events({'events': [{'domain': 'example.com'}, 'bad']}) == [{'domain': 'example.com'}]
    assert service.match_restricted_domain('sub.example.com', ['example.com']) == 'example.com'
    assert service.match_restricted_domain('safe.example', ['example.com']) is None
    assert service.parse_observed_datetime('2026-03-06T10:00:00Z').year == 2026
    assert 'hit_count=2' in service.build_restricted_alert_message('example.com', service.RESTRICTED_SOURCE_DNS, 'LOW', 2)


def test_helper_functions_cover_timezone_invalid_and_empty_paths():
    aware = datetime(2026, 3, 6, 10, 0, 0, tzinfo=timezone.utc)
    parsed = service.parse_observed_datetime(aware)
    assert parsed.tzinfo is None
    assert service.parse_observed_datetime('not-a-date')
    assert service.match_restricted_domain('', ['example.com']) is None
    assert service.match_restricted_domain('example.com', ['', 'example.com']) == 'example.com'


def test_plan_restricted_site_ingest_filters_and_merges_domains():
    device = _tracked_device()
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['global.example'])
    db.session.add(RestrictedSiteDomainMeta(device_id=device.id, domain='device.example', category='Custom'))
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [
            {'domain': 'global.example', 'source': 'window_title'},
            {'domain': 'sub.device.example', 'source': 'dns_cache'},
            {'domain': 'safe.example', 'source': 'dns_cache'},
        ],
        binding_key_id='bind-1',
        policy=policy,
        now_utc=datetime(2026, 3, 6, 10, 0, 0),
    )

    assert plan.device_id == device.id
    assert plan.binding_key_id == 'bind-1'
    assert plan.blocked_domains == ['device.example', 'global.example']
    assert len(plan.items) == 2
    assert {item.matched_rule for item in plan.items} == {'device.example', 'global.example'}


def test_plan_restricted_site_ingest_skips_invalid_domains_and_normalizes_bad_source():
    device = _tracked_device('AA:BB:CC:DD:EE:57')
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['example.com'])
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [
            {'domain': '', 'source': 'window_title'},
            {'domain': 'example.com', 'source': 'not-a-real-source'},
        ],
        policy=policy,
    )

    assert len(plan.items) == 1
    assert plan.items[0].source == service.RESTRICTED_SOURCE_DNS


def test_maybe_uplift_confidence_uses_corroborating_prior_event():
    device = _tracked_device('AA:BB:CC:DD:EE:52')
    prior = RestrictedSiteEvent(
        device_id=device.id,
        domain='example.com',
        matched_rule='example.com',
        source=service.RESTRICTED_SOURCE_WINDOW,
        confidence='HIGH',
        policy_version='v1',
        observed_at_utc=datetime.utcnow() - timedelta(seconds=30),
    )
    db.session.add(prior)
    db.session.commit()

    confidence = service.maybe_uplift_confidence(
        device.id,
        'example.com',
        service.RESTRICTED_SOURCE_DNS,
        datetime.utcnow(),
    )

    assert confidence == service.RESTRICTED_CONFIDENCE_MEDIUM


def test_maybe_uplift_confidence_keeps_window_events_high_when_corroborated():
    device = _tracked_device('AA:BB:CC:DD:EE:58')
    prior = RestrictedSiteEvent(
        device_id=device.id,
        domain='example.com',
        matched_rule='example.com',
        source=service.RESTRICTED_SOURCE_DNS,
        confidence='LOW',
        policy_version='v1',
        observed_at_utc=datetime.utcnow() - timedelta(seconds=30),
    )
    db.session.add(prior)
    db.session.commit()

    confidence = service.maybe_uplift_confidence(
        device.id,
        'example.com',
        service.RESTRICTED_SOURCE_WINDOW,
        datetime.utcnow(),
    )

    assert confidence == service.RESTRICTED_CONFIDENCE_HIGH


def test_apply_restricted_site_ingest_queues_fanout_tasks():
    device = _tracked_device('AA:BB:CC:DD:EE:53')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['example.com'])
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'example.com', 'source': 'window_title', 'process_name': 'chrome.exe'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 11, 0, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='queued')
    db.session.commit()

    assert result.ingested_events == 1
    assert result.alert_updates == 1
    assert result.queued_fanout_tasks == 2
    assert RestrictedSiteEvent.query.filter_by(device_id=device.id, domain='example.com').count() == 1
    assert RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain='example.com').count() == 1
    assert DashboardEvent.query.filter_by(metric_name=f'restricted_site:tracked:{device.id}:example.com').count() == 1
    assert AlertFanoutTask.query.filter_by(tracked_device_id=device.id).count() == 2


def test_apply_restricted_site_ingest_inline_mode_sends_sse_and_email(monkeypatch):
    device = _tracked_device('AA:BB:CC:DD:EE:54')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['inline.example'])
    db.session.commit()

    sent = {'events': [], 'emails': []}

    class NotificationStub:
        @staticmethod
        def send_warning_alert(device_arg, metric=None, value=None, message=None):
            sent['emails'].append((device_arg.id, metric, value, message))

    monkeypatch.setattr('services.notification_service.NotificationService', NotificationStub)
    monkeypatch.setattr('services.sse_broadcaster.broadcast_event', lambda event_name, payload: sent['events'].append((event_name, payload)))

    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'inline.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 12, 0, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='inline')

    assert result.ingested_events == 1
    assert result.emails_sent == 1
    assert sent['events'][0][0] == 'alert_created'
    assert sent['emails'][0][0] == device.id


def test_apply_restricted_site_ingest_works_without_device_link_service_import(monkeypatch):
    device = _tracked_device('AA:BB:CC:DD:EE:59')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['nolink.example'])
    db.session.commit()

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'services.device_link_service':
            raise ImportError('blocked for test')
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'nolink.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 12, 30, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='none')

    dashboard_event = DashboardEvent.query.filter_by(metric_name=f'restricted_site:tracked:{device.id}:nolink.example').one()
    assert result.ingested_events == 1
    assert dashboard_event.device_id is None


def test_apply_restricted_site_ingest_raises_for_missing_tracked_device():
    plan = service.RestrictedSiteIngestPlan(
        device_id=999999,
        binding_key_id=None,
        policy_version='v1',
        blocked_domains=['missing.example'],
        cooldown_seconds=900,
        now_utc=datetime.utcnow(),
        items=(
            service.RestrictedSiteEventPlanItem(
                observed_domain='missing.example',
                matched_rule='missing.example',
                source=service.RESTRICTED_SOURCE_WINDOW,
                confidence='HIGH',
                process_name=None,
                raw_evidence=None,
                observed_at=datetime.utcnow(),
            ),
        ),
    )

    with pytest.raises(ValueError, match='tracked device not found'):
        service.apply_restricted_site_ingest(plan, fanout_mode='none')


def test_apply_restricted_site_ingest_updates_existing_alert_and_sets_first_seen():
    device = _tracked_device('AA:BB:CC:DD:EE:5A')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['existing.example'])
    db.session.commit()

    first_plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'existing.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 11, 0, 0),
    )
    service.apply_restricted_site_ingest(first_plan, fanout_mode='none')
    existing_event = DashboardEvent.query.filter_by(metric_name=f'restricted_site:tracked:{device.id}:existing.example').one()
    state = RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain='existing.example').one()
    state.first_seen_at = None
    state.last_alerted_at = datetime(2026, 3, 6, 9, 0, 0)
    state.last_emailed_at = None
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'existing.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 13, 0, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='none')

    db.session.refresh(existing_event)
    db.session.refresh(state)
    assert result.alert_updates == 1
    assert state.first_seen_at == plan.items[0].observed_at
    assert existing_event.event_id == state.active_dashboard_event_id
    assert existing_event.severity == 'WARNING'
    assert 'existing.example' in existing_event.message
    assert existing_event.message != 'old message'
    assert existing_event.value >= 1.0


def test_apply_restricted_site_ingest_updates_existing_alert_without_emitting_new_fanout():
    device = _tracked_device('AA:BB:CC:DD:EE:5B')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['cooldown.example'])
    db.session.commit()

    first_plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'cooldown.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 12, 0, 0),
    )
    service.apply_restricted_site_ingest(first_plan, fanout_mode='none')
    existing_event = DashboardEvent.query.filter_by(metric_name=f'restricted_site:tracked:{device.id}:cooldown.example').one()
    state = RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain='cooldown.example').one()
    state.last_alerted_at = datetime(2026, 3, 6, 12, 59, 30)
    state.last_emailed_at = datetime(2026, 3, 6, 12, 59, 30)
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'cooldown.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 13, 0, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='queued')

    db.session.refresh(existing_event)
    db.session.refresh(state)
    assert result.alert_updates == 0
    assert result.queued_fanout_tasks == 0
    assert 'cooldown.example' in existing_event.message
    assert existing_event.message != 'old message'
    assert existing_event.value >= 1.0
    assert state.active_dashboard_event_id == existing_event.event_id


def test_apply_restricted_site_ingest_none_mode_marks_last_emailed_without_sending():
    device = _tracked_device('AA:BB:CC:DD:EE:5C')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['none.example'])
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'none.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 14, 0, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='none')

    state = RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain='none.example').one()
    assert result.emails_sent == 0
    assert state.last_emailed_at == datetime(2026, 3, 6, 14, 0, 0)


def test_apply_restricted_site_ingest_skips_alert_creation_in_maintenance_mode():
    device = _tracked_device('AA:BB:CC:DD:EE:55', maintenance_mode=True)
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['maint.example'])
    db.session.commit()

    plan = service.plan_restricted_site_ingest(
        device,
        [{'domain': 'maint.example', 'source': 'window_title'}],
        policy=policy,
        now_utc=datetime(2026, 3, 6, 13, 0, 0),
    )

    result = service.apply_restricted_site_ingest(plan, fanout_mode='none')
    db.session.commit()

    assert result.ingested_events == 1
    assert result.alert_updates == 0
    assert DashboardEvent.query.count() == 0


def test_apply_restricted_site_ingest_rejects_invalid_fanout_mode():
    device = _tracked_device('AA:BB:CC:DD:EE:56')
    plan = service.RestrictedSiteIngestPlan(
        device_id=device.id,
        binding_key_id=None,
        policy_version='v1',
        blocked_domains=[],
        cooldown_seconds=900,
        now_utc=datetime.utcnow(),
        items=(),
    )

    with pytest.raises(ValueError):
        service.apply_restricted_site_ingest(plan, fanout_mode='bad-mode')
