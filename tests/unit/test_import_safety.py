from pathlib import Path

import pytest

from models.tracked_device import RemoteDeviceScanHistory


pytestmark = pytest.mark.unit


def test_remote_device_scan_history_keeps_existing_table_name():
    assert RemoteDeviceScanHistory.__tablename__ == 'device_scan_history_remote'


def test_service_has_single_require_api_key_definition():
    service_source = Path('service.py').read_text(encoding='utf-8', errors='ignore')
    assert service_source.count('def require_api_key(') == 1
