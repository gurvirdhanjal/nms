import ipaddress
import logging
from collections import deque
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

try:
    from pysnmp.hlapi import (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        nextCmd,
        getCmd,
    )
    PYSNMP_AVAILABLE = True
except ImportError:
    PYSNMP_AVAILABLE = False
    log.warning("pysnmp not available. Switch SNMP discovery disabled.")


# ---------------------------
# OIDs (Cisco CDP + Standard MIBs)
# ---------------------------
OID_CDP_DEVICE_ID = "1.3.6.1.4.1.9.9.23.1.2.1.1.6"
OID_CDP_ADDRESS = "1.3.6.1.4.1.9.9.23.1.2.1.1.4"
OID_CDP_DEVICE_PORT = "1.3.6.1.4.1.9.9.23.1.2.1.1.7"
OID_CDP_PLATFORM = "1.3.6.1.4.1.9.9.23.1.2.1.1.8"
OID_CDP_CAPABILITIES = "1.3.6.1.4.1.9.9.23.1.2.1.1.9"

# LLDP (fallback)
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_MAN_ADDR = "1.0.8802.1.1.2.1.4.2.1.4"

# Bridge MIB (MAC table)
OID_FDB_ADDRESS = "1.3.6.1.2.1.17.4.3.1.1"
OID_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
OID_FDB_STATUS = "1.3.6.1.2.1.17.4.3.1.3"
OID_DOT1D_BASEPORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"

# Interfaces
OID_IFNAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IFDESCR = "1.3.6.1.2.1.2.2.1.2"

# ARP table
OID_IPNETTOMEDIA_PHYS = "1.3.6.1.2.1.4.22.1.2"


def _oid_to_tuple(oid_str: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in oid_str.split(".") if x)


def _mac_from_bytes(raw: bytes) -> Optional[str]:
    if not raw or len(raw) < 6:
        return None
    return ":".join(f"{b:02X}" for b in raw[:6])


def _mac_from_oid_suffix(suffix: Tuple[int, ...]) -> Optional[str]:
    if len(suffix) < 6:
        return None
    mac_bytes = suffix[-6:]
    return ":".join(f"{b:02X}" for b in mac_bytes)


def _ip_from_octets(raw: bytes) -> Optional[str]:
    if not raw:
        return None
    if len(raw) >= 4:
        ip_bytes = raw[-4:]
        return ".".join(str(b) for b in ip_bytes)
    return None


def _safe_octets(value) -> bytes:
    try:
        return bytes(value.asNumbers())
    except Exception:
        try:
            return bytes(value)
        except Exception:
            return b""


def _snmp_value_to_int(value) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


