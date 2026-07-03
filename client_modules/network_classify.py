import os
import platform
import socket
import subprocess

import psutil

_WIRELESS_NAME_TOKENS = ("wi-fi", "wifi", "wireless", "wlan", "wlp", "802.11", "wl")


def _iface_for_ip(ip_address):
    """Return the interface name that owns the given IPv4 address, if any."""
    if not ip_address:
        return None
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and (addr.address or "").strip() == ip_address:
                    return iface
    except Exception:
        pass
    return None


def _classify_interface(iface):
    """Classify an interface name as 'wifi', 'lan', or 'unknown'.

    Linux is authoritative via /sys/class/net/<iface>/wireless. Windows uses a
    name heuristic confirmed against `netsh wlan show interfaces`.
    """
    if not iface:
        return "unknown"

    system = platform.system()

    if system == "Linux":
        try:
            if os.path.isdir(f"/sys/class/net/{iface}/wireless") or \
               os.path.exists(f"/sys/class/net/{iface}/phy80211"):
                return "wifi"
            # A present interface dir without wireless markers is wired.
            if os.path.isdir(f"/sys/class/net/{iface}"):
                return "lan"
        except Exception:
            pass

    name = iface.lower()
    name_is_wireless = any(tok in name for tok in _WIRELESS_NAME_TOKENS)

    if system == "Windows":
        try:
            out = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=4,
            ).stdout.lower()
            # If this interface name appears in the wlan list, it's WiFi.
            if iface.lower() in out:
                return "wifi"
            # netsh ran and did not list it → treat as wired.
            if "name" in out:
                return "lan"
        except Exception:
            pass

    if name_is_wireless:
        return "wifi"
    # Common wired tokens
    if any(tok in name for tok in ("eth", "ethernet", "lan", "enp", "eno", "ens", "en0")):
        return "lan"
    return "unknown"


def detect_connection_type(ip_address=None):
    """Best-effort: classify the active network link as 'wifi' | 'lan' | 'unknown'.

    ICMP cannot reveal this; the agent runs on the device so it inspects its own
    active interface locally. Never raises — returns 'unknown' on any failure.
    """
    try:
        iface = _iface_for_ip(ip_address) if ip_address else None
        if iface:
            result = _classify_interface(iface)
            if result != "unknown":
                return result
        # Fallback: scan up interfaces and prefer a wireless classification.
        try:
            stats = psutil.net_if_stats()
            best = "unknown"
            for name, st in stats.items():
                if not getattr(st, "isup", False):
                    continue
                if name.lower().startswith("lo") or "loopback" in name.lower():
                    continue
                cls = _classify_interface(name)
                if cls == "wifi":
                    return "wifi"
                if cls == "lan":
                    best = "lan"
            return best
        except Exception:
            return "unknown"
    except Exception:
        return "unknown"
