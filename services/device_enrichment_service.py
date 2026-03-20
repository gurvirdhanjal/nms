"""
Device Enrichment Service
=========================
Pure-async service that gathers 5 supplementary network signals for unknown-device
classification.  Designed to run alongside (and feed into) the scan pipeline's
DeviceSignals dataclass — no Flask context, no DB access, no side effects.

Methods
-------
enrich(ip, open_ports, is_l2_reachable) -> dict
    Fan-out entry point.  Returns exactly:
    {
        "ttl":           int | None,   # IP hop-count from ping reply
        "http_banner":   str | None,   # "Server: … | Title: …" from HTTP/HTTPS
        "ssh_banner":    str | None,   # Raw SSH version banner (port 22)
        "mdns_services": list,         # Matched high-value mDNS service types
        "upnp_info":     dict | None,  # SSDP/UPnP device description fields
    }

_grab_ttl(ip)
    Runs a single ICMP ping in a thread-pool executor and parses the TTL= field
    from stdout.  Useful for OS-fingerprinting (Windows≈128, Linux≈64, routers≈255).

_grab_http_banner(ip, open_ports)
    Opens a plain or TLS TCP connection to the first reachable HTTP port and extracts
    the Server: response header and <title> element.

_grab_ssh_banner(ip, open_ports)
    Reads the SSH identification string that every SSH daemon sends before the
    cryptographic handshake (RFC 4253 §4.2).

_discover_mdns_services(ip)
    Sends a minimal DNS-SD PTR query (mDNS + unicast) and filters the answer
    section for high-value service type labels (printers, cast devices, etc.).

_discover_upnp(ip)
    Sends an SSDP M-SEARCH multicast, collects LOCATION: URLs from responses,
    fetches and parses the first UPnP device description XML.
    Only called when the device is confirmed L2-reachable to avoid flooding.

All methods:
- Swallow every exception — never raise to the caller.
- Bound every network operation to ≤1.5 s via asyncio.wait_for.
- Use loop.run_in_executor(None, …) for blocking socket/subprocess calls.
"""

import asyncio
import logging
import platform
import random
import re
import socket
import ssl
import struct
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Service type allow-list for mDNS filtering
# ---------------------------------------------------------------------------
_MDNS_HIGH_VALUE = frozenset([
    "_ipp._tcp",
    "_printer._tcp",
    "_pdl-datastream._tcp",
    "_googlecast._tcp",
    "_airplay._tcp",
    "_raop._tcp",
    "_ssh._tcp",
    "_smb._tcp",
    "_rfb._tcp",
    "_http._tcp",
])