class SnmpDiscovery:
    def __init__(self, community: str = "public", version: str = "2c", timeout: int = 2, retries: int = 1):
        self.community = community
        self.version = version
        self.timeout = timeout
        self.retries = retries

    # ---------------------------
    # SNMP helpers
    # ---------------------------
    def _community_data(self):
        if self.version == "1":
            return CommunityData(self.community, mpModel=0)
        return CommunityData(self.community, mpModel=1)  # v2c

    def _transport(self, ip: str):
        return UdpTransportTarget((ip, 161), timeout=self.timeout, retries=self.retries)

    def snmp_walk(self, ip: str, oid: str):
        results = []
        for (error_indication, error_status, error_index, var_binds) in nextCmd(
            SnmpEngine(),
            self._community_data(),
            self._transport(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            if error_indication:
                raise RuntimeError(str(error_indication))
            if error_status:
                raise RuntimeError(f"{error_status.prettyPrint()} at {error_index}")
            for oid_obj, value in var_binds:
                results.append((tuple(int(x) for x in oid_obj), value))
        return results

    def snmp_get(self, ip: str, oid: str):
        error_indication, error_status, error_index, var_binds = nextCmd(
            SnmpEngine(),
            self._community_data(),
            self._transport(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
            maxRows=1,
        ).__next__()

        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            raise RuntimeError(f"{error_status.prettyPrint()} at {error_index}")
        if var_binds:
            return var_binds[0][1]
        return None

    # ---------------------------
    # Interface map
    # ---------------------------
    def get_ifname_map(self, ip: str) -> Tuple[Dict[int, str], Dict[int, str]]:
        ifname_map: Dict[int, str] = {}
        ifdescr_map: Dict[int, str] = {}

        base_ifname = _oid_to_tuple(OID_IFNAME)
        for oid, val in self.snmp_walk(ip, OID_IFNAME):
            suffix = oid[len(base_ifname):]
            if not suffix:
                continue
            idx = suffix[0]
            ifname_map[idx] = str(val)

        base_ifdescr = _oid_to_tuple(OID_IFDESCR)
        for oid, val in self.snmp_walk(ip, OID_IFDESCR):
            suffix = oid[len(base_ifdescr):]
            if not suffix:
                continue
            idx = suffix[0]
            ifdescr_map[idx] = str(val)

        return ifname_map, ifdescr_map

    # ---------------------------
    # CDP neighbors
    # ---------------------------
    def get_cdp_neighbors(self, ip: str, ifname_map: Dict[int, str], ifdescr_map: Dict[int, str]):
        entries: Dict[Tuple[int, int], Dict] = {}

        def ensure(idx):
            if idx not in entries:
                entries[idx] = {}

        base_dev_id = _oid_to_tuple(OID_CDP_DEVICE_ID)
        for oid, val in self.snmp_walk(ip, OID_CDP_DEVICE_ID):
            suffix = oid[len(base_dev_id):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            entries[idx]["device_id"] = str(val)

        base_addr = _oid_to_tuple(OID_CDP_ADDRESS)
        for oid, val in self.snmp_walk(ip, OID_CDP_ADDRESS):
            suffix = oid[len(base_addr):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            ip_addr = _ip_from_octets(_safe_octets(val))
            entries[idx]["ip"] = ip_addr

        base_port = _oid_to_tuple(OID_CDP_DEVICE_PORT)
        for oid, val in self.snmp_walk(ip, OID_CDP_DEVICE_PORT):
            suffix = oid[len(base_port):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            entries[idx]["device_port"] = str(val)

        base_plat = _oid_to_tuple(OID_CDP_PLATFORM)
        for oid, val in self.snmp_walk(ip, OID_CDP_PLATFORM):
            suffix = oid[len(base_plat):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            entries[idx]["platform"] = str(val)

        base_caps = _oid_to_tuple(OID_CDP_CAPABILITIES)
        for oid, val in self.snmp_walk(ip, OID_CDP_CAPABILITIES):
            suffix = oid[len(base_caps):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            entries[idx]["capabilities"] = _snmp_value_to_int(val)

        neighbors = []
        for (if_index, dev_index), data in entries.items():
            caps = data.get("capabilities") or 0
            is_switch = bool(caps & 0x08) or bool(caps & 0x02)
            neighbors.append({
                "device_id": data.get("device_id"),
                "ip": data.get("ip"),
                "platform": data.get("platform"),
                "device_port": data.get("device_port"),
                "capabilities": caps,
                "is_switch": is_switch,
                "local_if_index": if_index,
                "local_interface": ifname_map.get(if_index) or ifdescr_map.get(if_index),
            })

        return neighbors

    # ---------------------------
    # LLDP neighbors (fallback)
    # ---------------------------
    def get_lldp_neighbors(self, ip: str, ifname_map: Dict[int, str], ifdescr_map: Dict[int, str]):
        entries: Dict[Tuple[int, int], Dict] = {}

        def ensure(idx):
            if idx not in entries:
                entries[idx] = {}

        base_sysname = _oid_to_tuple(OID_LLDP_REM_SYS_NAME)
        for oid, val in self.snmp_walk(ip, OID_LLDP_REM_SYS_NAME):
            suffix = oid[len(base_sysname):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            entries[idx]["device_id"] = str(val)

        base_manaddr = _oid_to_tuple(OID_LLDP_REM_MAN_ADDR)
        for oid, val in self.snmp_walk(ip, OID_LLDP_REM_MAN_ADDR):
            suffix = oid[len(base_manaddr):]
            if len(suffix) < 2:
                continue
            idx = (suffix[-2], suffix[-1])
            ensure(idx)
            ip_addr = _ip_from_octets(_safe_octets(val))
            entries[idx]["ip"] = ip_addr

        neighbors = []
        for (local_port_num, rem_index), data in entries.items():
            neighbors.append({
                "device_id": data.get("device_id"),
                "ip": data.get("ip"),
                "platform": None,
                "device_port": None,
                "capabilities": None,
                "is_switch": None,
                "local_if_index": local_port_num,
                "local_interface": ifname_map.get(local_port_num) or ifdescr_map.get(local_port_num),
            })
        return neighbors

    # ---------------------------
    # MAC table + ARP
    # ---------------------------
    def get_fdb_entries(self, ip: str):
        entries: Dict[str, Dict] = {}

        base_fdb_addr = _oid_to_tuple(OID_FDB_ADDRESS)
        for oid, val in self.snmp_walk(ip, OID_FDB_ADDRESS):
            suffix = oid[len(base_fdb_addr):]
            mac = _mac_from_oid_suffix(suffix) or _mac_from_bytes(_safe_octets(val))
            if not mac:
                continue
            entries.setdefault(mac, {})["mac"] = mac

        base_fdb_port = _oid_to_tuple(OID_FDB_PORT)
        for oid, val in self.snmp_walk(ip, OID_FDB_PORT):
            suffix = oid[len(base_fdb_port):]
            mac = _mac_from_oid_suffix(suffix)
            if not mac:
                continue
            entries.setdefault(mac, {})["bridge_port"] = _snmp_value_to_int(val)

        base_fdb_status = _oid_to_tuple(OID_FDB_STATUS)
        for oid, val in self.snmp_walk(ip, OID_FDB_STATUS):
            suffix = oid[len(base_fdb_status):]
            mac = _mac_from_oid_suffix(suffix)
            if not mac:
                continue
            entries.setdefault(mac, {})["status"] = _snmp_value_to_int(val)

        return entries

    def get_bridge_port_map(self, ip: str) -> Dict[int, int]:
        mapping: Dict[int, int] = {}
        base_map = _oid_to_tuple(OID_DOT1D_BASEPORT_IFINDEX)
        for oid, val in self.snmp_walk(ip, OID_DOT1D_BASEPORT_IFINDEX):
            suffix = oid[len(base_map):]
            if not suffix:
                continue
            bridge_port = suffix[0]
            if_index = _snmp_value_to_int(val)
            if if_index is not None:
                mapping[bridge_port] = if_index
        return mapping

    def get_arp_table(self, ip: str) -> Dict[str, str]:
        mac_to_ip: Dict[str, str] = {}
        base = _oid_to_tuple(OID_IPNETTOMEDIA_PHYS)
        for oid, val in self.snmp_walk(ip, OID_IPNETTOMEDIA_PHYS):
            suffix = oid[len(base):]
            if len(suffix) < 5:
                continue
            if_index = suffix[0]
            ip_addr = ".".join(str(x) for x in suffix[1:5])
            if ip_addr == "0.0.0.0":
                continue
            mac = _mac_from_bytes(_safe_octets(val))
            if not mac:
                continue
            # Prefer the first IP we see for a given MAC
            mac_to_ip.setdefault(mac, ip_addr)
        return mac_to_ip

    # ---------------------------
    # Switch inspection
    # ---------------------------
    def inspect_switch(self, ip: str) -> Dict:
        ifname_map, ifdescr_map = self.get_ifname_map(ip)
        neighbors = []
        errors = []

        try:
            neighbors = self.get_cdp_neighbors(ip, ifname_map, ifdescr_map)
        except Exception as e:
            errors.append(f"CDP error: {e}")

        if not neighbors:
            try:
                neighbors = self.get_lldp_neighbors(ip, ifname_map, ifdescr_map)
            except Exception as e:
                errors.append(f"LLDP error: {e}")

        uplink_ifindexes = {
            n["local_if_index"]
            for n in neighbors
            if n.get("is_switch") is True and n.get("local_if_index") is not None
        }

        # MAC table + ARP
        fdb = self.get_fdb_entries(ip)
        bridge_port_map = self.get_bridge_port_map(ip)
        mac_to_ip = self.get_arp_table(ip)

        devices = []
        for mac, entry in fdb.items():
            status = entry.get("status")
            # Only learned entries (3) are usually "real" devices
            if status not in (3, None):
                continue

            bridge_port = entry.get("bridge_port")
            if_index = bridge_port_map.get(bridge_port)
            if if_index in uplink_ifindexes:
                continue

            ip_addr = mac_to_ip.get(mac)
            iface_name = None
            if if_index is not None:
                iface_name = ifname_map.get(if_index) or ifdescr_map.get(if_index)

            devices.append({
                "mac": mac,
                "ip": ip_addr,
                "bridge_port": bridge_port,
                "if_index": if_index,
                "interface": iface_name,
            })

        return {
            "ip": ip,
            "neighbors": neighbors,
            "devices": devices,
            "errors": errors,
        }

    # ---------------------------
    # Full discovery (BFS)
    # ---------------------------
    def discover(self, seed_ip: str, max_depth: int = 3, max_switches: int = 50, on_switch=None) -> List[Dict]:
        switches = []
        visited = set()
        queue = deque([(seed_ip, 0)])

        while queue and len(visited) < max_switches:
            ip, depth = queue.popleft()
            if ip in visited:
                continue
            visited.add(ip)

            try:
                ipaddress.IPv4Address(ip)
            except Exception:
                continue

            result = self.inspect_switch(ip)
            switches.append(result)
            if on_switch:
                on_switch({"visited": len(visited), "ip": ip, "depth": depth, "queue": len(queue)})

            if depth >= max_depth:
                continue

            for n in result.get("neighbors", []):
                n_ip = n.get("ip")
                if n_ip and n.get("is_switch") is True:
                    if n_ip not in visited:
                        queue.append((n_ip, depth + 1))

        return switches
