import time
from types import SimpleNamespace

import pytest

from services import tracking_reconcile
from services.tracked_device_ip_change import TrackedDeviceIpSyncError


pytestmark = pytest.mark.unit


def test_attempt_identity_relocation_skips_ip_collision_without_failing(monkeypatch):
    device = SimpleNamespace(
        id=6,
        device_name="APL Developer",
        ip_address="10.26.122.173",
        unique_client_id=None,
        mac_address="AA:BB:CC:DD:EE:FF",
        hostname="apldeveloper",
    )
    discovery_cache = tracking_reconcile.DiscoveryCache(
        last_results=[
            {
                "status": "tracking_active",
                "availability_status": "online",
                "ip": "172.16.2.74",
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "hostname": "apldeveloper",
            }
        ],
        last_discovery_at=time.time(),
        force_discovery=False,
    )

    def _raise_collision(**kwargs):
        raise TrackedDeviceIpSyncError(
            {"reason_code": "IP_COLLISION", "fatal": True, "collision_device_id": 8196}
        )

    monkeypatch.setattr(tracking_reconcile, "apply_tracked_device_ip_change", _raise_collision)

    updated_count = tracking_reconcile.attempt_identity_relocation(
        offline_devices=[device],
        discovery_cache=discovery_cache,
        dry_run=False,
    )

    assert updated_count == 0
    assert discovery_cache.last_relocation_plan == [
        {
            "device_id": 6,
            "device_name": "APL Developer",
            "old_ip": "10.26.122.173",
            "new_ip": "172.16.2.74",
            "reason": "IDENTITY_RELOCATION",
            "status": "blocked",
            "sync_reason_code": "IP_COLLISION",
            "collision_device_id": 8196,
        }
    ]
