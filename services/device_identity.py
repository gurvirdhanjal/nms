import ipaddress
import logging
import re
from datetime import datetime

from sqlalchemy import inspect

from extensions import db
from models.device import Device

logger = logging.getLogger(__name__)


def compute_subnet_cidr(ip_str, default_prefix=24):
    """Derive a CIDR string from an IPv4 address."""
    try:
        net = ipaddress.ip_network(f"{ip_str}/{default_prefix}", strict=False)
        return str(net)
    except (ValueError, TypeError):
        return None


_INVALID_MACS = {"", "n/a", "na", "unknown", "none", "null"}
_INVALID_TEXT = {
    "",
    "n/a",
    "na",
    "unknown",
    "none",
    "null",
    "network device",
    "network_device",
    "network-device",
}

_GENERIC_HOSTNAME_PATTERNS = [
    r"^desktop-[a-z0-9]{6,}$",
    r"^win-[a-z0-9]{6,}$",
    r"^localhost$",
    r"^iphone",
    r"^android",
    r"^galaxy",
    r"^ipad",
    r"^device-\d+",
    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",
]


def _normalize_mac(mac):
    if not mac:
        return None
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(cleaned) != 12:
        return None
    cleaned = cleaned.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def _mac_candidates(mac):
    if not mac:
        return []
    canon = _normalize_mac(mac)
    if not canon:
        return []
    return list(
        {
            mac,
            mac.lower(),
            mac.upper(),
            canon,
            canon.upper(),
            canon.replace(":", "-"),
            canon.replace(":", ""),
        }
    )


def _is_invalid_text(value):
    if value is None:
        return True
    return str(value).strip().lower() in _INVALID_TEXT


def _is_invalid_mac(value):
    if value is None:
        return True
    return str(value).strip().lower() in _INVALID_MACS


def _is_generic_hostname(hostname):
    if not hostname or _is_invalid_text(hostname):
        return True
    name_lower = hostname.strip().lower()
    return any(re.match(pattern, name_lower) for pattern in _GENERIC_HOSTNAME_PATTERNS)


def find_device_by_mac(mac):
    candidates = _mac_candidates(mac)
    if not candidates:
        return None
    return Device.query.filter(Device.macaddress.in_(candidates)).first()


def find_device_by_hostname(hostname):
    """Return a device only when the hostname match is unique and non-generic."""
    if _is_generic_hostname(hostname):
        return None
    matches = Device.query.filter(
        db.func.lower(Device.hostname) == hostname.strip().lower()
    ).all()
    if len(matches) == 1:
        return matches[0]
    return None


def _existing_table_names():
    try:
        return set(inspect(db.engine).get_table_names())
    except Exception as exc:
        logger.warning("[Identity] Could not inspect table names: %s", exc)
        return set()


def _weighted_average(left_value, left_weight, right_value, right_weight):
    left_weight = int(left_weight or 0)
    right_weight = int(right_weight or 0)
    total_weight = left_weight + right_weight
    if total_weight <= 0:
        return left_value if left_value is not None else right_value

    weighted_total = 0.0
    if left_value is not None and left_weight > 0:
        weighted_total += float(left_value) * left_weight
    if right_value is not None and right_weight > 0:
        weighted_total += float(right_value) * right_weight
    return round(weighted_total / total_weight, 2)


def _candidate_rank(device, candidate_match_flags):
    flags = candidate_match_flags.get(device.device_id, set())
    created_at = device.created_at or datetime.max
    updated_at = device.updated_at
    updated_rank = -int(updated_at.timestamp()) if updated_at else 0
    return (
        0 if "mac" in flags else 1,
        0 if "hostname" in flags else 1,
        0 if bool(device.is_monitored) else 1,
        0 if bool(device.site_id) else 1,
        0 if bool(device.department_id) else 1,
        0 if not _is_invalid_text(device.hostname) else 1,
        0 if not _is_invalid_mac(device.macaddress) else 1,
        created_at,
        updated_rank,
        int(device.device_id or 0),
    )


def _propagate_ip_change(device_id, old_ip, new_ip):
    """Keep IP-based history tables aligned with the canonical device IP."""
    if not old_ip or not new_ip or old_ip == new_ip:
        return
    try:
        from models.scan_history import DeviceScanHistory

        updated = DeviceScanHistory.query.filter_by(device_ip=old_ip).update(
            {"device_ip": new_ip},
            synchronize_session=False,
        )
        if updated:
            logger.info(
                "[Identity] Propagated IP change: %s scan_history rows %s -> %s",
                updated,
                old_ip,
                new_ip,
            )
    except Exception as exc:
        logger.warning("[Identity] Could not propagate IP to scan_history: %s", exc)


