"""
Shared report formatting helpers — Master Report Specification.

All report rendering (narrative, PDF, frontend) imports from this module.
Never duplicate formatting logic elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


# ── Severity labels (Master Spec) ────────────────────────────────────────────

_SEVERITY_LABELS = {
    "critical": "CRITICAL",
    "warning": "WARNING",
    "ok": "OK",
    "healthy": "OK",
    "info": "INFO",
    "nodata": "NO DATA",
}

_SEVERITY_EMOJI = {
    "critical": "\U0001f534",   # 🔴
    "warning": "\U0001f7e1",    # 🟡
    "ok": "\U0001f7e2",         # 🟢
    "healthy": "\U0001f7e2",    # 🟢
    "info": "\u2139\ufe0f",     # ℹ️
    "nodata": "\u2b1c",         # ⬜
}

# Sort order for severity: lower = more severe
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3, "healthy": 3, "nodata": 4}


def severity_label(level: str, emoji: bool = True) -> str:
    """Format severity with optional emoji prefix per Master Spec."""
    level_lower = (level or "").lower()
    label = _SEVERITY_LABELS.get(level_lower, level)
    if emoji:
        prefix = _SEVERITY_EMOJI.get(level_lower, "")
        return f"{prefix} {label}".strip()
    return label


# ── Timestamp formatting ─────────────────────────────────────────────────────
# Backend stores UTC. Display renders IST (Asia/Kolkata) per user convention.

_IST = timezone(timedelta(hours=5, minutes=30))


def format_timestamp_utc(dt) -> str:
    """Render in IST (Asia/Kolkata) with 'IST' suffix.
    Never output raw ISO 8601 strings in displayed text.
    Note: named _utc for backward compat but outputs IST per user requirement."""
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return dt  # Return as-is if unparseable
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ist = dt.astimezone(_IST)
        return ist.strftime("%d %b %Y %H:%M IST")
    return str(dt)


def format_duration(hours: Optional[float]) -> str:
    """Human-readable duration: 'Xh Ym' for <24h, 'X days Y hours' for >=24h."""
    if hours is None:
        return "N/A"
    hours = float(hours)
    if hours < 0:
        return "N/A"
    if hours >= 24:
        days = int(hours // 24)
        rem_hours = int(hours % 24)
        if rem_hours:
            return f"{days} days {rem_hours} hours"
        return f"{days} days"
    h = int(hours)
    m = int(round((hours - h) * 60))
    if h == 0 and m == 0:
        return "0h"
    if m == 0:
        return f"{h}h"
    if h == 0:
        return f"{m}m"
    return f"{h}h {m}m"


# ── Device name normalization ────────────────────────────────────────────────

def normalize_device_display(name: Optional[str], ip: Optional[str] = None) -> str:
    """Normalize device display name per Master Spec:
    - Generic 'Device-X.X.X.X' → use IP as primary identifier
    - Camera 'DS-2CD*' → 'IP Camera — serial'
    - Otherwise return name, fallback to IP, fallback to 'Unknown'
    """
    if name and name.startswith("Device-") and ip:
        return ip
    if name and name.upper().startswith("DS-2CD"):
        return f"IP Camera \u2014 {name}"
    return name or ip or "Unknown"


# ── Safe metric formatting ───────────────────────────────────────────────────

def fmt(val, fmt_spec: str = ".1f", fallback: str = "N/A") -> str:
    """Safe formatter — prevents TypeError on None values in f-strings."""
    if val is None:
        return fallback
    try:
        return f"{float(val):{fmt_spec}}"
    except (TypeError, ValueError):
        return fallback


def fmt_pct(val, fallback: str = "N/A") -> str:
    """Format as percentage: '92.5%' or fallback."""
    if val is None:
        return fallback
    try:
        return f"{float(val):.1f}%"
    except (TypeError, ValueError):
        return fallback


def fmt_ms(val, fallback: str = "N/A") -> str:
    """Format milliseconds: '353ms' or fallback."""
    if val is None:
        return fallback
    try:
        return f"{float(val):.0f}ms"
    except (TypeError, ValueError):
        return fallback


# ── Policy violation risk classification ─────────────────────────────────────

AI_SERVICE_DOMAINS = frozenset({
    "chatgpt.com", "ab.chatgpt.com", "ws.chatgpt.com",
    "claude.ai", "gemini.google.com", "copilot.microsoft.com",
})

_STREAMING_DOMAINS = frozenset({
    "youtube.com", "netflix.com", "spotify.com", "twitch.tv",
})

_RISK_NOTES = {
    "HIGH": "AI tool \u2014 potential data exfiltration",
    "MEDIUM": "productivity/bandwidth",
    "LOW": "",
}


def classify_violation_risk(domain: str) -> str:
    """Classify restricted site violation risk level.
    HIGH = AI services (data exfiltration risk)
    MEDIUM = streaming/entertainment (productivity/bandwidth)
    LOW = everything else
    """
    if not domain:
        return "LOW"
    domain_lower = domain.lower().strip()
    if domain_lower in AI_SERVICE_DOMAINS or any(ai in domain_lower for ai in AI_SERVICE_DOMAINS):
        return "HIGH"
    if domain_lower in _STREAMING_DOMAINS or any(s in domain_lower for s in _STREAMING_DOMAINS):
        return "MEDIUM"
    return "LOW"


def violation_risk_note(risk: str) -> str:
    """Get the risk context note for a violation risk level."""
    return _RISK_NOTES.get(risk, "")


# ── Report header/footer ────────────────────────────────────────────────────

def build_report_header(report_type: str, scope: str, start_date, end_date) -> dict:
    """Build standardized report header per Master Spec."""
    return {
        "report_type": report_type,
        "scope": scope,
        "period_start": format_timestamp_utc(start_date),
        "period_end": format_timestamp_utc(end_date),
        "generated_at": format_timestamp_utc(datetime.now(timezone.utc)),
        "classification": "CONFIDENTIAL \u2014 Internal Use Only",
    }


def build_report_footer(confidence_level: str = "MEDIUM", confidence_source: str = "mixed sources") -> dict:
    """Build standardized report footer per Master Spec."""
    return {
        "data_confidence": f"{confidence_level} \u2014 {confidence_source}",
        "generated_by": "Network Monitoring System | Rule-Based Insights Engine",
    }