class DeviceEnrichmentService:
    """
    Gather network enrichment signals for a single IP address.

    Instantiate once and reuse across many ``enrich()`` calls — the service
    holds no per-call state.
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def enrich(
        self,
        ip: str,
        open_ports: List[int],
        is_l2_reachable: bool,
    ) -> Dict:
        """
        Fan-out all probes in parallel and return a normalised dict.

        Parameters
        ----------
        ip:               Target IPv4 address string.
        open_ports:       List of ports confirmed open by the caller's port scan.
        is_l2_reachable:  True when the device was confirmed reachable at Layer 2
                          (ARP success).  Used to gate the UPnP multicast probe.

        Returns
        -------
        {
            "ttl":           int | None,
            "http_banner":   str | None,
            "ssh_banner":    str | None,
            "mdns_services": list,
            "upnp_info":     dict | None,
        }
        Never raises.
        """
        # Build the no-op coroutine for the disabled UPnP case up-front so
        # that asyncio.gather always receives exactly 5 awaitables.
        if is_l2_reachable:
            upnp_coro = self._discover_upnp(ip)
        else:
            async def _noop():
                await asyncio.sleep(0)
                return None
            upnp_coro = _noop()

        coros = [
            self._grab_ttl(ip),
            self._grab_http_banner(ip, open_ports),
            self._grab_ssh_banner(ip, open_ports),
            self._discover_mdns_services(ip),
            upnp_coro,
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        return {
            "ttl":           results[0] if not isinstance(results[0], BaseException) else None,
            "http_banner":   results[1] if not isinstance(results[1], BaseException) else None,
            "ssh_banner":    results[2] if not isinstance(results[2], BaseException) else None,
            "mdns_services": results[3] if not isinstance(results[3], BaseException) else [],
            "upnp_info":     results[4] if not isinstance(results[4], BaseException) else None,
        }

    # ------------------------------------------------------------------
    # TTL probe
    # ------------------------------------------------------------------

    async def _grab_ttl(self, ip: str) -> Optional[int]:
        """
        Run a single ICMP ping in a thread-pool executor and extract the TTL.

        Interprets TTL ranges as a rough OS fingerprint:
          ≈64  → Linux/macOS
          ≈128 → Windows
          ≈255 → network device (router/firewall)

        Returns int or None.  Never raises.
        """
        try:
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._ping_for_ttl, ip),
                timeout=1.5,
            )
        except Exception:
            return None

    def _ping_for_ttl(self, ip: str) -> Optional[int]:
        """Blocking helper — runs in a thread.  Returns TTL int or None."""
        try:
            if platform.system() == "Windows":
                cmd = ["ping", "-n", "1", "-w", "1000", ip]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", ip]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3,
            )
            match = re.search(r"(?i)ttl=(\d+)", proc.stdout)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # HTTP banner grab
    # ------------------------------------------------------------------

    async def _grab_http_banner(
        self, ip: str, open_ports: List[int]
    ) -> Optional[str]:
        """
        Attempt to extract a Server: header and <title> from each reachable
        HTTP/HTTPS port.  Returns the first successful result as a formatted
        string, or None if all attempts fail.

        Only touches ports in {80, 443, 8080, 8443} that appear in open_ports.
        Never raises.
        """
        candidate_ports = [p for p in [80, 443, 8080, 8443] if p in open_ports]

        for port in candidate_ports:
            try:
                result = await asyncio.wait_for(
                    self._fetch_http_banner(ip, port),
                    timeout=1.5,
                )
                if result:
                    return result
            except Exception:
                continue

        return None

    async def _fetch_http_banner(self, ip: str, port: int) -> Optional[str]:
        """Open one TCP connection and read the HTTP response head."""
        use_ssl = port in (443, 8443)
        ctx: Optional[ssl.SSLContext] = None

        if use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            reader, writer = await asyncio.open_connection(ip, port, ssl=ctx)
        except Exception:
            return None

        try:
            request = (
                f"GET / HTTP/1.0\r\n"
                f"Host: {ip}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()
            writer.write(request)
            await writer.drain()

            raw = await asyncio.wait_for(reader.read(2048), timeout=1.5)
            text = raw.decode("utf-8", errors="ignore")

            server_match = re.search(r"(?i)server:\s*([^\r\n]+)", text)
            title_match  = re.search(r"(?i)<title[^>]*>([^<]{1,100})</title>", text)

            server = server_match.group(1).strip() if server_match else None
            title  = title_match.group(1).strip()  if title_match  else None

            parts = []
            if server:
                parts.append(f"Server: {server}")
            if title:
                parts.append(f"Title: {title}")

            return " | ".join(parts) if parts else None
        except Exception:
            return None
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # SSH banner grab
    # ------------------------------------------------------------------

    async def _grab_ssh_banner(
        self, ip: str, open_ports: List[int]
    ) -> Optional[str]:
        """
        Read the SSH identification string (e.g. ``SSH-2.0-OpenSSH_8.2``)
        that every SSH daemon sends immediately before the cryptographic
        handshake (RFC 4253 §4.2).

        Only attempted when port 22 appears in open_ports.
        Returns the decoded banner string or None.  Never raises.
        """
        if 22 not in open_ports:
            return None

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, 22),
                timeout=1.5,
            )
            try:
                raw = await asyncio.wait_for(reader.read(256), timeout=1.5)
                banner = raw.decode("utf-8", errors="ignore").strip()
                return banner if banner else None
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
        except Exception:
            return None

    # ------------------------------------------------------------------
    # mDNS / DNS-SD service discovery
    # ------------------------------------------------------------------

    async def _discover_mdns_services(self, ip: str) -> List[str]:
        """
        Send a DNS-SD PTR query for ``_services._dns-sd._udp.local`` to both
        the mDNS multicast group (224.0.0.251:5353) and directly to the target
        IP (unicast).  Parse the response and return matched high-value service
        type strings.

        Builds a minimal DNS wire-format packet without any external library.
        Returns a deduplicated list.  Never raises.  Overall cap: 2 s.
        """
        packet = self._build_dns_sd_query()
        targets = ["224.0.0.251", ip]
        found: set = set()

        loop = asyncio.get_running_loop()

        async def _query_target(target_ip: str) -> List[str]:
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(
                        None, self._send_mdns_query, target_ip, packet
                    ),
                    timeout=2.0,
                )
            except Exception:
                return []

        results = await asyncio.gather(
            *[_query_target(t) for t in targets],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, list):
                found.update(r)

        return list(found)

    def _build_dns_sd_query(self) -> bytes:
        """
        Build a minimal DNS PTR query for ``_services._dns-sd._udp.local``
        in DNS wire format.  No external library required.
        """
        # Transaction ID — 2 random bytes
        txid = struct.pack("!H", random.randint(0, 65535))
        # Flags: standard query, no recursion desired
        flags = b"\x00\x00"
        # Counts: 1 question, 0 answers, 0 authority, 0 additional
        counts = struct.pack("!HHHH", 1, 0, 0, 0)

        # Encode QNAME: _services._dns-sd._udp.local
        qname = b""
        for label in ["_services", "_dns-sd", "_udp", "local"]:
            encoded = label.encode("ascii")
            qname += struct.pack("!B", len(encoded)) + encoded
        qname += b"\x00"  # root label

        # QTYPE: PTR (12), QCLASS: IN (1)
        qtype_qclass = struct.pack("!HH", 12, 1)

        return txid + flags + counts + qname + qtype_qclass

    def _send_mdns_query(self, target_ip: str, packet: bytes) -> List[str]:
        """
        Blocking helper — runs in a thread.
        Sends the DNS-SD query packet and parses any PTR answer strings.
        """
        found = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.5)
            sock.sendto(packet, (target_ip, 5353))

            try:
                data, _ = sock.recvfrom(4096)
                found = self._parse_dns_service_names(data)
            except socket.timeout:
                pass
            except Exception:
                pass
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

        return [s for s in found if s in _MDNS_HIGH_VALUE]

    def _parse_dns_service_names(self, data: bytes) -> List[str]:
        """
        Walk the DNS answer section and extract PTR RDATA strings that look
        like service types (start with ``_``).  Naively reads label sequences
        from the packet — handles the common case without full pointer
        compression support.
        """
        services = []
        try:
            # Skip header (12 bytes) + question section (variable, skip via parsing)
            offset = 12
            # Skip question section — read QNAME, then QTYPE + QCLASS (4 bytes)
            offset = self._skip_dns_name(data, offset)
            offset += 4  # QTYPE + QCLASS

            # Parse answer records
            num_answers = struct.unpack("!H", data[6:8])[0]
            for _ in range(num_answers):
                if offset >= len(data):
                    break
                offset = self._skip_dns_name(data, offset)
                if offset + 10 > len(data):
                    break
                rtype  = struct.unpack("!H", data[offset:offset+2])[0]
                rdlen  = struct.unpack("!H", data[offset+8:offset+10])[0]
                offset += 10

                if rtype == 12 and offset + rdlen <= len(data):  # PTR
                    name = self._read_dns_name(data, offset)
                    if name and name.startswith("_"):
                        services.append(name)

                offset += rdlen
        except Exception:
            pass

        return services

    def _skip_dns_name(self, data: bytes, offset: int) -> int:
        """Advance offset past a DNS name (handles pointer compression)."""
        try:
            while offset < len(data):
                length = data[offset]
                if length == 0:
                    return offset + 1
                if (length & 0xC0) == 0xC0:  # pointer
                    return offset + 2
                offset += 1 + length
        except Exception:
            pass
        return offset

    def _read_dns_name(self, data: bytes, offset: int) -> str:
        """Read a DNS name from data starting at offset."""
        labels = []
        try:
            visited = set()
            while offset < len(data):
                if offset in visited:
                    break
                visited.add(offset)
                length = data[offset]
                if length == 0:
                    break
                if (length & 0xC0) == 0xC0:  # pointer
                    ptr = ((length & 0x3F) << 8) | data[offset + 1]
                    offset = ptr
                    continue
                offset += 1
                labels.append(data[offset:offset+length].decode("ascii", errors="ignore"))
                offset += length
        except Exception:
            pass
        return ".".join(labels)

    # ------------------------------------------------------------------
    # UPnP / SSDP discovery
    # ------------------------------------------------------------------

    async def _discover_upnp(self, ip: str) -> Optional[Dict]:
        """
        Send an SSDP M-SEARCH multicast, collect LOCATION: headers from
        responses for 1.5 s, then fetch and parse the first UPnP XML
        device description.

        Only called when the device is confirmed L2-reachable.
        Returns a dict with ``manufacturer``, ``modelName``, ``deviceType``,
        ``friendlyName`` keys, or None on any failure.  Never raises.
        """
        try:
            loop = asyncio.get_running_loop()

            location_urls = await asyncio.wait_for(
                loop.run_in_executor(None, self._ssdp_search),
                timeout=2.0,
            )
            if not location_urls:
                return None

            for url in location_urls:
                try:
                    desc = await asyncio.wait_for(
                        loop.run_in_executor(None, self._fetch_upnp_description, url),
                        timeout=2.0,
                    )
                    if desc:
                        return desc
                except Exception:
                    continue

        except Exception:
            pass

        return None

    # SSDP M-SEARCH message
    _SSDP_REQUEST = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    ).encode()

    def _ssdp_search(self) -> List[str]:
        """
        Blocking helper — runs in a thread.
        Sends the SSDP M-SEARCH and collects LOCATION URLs for 1.5 s.
        """
        locations = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.5)
            sock.sendto(self._SSDP_REQUEST, ("239.255.255.250", 1900))

            end = time.monotonic() + 1.5

            while time.monotonic() < end:
                try:
                    data, _ = sock.recvfrom(4096)
                    text = data.decode("utf-8", errors="ignore")
                    m = re.search(r"(?i)location:\s*(\S+)", text)
                    if m:
                        url = m.group(1).strip()
                        if url not in locations:
                            locations.append(url)
                except socket.timeout:
                    break
                except Exception:
                    break
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

        return locations

    def _fetch_upnp_description(self, url: str) -> Optional[Dict]:
        """
        Blocking helper — runs in a thread.
        Fetches the UPnP XML device description and extracts key fields.
        """
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                xml_bytes = resp.read(65536)

            root = ET.fromstring(xml_bytes)

            # Strip namespace for easier searching
            def _find(tag: str) -> Optional[str]:
                # Try with any namespace prefix
                for elem in root.iter():
                    local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if local == tag and elem.text:
                        return elem.text.strip()
                return None

            manufacturer  = _find("manufacturer")
            model_name    = _find("modelName")
            device_type   = _find("deviceType")
            friendly_name = _find("friendlyName")

            if any([manufacturer, model_name, device_type, friendly_name]):
                return {
                    "manufacturer":  manufacturer,
                    "modelName":     model_name,
                    "deviceType":    device_type,
                    "friendlyName":  friendly_name,
                }
        except Exception:
            pass

        return None
