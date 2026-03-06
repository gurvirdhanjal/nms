import re
import logging
import ipaddress
from datetime import datetime

from extensions import db
from models.device import Device

logger = logging.getLogger(__name__)


def compute_subnet_cidr(ip_str, default_prefix=24):
    """Derive /24 CIDR string from an IPv4 address.
    Returns None for invalid inputs."""
    try:
        net = ipaddress.ip_network(f"{ip_str}/{default_prefix}", strict=False)
        return str(net)
    except (ValueError, TypeError):
        return None

_INVALID_MACS = {"", "n/a", "na", "unknown", "none", "null"}
_INVALID_TEXT = {"", "n/a", "na", "unknown", "none", "null", "network device", "network_device", "network-device"}

# Hostnames that are auto-generated or too generic for identity matching
_GENERIC_HOSTNAME_PATTERNS = [
    r"^desktop-[a-z0-9]{6,}$",   # DESKTOP-ABC1234
    r"^win-[a-z0-9]{6,}$",       # WIN-ABC1234
    r"^localhost$",
    r"^iphone",
    r"^android",
    r"^galaxy",
    r"^ipad",
    r"^device-\d+",              # Device-10.0.1.5 (auto-named)
    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",  # Bare IP used as hostname
]


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


def _is_generic_hostname(hostname):
    """Return True if hostname is auto-generated or too generic for identity matching."""
    if not hostname or _is_invalid_text(hostname):
        return True
    name_lower = hostname.strip().lower()
    for pattern in _GENERIC_HOSTNAME_PATTERNS:
        if re.match(pattern, name_lower):
            return True
    return False


def find_device_by_mac(mac):
    candidates = _mac_candidates(mac)
    if not candidates:
        return None
    return Device.query.filter(Device.macaddress.in_(candidates)).first()


def find_device_by_hostname(hostname):
    """Find a device by unique, non-generic hostname.
    Returns the device only if exactly ONE match exists (uniqueness check)."""
    if _is_generic_hostname(hostname):
        return None
    matches = Device.query.filter(
        db.func.lower(Device.hostname) == hostname.strip().lower()
    ).all()
    if len(matches) == 1:
        return matches[0]
    return None  # 0 or 2+ matches — not safe to merge


def _propagate_ip_change(device_id, old_ip, new_ip):
    """Update related records when a device's IP changes (global consistency)."""
    try:
        from models.scan_history import DeviceScanHistory
        from sqlalchemy import text
        updated = DeviceScanHistory.query.filter_by(device_ip=old_ip).update(
            {"device_ip": new_ip}, synchronize_session=False
        )
        if updated:
            logger.info(f"[Identity] Propagated IP change: {updated} scan_history rows {old_ip} → {new_ip}")
    except Exception as exc:
        logger.warning(f"[Identity] Could not propagate IP to scan_history: {exc}")


def upsert_device_from_identity(
    ip,
    mac=None,
    hostname=None,
    manufacturer=None,
    device_type=None,
    is_monitored=None,
    is_active=True,
    site_id=None
):
    """
    Identity-first device upsert.

    Match priority:
      1. MAC address (strongest — hardware identity)
      2. Hostname (if unique AND non-generic, fallback when MAC missing)
      3. IP address (weakest — mutable)

    CRITICAL: Handles duplicate cleanup if multiple records match.
    Returns: (device, action, previous_ip)
      action: created | updated | existing | skipped
    """
    previous_ip = None
    
    # ── 1. Gather all candidates (MAC → Hostname → IP) ──
    candidates = []
    
    # 1a. MAC match (primary identity)
    if mac and not _is_invalid_mac(mac):
        mac_candidates_list = _mac_candidates(mac)
        mac_matches = Device.query.filter(Device.macaddress.in_(mac_candidates_list)).all()
        candidates.extend(mac_matches)

    # 1b. Hostname match (secondary identity — only if MAC found nothing)
    if not candidates and hostname and not _is_generic_hostname(hostname):
        hostname_match = find_device_by_hostname(hostname)
        if hostname_match:
            candidates.append(hostname_match)
            logger.info(f"[Identity] Hostname match for '{hostname}' → device_id={hostname_match.device_id}")

    # 1c. IP match (tertiary — weakest)
    if ip:
        ip_matches = Device.query.filter_by(device_ip=ip).all()
        candidates.extend(ip_matches)

    # Dedup candidates by ID
    unique_candidates = {d.device_id: d for d in candidates}
    candidates = list(unique_candidates.values())

    # ── 2. If no candidates, create new ──
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
            subnet_cidr=compute_subnet_cidr(ip),
            site_id=site_id,
            is_monitored=bool(is_monitored) if is_monitored is not None else False,
            is_active=bool(is_active),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.session.add(device)
        logger.info(f"[Identity] Created new device: {device_name} @ {ip} (MAC={normalized_mac})")
        return device, "created", None

    # ── 3. Pick primary (monitored > recent > highest ID) ──
    candidates.sort(key=lambda x: (x.is_monitored, x.updated_at or datetime.min, x.device_id), reverse=True)
    
    primary = candidates[0]
    duplicates = candidates[1:]

    # ── 4. Merge duplicates ──
    if duplicates:
        logger.info(f"[Identity] Merging {len(duplicates)} duplicate(s) into device_id={primary.device_id} ({primary.device_ip})")
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
            # Preserve operator-set fields from dup if primary lacks them
            if dup.maintenance_mode and not primary.maintenance_mode:
                primary.maintenance_mode = True
            if dup.is_monitored and not primary.is_monitored:
                primary.is_monitored = True
            if dup.site_id and not primary.site_id:
                primary.site_id = dup.site_id
                
            db.session.delete(dup)

    # ── 5. Update primary with new data ──
    updated = False
    normalized_mac = _normalize_mac(mac) if mac and not _is_invalid_mac(mac) else None
    
    if normalized_mac and (_is_invalid_mac(primary.macaddress) or primary.macaddress != normalized_mac):
        primary.macaddress = normalized_mac
        updated = True

    if ip and primary.device_ip != ip:
        previous_ip = primary.device_ip
        primary.device_ip = ip
        primary.subnet_cidr = compute_subnet_cidr(ip)
        updated = True
        logger.info(f"[Identity] Device {primary.device_id} IP changed: {previous_ip} → {ip}")
        
        # Global consistency: propagate IP to related records
        _propagate_ip_change(primary.device_id, previous_ip, ip)
        
        # Name sync (only for auto-named devices)
        if primary.device_name and previous_ip and primary.device_name.startswith("Device-") and previous_ip in primary.device_name:
            primary.device_name = f"Device-{ip}"

    if hostname and _is_invalid_text(primary.hostname):
        primary.hostname = hostname
        updated = True

    if manufacturer and _is_invalid_text(primary.manufacturer):
        primary.manufacturer = manufacturer
        updated = True

    # Only update device_type if NOT manually classified
    if device_type and _is_invalid_text(primary.device_type):
        conf = (primary.classification_confidence or "").strip().lower()
        if conf != "manual":
            primary.device_type = device_type
            updated = True

    if is_monitored is True and not primary.is_monitored:
        primary.is_monitored = True
        updated = True

    if is_active is True and not primary.is_active:
        primary.is_active = True
        updated = True
        
    if site_id and primary.site_id is None:
        primary.site_id = site_id
        updated = True
        
    primary.updated_at = datetime.utcnow()
    
    return primary, ("updated" if updated else "existing"), previous_ip
