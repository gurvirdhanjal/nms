"""
Config Backup API — Phase 3

GET  /api/devices/<id>/config-history   @require_login
POST /api/devices/<id>/config-backup    @require_role('admin')
GET  /api/devices/<id>/config-diff      @require_login
"""
import difflib
import logging
from flask import Blueprint, jsonify, request, abort
from extensions import db
from middleware.rbac import require_login, require_role

log = logging.getLogger(__name__)

config_backup_bp = Blueprint('config_backup_bp', __name__, url_prefix='/api/devices')

# Max snapshots returned by config-history
_HISTORY_LIMIT = 30


# ── GET /api/devices/<id>/config-history ─────────────────────────────────────

@config_backup_bp.route('/<int:device_id>/config-history', methods=['GET'])
@require_login
def config_history(device_id):
    """
    Return the last 30 config snapshots for a device.

    Response shape:
        [
          {
            "id": 1,
            "captured_at": "2026-03-12T10:00:00",
            "source": "manual",
            "captured_by": "admin",   // null for scheduled
            "config_hash": "abc123...",
            "changed": true           // hash differs from previous snapshot
          },
          ...
        ]
    """
    from models.config_snapshot import DeviceConfigSnapshot
    from models.device import Device

    device = Device.query.get(device_id)
    if not device:
        return jsonify({'error': 'Device not found'}), 404

    # Fetch one extra row so we can compute changed for the oldest visible row
    snapshots = (
        DeviceConfigSnapshot.query
        .filter_by(device_id=device_id)
        .order_by(DeviceConfigSnapshot.captured_at.desc())
        .limit(_HISTORY_LIMIT + 1)
        .all()
    )

    # rows[0] = newest … rows[-1] = oldest in this window
    # changed[i] = True if rows[i].hash != rows[i+1].hash
    result = []
    for i, snap in enumerate(snapshots[:_HISTORY_LIMIT]):
        older = snapshots[i + 1] if i + 1 < len(snapshots) else None
        changed = (
            older is None                        # first ever capture → always changed
            or snap.config_hash != older.config_hash
            or snap.config_hash is None
        )

        captured_by = None
        if snap.captured_by_user_id and snap.captured_by:
            captured_by = snap.captured_by.username

        result.append({
            'id': snap.id,
            'captured_at': snap.captured_at.isoformat() if snap.captured_at else None,
            'source': snap.source,
            'captured_by': captured_by,
            'config_hash': snap.config_hash,
            'changed': changed,
        })

    return jsonify(result), 200


# ── POST /api/devices/<id>/config-backup ─────────────────────────────────────

@config_backup_bp.route('/<int:device_id>/config-backup', methods=['POST'])
@require_role('admin')
def trigger_config_backup(device_id):
    """
    Trigger an on-demand config capture for a device.

    Response shape:
        { "success": true, "snapshot_id": 42, "changed": true }
        { "success": false, "error": "no_ssh_profile" }
    """
    from models.device import Device
    from flask import session
    from services.config_backup_service import capture_config

    device = Device.query.get(device_id)
    if not device:
        return jsonify({'error': 'Device not found'}), 404

    user_id = session.get('user_id')

    result = capture_config(
        device_id=device_id,
        source='manual',
        user_id=user_id,
    )

    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


# ── GET /api/devices/<id>/config-diff ────────────────────────────────────────

@config_backup_bp.route('/<int:device_id>/config-diff', methods=['GET'])
@require_login
def config_diff(device_id):
    """
    Return a unified diff between two snapshots.

    Query params:
        from=<snapshot_id>   older snapshot
        to=<snapshot_id>     newer snapshot

    Response shape (success):
        {
          "from_id": 1,
          "to_id": 2,
          "from_captured_at": "...",
          "to_captured_at": "...",
          "diff": "--- ...\n+++ ...\n@@...\n..."
        }

    Both snapshots must belong to device_id — returns 403 if not.
    Returns 400 if query params are missing or non-integer.
    """
    from models.config_snapshot import DeviceConfigSnapshot

    # Parse query params
    try:
        from_id = int(request.args['from'])
        to_id   = int(request.args['to'])
    except (KeyError, ValueError):
        return jsonify({'error': "Query params 'from' and 'to' (snapshot IDs) are required"}), 400

    snap_from = DeviceConfigSnapshot.query.get(from_id)
    snap_to   = DeviceConfigSnapshot.query.get(to_id)

    if not snap_from or not snap_to:
        return jsonify({'error': 'One or both snapshot IDs not found'}), 404

    # Ownership check — both snapshots must belong to this device
    if snap_from.device_id != device_id or snap_to.device_id != device_id:
        log.warning(
            f"[ConfigDiff] Ownership mismatch: device_id={device_id} "
            f"from.device_id={snap_from.device_id} to.device_id={snap_to.device_id}"
        )
        return jsonify({'error': 'Snapshot does not belong to this device'}), 403

    # Build unified diff
    from_lines = (snap_from.config_text or '').splitlines(keepends=True)
    to_lines   = (snap_to.config_text   or '').splitlines(keepends=True)

    from_label = f"snapshot-{from_id}  ({snap_from.captured_at.isoformat() if snap_from.captured_at else 'unknown'})"
    to_label   = f"snapshot-{to_id}  ({snap_to.captured_at.isoformat() if snap_to.captured_at else 'unknown'})"

    diff_lines = list(difflib.unified_diff(
        from_lines,
        to_lines,
        fromfile=from_label,
        tofile=to_label,
        lineterm='',
    ))
    diff_text = '\n'.join(diff_lines)

    return jsonify({
        'from_id':          snap_from.id,
        'to_id':            snap_to.id,
        'from_captured_at': snap_from.captured_at.isoformat() if snap_from.captured_at else None,
        'to_captured_at':   snap_to.captured_at.isoformat()   if snap_to.captured_at   else None,
        'changed':          snap_from.config_hash != snap_to.config_hash,
        'diff':             diff_text,
    }), 200
