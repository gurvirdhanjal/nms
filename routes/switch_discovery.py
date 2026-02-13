from flask import Blueprint, jsonify, request, session, current_app
from extensions import db

from services.snmp_discovery import SnmpDiscovery


switch_discovery_bp = Blueprint('switch_discovery_bp', __name__, url_prefix='')


def _persist_devices(switches):
    from models.device import Device

    inserted = 0
    updated = 0

    seen = set()

    for sw in switches:
        for dev in sw.get("devices", []):
            ip = dev.get("ip")
            mac = dev.get("mac")
            if not ip and not mac:
                continue

            key = (ip or "", mac or "")
            if key in seen:
                continue
            seen.add(key)

            existing = None
            if ip:
                existing = Device.query.filter_by(device_ip=ip).first()
            if not existing and mac:
                existing = Device.query.filter_by(macaddress=mac).first()

            if existing:
                if mac and (not existing.macaddress or existing.macaddress == "N/A"):
                    existing.macaddress = mac
                if ip and existing.device_ip != ip:
                    existing.device_ip = ip
                if dev.get("interface"):
                    existing.port = dev.get("interface")
                if not existing.device_type:
                    existing.device_type = "switch"
                if not existing.device_name or existing.device_name.startswith("Device-"):
                    existing.device_name = f"Device-{existing.device_ip}"
                updated += 1
            else:
                if not ip:
                    # Skip MAC-only entries to avoid cluttering inventory
                    continue
                device = Device(
                    device_name=f"Device-{ip}",
                    device_ip=ip,
                    device_type="switch",
                    port=dev.get("interface") or "",
                    macaddress=mac or "N/A",
                    hostname="Unknown",
                    manufacturer="Unknown",
                    is_monitored=False,
                    is_active=True,
                )
                db.session.add(device)
                inserted += 1

    db.session.commit()
    return inserted, updated


@switch_discovery_bp.route('/api/switches/discover', methods=['POST'])
def discover_switches():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    seed_ip = data.get('seed_ip')

    if not seed_ip:
        return jsonify({'error': 'seed_ip is required'}), 400

    community = data.get('community') or current_app.config.get('SNMP_COMMUNITY', 'public')
    version = data.get('version') or current_app.config.get('SNMP_VERSION', '2c')
    max_depth = int(data.get('max_depth', 3))
    max_switches = int(data.get('max_switches', 50))
    persist = data.get('persist', True)

    discovery = SnmpDiscovery(
        community=community,
        version=version,
        timeout=int(data.get('timeout', 2)),
        retries=int(data.get('retries', 1))
    )

    switches = discovery.discover(seed_ip, max_depth=max_depth, max_switches=max_switches)

    device_count = sum(len(sw.get("devices", [])) for sw in switches)
    inserted = updated = 0
    if persist:
        inserted, updated = _persist_devices(switches)

    return jsonify({
        "success": True,
        "seed_ip": seed_ip,
        "switch_count": len(switches),
        "device_count": device_count,
        "persisted_inserted": inserted,
        "persisted_updated": updated,
        "switches": switches,
    })
