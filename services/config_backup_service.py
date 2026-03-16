"""
config_backup_service.py — SSH config capture for Phase 3.

Connects to a device via its SSH profile, runs the appropriate
show-config command for the device type, and persists the output
as a DeviceConfigSnapshot.

Public API:
    capture_config(device_id, source='scheduled', user_id=None) -> dict

Returns:
    {'success': True,  'snapshot_id': int, 'changed': bool}
    {'success': False, 'error': str}

Not wired to scheduler or routes yet (Sessions 3 & 4).
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# ── Device-type → show-config command map ─────────────────────────────────────
# Keys are matched case-insensitively against device.device_type.
# Add new device families here without touching any other code.
DEVICE_TYPE_COMMANDS: dict[str, str] = {
    'cisco ios':     'show running-config',
    'cisco ios-xe':  'show running-config',
    'cisco ios xe':  'show running-config',
    'cisco nx-os':   'show running-config',
    'cisco nxos':    'show running-config',
    'cisco asa':     'show running-config',
    'juniper':       'show configuration',
    'juniper junos': 'show configuration',
    'aruba':         'show running-config',
    'hp procurve':   'show running-config',
    'fortinet':      'show full-configuration',
    'palo alto':     'show config running',
    'mikrotik':      'export',
}

# Fallback used when device_type doesn't match any key above
DEFAULT_COMMAND = 'show running-config'

# SSH command timeout (seconds) — long enough for large configs
SSH_TIMEOUT = 30


def _resolve_command(device_type: str | None) -> str:
    """Return the appropriate show-config command for a device type."""
    if not device_type:
        return DEFAULT_COMMAND
    key = device_type.strip().lower()
    # Exact match first
    if key in DEVICE_TYPE_COMMANDS:
        return DEVICE_TYPE_COMMANDS[key]
    # Prefix match — e.g. "Cisco IOS 15.2" → "cisco ios"
    for pattern, command in DEVICE_TYPE_COMMANDS.items():
        if key.startswith(pattern):
            return command
    return DEFAULT_COMMAND


def _resolve_ssh_profile(device):
    """
    Return (profile_id, SSHProfile) for a device, or (None, None) if none found.

    Resolution order:
      1. device.ssh_profile_id (FK column, may be commented-out in ORM — use getattr)
      2. No automatic fallback — a missing profile is a genuine config gap.
    """
    from models.ssh_profile import SSHProfile

    profile_id = getattr(device, 'ssh_profile_id', None)
    if profile_id:
        profile = SSHProfile.query.get(profile_id)
        if profile:
            return profile_id, profile
        log.warning(
            f"[ConfigBackup] device_id={device.device_id}: ssh_profile_id={profile_id} "
            f"references a missing SSHProfile row"
        )
        return None, None

    return None, None


def capture_config(device_id: int, source: str = 'scheduled', user_id: int | None = None) -> dict:
    """
    Capture the running configuration of a device via SSH.

    Args:
        device_id:  Device.device_id to capture from.
        source:     'scheduled' or 'manual'.
        user_id:    User.id for manual captures; None for scheduled.

    Returns:
        {'success': True,  'snapshot_id': int, 'changed': bool}
        {'success': False, 'error': str}
    """
    from extensions import db
    from models.device import Device
    from models.config_snapshot import DeviceConfigSnapshot
    from services.ssh_service import SSHService

    try:
        # ── 1. Load device ──────────────────────────────────────────────────
        device = Device.query.get(device_id)
        if not device:
            log.warning(f"[ConfigBackup] device_id={device_id}: device not found")
            return {'success': False, 'error': 'device_not_found'}

        # ── 2. Resolve SSH profile ──────────────────────────────────────────
        profile_id, profile = _resolve_ssh_profile(device)
        if not profile:
            log.warning(
                f"[ConfigBackup] device_id={device_id} ({device.device_ip}): "
                f"no SSH profile assigned — skipping capture"
            )
            return {'success': False, 'error': 'no_ssh_profile'}

        # ── 3. Determine command ────────────────────────────────────────────
        command = _resolve_command(device.device_type)
        log.debug(
            f"[ConfigBackup] device_id={device_id} ({device.device_ip}) "
            f"type='{device.device_type}' → command='{command}'"
        )

        # ── 4. Execute via SSH ──────────────────────────────────────────────
        ssh = SSHService()
        try:
            output, stderr = ssh.execute_command(
                host=device.device_ip,
                profile_id=profile_id,
                command=command,
                timeout=SSH_TIMEOUT,
            )
        except ImportError:
            log.error("[ConfigBackup] paramiko not installed — cannot capture config")
            return {'success': False, 'error': 'paramiko_not_installed'}
        except Exception as e:
            log.error(
                f"[ConfigBackup] device_id={device_id} ({device.device_ip}): "
                f"SSH error — {e}"
            )
            return {'success': False, 'error': f'ssh_error: {str(e)[:200]}'}

        if not output or not output.strip():
            log.warning(
                f"[ConfigBackup] device_id={device_id} ({device.device_ip}): "
                f"empty output from '{command}'"
                + (f" stderr={stderr.strip()!r}" if stderr and stderr.strip() else "")
            )
            return {'success': False, 'error': 'empty_output'}

        # ── 5. Change detection — fetch previous snapshot hash ──────────────
        previous = (
            DeviceConfigSnapshot.query
            .filter_by(device_id=device_id)
            .order_by(DeviceConfigSnapshot.captured_at.desc())
            .first()
        )
        prev_hash = previous.config_hash if previous else None

        # ── 6. Persist new snapshot ─────────────────────────────────────────
        # config_hash is auto-computed by the @validates on config_text
        snapshot = DeviceConfigSnapshot(
            device_id=device_id,
            captured_at=datetime.utcnow(),
            config_text=output,
            source=source,
            captured_by_user_id=user_id,
        )
        db.session.add(snapshot)
        db.session.flush()   # populate snapshot.id before commit

        changed = snapshot.config_hash != prev_hash

        db.session.commit()

        log.info(
            f"[ConfigBackup] device_id={device_id} ({device.device_ip}): "
            f"snapshot_id={snapshot.id} hash={snapshot.config_hash[:12]}… "
            f"changed={changed} source={source}"
        )
        return {'success': True, 'snapshot_id': snapshot.id, 'changed': changed}

    except Exception as e:
        log.error(f"[ConfigBackup] Unhandled error for device_id={device_id}: {e}")
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass
        return {'success': False, 'error': f'unexpected: {str(e)[:300]}'}
