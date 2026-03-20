"""
Gemini-powered device type classifier — DB-backed LLM fallback.

Lookup order:
  1. In-memory _cache dict (fast path, populated lazily from DB)
  2. device_classification_cache table (DB)
  3. Gemini API call → writes result to DB + in-memory cache
  4. 'unknown' on any failure

Called only when DeviceClassifier returns LOW confidence (score < 70).
Report endpoints never trigger this — classification runs at scan/ingest time only.
"""
from __future__ import annotations

import hashlib
import logging
import threading

logger = logging.getLogger(__name__)

VALID_DEVICE_TYPES = frozenset({
    "firewall", "router", "switch", "access_point",
    "server", "workstation", "printer", "camera",
    "mobile", "unknown",
})

# ---------------------------------------------------------------------------
# In-memory cache (populated lazily; shared across the process lifetime)
# ---------------------------------------------------------------------------
_cache: dict[str, str] = {}
_cache_loaded: bool = False
_cache_lock = threading.Lock()  # Guards cold-start race in multi-threaded Flask


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache_from_db() -> None:
    """Populate in-memory cache from device_classification_cache table on first call.

    Thread-safe: only the first thread to acquire _cache_lock loads from DB.
    Subsequent calls return immediately via the double-checked locking pattern.
    """
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    with _cache_lock:
        if _cache_loaded:  # double-checked locking
            return
        try:
            from models.device_classification_cache import DeviceClassificationCache
            from extensions import db  # noqa: F401 — imported to ensure session is available
            rows = DeviceClassificationCache.query.all()
            for row in rows:
                _cache[row.fingerprint_hash] = row.device_type
        except Exception:
            logger.debug(
                "device_classification_cache table not yet available — skipping cache load"
            )
        finally:
            _cache_loaded = True


def _save_to_db(fingerprint_hash: str, device_type: str, reasoning: str, source: str) -> None:
    try:
        from models.device_classification_cache import DeviceClassificationCache
        from extensions import db
        existing = DeviceClassificationCache.query.get(fingerprint_hash)
        if existing:
            existing.device_type = device_type
            existing.reasoning = reasoning
        else:
            entry = DeviceClassificationCache(
                fingerprint_hash=fingerprint_hash,
                device_type=device_type,
                reasoning=reasoning,
                source=source,
            )
            db.session.add(entry)
        db.session.commit()
    except Exception:
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def compute_fingerprint(signals_dict: dict) -> str:
    """Compute SHA-256 of stable signals: OUI + sorted ports + normalized manufacturer.

    Stable means it does NOT include TTL, banners, or hostnames that change frequently.
    OUI is extracted from mac_address when available, else falls back to manufacturer prefix.

    Returns a 64-char hex string.
    """
    mac = signals_dict.get("mac_address", "") or ""
    if mac and mac.upper() != "N/A":
        oui = mac.replace(":", "").replace("-", "")[:6].upper()
    else:
        manufacturer = signals_dict.get("manufacturer", "") or ""
        oui = manufacturer[:6].upper()

    ports = signals_dict.get("open_ports", []) or []
    sorted_ports = ",".join(str(p) for p in sorted(ports))

    manufacturer = (signals_dict.get("manufacturer", "") or "").strip().upper()

    raw = f"{oui}|{sorted_ports}|{manufacturer}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(signals_dict: dict) -> str:
    ports = signals_dict.get("open_ports", [])
    return (
        "Classify this network device into exactly one of these types:\n"
        "firewall, router, switch, access_point, server, workstation, printer, camera, mobile, unknown\n\n"
        "Signals:\n"
        f"- MAC vendor/manufacturer: {signals_dict.get('manufacturer', 'Unknown')}\n"
        f"- TTL from ping: {signals_dict.get('ttl', 'unknown')} "
        "(255=network gear, 128=Windows, 64=Linux)\n"
        f"- Open TCP ports: {sorted(ports) if ports else 'none'}\n"
        f"- HTTP Server header + page title: {signals_dict.get('http_banner') or 'none'}\n"
        f"- SSH banner: {signals_dict.get('ssh_banner') or 'none'}\n"
        f"- mDNS service types: {signals_dict.get('mdns_services') or 'none'}\n"
        f"- UPnP device info: {signals_dict.get('upnp_info') or 'none'}\n"
        f"- Hostname: {signals_dict.get('hostname', 'unknown')}\n\n"
        "Respond with exactly ONE word from the list above. No explanation."
    )


# ---------------------------------------------------------------------------
# Gemini API call
# ---------------------------------------------------------------------------

def _classify_via_gemini(signals_dict: dict) -> str:
    """Call Gemini 2.0 Flash to classify a device. Returns 'unknown' on any failure."""
    try:
        import os
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return "unknown"

        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = _build_prompt(signals_dict)
        response = model.generate_content(prompt)
        raw = response.text.strip().lower().split()[0] if response.text.strip() else "unknown"

        # Normalize to a valid device type via DeviceClassifier
        from services.device_classifier import DeviceClassifier
        normalized = DeviceClassifier.normalize_device_type(raw)
        return normalized if normalized in VALID_DEVICE_TYPES else "unknown"

    except Exception as exc:
        exc_name = type(exc).__name__
        if any(x in exc_name.lower() for x in ["auth", "permission", "api_key", "credential"]):
            logger.warning(
                "GEMINI_API_KEY is invalid or missing — Gemini device classification disabled "
                "(error: %s). Set GEMINI_API_KEY in .env to enable AI classification.",
                exc_name,
            )
        else:
            logger.debug("Gemini classify_device failed: %s: %s", exc_name, exc)
        return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_device(signals_dict: dict) -> str:
    """
    Classify a device using Gemini API as fallback.

    Called only when DeviceClassifier returns LOW confidence (score < 70).
    Lookup chain: in-memory cache → DB → Gemini API → 'unknown'

    Args:
        signals_dict: dict with keys: manufacturer, ttl, open_ports, http_banner,
                      ssh_banner, mdns_services, upnp_info, hostname.
                      May also include mac_address (used for OUI fingerprinting).
    Returns:
        Normalized device type string (e.g. 'printer', 'server', 'unknown')
    """
    try:
        _load_cache_from_db()

        fingerprint = compute_fingerprint(signals_dict)

        # Check in-memory cache
        if fingerprint in _cache:
            logger.debug(
                "Gemini cache hit (memory): %s → %s", fingerprint[:8], _cache[fingerprint]
            )
            return _cache[fingerprint]

        # Check DB cache
        try:
            from models.device_classification_cache import DeviceClassificationCache
            row = DeviceClassificationCache.query.get(fingerprint)
            if row:
                _cache[fingerprint] = row.device_type
                logger.debug(
                    "Gemini cache hit (DB): %s → %s", fingerprint[:8], row.device_type
                )
                return row.device_type
        except Exception:
            pass

        # Call Gemini API
        device_type = _classify_via_gemini(signals_dict)

        # Persist result
        _cache[fingerprint] = device_type
        _save_to_db(fingerprint, device_type, "", "gemini")

        return device_type

    except Exception as exc:
        logger.debug("classify_device unexpected error: %s", exc)
        return "unknown"