def _merge_duplicate_daily_stats(primary_id, duplicate_id):
    from models.dashboard import DailyDeviceStats

    duplicate_rows = DailyDeviceStats.query.filter_by(device_id=duplicate_id).all()
    for duplicate_row in duplicate_rows:
        primary_row = DailyDeviceStats.query.filter_by(
            device_id=primary_id,
            date=duplicate_row.date,
        ).first()
        if not primary_row:
            duplicate_row.device_id = primary_id
            continue

        left_scans = int(primary_row.total_scans or 0)
        right_scans = int(duplicate_row.total_scans or 0)
        primary_row.avg_latency_ms = _weighted_average(
            primary_row.avg_latency_ms,
            left_scans,
            duplicate_row.avg_latency_ms,
            right_scans,
        )
        primary_row.avg_packet_loss_pct = _weighted_average(
            primary_row.avg_packet_loss_pct,
            left_scans,
            duplicate_row.avg_packet_loss_pct,
            right_scans,
        )
        if duplicate_row.max_latency_ms is not None:
            primary_row.max_latency_ms = max(
                primary_row.max_latency_ms
                if primary_row.max_latency_ms is not None
                else duplicate_row.max_latency_ms,
                duplicate_row.max_latency_ms,
            )
        if duplicate_row.min_latency_ms is not None:
            primary_row.min_latency_ms = min(
                primary_row.min_latency_ms
                if primary_row.min_latency_ms is not None
                else duplicate_row.min_latency_ms,
                duplicate_row.min_latency_ms,
            )

        primary_row.total_scans = left_scans + right_scans
        primary_row.online_scans = int(primary_row.online_scans or 0) + int(
            duplicate_row.online_scans or 0
        )
        primary_row.total_alerts = int(primary_row.total_alerts or 0) + int(
            duplicate_row.total_alerts or 0
        )
        if primary_row.total_scans:
            primary_row.uptime_percent = round(
                (float(primary_row.online_scans) / float(primary_row.total_scans)) * 100.0,
                2,
            )

        db.session.delete(duplicate_row)


def _merge_duplicate_rollups(model, primary_id, duplicate_id, unique_fields):
    duplicate_rows = model.query.filter_by(device_id=duplicate_id).all()
    for duplicate_row in duplicate_rows:
        lookup = {"device_id": primary_id}
        for field_name in unique_fields:
            lookup[field_name] = getattr(duplicate_row, field_name)
        primary_row = model.query.filter_by(**lookup).first()
        if primary_row:
            db.session.delete(duplicate_row)
            continue
        duplicate_row.device_id = primary_id


def _merge_duplicate_snmp_config(primary_id, duplicate_id):
    from models.snmp_config import DeviceSnmpConfig

    duplicate_cfg = DeviceSnmpConfig.query.filter_by(device_id=duplicate_id).first()
    if not duplicate_cfg:
        return

    primary_cfg = DeviceSnmpConfig.query.filter_by(device_id=primary_id).first()
    if not primary_cfg:
        duplicate_cfg.device_id = primary_id
        return

    copy_if_missing_fields = (
        "community_string",
        "snmp_version",
        "snmp_port",
        "security_name",
        "auth_protocol",
        "auth_password",
        "priv_protocol",
        "priv_password",
        "poll_interval_seconds",
    )
    for field_name in copy_if_missing_fields:
        primary_value = getattr(primary_cfg, field_name, None)
        duplicate_value = getattr(duplicate_cfg, field_name, None)
        if primary_value in (None, "", 0) and duplicate_value not in (None, "", 0):
            setattr(primary_cfg, field_name, duplicate_value)

    primary_cfg.is_enabled = bool(primary_cfg.is_enabled or duplicate_cfg.is_enabled)
    if duplicate_cfg.last_successful_poll and (
        not primary_cfg.last_successful_poll
        or duplicate_cfg.last_successful_poll > primary_cfg.last_successful_poll
    ):
        primary_cfg.last_successful_poll = duplicate_cfg.last_successful_poll
    if duplicate_cfg.last_poll_error and not primary_cfg.last_poll_error:
        primary_cfg.last_poll_error = duplicate_cfg.last_poll_error

    db.session.delete(duplicate_cfg)


