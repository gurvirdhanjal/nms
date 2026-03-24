"""Route-level tests for /api/device_statistics/pdf (RBAC + error paths)."""
import io
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    """Log in as a user with reports.export permission."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'admin'
        sess['permissions'] = ['reports.export']


def test_pdf_route_400_missing_ip(client):
    _login(client)
    resp = client.get('/api/device_statistics/pdf')
    assert resp.status_code == 400
    assert b'device_ip is required' in resp.data


def test_pdf_route_403_device_not_in_scope(client):
    _login(client)
    with patch('routes.reports._build_device_stats', return_value=(None, None)):
        resp = client.get('/api/device_statistics/pdf?device_ip=10.0.0.1')
    assert resp.status_code == 403
    assert b'Device not found' in resp.data


def test_pdf_route_404_no_scan_data(client):
    _login(client)
    fake_device = MagicMock()
    with patch('routes.reports._build_device_stats', return_value=(fake_device, None)):
        resp = client.get('/api/device_statistics/pdf?device_ip=10.0.0.1')
    assert resp.status_code == 404


def test_pdf_route_200_returns_pdf(client):
    _login(client)
    fake_device = MagicMock(device_name='Server-01')
    fake_stats = {'total_scans': 10, 'online_count': 9, 'offline_count': 1,
                  'no_response_count': 1, 'uptime_percentage': 90.0,
                  'downtime_percentage': 10.0, 'agent_data': {'available': False}}
    fake_buf = io.BytesIO(b'%PDF-1.4 fake')
    with patch('routes.reports._build_device_stats', return_value=(fake_device, fake_stats)), \
         patch('services.enterprise_pdf_service.generate_device_inspector_pdf',
               return_value=fake_buf):
        resp = client.get('/api/device_statistics/pdf?device_ip=10.0.0.1&period=24h')
    assert resp.status_code == 200
    assert resp.content_type == 'application/pdf'


def test_pdf_route_500_on_generation_error(client):
    _login(client)
    fake_device = MagicMock(device_name='Server-01')
    fake_stats = {'total_scans': 10, 'uptime_percentage': 90.0,
                  'agent_data': {'available': False}}
    with patch('routes.reports._build_device_stats', return_value=(fake_device, fake_stats)), \
         patch('services.enterprise_pdf_service.generate_device_inspector_pdf',
               side_effect=RuntimeError('boom')):
        resp = client.get('/api/device_statistics/pdf?device_ip=10.0.0.1')
    assert resp.status_code == 500
    assert b'PDF generation failed' in resp.data
