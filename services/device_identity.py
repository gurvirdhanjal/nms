import re
from datetime import datetime

from extensions import db
from models.device import Device

_INVALID_MACS = {"", "n/a", "na", "unknown", "none", "null"}
_INVALID_TEXT = {"", "n/a", "na", "unknown", "none", "null", "network device", "network_device", "network-device"}


def _normalize_mac(mac):
    if not mac:
        return None
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(cleaned) != 12:
        return None
    cleaned = cleaned.lower()
    return ":".join(cleaned[i:i + 2] for i in range(0, 12, 2))


def _mac_candidates(mac):
    if not mac:
        return []
    canon = _normalize_mac(mac)
    if not canon:
        return []
    return list({
        mac,
        mac.lower(),
        mac.upper(),
        canon,
        canon.upper(),
        canon.replace(":", "-"),
        canon.replace(":", "")
    })


def _is_invalid_text(value):
    if value is None:
        return True
    return str(value).strip().lower() in _INVALID_TEXT


def _is_invalid_mac(value):
    if value is None:
        return True
    return str(value).strip().lower() in _INVALID_MACS


def find_device_by_mac(mac):
    candidates = _mac_candidates(mac)
    if not candidates:
        return None
    return Device.query.filter(Device.macaddress.in_(candidates)).first()


def upsert_device_from_identity(
    ip,
    mac=None,
    hostname=None,
    manufacturer=None,
    device_type=None,
    is_monitored=None,
    is_active=True,
):
    """
    Match devices by IP first, then MAC.
    CRITICAL: Handles duplicate cleanup if multiple records match.
    Returns: (device, action, previous_ip)
      action: created | updated | existing | skipped
    """
    previous_ip = None
    
    # 1. Gather all candidates (IP matches + MAC matches)
    candidates = []
    
    if ip:
        # Get ALL devices with this IP
        ip_matches = Device.query.filter_by(device_ip=ip).all()
        candidates.extend(ip_matches)
        
    if mac and not _is_invalid_mac(mac):
        # Get ALL devices with this MAC
        mac_candidates = _mac_candidates(mac)
        mac_matches = Device.query.filter(Device.macaddress.in_(mac_candidates)).all()
        candidates.extend(mac_matches)

    # Dedup candidates by ID
    unique_candidates = {d.device_id: d for d in candidates}
    candidates = list(unique_candidates.values())

    # 2. If no candidates, create new
    if not candidates:
        if not ip:
             return None, "skipped", None
             
        normalized_mac = _normalize_mac(mac) if mac and not _is_invalid_mac(mac) else None
        device_name = hostname if hostname and not _is_invalid_text(hostname) else f"Device-{ip}"
        
        device = Device(
            device_name=device_name,
            device_ip=ip,
            device_type=device_type or "unknown",
            macaddress=normalized_mac or (mac if mac else "N/A"),
            hostname=hostname or "Unknown",
            manufacturer=manufacturer or "Unknown",
            is_monitored=bool(is_monitored) if is_monitored is not None else False,
            is_active=bool(is_active),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.session.add(device)
        return device, "created", None

    # 3. Sort candidates to pick a "primary" (most recently updated or created)
    # Sort key: (is_monitored DESC, updated_at DESC, device_id DESC)
    # We prefer monitored devices, then recent ones.
    candidates.sort(key=lambda x: (x.is_monitored, x.updated_at or datetime.min, x.device_id), reverse=True)
    
    primary = candidates[0]
    duplicates = candidates[1:]

    # 4. Merge Duplicates
    if duplicates:
        # print(f"[Identity] Merging {len(duplicates)} duplicates into {primary.device_id} ({primary.device_ip})")
        for dup in duplicates:
            # Merge fields if primary is missing them
            if not _is_invalid_mac(dup.macaddress) and _is_invalid_mac(primary.macaddress):
                primary.macaddress = dup.macaddress
            if not _is_invalid_text(dup.hostname) and _is_invalid_text(primary.hostname):
                primary.hostname = dup.hostname
            if not _is_invalid_text(dup.manufacturer) and _is_invalid_text(primary.manufacturer):
                primary.manufacturer = dup.manufacturer
            if not _is_invalid_text(dup.switch_brand) and _is_invalid_text(primary.switch_brand):
                primary.switch_brand = dup.switch_brand
                
            # If dup had a different IP than primary, that might be useful history (skipped for now)
            
            db.session.delete(dup)

    # 5. Update Primary with new data
    updated = False
    normalized_mac = _normalize_mac(mac) if mac and not _is_invalid_mac(mac) else None
    
    if normalized_mac and (_is_invalid_mac(primary.macaddress) or primary.macaddress != normalized_mac):
        primary.macaddress = normalized_mac
        updated = True

    if ip and primary.device_ip != ip:
        previous_ip = primary.device_ip
        primary.device_ip = ip
        updated = True
        
        # Name sync
        if primary.device_name and previous_ip and primary.device_name.startswith("Device-") and previous_ip in primary.device_name:
            primary.device_name = f"Device-{ip}"

    if hostname and _is_invalid_text(primary.hostname):
        primary.hostname = hostname
        updated = True

    if manufacturer and _is_invalid_text(primary.manufacturer):
        primary.manufacturer = manufacturer
        updated = True

    if device_type and _is_invalid_text(primary.device_type):
        primary.device_type = device_type
        updated = True

    if is_monitored is True and not primary.is_monitored:
        primary.is_monitored = True
        updated = True

    if is_active is True and not primary.is_active:
        primary.is_active = True
        updated = True
        
    primary.updated_at = datetime.utcnow()
    
    return primary, ("updated" if updated else "existing"), previous_ip
