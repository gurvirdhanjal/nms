"""
Transactional tracked-device identity reconciliation utility.

Usage:
    python scripts/reconcile_tracked_device_identities.py --dry-run
    python scripts/reconcile_tracked_device_identities.py --device-id 123 --apply
    python scripts/reconcile_tracked_device_identities.py --ip 172.16.2.86 --apply
    python scripts/reconcile_tracked_device_identities.py --mac 64:6C:80:EE:9E:D1 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from extensions import db
from models.tracked_device import TrackedDevice
from services.tracking_identity_resolution_service import (
    build_actor_context,
    build_identity_input_for_tracked_device,
    preview_reconciliation_for_tracked_device,
    reconcile_tracking_identity,
)
from services.tracking_reconcile import normalize_mac


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, default=str))


def _build_query(*, device_id: int | None, ip_address: str | None, mac_address: str | None):
    query = TrackedDevice.query.order_by(TrackedDevice.id.asc())
    if device_id is not None:
        query = query.filter(TrackedDevice.id == int(device_id))
    if ip_address:
        query = query.filter(TrackedDevice.ip_address == ip_address)
    if mac_address:
        query = query.filter(
            db.func.replace(db.func.upper(TrackedDevice.mac_address), "-", ":") == mac_address
        )
    return query


def _payload_for_cleanup(device: TrackedDevice) -> dict:
    return {
        "mac_address": device.mac_address,
        "unique_client_id": device.unique_client_id,
        "hostname": device.hostname,
        "ip_address": device.ip_address,
        "device_name": device.device_name,
        "reconciliation_source": "cleanup_script",
    }


def run(
    *,
    dry_run: bool,
    device_id: int | None,
    ip_address: str | None,
    mac_address: str | None,
    limit: int | None,
) -> int:
    app = create_app()
    with app.app_context():
        normalized_mac = normalize_mac(mac_address) if mac_address else None
        if mac_address and not normalized_mac:
            _emit({"mode": "error", "reason": "invalid_mac", "mac": mac_address})
            return 1

        query = _build_query(
            device_id=device_id,
            ip_address=ip_address,
            mac_address=normalized_mac,
        )
        if limit is not None:
            query = query.limit(max(1, int(limit)))

        target_ids = [int(device.id) for device in query.all()]
        _emit(
            {
                "mode": "selection",
                "dry_run": bool(dry_run),
                "device_count": len(target_ids),
                "device_id": device_id,
                "ip_address": ip_address,
                "mac_address": normalized_mac,
                "limit": limit,
            }
        )

        actor = build_actor_context(username="cleanup_script", role="system")
        actionable_count = 0
        merged_count = 0
        skipped_count = 0

        for tracked_device_id in target_ids:
            preview = preview_reconciliation_for_tracked_device(tracked_device_id)
            _emit({"mode": "preview", **preview})
            if not preview.get("actionable"):
                skipped_count += 1
                continue

            actionable_count += 1
            if dry_run:
                continue

            try:
                if db.session.in_transaction():
                    db.session.rollback()
                with db.session.begin():
                    tracked_device = TrackedDevice.query.get(int(tracked_device_id))
                    if tracked_device is None:
                        _emit(
                            {
                                "mode": "apply",
                                "device_id": int(tracked_device_id),
                                "status": "missing",
                            }
                        )
                        continue

                    resolution = reconcile_tracking_identity(
                        identity_input=build_identity_input_for_tracked_device(tracked_device),
                        payload=_payload_for_cleanup(tracked_device),
                        actor=actor,
                        sync_mode="inline",
                        resolution_source="cleanup_script",
                        allow_create=False,
                    )
                    merged_duplicate_ids = list(resolution.merged_duplicate_device_ids or [])
                    if merged_duplicate_ids:
                        merged_count += len(merged_duplicate_ids)
                    _emit(
                        {
                            "mode": "apply",
                            "device_id": int(tracked_device_id),
                            "status": resolution.identity_status,
                            "survivor_id": int(resolution.device.id) if resolution.device is not None else None,
                            "merged_duplicate_device_id": resolution.merged_duplicate_device_id,
                            "merged_duplicate_device_ids": merged_duplicate_ids,
                            "authoritative_mac": resolution.authoritative_mac,
                            "authoritative_mac_source": resolution.authoritative_mac_source,
                            "resolved_inventory_device_id": resolution.resolved_inventory_device_id,
                            "resolution_path": resolution.resolution_path,
                            "resolution_source": resolution.resolution_source,
                            "merge_reason": resolution.merge_reason,
                        }
                    )
            except Exception as exc:
                db.session.rollback()
                _emit(
                    {
                        "mode": "error",
                        "device_id": int(tracked_device_id),
                        "error": str(exc),
                    }
                )

        _emit(
            {
                "mode": "summary",
                "dry_run": bool(dry_run),
                "target_count": len(target_ids),
                "actionable_count": actionable_count,
                "merged_count": merged_count,
                "skipped_count": skipped_count,
            }
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile tracked-device identities against scanner-backed evidence.")
    parser.add_argument("--dry-run", action="store_true", help="Preview reconciliation candidates without writing changes.")
    parser.add_argument("--apply", action="store_true", help="Apply reconciliation changes. If omitted, the script runs in dry-run mode.")
    parser.add_argument("--device-id", type=int, help="Limit reconciliation to one tracked device id.")
    parser.add_argument("--ip", dest="ip_address", help="Limit reconciliation to a tracked device IP.")
    parser.add_argument("--mac", dest="mac_address", help="Limit reconciliation to a tracked device MAC.")
    parser.add_argument("--limit", type=int, help="Maximum number of tracked devices to inspect.")
    args = parser.parse_args()

    effective_dry_run = True
    if args.apply:
        effective_dry_run = False
    elif args.dry_run:
        effective_dry_run = True

    raise SystemExit(
        run(
            dry_run=effective_dry_run,
            device_id=args.device_id,
            ip_address=(args.ip_address or "").strip() or None,
            mac_address=(args.mac_address or "").strip() or None,
            limit=args.limit,
        )
    )