def _merge_duplicate_device_dependencies(primary, duplicate, existing_tables=None, target_ip=None):
    primary_id = int(primary.device_id or 0)
    duplicate_id = int(duplicate.device_id or 0)
    if not primary_id or not duplicate_id or primary_id == duplicate_id:
        return

    existing_tables = existing_tables or _existing_table_names()
    target_ip = (target_ip or primary.device_ip or "").strip() or None

    if primary.parent_switch_id == duplicate_id:
        primary.parent_switch_id = None

    duplicate_interface_ids = []
    if "device_interfaces" in existing_tables:
        from models.interfaces import DeviceInterface

        duplicate_interfaces = DeviceInterface.query.filter_by(device_id=duplicate_id).all()
        primary_interfaces = DeviceInterface.query.filter_by(device_id=primary_id).all()
        primary_interfaces_by_index = {
            interface.if_index: interface
            for interface in primary_interfaces
            if interface.if_index is not None
        }

        for duplicate_interface in duplicate_interfaces:
            duplicate_interface_ids.append(duplicate_interface.interface_id)
            mapped_primary = primary_interfaces_by_index.get(duplicate_interface.if_index)
            if mapped_primary:
                Device.query.filter(Device.parent_port_id == duplicate_interface.interface_id).update(
                    {Device.parent_port_id: mapped_primary.interface_id},
                    synchronize_session=False,
                )
                db.session.delete(duplicate_interface)
                continue

            duplicate_interface.device_id = primary_id
            if duplicate_interface.if_index is not None:
                primary_interfaces_by_index[duplicate_interface.if_index] = duplicate_interface

    if duplicate_interface_ids and primary.parent_port_id in duplicate_interface_ids:
        primary.parent_port_id = None

    Device.query.filter(
        Device.parent_switch_id == duplicate_id,
        Device.device_id != primary_id,
    ).update(
        {Device.parent_switch_id: primary_id},
        synchronize_session=False,
    )
    if duplicate_interface_ids:
        Device.query.filter(
            Device.parent_port_id.in_(duplicate_interface_ids),
            Device.device_id != primary_id,
        ).update(
            {Device.parent_port_id: None},
            synchronize_session=False,
        )

    if "switch_topology" in existing_tables:
        from models.topology import SwitchTopology

        SwitchTopology.query.filter_by(remote_device_id=duplicate_id).update(
            {"remote_device_id": primary_id},
            synchronize_session=False,
        )
        SwitchTopology.query.filter_by(local_device_id=duplicate_id).delete(
            synchronize_session=False
        )
        if duplicate_interface_ids:
            SwitchTopology.query.filter(
                SwitchTopology.local_interface_id.in_(duplicate_interface_ids)
            ).delete(synchronize_session=False)

    if "dashboard_events" in existing_tables:
        from models.dashboard import DashboardEvent

        DashboardEvent.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    if "daily_device_stats" in existing_tables:
        _merge_duplicate_daily_stats(primary_id, duplicate_id)

    if "device_snmp_config" in existing_tables:
        _merge_duplicate_snmp_config(primary_id, duplicate_id)

    if "poll_tasks" in existing_tables:
        from models.poll_task import PollTask

        PollTask.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    if "server_health_logs" in existing_tables:
        from models.server_health import ServerHealthLog

        ServerHealthLog.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    if "server_health_hourly_rollups" in existing_tables:
        from models.server_health_rollups import ServerHealthHourlyRollup

        _merge_duplicate_rollups(
            ServerHealthHourlyRollup,
            primary_id,
            duplicate_id,
            ("source", "bucket_hour"),
        )

    if "server_health_daily_rollups" in existing_tables:
        from models.server_health_rollups import ServerHealthDailyRollup

        _merge_duplicate_rollups(
            ServerHealthDailyRollup,
            primary_id,
            duplicate_id,
            ("source", "bucket_day"),
        )

    if "printer_metrics" in existing_tables:
        from models.printer import PrinterMetrics

        PrinterMetrics.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    if "print_job_audit" in existing_tables:
        from models.printer import PrintJobAudit

        PrintJobAudit.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )
        PrintJobAudit.query.filter_by(print_server_id=duplicate_id).update(
            {"print_server_id": primary_id},
            synchronize_session=False,
        )

    if "maintenance_window" in existing_tables:
        from models.maintenance_window import MaintenanceWindow

        MaintenanceWindow.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    if "device_identity_links" in existing_tables:
        from models.device_identity_link import DeviceIdentityLink

        DeviceIdentityLink.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    if "device_identity_link_candidates" in existing_tables:
        from models.device_identity_link_candidate import DeviceIdentityLinkCandidate

        DeviceIdentityLinkCandidate.query.filter_by(device_id=duplicate_id).update(
            {"device_id": primary_id},
            synchronize_session=False,
        )

    duplicate_ip = (duplicate.device_ip or "").strip()
    if (
        "device_scan_history" in existing_tables
        and duplicate_ip
        and target_ip
        and duplicate_ip != target_ip
    ):
        _propagate_ip_change(primary_id, duplicate_ip, target_ip)

    db.session.delete(duplicate)


