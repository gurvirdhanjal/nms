"""
Auto-Discovery Service — Two-Tier Scan Engine
==============================================
Light sweep  : ICMP ping every N minutes, auto-add after M consecutive detections.
Heavy scan   : SNMP enrichment first → fallback to port-scan + OUI lookup.

Thread-safe: a global lock prevents overlapping scans.
"""

import asyncio
import ipaddress
import platform
import subprocess
import re
import threading
import time
from datetime import datetime
from sqlalchemy.orm.exc import StaleDataError, ObjectDeletedError

import psutil

from extensions import db
from models.discovery_config import DiscoveryConfig, get_config
from services.device_identity import upsert_device_from_identity

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_scan_lock = threading.Lock()

# Tracks consecutive ICMP detections for new IPs:
#   { "192.168.1.50": { "count": 2, "mac": "aa:bb:..." } }
_pending_devices: dict = {}

# Global singleton
_auto_discovery_service = None


def get_auto_discovery_service():
    global _auto_discovery_service
    if _auto_discovery_service is None:
        _auto_discovery_service = AutoDiscoveryService()
    return _auto_discovery_service


# ---------------------------------------------------------------------------
# ARP cache helper (passive — no extra traffic)
# ---------------------------------------------------------------------------
def _read_arp_cache() -> dict:
    """Parse local ARP table.  Returns {ip: mac, …}."""
    result = {}
    try:
        is_win = platform.system().lower() == "windows"
        cmd = ["arp", "-a"]
        out = subprocess.check_output(cmd, timeout=5, text=True, creationflags=0x08000000 if is_win else 0)

        for line in out.splitlines():
            # Windows:  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic
            # Linux:    ? (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on eth0
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9A-Fa-f]{2}[:\-][0-9A-Fa-f]{2}[:\-][0-9A-Fa-f]{2}[:\-][0-9A-Fa-f]{2}[:\-][0-9A-Fa-f]{2}[:\-][0-9A-Fa-f]{2})", line)
            if m:
                ip_addr = m.group(1)
                mac = m.group(2).replace("-", ":").lower()
                # Skip broadcast / incomplete
                if mac not in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
                    result[ip_addr] = mac
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class AutoDiscoveryService:

    def __init__(self):
        from services.network_scanner import NetworkScanner
        self.scanner = NetworkScanner()

    # ------------------------------------------------------------------ #
    # LIGHT SWEEP
    # ------------------------------------------------------------------ #
    def run_light_sweep(self, app):
        """ICMP ping sweep across configured subnets.
        Auto-adds devices after N consecutive detections.
        """
        if not _scan_lock.acquire(blocking=False):
            print("[AutoDiscovery] Light sweep skipped — another scan is running.")
            return

        try:
            with app.app_context():
                cfg = get_config()

                if not cfg.enabled:
                    return

                # Resource guard
                if psutil.cpu_percent(interval=0.5) > 85:
                    print("[AutoDiscovery] High CPU — skipping light sweep.")
                    return
                if psutil.virtual_memory().percent > 90:
                    print("[AutoDiscovery] High RAM — skipping light sweep.")
                    return

                subnets = cfg.subnets
                if not subnets:
                    print("[AutoDiscovery] No subnets configured — skipping.")
                    return

                start = time.time()
                new_count = 0
                updated_count = 0
                error_msg = None

                # Pre-read ARP for passive MAC enrichment
                arp_cache = _read_arp_cache()

                try:
                    for subnet_cidr in subnets:
                        n, u = self._sweep_subnet(cfg, subnet_cidr, arp_cache)
                        new_count += n
                        updated_count += u
                except Exception as e:
                    error_msg = str(e)
                    print(f"[AutoDiscovery] Light sweep error: {e}")

                duration = round(time.time() - start, 2)

                # Persist stats
                cfg.last_light_scan = datetime.utcnow()
                cfg.last_scan_duration = duration
                cfg.last_new_count = new_count
                cfg.last_updated_count = updated_count
                cfg.last_error = error_msg
                try:
                    db.session.commit()
                except Exception as commit_error:
                    db.session.rollback()
                    print(f"[AutoDiscovery] Light sweep metadata commit failed: {commit_error}")

                print(f"[AutoDiscovery] Light sweep done in {duration}s — "
                      f"new={new_count}, updated={updated_count}")
        finally:
            try:
                with app.app_context():
                    db.session.remove()
            except Exception as cleanup_error:
                print(f"[AutoDiscovery] Light sweep cleanup warning: {cleanup_error}")
            finally:
                _scan_lock.release()

    def _sweep_subnet(self, cfg, subnet_cidr, arp_cache):
        """Ping every host in a /24-ish subnet, reconcile results."""
        global _pending_devices

        try:
            network = ipaddress.IPv4Network(subnet_cidr, strict=False)
        except Exception as e:
            print(f"[AutoDiscovery] Bad subnet {subnet_cidr}: {e}")
            return 0, 0

        hosts = [str(h) for h in network.hosts()]
        if len(hosts) > 4096:
            print(f"[AutoDiscovery] Subnet too large ({len(hosts)} hosts), skipping.")
            return 0, 0

        # Async ping with semaphore
        concurrency = cfg.max_concurrent_pings or 50
        timeout = cfg.ping_timeout or 2

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(
                self._ping_batch(hosts, concurrency, timeout)
            )
        finally:
            loop.close()

        new_count = 0
        updated_count = 0

        for ip, is_online in results:
            if not is_online:
                # Reset consecutive count for offline IPs
                _pending_devices.pop(ip, None)
                continue

            mac = arp_cache.get(ip)

            # Track consecutive detections
            if ip not in _pending_devices:
                _pending_devices[ip] = {"count": 1, "mac": mac}
                continue

            _pending_devices[ip]["count"] += 1
            if mac and not _pending_devices[ip].get("mac"):
                _pending_devices[ip]["mac"] = mac

            if _pending_devices[ip]["count"] >= (cfg.auto_add_after_n or 2):
                # Device is stable — upsert
                device, action, _ = upsert_device_from_identity(
                    ip=ip,
                    mac=_pending_devices[ip].get("mac"),
                    hostname=None,
                    manufacturer=None,
                    device_type="unknown",
                    is_monitored=cfg.auto_monitor_new,
                    is_active=True,
                )
                if action == "created":
                    new_count += 1
                elif action == "updated":
                    updated_count += 1

                _pending_devices.pop(ip, None)

        if new_count > 0 or updated_count > 0:
            try:
                db.session.commit()
            except Exception as commit_error:
                db.session.rollback()
                print(f"[AutoDiscovery] Light sweep upsert commit failed: {commit_error}")

        return new_count, updated_count

    async def _ping_batch(self, hosts, concurrency, timeout):
        """Ping a list of hosts with bounded concurrency. Returns [(ip, is_online)]."""
        sem = asyncio.Semaphore(concurrency)
        results = []

        async def _ping_one(ip):
            async with sem:
                try:
                    status, _, _ = await self.scanner.ping_device(ip, timeout=timeout, count=1)
                    return ip, status == "Online"
                except Exception:
                    return ip, False

        tasks = [_ping_one(ip) for ip in hosts]
        results = await asyncio.gather(*tasks)
        return results

    # ------------------------------------------------------------------ #
    # HEAVY SCAN — SNMP first, fallback to port scan
    # ------------------------------------------------------------------ #
    def run_heavy_scan(self, app):
        """Enrich existing devices: try SNMP first, fallback to port scan."""
        if not _scan_lock.acquire(blocking=False):
            print("[AutoDiscovery] Heavy scan skipped — another scan is running.")
            return

        try:
            with app.app_context():
                cfg = get_config()

                if not cfg.enabled:
                    return

                # Resource guard
                if psutil.cpu_percent(interval=0.5) > 85:
                    print("[AutoDiscovery] High CPU — skipping heavy scan.")
                    return

                start = time.time()
                error_msg = None
                updated_count = 0

                try:
                    updated_count = self._enrich_devices(cfg)
                except Exception as e:
                    db.session.rollback()
                    error_msg = str(e)
                    print(f"[AutoDiscovery] Heavy scan error: {e}")

                duration = round(time.time() - start, 2)

                cfg.last_heavy_scan = datetime.utcnow()
                cfg.last_scan_duration = duration
                cfg.last_updated_count = updated_count
                cfg.last_error = error_msg
                try:
                    db.session.commit()
                except Exception as commit_error:
                    db.session.rollback()
                    print(f"[AutoDiscovery] Heavy scan metadata commit failed: {commit_error}")

                print(f"[AutoDiscovery] Heavy scan done in {duration}s — updated={updated_count}")
        finally:
            try:
                with app.app_context():
                    db.session.remove()
            except Exception as cleanup_error:
                print(f"[AutoDiscovery] Heavy scan cleanup warning: {cleanup_error}")
            finally:
                _scan_lock.release()

    def _enrich_devices(self, cfg):
        """For each active device, try SNMP enrichment → fallback port scan."""
        from models.device import Device
        from services.snmp_service import snmp_service

        device_ids = [
            row[0]
            for row in db.session.query(Device.device_id).filter_by(is_active=True).all()
        ]
        updated = 0

        for device_id in device_ids:
            device = db.session.get(Device, device_id)
            if not device:
                continue

            ip = device.device_ip
            if not ip:
                continue

            enriched = False
            changed = False

            # ---- Try SNMP first ----
            try:
                # Use default community from config, or device-specific if available
                community = "public"
                version = "2c"
                port = 161

                # Check if device has its own SNMP config
                from models.snmp_config import DeviceSnmpConfig
                snmp_cfg = DeviceSnmpConfig.query.filter_by(
                    device_id=device.device_id, is_enabled=True
                ).first()
                if snmp_cfg:
                    community = snmp_cfg.community_string or community
                    version = snmp_cfg.snmp_version or version
                    port = snmp_cfg.snmp_port or port

                sys_info = snmp_service.get_system_info(ip, community, version, port)

                if sys_info and sys_info.get("sys_name"):
                    # SNMP reachable — enrich
                    sys_name = sys_info.get("sys_name", "")
                    sys_descr = sys_info.get("sys_descr", "")

                    if sys_name and (not device.hostname or device.hostname in ("Unknown", "N/A", "")):
                        device.hostname = sys_name
                        changed = True

                    if sys_descr and (not device.manufacturer or device.manufacturer in ("Unknown", "N/A", "")):
                        # Extract manufacturer hint from sysDescr
                        mfr = _guess_manufacturer_from_descr(sys_descr)
                        if mfr:
                            device.manufacturer = mfr
                            changed = True

                    enriched = True

            except (StaleDataError, ObjectDeletedError) as stale_error:
                db.session.rollback()
                print(f"[AutoDiscovery] Device became stale during SNMP enrichment ({ip}): {stale_error}")
                continue
            except Exception:
                # SNMP not available — will fallback
                pass

            # ---- Fallback: port scan + OUI ----
            if not enriched:
                try:
                    loop = asyncio.new_event_loop()
                    try:
                        open_ports = loop.run_until_complete(
                            self.scanner.scan_ports(ip)
                        )

                        # Try to get manufacturer from MAC OUI
                        if device.macaddress and device.macaddress not in ("N/A", "Unknown", ""):
                            mfr = loop.run_until_complete(
                                self.scanner.get_manufacturer(device.macaddress)
                            )
                            if mfr and (not device.manufacturer or device.manufacturer in ("Unknown", "N/A", "")):
                                device.manufacturer = mfr
                                changed = True
                    finally:
                        loop.close()

                    # Try hostname from DNS
                    hostname = self.scanner.get_hostname(ip)
                    if hostname and (not device.hostname or device.hostname in ("Unknown", "N/A", "")):
                        device.hostname = hostname
                        changed = True

                except Exception:
                    pass

            if changed:
                try:
                    db.session.commit()
                    updated += 1
                except (StaleDataError, ObjectDeletedError) as stale_error:
                    db.session.rollback()
                    print(f"[AutoDiscovery] Device disappeared before commit ({ip}): {stale_error}")
                except Exception as commit_error:
                    db.session.rollback()
                    print(f"[AutoDiscovery] Device enrichment commit failed ({ip}): {commit_error}")

        return updated

    # ------------------------------------------------------------------ #
    # Manual triggers (called from API)
    # ------------------------------------------------------------------ #
    def trigger_heavy_scan(self, app):
        """Run heavy scan in background thread."""
        t = threading.Thread(target=self.run_heavy_scan, args=(app,), daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _guess_manufacturer_from_descr(sys_descr: str) -> str:
    """Best-effort manufacturer extraction from SNMP sysDescr."""
    sys_descr_lower = sys_descr.lower()
    vendors = {
        "cisco": "Cisco",
        "juniper": "Juniper",
        "aruba": "Aruba",
        "hewlett": "HPE",
        "hp ": "HP",
        "dell": "Dell",
        "mikrotik": "MikroTik",
        "ubiquiti": "Ubiquiti",
        "fortinet": "Fortinet",
        "palo alto": "Palo Alto",
        "huawei": "Huawei",
        "linux": "Linux",
        "windows": "Microsoft",
        "net-snmp": "Linux",
        "arista": "Arista",
        "extreme": "Extreme",
        "brocade": "Brocade",
        "tp-link": "TP-Link",
        "d-link": "D-Link",
        "netgear": "Netgear",
        "zyxel": "ZyXEL",
    }
    for keyword, name in vendors.items():
        if keyword in sys_descr_lower:
            return name
    return ""
