import pytest

from extensions import db
from models.device import Device


pytestmark = pytest.mark.integration


def test_server_device_details_fullpage_telemetry_flag_gates_panel(app, admin_client):
    server = Device(
        device_name='Server Gamma',
        device_type='server',
        device_ip='10.5.0.10',
    )
    db.session.add(server)
    db.session.commit()

    app.config['ENABLE_SERVER_FULLPAGE_TELEMETRY'] = False
    response = admin_client.get(f'/devices/{server.device_id}/details')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="server-page-surface"' not in html

    app.config['ENABLE_SERVER_FULLPAGE_TELEMETRY'] = True
    response = admin_client.get(f'/devices/{server.device_id}/details')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="server-page-surface"' in html
    assert 'id="server-page-thresholds-body"' in html

    app.config['ENABLE_SERVER_FULLPAGE_TELEMETRY'] = False
