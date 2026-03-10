from datetime import datetime, timedelta, timezone

import pytest

from extensions import db
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackingSample,
)
from services.tracking_freshness import (
    _count_map,
    _coverage_pct,
    _expected_slots,
    _max_timestamp_map,
    _pct,
    _utc_naive,
    _weighted_confidence,
    build_controls_contract,
    build_live_freshness,
    build_productivity_freshness_summary,
    build_workstation_report_freshness,
    map_probe_error_to_ui_reason,
)


pytestmark = pytest.mark.unit


def _create_device(mac_suffix: str, *, last_sync_at: datetime | None = None) -> TrackedDevice:
    device = TrackedDevice(
        mac_address=f'AA:BB:CC:DD:EE:{mac_suffix}',
        device_name=f'Device-{mac_suffix}',
        ip_address='10.0.0.25',
        availability_status='online',
        last_agent_sync_at=last_sync_at,
    )
    db.session.add(device)
    db.session.commit()
    return device


def _add_sample(device_id: int, received_at: datetime, integrity_status: str = 'verified') -> TrackingSample:
    row = TrackingSample(
        device_id=device_id,
        idempotency_key=f'{device_id}:{received_at.isoformat()}',
        received_at=received_at,
        sampled_at=received_at,
        integrity_status=integrity_status,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _add_availability(device_id: int, observed_at: datetime, status: str = 'online') -> None:
    db.session.add(
        TrackedDeviceAvailabilityEvent(
            device_id=device_id,
            observed_at=observed_at,
            status=status,
            metrics_available=True,
        )
    )


def test_build_live_freshness_transitions_across_all_states():
    now_utc = datetime.utcnow()
    device = _create_device('A1', last_sync_at=now_utc - timedelta(seconds=30))
    _add_sample(device.id, now_utc - timedelta(seconds=20))
    _add_availability(device.id, now_utc - timedelta(seconds=15))
    db.session.commit()

    live = build_live_freshness(
        device,
        {
            'probe_failed': False,
            'metrics_missing': False,
            'data_source': 'live_probe',
            'probe_latency_ms': 45,
        },
        now_utc,
        180,
        15,
    )
    assert live['telemetry_state'] == 'live'
    assert live['data_source'] == 'live_probe'
    assert live['report_eligible'] is True

    degraded = build_live_freshness(
        device,
        {
            'probe_failed': False,
            'metrics_missing': False,
            'data_source': 'live_probe',
            'probe_latency_ms': 150,
        },
        now_utc,
        180,
        15,
    )
    assert degraded['telemetry_state'] == 'degraded'

    stale = build_live_freshness(
        device,
        {
            'probe_failed': False,
            'metrics_missing': True,
            'data_source': 'db_snapshot',
        },
        now_utc,
        180,
        15,
    )
    assert stale['telemetry_state'] == 'stale'
    assert stale['is_fallback'] is True

    offline_fallback = build_live_freshness(
        device,
        {
            'probe_failed': True,
            'persisted_fallback_eligible': True,
            'reason_code': 'AGENT_UNREACHABLE',
            'data_source': 'sync_recent_fallback',
        },
        now_utc,
        180,
        15,
    )
    assert offline_fallback['telemetry_state'] == 'offline-fallback'
    assert offline_fallback['reason_code'] == 'AGENT_UNREACHABLE'

    offline_empty = build_live_freshness(
        device,
        {
            'probe_failed': True,
            'persisted_fallback_eligible': False,
            'reason_code': 'DEVICE_NO_IP',
        },
        now_utc,
        180,
        15,
    )
    assert offline_empty['telemetry_state'] == 'offline-empty'
    assert offline_empty['data_source'] == 'none'


def test_build_controls_contract_enables_only_live_degraded_and_stale():
    assert build_controls_contract('live', None)['remote_view']['enabled'] is True
    assert build_controls_contract('degraded', None)['camera']['enabled'] is True
    assert build_controls_contract('stale', None)['mic']['enabled'] is True

    offline_controls = build_controls_contract('offline-fallback', 'AGENT_UNREACHABLE')
    assert offline_controls['remote_view']['enabled'] is False
    assert offline_controls['remote_view']['reason_code'] == 'AGENT_UNREACHABLE'

    empty_controls = build_controls_contract('offline-empty', 'DEVICE_NO_IP')
    assert empty_controls['message']['enabled'] is False
    assert empty_controls['message']['reason_code'] == 'DEVICE_NO_IP'


def test_build_workstation_report_freshness_enforces_eligibility_threshold():
    now_utc = datetime.utcnow()
    start_utc = now_utc - timedelta(minutes=30)
    device = _create_device('A2', last_sync_at=now_utc - timedelta(minutes=2))

    _add_sample(device.id, start_utc + timedelta(minutes=1), integrity_status='verified')
    _add_sample(device.id, start_utc + timedelta(minutes=10), integrity_status='partial')
    _add_availability(device.id, start_utc + timedelta(minutes=10), status='online')
    db.session.commit()

    freshness = build_workstation_report_freshness(device.id, start_utc, now_utc)
    assert freshness['source_basis'] == 'persisted_samples'
    assert freshness['sample_count'] == 2
    assert freshness['coverage_pct'] >= 10.0
    assert freshness['report_eligible'] is True
    assert freshness['data_confidence_pct'] == 75.0

    sparse_device = _create_device('A3', last_sync_at=now_utc - timedelta(minutes=20))
    _add_sample(sparse_device.id, now_utc - timedelta(minutes=29), integrity_status='verified')
    db.session.commit()

    sparse = build_workstation_report_freshness(sparse_device.id, start_utc, now_utc)
    assert sparse['sample_count'] == 1
    assert sparse['coverage_pct'] == 10.0
    assert sparse['report_eligible'] is True


def test_build_productivity_freshness_summary_uses_grouped_window_data():
    now_utc = datetime.utcnow()
    start_utc = now_utc - timedelta(minutes=30)

    fresh_device = _create_device('B1', last_sync_at=now_utc - timedelta(minutes=1))
    stale_device = _create_device('B2', last_sync_at=now_utc - timedelta(minutes=20))
    empty_device = _create_device('B3', last_sync_at=now_utc - timedelta(minutes=25))

    for minute in (2, 28):
        sample_time = start_utc + timedelta(minutes=minute)
        _add_sample(fresh_device.id, sample_time)
        db.session.add(DeviceActivityLog(device_id=fresh_device.id, timestamp=sample_time, activity_type='keyboard', event_count=5))
        db.session.add(DeviceApplicationLog(device_id=fresh_device.id, timestamp=sample_time, application_name='Microsoft Word', duration=60))

    old_sample_time = start_utc + timedelta(minutes=1)
    _add_sample(stale_device.id, old_sample_time)
    db.session.add(DeviceActivityLog(device_id=stale_device.id, timestamp=old_sample_time, activity_type='mouse', event_count=3))
    db.session.add(DeviceApplicationLog(device_id=stale_device.id, timestamp=old_sample_time, application_name='Google Chrome', duration=30))
    db.session.commit()

    summary = build_productivity_freshness_summary(
        [fresh_device.id, stale_device.id, empty_device.id],
        start_utc,
        now_utc,
    )

    assert summary['source_basis'] == 'persisted_samples'
    assert summary['devices'][str(fresh_device.id)]['freshness_state'] == 'fresh'
    assert summary['devices'][str(stale_device.id)]['freshness_state'] == 'stale'
    assert summary['devices'][str(empty_device.id)]['freshness_state'] == 'empty'
    assert summary['totals']['fresh_devices'] == 1
    assert summary['totals']['stale_devices'] == 1
    assert summary['totals']['empty_devices'] == 1


def test_map_probe_error_to_ui_reason_humanizes_known_and_unknown_codes():
    assert map_probe_error_to_ui_reason('AGENT_UNREACHABLE') == 'Agent service did not respond.'
    assert map_probe_error_to_ui_reason('STATS_HTTP_503') == 'Agent telemetry probe returned an unexpected HTTP status.'
    assert map_probe_error_to_ui_reason('custom_reason_code') == 'Custom Reason Code'


def test_tracking_freshness_helpers_cover_empty_and_timezone_paths():
    aware = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)
    assert _utc_naive(aware).tzinfo is None
    assert _pct(5, 0) == 0.0
    assert _pct(3, 4) == 75.0
    assert _expected_slots(aware, aware, 180) == 0
    assert _expected_slots(aware, aware - timedelta(seconds=1), 180) == 0
    assert _coverage_pct(1, 0) == 0.0
    assert _weighted_confidence({}) == 0.0
    assert _max_timestamp_map(TrackingSample, [], TrackingSample.received_at) == {}
    assert _count_map(TrackingSample, [], TrackingSample.received_at, datetime.utcnow(), datetime.utcnow()) == {}