def upsert_device_from_identity(
    ip,
    mac=None,
    hostname=None,
    manufacturer=None,
    device_type=None,
    is_monitored=None,
    is_active=True,
    site_id=None,
):
    """
    Identity-first device upsert.

    Match priority:
      1. MAC address
      2. Hostname when unique and non-generic
      3. IP address

    Returns: (device, action, previous_ip)
      action: created | updated | existing | skipped
    """
    previous_ip = None
    candidates = []
    candidate_match_flags = {}

    def _record_matches(rows, match_type):
        for row in rows:
            candidates.append(row)
            candidate_match_flags.setdefault(row.device_id, set()).add(match_type)

    if mac and not _is_invalid_mac(mac):
        mac_candidates_list = _mac_candidates(mac)
        mac_matches = Device.query.filter(Device.macaddress.in_(mac_candidates_list)).all()
        _record_matches(mac_matches, "mac")

    if not candidates and hostname and not _is_generic_hostname(hostname):
        hostname_match = find_device_by_hostname(hostname)
        if hostname_match:
            _record_matches([hostname_match], "hostname")
            logger.info(
                "[Identity] Hostname match for '%s' -> device_id=%s",
                hostname,
                hostname_match.device_id,
            )

    if ip:
        ip_matches = Device.query.filter_by(device_ip=ip).all()
        _record_matches(ip_matches, "ip")

    candidates = list({device.device_id: device for device in candidates}.values())

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
            updated_at=datetime.utcnow(),
        )
        db.session.add(device)
        logger.info(
            "[Identity] Created new device: %s @ %s (MAC=%s)",
            device_name,
            ip,
            normalized_mac,
        )
        return device, "created", None

    candidates.sort(key=lambda device: _candidate_rank(device, candidate_match_flags))
    primary = candidates[0]
    duplicates = candidates[1:]
    updated = bool(duplicates)

    if duplicates:
        logger.info(
            "[Identity] Merging %s duplicate(s) into device_id=%s (%s)",
            len(duplicates),
            primary.device_id,
            primary.device_ip,
        )
        existing_tables = _existing_table_names()
        target_ip = ip or primary.device_ip
        for duplicate in duplicates:
            if not _is_invalid_mac(duplicate.macaddress) and _is_invalid_mac(primary.macaddress):
                primary.macaddress = duplicate.macaddress
            if not _is_invalid_text(duplicate.hostname) and _is_invalid_text(primary.hostname):
                primary.hostname = duplicate.hostname
            if not _is_invalid_text(duplicate.manufacturer) and _is_invalid_text(primary.manufacturer):
                primary.manufacturer = duplicate.manufacturer
            if not _is_invalid_text(duplicate.switch_brand) and _is_invalid_text(primary.switch_brand):
                primary.switch_brand = duplicate.switch_brand
            if duplicate.maintenance_mode and not primary.maintenance_mode:
                primary.maintenance_mode = True
            if duplicate.is_monitored and not primary.is_monitored:
                primary.is_monitored = True
            if duplicate.site_id and not primary.site_id:
                primary.site_id = duplicate.site_id
            if duplicate.department_id and not primary.department_id:
                primary.department_id = duplicate.department_id

            _merge_duplicate_device_dependencies(
                primary,
                duplicate,
                existing_tables=existing_tables,
                target_ip=target_ip,
            )

        # Delete duplicates before the canonical row takes over a new IP.
        db.session.flush()

    normalized_mac = _normalize_mac(mac) if mac and not _is_invalid_mac(mac) else None
    if normalized_mac and (_is_invalid_mac(primary.macaddress) or primary.macaddress != normalized_mac):
        primary.macaddress = normalized_mac
        updated = True

    if ip and primary.device_ip != ip:
        previous_ip = primary.device_ip
        primary.device_ip = ip
        primary.subnet_cidr = compute_subnet_cidr(ip)
        updated = True
        logger.info(
            "[Identity] Device %s IP changed: %s -> %s",
            primary.device_id,
            previous_ip,
            ip,
        )
        _propagate_ip_change(primary.device_id, previous_ip, ip)
        if (
            primary.device_name
            and previous_ip
            and primary.device_name.startswith("Device-")
            and previous_ip in primary.device_name
        ):
            primary.device_name = f"Device-{ip}"

    if hostname and _is_invalid_text(primary.hostname):
        primary.hostname = hostname
        updated = True

    if manufacturer and _is_invalid_text(primary.manufacturer):
        primary.manufacturer = manufacturer
        updated = True

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
