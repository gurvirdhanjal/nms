import pytest

from extensions import db
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def test_device_live_page_renders_valid_bootstrap_config(admin_client):
    device = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:70',
        device_name='Bootstrap Device',
        hostname='bootstrap-host',
        ip_address='10.20.30.40',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()

    response = admin_client.get(f'/devices/{device.id}')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'window.TRACKING_DEVICE_LIVE_CONFIG' in html
    assert 'macAddress: "AA:BB:CC:DD:EE:70"' in html
    assert 'initialDisplayIp: "10.20.30.40"' in html
    assert 'initialStatus: "online"' in html
    assert 'fileTransferEnabled: true' in html
    assert '{ {' not in html
    assert 'id="policyRestrictedSitesList"' in html
    assert 'id="policyViewFullLogsBtn"' in html
    assert 'id="policyLogsModal"' in html
    assert 'id="policyLogsModalList"' in html
    assert 'id="policyRemoveSiteList"' in html
    assert 'class="modal fade remote-view-modal"' in html
    assert 'id="remoteViewStatus"' in html
    assert 'data-tab="files"' in html
    assert 'data-panel="files"' in html
    assert 'id="filesUploadInput"' in html
    assert 'id="filesList"' in html
    assert 'multiple size="8"' in html
    assert 'Stored Device Restrictions' in html
    assert 'Effective Policy Scope' in html
