"""
GPS/location capture with a 3-tier fallback chain:
  1. Windows Location API (winsdk)         - most accurate, needs Windows
                                              Location Services enabled.
  2. Wi-Fi BSSID scan + Google Geolocation - needs an API key; used when
                                              tier 1 is unavailable/disabled.
  3. IP-based geolocation                  - last resort, city-level accuracy.
                                              Prefers a local MaxMind GeoLite2
                                              database (GEOIP_DB_PATH env var)
                                              over the public ipapi.co API —
                                              a fleet of agents sharing one
                                              corporate egress IP all share
                                              ipapi.co's free-tier rate limit
                                              too, so a local DB lookup (no
                                              external call, no rate limit) is
                                              the fleet-safe default whenever
                                              it's configured. Falls back to
                                              the public API only if no local
                                              DB is set up.

Every tier is independently try/except-wrapped. A tier that fails or is
unavailable falls through to the next; total failure returns None. This
module never raises into the caller.
"""
import os
import platform
import re
import time

import requests

_BSSID_RE = re.compile(r'BSSID \d+\s*:\s*([0-9A-Fa-f:]{17})')
_SIGNAL_RE = re.compile(r'Signal\s*:\s*(\d+)%')

_IP_GEOLOCATION_URL = "https://ipapi.co/json/"
_GOOGLE_GEOLOCATION_URL = "https://www.googleapis.com/geolocation/v1/geolocate"
_PUBLIC_IP_ECHO_URL = "https://api.ipify.org?format=json"


def _get_windows_location():
    """Tier 1: Windows Location API via winsdk (Windows.Devices.Geolocation).
    Returns None on any error, on non-Windows platforms, if winsdk isn't
    installed, or if Windows Location Services are disabled."""
    if platform.system() != "Windows":
        return None
    try:
        import asyncio
        from winsdk.windows.devices.geolocation import Geolocator

        async def _fetch():
            geolocator = Geolocator()
            position = await geolocator.get_geoposition_async()
            coord = position.coordinate
            point = coord.point.position
            return {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "accuracy_meters": coord.accuracy,
                "source": "gps",
            }

        return asyncio.run(asyncio.wait_for(_fetch(), timeout=8))
    except Exception as e:
        print(f"[Location] Tier 1 (Windows Location API) failed: {type(e).__name__}: {e}")
        return None


def _scan_wifi_access_points_windows():
    """Scan nearby Wi-Fi BSSIDs via the built-in `netsh` command (no extra
    dependency). Returns a list of {"macAddress": ...} dicts, or []."""
    if platform.system() != "Windows":
        return []
    try:
        import subprocess
        result = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return []

    output = result.stdout or ""
    access_points = []
    for line in output.splitlines():
        match = _BSSID_RE.search(line)
        if match:
            access_points.append({"macAddress": match.group(1)})
    return access_points


def _get_wifi_location(google_api_key):
    """Tier 2: resolve nearby Wi-Fi BSSIDs via the Google Geolocation API.
    Returns None if no API key is configured, fewer than 2 APs are visible,
    or the request fails."""
    if not google_api_key:
        print("[Location] Tier 2 (Wi-Fi) skipped: GOOGLE_GEOLOCATION_API_KEY not configured.")
        return None
    access_points = _scan_wifi_access_points_windows()
    if len(access_points) < 2:
        print(f"[Location] Tier 2 (Wi-Fi) skipped: only {len(access_points)} access point(s) visible (need >=2).")
        return None
    try:
        response = requests.post(
            f"{_GOOGLE_GEOLOCATION_URL}?key={google_api_key}",
            json={"wifiAccessPoints": access_points},
            timeout=8,
        )
        if response.status_code != 200:
            print(f"[Location] Tier 2 (Wi-Fi) failed: Google Geolocation API returned HTTP {response.status_code}.")
            return None
        data = response.json()
        location = data.get("location") or {}
        latitude = location.get("lat")
        longitude = location.get("lng")
        if latitude is None or longitude is None:
            print("[Location] Tier 2 (Wi-Fi) failed: response had no lat/lng.")
            return None
        return {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_meters": data.get("accuracy"),
            "source": "wifi",
        }
    except Exception as e:
        print(f"[Location] Tier 2 (Wi-Fi) failed: {type(e).__name__}: {e}")
        return None


