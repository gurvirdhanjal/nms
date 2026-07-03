"""
GPS/location capture with a 3-tier fallback chain:
  1. Windows Location API (winsdk)         - most accurate, needs Windows
                                              Location Services enabled.
  2. Wi-Fi BSSID scan + Google Geolocation - needs an API key; used when
                                              tier 1 is unavailable/disabled.
  3. IP-based geolocation                  - last resort, city-level accuracy.

Every tier is independently try/except-wrapped. A tier that fails or is
unavailable falls through to the next; total failure returns None. This
module never raises into the caller.
"""
import platform
import re
import time

import requests

_BSSID_RE = re.compile(r'BSSID \d+\s*:\s*([0-9A-Fa-f:]{17})')
_SIGNAL_RE = re.compile(r'Signal\s*:\s*(\d+)%')

_IP_GEOLOCATION_URL = "https://ipapi.co/json/"
_GOOGLE_GEOLOCATION_URL = "https://www.googleapis.com/geolocation/v1/geolocate"


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
    except Exception:
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
        return None
    access_points = _scan_wifi_access_points_windows()
    if len(access_points) < 2:
        return None
    try:
        response = requests.post(
            f"{_GOOGLE_GEOLOCATION_URL}?key={google_api_key}",
            json={"wifiAccessPoints": access_points},
            timeout=8,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        location = data.get("location") or {}
        latitude = location.get("lat")
        longitude = location.get("lng")
        if latitude is None or longitude is None:
            return None
        return {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_meters": data.get("accuracy"),
            "source": "wifi",
        }
    except Exception:
        return None


def _get_ip_location():
    """Tier 3 (last resort): IP-based geolocation. City-level accuracy at best;
    no reliable radius, so accuracy_meters is left as None."""
    try:
        response = requests.get(_IP_GEOLOCATION_URL, timeout=8)
        if response.status_code != 200:
            return None
        data = response.json()
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        if latitude is None or longitude is None:
            return None
        return {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy_meters": None,
            "source": "ip",
        }
    except Exception:
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
            except Exception:
                fix = None
            if fix:
                break

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