def test_map_probe_error_to_ui_reason_handles_blank_and_http_families():
    assert map_probe_error_to_ui_reason(None) == 'Agent state is unavailable.'
    assert map_probe_error_to_ui_reason('IDENTITY_HTTP_418') == 'Agent identity probe returned an unexpected HTTP status.'
    assert map_probe_error_to_ui_reason('HEALTH_HTTP_502') == 'Agent health probe returned an unexpected HTTP status.'


def test_build_live_freshness_normalizes_invalid_probe_latency_and_default_sources():
    now_utc = datetime.utcnow()
    device = _create_device('C1', last_sync_at=now_utc - timedelta(seconds=15))
    _add_sample(device.id, now_utc - timedelta(seconds=10))
    db.session.commit()

    live_default = build_live_freshness(
        device,
        {
            'probe_failed': False,
            'metrics_missing': False,
            'probe_latency_ms': 'not-a-number',
        },
        now_utc,
        180,
        15,
    )
    assert live_default['telemetry_state'] == 'live'
    assert live_default['data_source'] == 'live_probe'

    stale_default = build_live_freshness(
        device,
        {
            'probe_failed': False,
            'metrics_missing': True,
        },
        now_utc,
        180,
        15,
    )
    assert stale_default['telemetry_state'] == 'stale'
    assert stale_default['data_source'] == 'db_snapshot'

    fallback_default = build_live_freshness(
        device,
        {
            'probe_failed': True,
            'persisted_fallback_eligible': True,
        },
        now_utc,
        180,
        15,
    )
    assert fallback_default['telemetry_state'] == 'offline-fallback'
    assert fallback_default['data_source'] == 'sync_recent_fallback'


def test_build_productivity_freshness_summary_handles_empty_input_list():
    now_utc = datetime.utcnow()
    start_utc = now_utc - timedelta(minutes=30)
    summary = build_productivity_freshness_summary([], start_utc, now_utc)

    assert summary == {
        'source_basis': 'persisted_samples',
        'devices': {},
        'totals': {
            'fresh_devices': 0,
            'stale_devices': 0,
            'empty_devices': 0,
        },
    }