def _get_public_ip():
    """Lightweight IP echo — NOT a geolocation call, just asks what our own
    public IP is. Far less likely to be rate-limited than a full geo-lookup
    API (no database lookup on their end, trivially cheap to serve)."""
    try:
        response = requests.get(_PUBLIC_IP_ECHO_URL, timeout=5)
        if response.status_code == 200:
            return (response.json() or {}).get("ip")
    except Exception as e:
        print(f"[Location] Public-IP echo failed: {type(e).__name__}: {e}")
    return None


def _get_geoip_db_location(ip_address):
    """Tier 3, preferred path: local MaxMind GeoLite2-City database lookup.
    No external API call, no rate limit — every agent resolves its own
    public IP against a file on disk, so a whole fleet sharing one corporate
    egress IP never contends for a shared quota the way a public API would.
    Requires GEOIP_DB_PATH to point at a downloaded GeoLite2-City.mmdb file
    (see client_modules/GEOIP_SETUP.md) and the optional `geoip2` package.
    Returns None (falls through to the public API) if unavailable for any
    reason — never raises."""
    db_path = (os.environ.get('GEOIP_DB_PATH') or '').strip()
    if not db_path or not ip_address:
        return None
    if not os.path.exists(db_path):
        print(f"[Location] GEOIP_DB_PATH is set but file not found: {db_path}")
        return None
    try:
        import geoip2.database
    except ImportError:
        print("[Location] GEOIP_DB_PATH is set but the 'geoip2' package isn't installed.")
        return None
    try:
        with geoip2.database.Reader(db_path) as reader:
            response = reader.city(ip_address)
            latitude = response.location.latitude
            longitude = response.location.longitude
            if latitude is None or longitude is None:
                print("[Location] GeoIP DB lookup had no coordinates for this IP.")
                return None
            radius_km = response.location.accuracy_radius
            return {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_meters": (radius_km * 1000) if radius_km else None,
                "source": "ip",
            }
    except Exception as e:
        print(f"[Location] GeoIP DB lookup failed: {type(e).__name__}: {e}")
        return None


def _get_ip_location():
    """Tier 3 (last resort): IP-based geolocation. City-level accuracy at best;
    no reliable radius from the public-API path, so accuracy_meters is left
    as None there. Tries the local GeoIP database first (see
    _get_geoip_db_location) — only falls back to the public ipapi.co API if
    no local database is configured."""
    if (os.environ.get('GEOIP_DB_PATH') or '').strip():
        public_ip = _get_public_ip()
        if public_ip:
            fix = _get_geoip_db_location(public_ip)
            if fix:
                return fix
        # Local DB configured but unavailable this cycle — fall through to
        # the public API below rather than giving up entirely.

    try:
        response = requests.get(_IP_GEOLOCATION_URL, timeout=8)
        if response.status_code != 200:
            print(f"[Location] Tier 3 (IP, public API) failed: {_IP_GEOLOCATION_URL} returned HTTP {response.status_code}.")
            return None
        data = response.json()
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        if latitude is None or longitude is None:
            print(f"[Location] Tier 3 (IP, public API) failed: response had no latitude/longitude ({data.get('error') or data.get('reason') or 'no error field'}).")
            return None
        return {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_meters": None,
            "source": "ip",
        }
    except Exception as e:
        print(f"[Location] Tier 3 (IP, public API) failed: {type(e).__name__}: {e}")
        return None


class LocationProvider:
    """Runs the 3-tier fallback chain and caches the last good fix for
    min_interval_seconds so callers can poll frequently without hammering
    the OS API / external services."""

    def __init__(self, google_api_key=None, min_interval_seconds=300):
        self._google_api_key = (google_api_key or "").strip() or None
        self._min_interval_seconds = max(30, int(min_interval_seconds or 300))
        self._last_fix = None
        self._last_fix_at = 0.0

    def get_location(self, force=False):
        now = time.monotonic()
        if not force and self._last_fix and (now - self._last_fix_at) < self._min_interval_seconds:
            return self._last_fix

        fix = None
        for tier in (self._tier_windows, self._tier_wifi, self._tier_ip):
            try:
                fix = tier()
            except Exception as e:
                print(f"[Location] {tier.__name__} raised unexpectedly: {type(e).__name__}: {e}")
                fix = None
            if fix:
                break

        if not fix:
            print("[Location] All 3 tiers failed — no fix this cycle.")

        if fix:
            self._last_fix = fix
            self._last_fix_at = now
        return fix

    def _tier_windows(self):
        return _get_windows_location()

    def _tier_wifi(self):
        return _get_wifi_location(self._google_api_key)

    def _tier_ip(self):
        return _get_ip_location()
