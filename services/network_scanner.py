import asyncio
import aioping
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
    EXECUTOR_WORKERS = 12            # threads for blocking ops (ARP/DNS/vendor)

    def __init__(self):
        self.mac_lookup = MacLookup()
        self.timeout = 2
        self.workers = self.DEFAULT_WORKERS

        # One executor for the lifetime of the object (BIG speed improvement)
        self._executor = ThreadPoolExecutor(max_workers=self.EXECUTOR_WORKERS)

        # Manufacturer cache (MAC prefix → vendor)
        self._vendor_cache = {}

    # ---------------------------
    # Local network detection
    # ---------------------------

    def get_local_ip_range(self):
        """Get the local IP range based on the machine's primary network interface."""
        try:
            # Method 1: Connect to specific external server to find accurate "main" IP
            # We don't actually send data, just determine the routing source IP
            primary_ip = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(2)
                s.connect(("8.8.8.8", 80))
                primary_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass

            interfaces = psutil.net_if_addrs()
            
            # If we found the primary IP, search for it directly
            if primary_ip:
                for interface_name, addrs in interfaces.items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET and addr.address == primary_ip:
                             if addr.netmask:
                                 network = ipaddress.IPv4Network(f"{primary_ip}/{addr.netmask}", strict=False)
                                 return str(network)

            # Method 2: Fallback to iterating interfaces (Status UP, not loopback)
            stats = psutil.net_if_stats()
            for interface_name, addrs in interfaces.items():
                st = stats.get(interface_name)
                if st and not st.isup:
                    continue

                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ip = addr.address
                        netmask = addr.netmask

                        if not ip or not netmask:
                            continue
                        if ip.startswith("127."):
                            continue

                        try:
                            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                            return str(network)
                        except Exception:
                            continue

        except Exception as e:
            print(f"Error getting local IP range: {e}")

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
                        # Return the first readable name that isn't the workgroup/MAC
                        if name and name.isalnum(): 
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

            # mac_vendor_lookup's lookup can be a coroutine in newer versions
            vendor = self.mac_lookup.lookup(mac)
            if asyncio.iscoroutine(vendor):
                vendor = await vendor
                
            vendor = vendor if vendor else "Unknown"
            self._vendor_cache[oui] = vendor
            return vendor
        except Exception:
            return "Unknown"

    # ---------------------------
    # Ping / Port scan
    # ---------------------------

    async def ping_device(self, ip: str, timeout: int = 2, count: int = 4):
        """
        Ping a device and return status, latency (ms), and packet loss (%).
        Safe fallback to system ping if aioping lacks permissions.
        """
        successful_pings = 0
        latencies = []
        
        # Check system type once
        is_windows = platform.system().lower() == "windows"
        
        for _ in range(count):
            try:
                # Try aioping first (faster, accurate)
                delay = await aioping.ping(ip, timeout=timeout)
                latencies.append(delay * 1000)  # Convert to ms
                successful_pings += 1
            except (asyncio.TimeoutError, TimeoutError):
                pass  # Genuine timeout
            except Exception:
                # Permission error or other aioping issue -> Fallback to system ping
                delay = await self._ping_system(ip, timeout, is_windows)
                if delay is not None:
                     latencies.append(delay * 1000)
                     successful_pings += 1
        
        packet_loss = ((count - successful_pings) / count) * 100
        
        if successful_pings > 0:
            avg_latency = round(sum(latencies) / len(latencies), 2)
            return "Online", avg_latency, packet_loss
        else:
            return "Offline", None, 100.0

    async def _ping_system(self, ip: str, timeout: int, is_windows: bool) -> float:
        """Fallback system ping (executes ping command). Returns delay in seconds or None."""
        try:
            param = '-n' if is_windows else '-c'
            wait_param = '-w' if is_windows else '-W'
            # Windows -w is milliseconds, Linux -W is seconds
            wait_value = str(int(timeout * 1000)) if is_windows else str(timeout)
            
            # Simple ping, 1 packet
            cmd = ['ping', param, '1', wait_param, wait_value, ip]
            
            start = time.time()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            end = time.time()
            
            if proc.returncode == 0:
                # Note: This latency includes process overhead, but verifies status.
                return max(0.001, end - start) 
            return None
        except Exception:
            return None

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
            print(f"[DEBUG] Checking agent on {ip}:5002...")
            # First check if port is open quickly
            _, is_open = await self.check_port(ip, 5002)
            if not is_open:
                print(f"[DEBUG] {ip}:5002 is CLOSED")
                return None
            
            print(f"[DEBUG] {ip}:5002 is OPEN, fetching identity...")

            # Fetch identity endpoints
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, self._fetch_agent_identity, ip)
            print(f"[DEBUG] Identity result for {ip}: {result}")
            return result
        except Exception as e:
            print(f"[DEBUG] Error checking agent on {ip}: {e}")
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
            url = f"http://{ip}:5002/api/identity"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    return json.loads(response.read().decode())
        except Exception as e:
            print(f"[DEBUG] Fetch identity failed for {ip}: {e}")
            pass
        return None

    # ---------------------------
    # Single device scan (faster)
    # ---------------------------

    async def scan_single_device(self, ip: str):
        """Comprehensive scan of a single device (fast + safe)."""
        try:
            status, latency, packet_loss = await self.ping_device(ip, timeout=self.timeout)

            device_info = {
                "ip": ip,
                "status": status,
                "latency": latency,
                "packet_loss": packet_loss,  # NEW: Include packet loss
                "hostname": "Unknown",
                "mac": "N/A",
                "manufacturer": "Unknown",
                "open_ports": []
            }

            if status != "Online":
                return device_info

            loop = asyncio.get_running_loop()

            # Run blocking lookups concurrently in the shared executor
            mac_f = loop.run_in_executor(self._executor, self.get_mac_address, ip)
            host_f = loop.run_in_executor(self._executor, self.get_hostname, ip)

            mac_address, hostname = await asyncio.gather(mac_f, host_f)

            manufacturer = await self.get_manufacturer(mac_address)

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
                    
                    classifier = DeviceClassifier()
                    signals = DeviceSignals(
                        ip_address=ip,
                        mac_address=device_info.get("mac"),
                        hostname=device_info.get("hostname"),
                        open_ports=port_numbers,
                        manufacturer=device_info.get("manufacturer")
                        # Add SNMP here if gathered
                    )
                    
                    classification = classifier.classify(signals)
                    
                    device_info.update({
                        "device_type": classification.device_type.value,
                        "confidence_score": classification.score,
                        "classification_confidence": classification.confidence.value,
                        "classification_details": classification.to_dict()
                    })

                    # Broadcast real-time classification update
                    try:
                        from services.sse_broadcaster import broadcast_event
                        # Only broadcast if confidence is medium or high to reduce noise
                        if classification.score >= 25:
                            broadcast_event('classification_update', {
                                'ip_address': ip,
                                'classification': classification.to_dict(),
                                'device': device_info
                            })
                    except Exception as b_err:
                        print(f"Broadcast error: {b_err}")

                except Exception as c_err:
                    print(f"Classification error for {ip}: {c_err}")
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
            print(f"Scan completed in {scan_duration:.2f}s. Found {len(devices)} devices.")
            return devices

        except Exception as e:
            print(f"Error scanning network: {e}")
            return []

    # ---------------------------
    # Incremental scan (FASTER + SAFE, same architecture)
    # ---------------------------

    async def scan_network_range_incremental(self, ip_range=None, scan_id=None, active_scans=None, active_scans_lock=None):
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

            print(f"Starting incremental scan of {total_hosts} IP addresses...")

            sem = asyncio.Semaphore(self.workers)

            async def bounded_scan(ip_str: str):
                async with sem:
                    # stop check inside the bounded call (fast response)
                    if self._scan_stopped(scan_id, active_scans, active_scans_lock):
                        return None
                    return await self.scan_single_device(ip_str)

            # We still do "batch-ish" updates so UI gets updates frequently.
            # But internally we do concurrency-limited scans for speed.
            batch_size = 40  # you can tune 20-80; 40 is a solid LAN default

            for i in range(0, total_hosts, batch_size):
                if self._scan_stopped(scan_id, active_scans, active_scans_lock):
                    print("Scan stopped by user")
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

            print(f"Incremental scan completed. Found {len(online_devices)} online devices.")
            return online_devices

        except Exception as e:
            print(f"Error in incremental scan: {e}")
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
                print(f"Batch: +{len(batch_online_devices)} online (Total online: {len(online_devices)})")

        # Console progress
        progress = (scanned_hosts / total_hosts) * 100 if total_hosts > 0 else 0.0
        print(f"Progress: {scanned_hosts}/{total_hosts} ({progress:.1f}%) - Online: {len(online_devices)}")

    # ---------------------------
    # Internal safe helpers
    # ---------------------------

    def _scan_stopped(self, scan_id, active_scans, active_scans_lock) -> bool:
        if not scan_id or not active_scans:
            return False
        if active_scans_lock:
            with active_scans_lock:
                return active_scans.get(scan_id, {}).get("status") == "stopped"
        return active_scans.get(scan_id, {}).get("status") == "stopped"

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
