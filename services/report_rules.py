"""
Report data reduction rules — row budgets, column budgets, top-N filtering.

All functions are pure (no DB access, no Flask context).
Used by enterprise_pdf_service.py and report_context.py.
"""
from __future__ import annotations

from typing import Any

# ── Row budgets ───────────────────────────────────────────────────────────────
MAX_DETAIL_ROWS      = 50   # Full fleet table hard cap
MAX_EXCEPTION_ROWS   = 10   # Attention / top-offender tables
MAX_EXCEPTION_SHORT  = 5    # Pre-table "worst offenders" strip
MAX_DOMAIN_ROWS      = 5    # Violations domain table
MAX_OFFENDER_ROWS    = 15   # Violations offender table
MAX_ALERTS_EXECUTIVE = 5    # Alert rows rendered inside executive summary context

# ── Column budget ─────────────────────────────────────────────────────────────
MAX_COLS_LANDSCAPE   = 9    # Absolute maximum for landscape A4 (773pt usable)

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_DEVICES_FOR_DISTRIBUTION = 3  # SLA distribution bars need >= 3 devices


def top_n_by(rows: list, key: str, n: int = 10,
             ascending: bool = True) -> tuple[list, int]:
    """Return (top_n_rows, total_count) sorted by `key`.

    ascending=True  → lowest values first (worst uptime / highest risk at top).
    ascending=False → highest values first (most violations at top).
    Rows with None for `key` are placed at the end regardless of direction.
    """
    def _sort_key(row: dict) -> tuple:
        val = row.get(key)
        if val is None:
            # Always sort None to the back
            return (1, 0)
        try:
            v = float(val)
            return (0, v if ascending else -v)
        except (TypeError, ValueError):
            return (1, 0)

    ranked = sorted(rows, key=_sort_key)
    return ranked[:n], len(rows)


def cap_rows(rows: list, cap: int,
             label: str = "rows") -> tuple[list, str]:
    """Return (capped_rows, caption_string).

    Caption is an empty string when no cap was applied.
    """
    if len(rows) <= cap:
        return rows, ""
    caption = f"Showing {cap} of {len(rows)} {label}, ranked by severity."
    return rows[:cap], caption


def caption_for_top_n(showing: int, total: int,
                       noun: str = "devices") -> str:
    """Build a 'Showing N of M' caption string. Returns empty string if not needed."""
    if total <= showing:
        return ""
    return f"Showing {showing} of {total} {noun}, ranked by severity."


def should_render_distribution(total_devices: int) -> bool:
    """SLA distribution bars are meaningful only with >= 3 devices."""
    return total_devices >= MIN_DEVICES_FOR_DISTRIBUTION


def should_render_exception_table(rows: list) -> bool:
    """Exception tables add value only when there are actually exceptional rows."""
    critical = sum(
        1 for r in rows
        if r.get("sla_tier") in ("Warning", "Critical")
    )
    return critical > 0


def truncate_name(name: Any, max_chars: int = 28) -> str:
    """Safely truncate a device name for table display."""
    if not name:
        return "—"
    return str(name)[:max_chars]
