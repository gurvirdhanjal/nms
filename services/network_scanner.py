import asyncio
import inspect
import socket
import subprocess
import platform
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from mac_vendor_lookup import MacLookup
import psutil
from datetime import datetime
from itertools import islice
import json
import urllib.request
import struct
import random
import time
import logging
import re

from services.operational_error_handling import log_operational_exception

logger = logging.getLogger(__name__)

class NetworkScanner:
    """
    Faster + safe NetworkScanner that keeps your current architecture:

    - Keeps the same public methods and signatures:
        get_local_ip_range()
        get_mac_address(ip)
        get_hostname(ip)
        get_manufacturer(mac)
        ping_device(ip, timeout=2)
        scan_ports(ip, ports=[...])
        scan_single_device(ip)
        scan_network_range(ip_range=None)
        scan_network_range_incremental(ip_range=None, scan_id=None, active_scans=None)
        process_batch_results(...)

    - Faster:
        * Uses async concurrency with a semaphore
        * Avoids recreating ThreadPoolExecutor per device
        * Avoids building huge host lists for large CIDRs
        * Low overhead progress updates

    - Safer:
        * No shell=True in subprocess (prevents injection)
        * Timeouts for ARP calls
        * Stops quickly when requested
        * Limits scan size by default (prevents freezing)
    """

    # Safety caps (tune as needed)
    MAX_HOSTS_DEFAULT = 254           # keeps your current behavior (fast + safe)
    MAX_HOSTS_HARD_CAP = 4096        # hard safety cap to avoid freezes
    DEFAULT_WORKERS = 80             # async concurrency (safe on LAN; tune 40-120)
    EXECUTOR_WORKERS = 32            # threads for blocking ops; raised from 12 to handle 200+ concurrent ping_batch calls
    VIRTUAL_INTERFACE_HINTS = (
        "loopback",
        "vmware",
        "virtual",
        "vbox",
        "hyper-v",
        "vethernet",
        "docker",
        "br-",
        "virbr",
        "tailscale",
        "zerotier",
        "hamachi",
        "tun",
        "tap",
        "wireguard",
        "wg",
        "vpn",
        "npcap",
    )
    PREFERRED_INTERFACE_HINTS = (
        "ethernet",
        "wi-fi",
        "wifi",
        "wlan",
        "lan",
        "eth",
        "en",
    )

    def __init__(self):
        self.mac_lookup = MacLookup()
        self.timeout = 2
        self.workers = self.DEFAULT_WORKERS

        # One executor for the lifetime of the object (BIG speed improvement)
        self._executor = ThreadPoolExecutor(max_workers=self.EXECUTOR_WORKERS)

        # Manufacturer cache (MAC prefix → vendor). Bounded to prevent unbounded
        # memory growth over weeks of continuous scanning (each unique OUI added once,
        # never evicted in the original code).  5 000 entries ≈ all real-world OUIs
        # ever seen in a typical enterprise network, ~200 KB resident.
        self._vendor_cache: dict[str, str] = {}
        self._vendor_cache_max = 5000

    # ---------------------------
    # Local network detection
    # ---------------------------

    def _detect_primary_ipv4(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            sock.connect(("8.8.8.8", 80))
            primary_ip = sock.getsockname()[0]
            sock.close()
            return primary_ip
        except Exception:
            return None

    def _build_ipv4_network(self, ip_address, netmask):
        if not ip_address or not netmask:
            return None
        try:
            return ipaddress.IPv4Network(f"{ip_address}/{netmask}", strict=False)
        except Exception:
            return None

    def _is_virtual_interface(self, interface_name):
        normalized = (interface_name or "").strip().lower()
        return any(hint in normalized for hint in self.VIRTUAL_INTERFACE_HINTS)

    def _is_preferred_interface(self, interface_name):
        normalized = (interface_name or "").strip().lower()
        return any(hint in normalized for hint in self.PREFERRED_INTERFACE_HINTS)

    def _score_interface_candidate(self, interface_name, ip_address, network, primary_ip):
        ip_obj = ipaddress.IPv4Address(ip_address)
        score = 0

        if ip_address == primary_ip:
            score += 24
        if ip_obj.is_private:
            score += 40
        else:
            score -= 20
        if ip_obj.is_link_local:
            score -= 70
        if self._is_virtual_interface(interface_name):
            score -= 48
        if self._is_preferred_interface(interface_name):
            score += 10
        if network.prefixlen >= 31:
            score -= 40

        return score

    def _iter_ipv4_candidates(self, interfaces, stats, primary_ip):
        candidates = []

        for interface_name, addrs in interfaces.items():
            interface_stats = stats.get(interface_name)
            if interface_stats and not getattr(interface_stats, "isup", False):
                continue

            for addr in addrs:
                if getattr(addr, "family", None) != socket.AF_INET:
                    continue

                ip_address = getattr(addr, "address", None)
                if not ip_address:
                    continue

                try:
                    ip_obj = ipaddress.IPv4Address(ip_address)
                except Exception:
                    continue

                if ip_obj.is_loopback or ip_obj.is_unspecified or ip_obj.is_multicast:
                    continue

                network = self._build_ipv4_network(ip_address, getattr(addr, "netmask", None))
                if network is None:
                    continue

                candidates.append({
                    "interface": interface_name,
                    "ip": ip_address,
                    "network": str(network),
                    "prefixlen": network.prefixlen,
                    "is_private": ip_obj.is_private and not ip_obj.is_link_local,
                    "score": self._score_interface_candidate(interface_name, ip_address, network, primary_ip),
                })

        return candidates

    def get_local_ip_range(self):
        """Get the local IP range based on the machine's primary network interface."""
        try:
            primary_ip = self._detect_primary_ipv4()
            interfaces = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            candidates = self._iter_ipv4_candidates(interfaces, stats, primary_ip)
            if candidates:
                candidates.sort(
                    key=lambda item: (
                        item["score"],
                        1 if item["is_private"] else 0,
                        1 if item["ip"] == primary_ip else 0,
                        item["prefixlen"],
                    ),
                    reverse=True,
                )
                selected = candidates[0]
                logger.info(
                    "Selected local IP range %s via interface=%s ip=%s primary_ip=%s score=%s",
                    selected["network"],
                    selected["interface"],
                    selected["ip"],
                    primary_ip,
                    selected["score"],
                )
                return selected["network"]

        except Exception as e:
            logger.warning("Error getting local IP range: %s", e)

        # fallback
        return ipaddress.IPv4Network("192.168.1.0/24")

    # ---------------------------
    # Host info helpers
    # ---------------------------

    def get_mac_address(self, ip_address: str) -> str:
        """
        Get MAC address for an IP address from ARP cache.
        SAFE: no shell=True + timeout
        NOTE: ARP will often be empty unless we already pinged the host (we do).
        """
        try:
            sysname = platform.system().lower()

            if sysname == "windows":
                # arp -a <ip>
                cmd = ["arp", "-a", ip_address]
            else:
                # arp -n <ip>
                cmd = ["arp", "-n", ip_address]

            arp_output = subprocess.check_output(
                cmd,
                stderr=subprocess.DEVNULL,
                timeout=2
            ).decode("utf-8", errors="ignore")

            # Parse output for MAC pattern
            for line in arp_output.splitlines():
                if ip_address not in line:
                    continue
                parts = line.split()
                for part in parts:
                    # supports xx-xx-xx-xx-xx-xx or xx:xx:xx:xx:xx:xx
                    if (":" in part or "-" in part) and len(part) >= 12:
                        return part.upper().replace("-", ":")

        except Exception:
            pass

        return "N/A"

    def get_hostname(self, ip_address: str) -> str:
        """Get hostname using DNS -> NetBIOS -> mDNS."""
        # 1. Try standard DNS (Reverse Lookup)
        try:
            return socket.gethostbyaddr(ip_address)[0]
        except Exception:
            pass

        # 2. Try NetBIOS (Port 137) - Great for Windows
        nb_name = self.get_netbios_name(ip_address)
        if nb_name:
            return nb_name

        # 3. Try mDNS (Port 5353) - Great for Apple/IoT
        mdns_name = self.get_mdns_name(ip_address)
        if mdns_name:
            return mdns_name

        return "Unknown"

    def get_netbios_name(self, ip_address: str) -> str:
        """Detailed NetBIOS Node Status Query (Raw UDP)"""
        try:
            # Transaction ID
            txn_id = struct.pack('>H', random.randint(1, 65535))
            # Flags: Query, Opcode=0, AA=0, TC=0, RD=1, RA=0
            flags = b'\x00\x00' 
            # Questions: 1
            questions = b'\x00\x01'
            # AnswerRRs: 0, AuthorityRRs: 0, AdditionalRRs: 0
            others = b'\x00\x00\x00\x00\x00\x00'
            # Query Name: CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA (Wildcard encoded)
            # This is the standard "wildcard" for NetBIOS status query
            encoded_name = b'\x20CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\x00'
            # Type: NB STAT (0x0021), Class: IN (0x0001)
            footer = b'\x00\x21\x00\x01'

            packet = txn_id + flags + questions + others + encoded_name + footer

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.5) # Fast timeout
            sock.sendto(packet, (ip_address, 137))
            
            data, _ = sock.recvfrom(1024)
            sock.close()

            # Parse Response
            # Skip Header (12 bytes) + Query Name (34 bytes) + Type/Class (4 bytes)
            # Then TTL (4), DataLength (2)
            # The Name array usually starts around offset 57
            # Simple heuristic parser to find the first readable name
            
            if len(data) > 57:
                # Number of names is at offset 56
                num_names = data[56]
                offset = 57
                for _ in range(num_names):
                    # Each name is 15 bytes + 1 byte suffix
                    name_bytes = data[offset:offset+15]
                    try:
                        name = name_bytes.decode('utf-8', errors='ignore').strip()
                        # Accept hostnames with hyphens/underscores (e.g. DESKTOP-PC1, PC_FLOOR2)
                        if name and name.replace('-', '').replace('_', '').replace('.', '').isalnum():
                            return name
                    except Exception:
                        pass
                    offset += 18 # 16 byte name + 2 byte flags
            
        except Exception:
            pass
        return None

    def get_mdns_name(self, ip_address: str) -> str:
        """Detailed mDNS Reverse Lookup (Raw UDP)"""
        try:
            # Reverse IP for PTR query
            try:
                rev_ip = ".".join(reversed(ip_address.split("."))) + ".in-addr.arpa"
            except Exception:
                return None

            # Build Packet
            txn_id = struct.pack('>H', random.randint(1, 65535))
            flags = b'\x00\x00' # Standard Query
            questions = b'\x00\x01'
            others = b'\x00\x00\x00\x00\x00\x00'

            # Encode QNAME
            qname = b''
            for part in rev_ip.split('.'):
                qname += struct.pack('B', len(part)) + part.encode('utf-8')
            qname += b'\x00'

            # Type: PTR (12), Class: IN (1)
            footer = b'\x00\x0c\x00\x01' # QTYPE=PTR, QCLASS=IN

            packet = txn_id + flags + questions + others + qname + footer

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.5)
            # mDNS multicast address
            # Sending to the device directly on 5353 sometimes works,
            # but standard is mcast 224.0.0.251.
            # We try unicast first as it's less noisy/blocked.
            sock.sendto(packet, (ip_address, 5353))
            
            data, _ = sock.recvfrom(1024)
            sock.close()

            # Parse simple response
            # Look for the PTR record data at the end
            # This is a hacky parser but avoids 'dnspython' dependency
            if len(data) > len(packet):
                 # Try to extract readable strings from the end of packet
                 # which is usually the target hostname
                 clean_data = data[len(packet):]
                 # Find strings that end in .local
                 try:
                     text = clean_data.decode('utf-8', errors='ignore')
                     # Extract readable chunks
                     parts = [p for p in text.split('\x00') if len(p) > 2]
                     for p in parts:
                         # Start with length byte, so skip first char if it's unreadable
                         clean_p = ''.join(filter(lambda x: 32 <= ord(x) <= 126, p))
                         if clean_p.endswith('local'):
                             return clean_p[:-6] # strip .local
                         if '.' in clean_p: # fallback
                             return clean_p
                 except Exception:
                     pass

        except Exception:
            pass
        return None

    def _nmap_available(self) -> bool:
        try:
            subprocess.run(['nmap', '--version'], capture_output=True, timeout=3)
            return True
        except Exception:
            return False

    def _nmap_quick_host(self, ip: str) -> dict:
        """
        Runs 'nmap -sn' to get hostname (PTR/rDNS) and MAC (if running as root via ARP).
        Returns a dict with keys 'mac' and/or 'hostname', empty dict on failure.
        """
        try:
            result = subprocess.run(
                ['nmap', '-sn', '--host-timeout', '5s', ip],
                capture_output=True, text=True, timeout=8
            )
            output = result.stdout

            import re as _re
            mac_m = _re.search(r'MAC Address:\s*([0-9A-Fa-f:]{17})\s*\(([^)]+)\)', output)
            # "Nmap scan report for hostname (ip)" or "Nmap scan report for ip"
            host_m = _re.search(r'Nmap scan report for (.+?) \(', output)

            info = {}
            if mac_m:
                info['mac'] = mac_m.group(1).upper()
                info['manufacturer'] = mac_m.group(2).strip()
            if host_m:
                candidate = host_m.group(1).strip()
                # Only use if it's an actual hostname, not an IP string
                if not _re.match(r'^\d+\.\d+\.\d+\.\d+$', candidate):
                    info['hostname'] = candidate
            return info
        except Exception:
            return {}

    def _nmap_port_scan(self, ip: str, ports: list) -> list:
        """
        Runs nmap TCP connect scan (-sT) on the given ports.
        Returns list of {port, status, service, protocol} dicts, including 'closed' entries.
        Falls back gracefully if nmap is unavailable.
        """
        if not ports:
            return []
        try:
            port_spec = ','.join(str(p) for p in ports)
            result = subprocess.run(
                ['nmap', '-sT', '-p', port_spec, '--host-timeout', '10s', '--open', ip],
                capture_output=True, text=True, timeout=15
            )
            import re as _re
            entries = []
            for line in result.stdout.splitlines():
                m = _re.match(r'^(\d+)/(tcp|udp)\s+(open|closed|filtered)\s+(\S+)', line.strip())
                if m:
                    entries.append({
                        'port': int(m.group(1)),
                        'protocol': m.group(2).upper(),
                        'status': m.group(3),
                        'service': m.group(4) if m.group(4) != 'unknown' else self.get_service_name(int(m.group(1))),
                    })
            return entries
        except Exception:
            return []

    async def get_manufacturer(self, mac_address: str) -> str:
        """Get manufacturer from MAC address (cached)."""
        try:
            if not mac_address or mac_address == "N/A":
                return "Unknown"

            # Normalize MAC and cache by OUI
            mac = mac_address.upper().replace("-", ":")
            oui = mac.replace(":", "")[:6]

            if oui in self._vendor_cache:
                return self._vendor_cache[oui]

            # mac_vendor_lookup.MacLookup.lookup() - Handle sync vs async return
            try:
                # Newer versions might return a coroutine directly
                vendor = self.mac_lookup.lookup(mac)
                if inspect.isawaitable(vendor):
                    vendor = await vendor
            except Exception:
                vendor = "Unknown"
                 
            vendor = vendor if vendor else "Unknown"
            # Evict oldest entry when cap is reached (simple FIFO)
            if len(self._vendor_cache) >= self._vendor_cache_max:
                try:
                    self._vendor_cache.pop(next(iter(self._vendor_cache)))
                except StopIteration:
                    pass
            self._vendor_cache[oui] = vendor
            return vendor
        except Exception:
            return "Unknown"

    # ---------------------------
    # Ping / Port scan
    # ---------------------------

    def _ping_for_ttl(self, ip: str):
        """Extract TTL from a single ping. Returns None on any failure."""
        try:
            import re as _re
            is_windows = platform.system().lower() == "windows"
            if is_windows:
                cmd = ["ping", "-n", "1", "-w", "1000", ip]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", ip]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            match = _re.search(r'(?i)ttl=(\d+)', result.stdout)
            return int(match.group(1)) if match else None
        except Exception:
            return None

    def _parse_system_ping_result(self, stdout: str, stderr: str, returncode: int, is_windows: bool):
        """Parse OS ping output and return (latency_seconds|None, detail)."""
        output = "\n".join(part for part in (stdout, stderr) if part).strip()
        lowered = output.lower()

        if is_windows:
            match = re.search(r'time[=<]\s*(\d+(?:\.\d+)?)\s*ms', output, re.IGNORECASE)
            if match:
                latency_ms = float(match.group(1))
                if 'time<' in match.group(0).lower():
                    latency_ms = min(latency_ms, 1.0)
                return latency_ms / 1000.0, "Reply received"

            if 'request timed out' in lowered:
                return None, "Request timed out"
            if 'destination host unreachable' in lowered:
                return None, "Destination host unreachable"
            if 'general failure' in lowered:
                return None, "General failure"
        else:
            match = re.search(r'time=(\d+(?:\.\d+)?)\s*ms', output, re.IGNORECASE)
            if match:
                return float(match.group(1)) / 1000.0, "Reply received"

            if '100% packet loss' in lowered or ', 0 received' in lowered:
                return None, "No reply"
            if 'destination host unreachable' in lowered or 'network is unreachable' in lowered:
                return None, "Destination host unreachable"

        if returncode == 0:
            return None, "Reply received"
        return None, "No reply"

    def _ping_batch(
        self, ip: str, count: int, timeout: int, is_windows: bool
    ) -> tuple:
        """Run a single multi-packet ping subprocess.

        Returns (avg_ms, min_ms, max_ms, jitter_ms, packet_loss_pct, ttl).
        avg_ms is None when all packets are lost.
        Measures RTT at OS level — not affected by Python event loop scheduling.
        """
        if is_windows:
            cmd = ['ping', '-n', str(count), '-w', str(timeout * 1000), ip]
        else:
            cmd = ['ping', '-c', str(count), '-W', str(timeout), ip]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout * count + 5,
            )
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            return None, None, None, None, 100.0, None
        except Exception:
            return None, None, None, None, 100.0, None

        ttl = None
        ttl_m = re.search(r'ttl=(\d+)', output, re.IGNORECASE)
        if ttl_m:
            ttl = int(ttl_m.group(1))

        if is_windows:
            times = []
            for m in re.finditer(r'time[=<]\s*(\d+(?:\.\d+)?)\s*ms', output, re.IGNORECASE):
                t = float(m.group(1))
                if '<' in m.group(0).lower():
                    t = min(t, 1.0)
                times.append(t)

            stats_m = re.search(
                r'Minimum\s*=\s*(\d+)ms.*?Maximum\s*=\s*(\d+)ms.*?Average\s*=\s*(\d+)ms',
                output, re.IGNORECASE | re.DOTALL,
            )
            loss_m = re.search(r'\((\d+)%\s*loss\)', output, re.IGNORECASE)
            packet_loss = float(loss_m.group(1)) if loss_m else (0.0 if times else 100.0)

            if stats_m:
                min_ms = float(stats_m.group(1))
                max_ms = float(stats_m.group(2))
                avg_ms = float(stats_m.group(3))
                if len(times) > 1:
                    diffs = [abs(times[j] - times[j - 1]) for j in range(1, len(times))]
                    jitter_ms = round(sum(diffs) / len(diffs), 2)
                else:
                    jitter_ms = 0.0
                return avg_ms, min_ms, max_ms, jitter_ms, packet_loss, ttl
            return None, None, None, None, packet_loss, ttl
        else:
            stats_m = re.search(
                r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms',
                output,
            )
            loss_m = re.search(r'(\d+)%\s*packet loss', output)
            packet_loss = float(loss_m.group(1)) if loss_m else (0.0 if stats_m else 100.0)

            if stats_m:
                min_ms = float(stats_m.group(1))
                avg_ms = float(stats_m.group(2))
                max_ms = float(stats_m.group(3))
                jitter_ms = float(stats_m.group(4))  # mdev ≈ jitter
                return avg_ms, min_ms, max_ms, jitter_ms, packet_loss, ttl
            return None, None, None, None, packet_loss, ttl

    async def ping_device(self, ip: str, timeout: int = 2, count: int = 3):
        """
        Ping a device and return status, latency (ms), packet loss (%), jitter (ms), TTL, detail,
        min_rtt (ms), max_rtt (ms).

        Uses a single subprocess ping call (_ping_batch) so RTT is measured at OS level.
        Avoids aioping's event loop wall-clock timing which inflated latency to 150-250ms
        when 200+ devices were scanned concurrently on one event loop.
        """
        is_windows = platform.system().lower() == "windows"
        loop = asyncio.get_running_loop()

        avg_ms, min_ms, max_ms, jitter_ms, packet_loss, ttl = await loop.run_in_executor(
            self._executor, self._ping_batch, ip, count, timeout, is_windows
        )

        if avg_ms is not None:
            return (
                "Online",
                round(avg_ms, 2),
                packet_loss,
                round(jitter_ms, 2) if jitter_ms is not None else 0.0,
                ttl,
                "Reply received",
                round(min_ms, 2),
                round(max_ms, 2),
            )
        else:
            return "Offline", None, 100.0, None, None, "No reply", None, None

    async def _ping_system(self, ip: str, timeout: int, is_windows: bool):
        """Fallback system ping. Returns (delay_seconds|None, detail)."""
        try:
            loop = asyncio.get_running_loop()
            param = '-n' if is_windows else '-c'
            wait_param = '-w' if is_windows else '-W'
            wait_value = str(int(timeout * 1000)) if is_windows else str(timeout)
            cmd = ['ping', param, '1', wait_param, wait_value, ip]

            def _run_ping():
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=max(3, int(timeout) + 2),
                )

            proc = await loop.run_in_executor(self._executor, _run_ping)
            return self._parse_system_ping_result(
                proc.stdout,
                proc.stderr,
                proc.returncode,
                is_windows,
            )
        except subprocess.TimeoutExpired:
            return None, "Request timed out"
        except Exception:
            return None, "No reply"

    async def scan_ports(self, ip: str, ports=None):
        """Scan ports on a device (concurrent with timeouts)."""
        if ports is None:
            # Expanded list aligned with DeviceClassifier fingerprints + common services
            ports = [
                21, 22, 23, 25, 53, 80, 110, 443, 993, 995, 3389, 5002,
                161, 179, 520, 8080, 8443, 554,
                3306, 5432, 27017, 6379, 1433,
                445, 139, 9100, 631, 515
            ]

        open_ports = []

        async def check_port(port: int):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port),
                    timeout=1.0
                )
                writer.close()
                await writer.wait_closed()
                return port, True
            except Exception:
                return port, False

        results = await asyncio.gather(*(check_port(int(p)) for p in ports), return_exceptions=False)

        for port, is_open in results:
            if is_open:
                open_ports.append({
                    "port": port,
                    "status": "open",
                    "service": self.get_service_name(port)
                })

        return open_ports

    def get_service_name(self, port: int) -> str:
        services = {
            21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
            80: "HTTP", 110: "POP3", 443: "HTTPS", 993: "IMAPS", 995: "POP3S",
            3389: "RDP", 5002: "Tactical Agent",
            161: "SNMP", 179: "BGP", 520: "RIP",
            8080: "HTTP-Alt", 8443: "HTTPS-Alt", 554: "RTSP",
            3306: "MySQL", 5432: "PostgreSQL", 27017: "MongoDB",
            6379: "Redis", 1433: "MSSQL",
            445: "SMB", 139: "NetBIOS-SSN", 9100: "JetDirect",
            631: "IPP", 515: "LPD"
        }
        return services.get(port, "Unknown")
    
    # ---------------------------
    # Agent Discovery
    # ---------------------------
    async def check_tactical_agent(self, ip: str):
        """Check if device is running the Tactical Agent service on port 5002."""
        try:
            logger.debug("[AgentScan] probing ip=%s port=5002", ip)
            # First check if port is open quickly
            _, is_open = await self.check_port(ip, 5002)
            if not is_open:
                logger.debug("[AgentScan] ip=%s port=5002 state=closed", ip)
                return None
            
            logger.debug("[AgentScan] ip=%s port=5002 state=open fetching_identity=true", ip)

            # Fetch identity endpoints
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, self._fetch_agent_identity, ip)
            logger.debug("[AgentScan] ip=%s identity_found=%s", ip, bool(result))
            return result
        except Exception as e:
            log_operational_exception(
                logger,
                f"[AgentScan] probe failed ip={ip}",
                e,
                error_code='AGENT_DISCOVERY_FAILED',
                expected_level='debug',
            )
            return None

    async def check_port(self, ip, port, timeout=1.0):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return port, True
        except Exception:
            return port, False

    def _fetch_agent_identity(self, ip):
        try:
            import time
            start_time = time.time()
            url = f"http://{ip}:5002/api/identity"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    data["http_latency_ms"] = (time.time() - start_time) * 1000.0
                    return data
        except Exception as e:
            log_operational_exception(
                logger,
                f"[AgentScan] identity fetch failed ip={ip}",
                e,
                error_code='AGENT_IDENTITY_FAILED',
                expected_level='debug',
            )
        return None

    # ---------------------------
    # Agent Discovery
    # ---------------------------
    # FIXME: check_tactical_agent, check_port, and _fetch_agent_identity are defined twice
    # (first block ~line 553, this duplicate block here). Pre-existing bug — second definition
    # silently overrides the first. Safe to remove this duplicate section in a future cleanup.
    async def check_tactical_agent(self, ip: str):
        """Check if device is running the Tactical Agent service on port 5002."""
        try:
            logger.debug("[AgentScan] probing ip=%s port=5002", ip)
            # First check if port is open quickly
            _, is_open = await self.check_port(ip, 5002)
            if not is_open:
                logger.debug("[AgentScan] ip=%s port=5002 state=closed", ip)
                return None
            
            logger.debug("[AgentScan] ip=%s port=5002 state=open fetching_identity=true", ip)

            # Fetch identity endpoints
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, self._fetch_agent_identity, ip)
            logger.debug("[AgentScan] ip=%s identity_found=%s", ip, bool(result))
            return result
        except Exception as e:
            log_operational_exception(
                logger,
                f"[AgentScan] probe failed ip={ip}",
                e,
                error_code='AGENT_DISCOVERY_FAILED',
                expected_level='debug',
            )
            return None

    async def check_port(self, ip, port, timeout=1.0):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return port, True
        except Exception:
            return port, False

    def _fetch_agent_identity(self, ip):
        try:
            import time
            start_time = time.time()
            url = f"http://{ip}:5002/api/identity"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    data["http_latency_ms"] = (time.time() - start_time) * 1000.0
                    return data
        except Exception as e:
            log_operational_exception(
                logger,
                f"[AgentScan] identity fetch failed ip={ip}",
                e,
                error_code='AGENT_IDENTITY_FAILED',
                expected_level='debug',
            )
        return None

    # ---------------------------
    # Single device scan (faster)
    # ---------------------------

    async def scan_single_device(self, ip: str, scan_mode: str = 'heavy'):
        """Comprehensive scan of a single device (fast + safe)."""
        try:
            status, latency, packet_loss, jitter, ttl, status_detail, *_ = await self.ping_device(ip, timeout=self.timeout)

            device_info = {
                "ip": ip,
                "status": status,
                "status_detail": status_detail,
                "latency": latency,
                "packet_loss": packet_loss,  # NEW: Include packet loss
                "jitter": jitter,            # NEW: Include jitter
                "ttl": ttl,                  # NEW: Include TTL for OS fingerprinting
                "hostname": "Unknown",
                "mac": "N/A",
                "manufacturer": "Unknown",
                "open_ports": []
            }

            if status != "Online":
                return device_info

            # HEAVY MODE: Full enrichment
            loop = asyncio.get_running_loop()

            # Run ARP/DNS lookups and optional nmap enrichment concurrently
            mac_f    = loop.run_in_executor(self._executor, self.get_mac_address, ip)
            host_f   = loop.run_in_executor(self._executor, self.get_hostname, ip)
            nmap_f   = loop.run_in_executor(self._executor, self._nmap_quick_host, ip)

            mac_address, hostname, nmap_info = await asyncio.gather(mac_f, host_f, nmap_f)

            # nmap enrichment: prefer nmap hostname (PTR/rDNS) when our probes returned Unknown
            if nmap_info.get('hostname') and hostname in ('Unknown', None, ''):
                hostname = nmap_info['hostname']
            # nmap gets MAC via ARP when running as root; use it when ARP cache is empty
            if nmap_info.get('mac') and (not mac_address or mac_address == 'N/A'):
                mac_address = nmap_info['mac']

            manufacturer = nmap_info.get('manufacturer') or await self.get_manufacturer(mac_address)

            device_info.update({
                "hostname": hostname or "Unknown",
                "mac": mac_address or "N/A",
                "manufacturer": manufacturer or "Unknown"
            })

            # Check for Tactical Agent
            agent_info = await self.check_tactical_agent(ip)
            if agent_info:
                device_info.update({
                    "hostname": agent_info.get("hostname", device_info["hostname"]),
                    "mac": agent_info.get("mac_address", device_info["mac"]),
                    "type": "Tactical Agent",
                    "agent_version": agent_info.get("agent_version"),
                    "os": agent_info.get("os"),
                    "is_agent": True
                })
            else:
                # Port scan enabled for device classification (provides 15% classification weight)
                device_info["open_ports"] = await self.scan_ports(ip)

                # Run Classification Engine if not agent


                # (Agent devices are trusted as "Tactical Agent" or specific OS)


                try:


                    from services.device_classifier import DeviceClassifier, DeviceSignals



                    # Extract port numbers from scan results for classifier


                    open_ports_list = device_info.get("open_ports", [])


                    port_numbers = [p["port"] for p in open_ports_list if isinstance(p, dict) and "port" in p]


                    # Enrichment (Layer 1 — new signals for classification)
                    from services.device_enrichment_service import DeviceEnrichmentService
                    _enrichment_svc = DeviceEnrichmentService()
                    mac_address = device_info.get("mac", "N/A")
                    is_l2_reachable = bool(mac_address and mac_address != "N/A")
                    enriched = await _enrichment_svc.enrich(ip, port_numbers, is_l2_reachable)

                    classifier = DeviceClassifier()


                    signals = DeviceSignals(


                        ip_address=ip,


                        mac_address=device_info.get("mac"),


                        hostname=device_info.get("hostname"),


                        open_ports=port_numbers,


                        manufacturer=device_info.get("manufacturer"),
                        ttl=device_info.get("ttl"),
                        http_banner=enriched.get("http_banner"),
                        ssh_banner=enriched.get("ssh_banner"),
                        mdns_services=enriched.get("mdns_services", []),
                        upnp_info=enriched.get("upnp_info"),
                        # Add SNMP here if gathered


                    )



                    classification = classifier.classify(signals)



                    device_info.update({
                        "device_type": DeviceClassifier.normalize_device_type(classification.device_type),
                        "confidence_score": classification.score,
                        "classification_confidence": classification.confidence.value,
                        "classification_details": classification.to_dict()
                    })

                    # Gemini LLM fallback for LOW confidence classifications
                    try:
                        from services.device_classifier import ConfidenceLevel
                        if classification.confidence == ConfidenceLevel.LOW:
                            from services.gemini_classifier import classify_device as gemini_classify
                            gemini_signals = {
                                "manufacturer": device_info.get("manufacturer", ""),
                                "mac_address": device_info.get("mac", ""),
                                "ttl": device_info.get("ttl"),
                                "open_ports": port_numbers,
                                "http_banner": enriched.get("http_banner"),
                                "ssh_banner": enriched.get("ssh_banner"),
                                "mdns_services": enriched.get("mdns_services", []),
                                "upnp_info": enriched.get("upnp_info"),
                                "hostname": device_info.get("hostname", ""),
                            }
                            gemini_type = gemini_classify(gemini_signals)
                            if gemini_type and gemini_type != "unknown":
                                device_info["device_type"] = gemini_type
                                device_info["classification_confidence"] = "gemini_fallback"
                    except Exception:
                        pass  # Never block scan on LLM failure



                    # Broadcast real-time classification update


                    try:


                        from services.sse_broadcaster import broadcast_event


                        # Only broadcast if confidence is medium or high to reduce noise
                        if classification.score >= classifier.THRESHOLD_MEDIUM:


                            broadcast_event('classification_update', {


                                'ip_address': ip,


                                'classification': classification.to_dict(),


                                'device': device_info


                            })


                    except Exception as b_err:


                        logger.warning("Broadcast error: %s", b_err)



                except Exception as c_err:


                    logger.warning("Classification error for %s: %s", ip, c_err)


                    pass

            return device_info
        except Exception as e:
            return {
                "ip": ip,
                "status": "Error",
                "latency": None,
                "hostname": "Unknown",
                "mac": "N/A",
                "manufacturer": "Unknown",
                "open_ports": [],
                "error": str(e)
            }

    # ---------------------------
    # Backward compatible scan
    # ---------------------------

    async def scan_network_range(self, ip_range=None):
        """
        Original method: returns devices list (kept for compatibility).
        Faster than before (uses semaphore + gather), but still caps hosts for safety.
        """
        start_time = datetime.now()
        devices = []

        try:
            network = ipaddress.IPv4Network(ip_range, strict=False) if ip_range else ipaddress.IPv4Network(
                str(self.get_local_ip_range()), strict=False
            )

            hosts_iter = network.hosts()
            hosts = list(islice(hosts_iter, self.MAX_HOSTS_DEFAULT))

            sem = asyncio.Semaphore(self.workers)

            async def bounded_scan(ip_str: str):
                async with sem:
                    return await self.scan_single_device(ip_str)

            results = await asyncio.gather(*(bounded_scan(str(ip)) for ip in hosts), return_exceptions=True)

            for r in results:
                if isinstance(r, dict):
                    devices.append(r)

            scan_duration = (datetime.now() - start_time).total_seconds()
            logger.info("Scan completed in %.2fs. Found %d devices.", scan_duration, len(devices))
            return devices

        except Exception as e:
            logger.exception("Error scanning network: %s", e)
            return []

    # ---------------------------
    # Incremental scan (FASTER + SAFE, same architecture)
    # ---------------------------

    async def scan_network_range_incremental(self, ip_range=None, scan_id=None, active_scans=None, active_scans_lock=None, scan_mode='heavy'):
        """
        Incremental scan with batch discovery + progress updates.
        Keeps your polling architecture:
          active_scans[scan_id]['new_devices'] buffer
          active_scans[scan_id]['progress'], scanned_hosts, total_hosts, total_found

        Safety:
          - caps hosts (default 254, hard cap 4096)
          - stop check BEFORE and DURING scanning
          - avoids overwhelming by semaphore-limited concurrency
          - avoids building huge lists (islice)
        """
        try:
            network = ipaddress.IPv4Network(ip_range, strict=False) if ip_range else ipaddress.IPv4Network(
                str(self.get_local_ip_range()), strict=False
            )

            # Cap hosts safely (keep current behavior: default 254; allow up to hard cap if you ever raise default)
            max_hosts = min(self.MAX_HOSTS_DEFAULT, self.MAX_HOSTS_HARD_CAP)
            hosts = list(islice(network.hosts(), max_hosts))

            total_hosts = len(hosts)
            scanned_hosts = 0
            online_devices = []

            # Initialize totals
            if scan_id and active_scans:
                self._safe_update_scan(
                    scan_id, active_scans, active_scans_lock,
                    {"total_hosts": total_hosts, "scanned_hosts": 0, "progress": 0, "total_found": 0}
                )

            logger.info("Starting incremental scan of %d IP addresses...", total_hosts)

            sem = asyncio.Semaphore(self.workers)

            async def bounded_scan(ip_str: str):
                async with sem:
                    # stop check inside the bounded call (fast response)
                    if self._scan_stopped(scan_id, active_scans, active_scans_lock):
                        return None
                    return await self.scan_single_device(ip_str, scan_mode=scan_mode)

            # We still do "batch-ish" updates so UI gets updates frequently.
            # But internally we do concurrency-limited scans for speed.
            batch_size = 40  # you can tune 20-80; 40 is a solid LAN default

            for i in range(0, total_hosts, batch_size):
                if self._scan_stopped(scan_id, active_scans, active_scans_lock):
                    logger.info("Scan stopped by user")
                    break

                batch = hosts[i:i + batch_size]
                results = await asyncio.gather(*(bounded_scan(str(ip)) for ip in batch), return_exceptions=True)

                # Count scanned in this batch (exclude None when stopped mid-batch)
                finished = 0
                batch_dicts = []
                for r in results:
                    if r is None:
                        continue
                    finished += 1
                    if isinstance(r, dict):
                        batch_dicts.append(r)

                scanned_hosts += finished

                # Process and push incremental updates
                await self.process_batch_results(
                    batch_dicts,
                    online_devices,
                    scan_id,
                    active_scans,
                    scanned_hosts,
                    total_hosts,
                    active_scans_lock=active_scans_lock
                )

                # tiny yield so UI polling feels smooth without slowing scan materially
                await asyncio.sleep(0)

            logger.info("Incremental scan completed. Found %d online devices.", len(online_devices))
            return online_devices

        except Exception as e:
            logger.exception("Error in incremental scan: %s", e)
            return []

    async def process_batch_results(
        self,
        batch_results,
        online_devices,
        scan_id,
        active_scans,
        scanned_hosts,
        total_hosts,
        active_scans_lock=None
    ):
        """
        Process batch results and update progress.
        FIXED:
          - online_devices now correctly accumulates
          - total_found now correct
          - progress uses current scanned_hosts
        """
        batch_online_devices = []

        for result in batch_results:
            if not isinstance(result, dict):
                continue
            if result.get("status") == "Online":
                batch_online_devices.append(result)

        # IMPORTANT: accumulate
        if batch_online_devices:
            online_devices.extend(batch_online_devices)

        # Update progress and buffer for real-time updates
        if scan_id and active_scans:
            progress = (scanned_hosts / total_hosts) * 100 if total_hosts > 0 else 0.0

            updates = {
                "scanned_hosts": scanned_hosts,
                "progress": round(progress, 2),
                "total_found": len(online_devices),
            }

            # Safe update (optional lock from Flask side)
            self._safe_update_scan(scan_id, active_scans, active_scans_lock, updates)

            if batch_online_devices:
                # Append to "new_devices" buffer safely
                self._safe_extend_new_devices(scan_id, active_scans, active_scans_lock, batch_online_devices)
                # ALSO Append to main "devices" list for persistence/resume
                self._safe_extend_all_devices(scan_id, active_scans, active_scans_lock, batch_online_devices)
                logger.debug("Batch: +%d online (Total online: %d)", len(batch_online_devices), len(online_devices))

        # Console progress
        progress = (scanned_hosts / total_hosts) * 100 if total_hosts > 0 else 0.0
        logger.debug("Progress: %d/%d (%.1f%%) - Online: %d", scanned_hosts, total_hosts, progress, len(online_devices))

    # ---------------------------
    # Internal safe helpers
    # ---------------------------

    def _scan_stopped(self, scan_id, active_scans, active_scans_lock) -> bool:
        if not scan_id or not active_scans:
            return False
        if active_scans_lock:
            with active_scans_lock:
                scan_state = active_scans.get(scan_id, {})
                return bool(scan_state.get("stop")) or scan_state.get("status") == "stopped"
        scan_state = active_scans.get(scan_id, {})
        return bool(scan_state.get("stop")) or scan_state.get("status") == "stopped"

    def _safe_update_scan(self, scan_id, active_scans, active_scans_lock, updates: dict) -> None:
        if not scan_id or not active_scans:
            return
        if active_scans_lock:
            with active_scans_lock:
                if scan_id in active_scans:
                    active_scans[scan_id].update(updates)
        else:
            if scan_id in active_scans:
                active_scans[scan_id].update(updates)

    def _safe_extend_new_devices(self, scan_id, active_scans, active_scans_lock, devices: list) -> None:
        if not scan_id or not active_scans:
            return
        if active_scans_lock:
            with active_scans_lock:
                if scan_id in active_scans:
                    active_scans[scan_id].setdefault("new_devices", [])
                    active_scans[scan_id]["new_devices"].extend(devices)
        else:
            if scan_id in active_scans:
                active_scans[scan_id].setdefault("new_devices", [])
                active_scans[scan_id]["new_devices"].extend(devices)

    def _safe_extend_all_devices(self, scan_id, active_scans, active_scans_lock, devices: list) -> None:
        if not scan_id or not active_scans:
            return
        if active_scans_lock:
            with active_scans_lock:
                if scan_id in active_scans:
                    active_scans[scan_id].setdefault("devices", [])
                    active_scans[scan_id]["devices"].extend(devices)
        else:
            if scan_id in active_scans:
                active_scans[scan_id].setdefault("devices", [])
                active_scans[scan_id]["devices"].extend(devices)
