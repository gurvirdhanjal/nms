import json
import time
from datetime import datetime, timedelta

import pytest

from extensions import db
from models.tracked_device import TrackedDevice, TrackingSample
from routes import tracking as tracking_routes


pytestmark = pytest.mark.integration


def _create_device(*, mac_suffix: str, last_sync_at=None, tracking_data=None):
    device = TrackedDevice(
        mac_address=f'AA:BB:CC:DD:EE:{mac_suffix}',
        device_name=f'RealTime-{mac_suffix}',
        hostname=f'rt-{mac_suffix.lower()}',
        ip_address='10.0.0.55',
        availability_status='online',
        last_agent_sync_at=last_sync_at,
        tracking_data=json.dumps(tracking_data or {}),
        metrics_available=bool(tracking_data),
    )
    db.session.add(device)
    db.session.commit()
    return device


def _add_sample(device_id: int, received_at: datetime):
    db.session.add(
        TrackingSample(
            device_id=device_id,
            idempotency_key=f'{device_id}:{received_at.isoformat()}',
            received_at=received_at,
            sampled_at=received_at,
            integrity_status='verified',
        )
    )
    db.session.commit()


@pytest.fixture(autouse=True)
def _clear_realtime_cache():
    tracking_routes.real_time_data.clear()
    yield
    tracking_routes.real_time_data.clear()


def test_real_time_api_returns_live_freshness_and_enabled_controls(admin_client, monkeypatch):
    now_utc = datetime.utcnow()
    device = _create_device(mac_suffix='C1', last_sync_at=now_utc - timedelta(seconds=20))
    _add_sample(device.id, now_utc - timedelta(seconds=15))

    def fake_probe(self, ip, profile='interactive'):
        return {
            'availability_status': 'online',
            'probe_method': 'stats',
            'probe_error_code': None,
            'data': {
                'system_metrics': {'cpu_percent': 12},
                'today_stats': {'keyboard_events': 4},
                'current_activity': {'idle_seconds': 5},
            },
        }

    monkeypatch.setattr(tracking_routes.NetworkScanner, 'check_tracking_service', fake_probe)

    response = admin_client.get(f'/api/tracking/real-time/{device.mac_address}')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['freshness']['telemetry_state'] == 'live'
    assert payload['freshness']['data_source'] == 'live_probe'
    assert payload['controls']['remote_view']['enabled'] is True
    assert payload['controls']['camera']['enabled'] is True


def test_real_time_api_returns_degraded_state_when_probe_is_slow(admin_client, monkeypatch):
    now_utc = datetime.utcnow()
    device = _create_device(mac_suffix='C2', last_sync_at=now_utc - timedelta(seconds=10))
    _add_sample(device.id, now_utc - timedelta(seconds=8))

    def slow_probe(self, ip, profile='interactive'):
        time.sleep(0.12)
        return {
            'availability_status': 'online',
            'probe_method': 'stats',
            'probe_error_code': None,
            'data': {
                'system_metrics': {'cpu_percent': 18},
                'today_stats': {'keyboard_events': 3},
                'current_activity': {'idle_seconds': 9},
            },
        }

    monkeypatch.setattr(tracking_routes.NetworkScanner, 'check_tracking_service', slow_probe)

    response = admin_client.get(f'/api/tracking/real-time/{device.mac_address}')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['freshness']['telemetry_state'] == 'degraded'
    assert payload['controls']['message']['enabled'] is True


def test_real_time_api_returns_stale_state_with_persisted_snapshot(admin_client, monkeypatch):
    now_utc = datetime.utcnow()
    device = _create_device(
        mac_suffix='C3',
        last_sync_at=now_utc - timedelta(seconds=40),
        tracking_data={
            'system_metrics': {'cpu_percent': 22, 'memory_percent': 33},
            'current_activity': {'idle_seconds': 14},
            'today_stats': {'keyboard_events': 2},
        },
    )
    _add_sample(device.id, now_utc - timedelta(seconds=35))

    def stale_probe(self, ip, profile='interactive'):
        return {
            'availability_status': 'online',
            'probe_method': 'stats',
            'probe_error_code': None,
            'data': {},
        }

    monkeypatch.setattr(tracking_routes.NetworkScanner, 'check_tracking_service', stale_probe)

    response = admin_client.get(f'/api/tracking/real-time/{device.mac_address}')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['freshness']['telemetry_state'] == 'stale'
    assert payload['freshness']['data_source'] == 'db_snapshot'
    assert payload['metrics_stale'] is True
    assert payload['controls']['remote_view']['enabled'] is True


def test_real_time_api_returns_offline_fallback_when_agent_is_down_but_recent_sync_exists(admin_client, monkeypatch):
    now_utc = datetime.utcnow()
    device = _create_device(
        mac_suffix='C4',
        last_sync_at=now_utc - timedelta(seconds=50),
        tracking_data={
            'system_metrics': {'cpu_percent': 41},
            'current_activity': {'idle_seconds': 30},
            'today_stats': {'keyboard_events': 8},
        },
    )
    _add_sample(device.id, now_utc - timedelta(seconds=45))

    def offline_probe(self, ip, profile='interactive'):
        return {
            'availability_status': 'offline',
            'probe_method': 'interactive',
            'probe_error_code': 'AGENT_UNREACHABLE',
            'data': {},
        }

    monkeypatch.setattr(tracking_routes.NetworkScanner, 'check_tracking_service', offline_probe)

    response = admin_client.get(f'/api/tracking/real-time/{device.mac_address}')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['freshness']['telemetry_state'] == 'offline-fallback'
    assert payload['freshness']['data_source'] == 'sync_recent_fallback'
    assert payload['controls']['remote_view']['enabled'] is False
    assert payload['controls']['remote_view']['reason_code'] == 'AGENT_UNREACHABLE'


def test_real_time_api_returns_offline_empty_when_no_fallback_exists(admin_client, monkeypatch):
    device = _create_device(mac_suffix='C5', last_sync_at=None, tracking_data={})

    def offline_probe(self, ip, profile='interactive'):
        return {
            'availability_status': 'offline',
            'probe_method': 'interactive',
            'probe_error_code': 'DEVICE_NO_IP',
            'data': {},
        }

    monkeypatch.setattr(tracking_routes.NetworkScanner, 'check_tracking_service', offline_probe)

    response = admin_client.get(f'/api/tracking/real-time/{device.mac_address}')
    assert response.status_code == 503
    payload = response.get_json()

    assert payload['success'] is False
    assert payload['freshness']['telemetry_state'] == 'offline-empty'
    assert payload['controls']['camera']['enabled'] is False
    assert payload['freshness']['reason_code'] == 'DEVICE_NO_IP'
