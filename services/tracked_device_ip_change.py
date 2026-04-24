from __future__ import annotations

import json
from datetime import datetime

from extensions import db
from models.tracked_device import TrackedDeviceIpHistory
from services.server_inventory_sync import sync_linked_inventory_ip


class TrackedDeviceIpSyncError(RuntimeError):
    def __init__(self, sync_result: dict):
        self.sync_result = sync_result or {}
        self.reason_code = self.sync_result.get("reason_code") or "SYNC_FAILED"
        self.status_code = 409
        super().__init__(self.reason_code)


def apply_tracked_device_ip_change(
    *,
    tracked_device,
    new_ip,
    resolved_hostname=None,
    now_utc=None,
    payload_ip=None,
    payload_candidates=None,
    transport_remote_ip=None,
    transport_forwarded_for=None,
    agent_key_id=None,
    reason,
    ip_source=None,
    network_signature=None,
    update_last_seen=False,
    update_updated_at=True,
    sync_reason=None,
):
    old_ip = (str(getattr(tracked_device, "ip_address", "") or "").strip() or None)
    next_ip = (str(new_ip or "").strip() or None)
    next_hostname = (str(resolved_hostname or "").strip() or None)
    if not next_ip or next_ip == old_ip:
        if next_hostname and next_hostname != (str(getattr(tracked_device, "hostname", "") or "").strip() or None):
            tracked_device.hostname = next_hostname
        return {
            "changed": False,
            "old_ip": old_ip,
            "new_ip": next_ip,
            "sync_result": {
                "updated": False,
                "reason_code": "NO_CHANGE",
                "tracked_device_id": int(getattr(tracked_device, "id", 0) or 0) or None,
                "device_id": None,
                "link_id": None,
                "fatal": False,
            },
        }

    timestamp = now_utc or datetime.utcnow()
    tracked_device.ip_address = next_ip
    if next_hostname and next_hostname != (str(getattr(tracked_device, "hostname", "") or "").strip() or None):
        tracked_device.hostname = next_hostname
    if update_last_seen:
        tracked_device.last_seen = timestamp
    if update_updated_at:
        tracked_device.updated_at = timestamp

    history_row = TrackedDeviceIpHistory(
        device_id=int(tracked_device.id),
        old_ip=old_ip,
        new_ip=next_ip,
        resolved_ip=next_ip,
        payload_ip=(str(payload_ip).strip() or None) if payload_ip is not None else None,
        payload_candidates_json=json.dumps(payload_candidates or [], ensure_ascii=True),
        transport_remote_ip=(str(transport_remote_ip).strip() or None) if transport_remote_ip is not None else None,
        transport_forwarded_for=(str(transport_forwarded_for).strip() or None) if transport_forwarded_for is not None else None,
        agent_key_id=(str(agent_key_id).strip() or None) if agent_key_id is not None else None,
        reason=(str(reason).strip() or "SYNC_IP"),
        ip_source=(str(ip_source).strip() or None) if ip_source is not None else None,
        network_signature=(str(network_signature).strip() or None) if network_signature is not None else None,
        changed_at_utc=timestamp,
        received_at_utc=timestamp,
    )
    db.session.add(history_row)
    db.session.flush()

    sync_result = sync_linked_inventory_ip(
        tracked_device=tracked_device,
        old_ip=old_ip,
        new_ip=next_ip,
        reason=sync_reason or reason,
        hostname=next_hostname,
        changed_at_utc=timestamp,
    )
    if sync_result.get("fatal"):
        raise TrackedDeviceIpSyncError(sync_result)

    return {
        "changed": True,
        "old_ip": old_ip,
        "new_ip": next_ip,
        "history_row": history_row,
        "sync_result": sync_result,
    }
