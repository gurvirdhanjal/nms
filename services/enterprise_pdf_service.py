"""
Enterprise PDF report generator.

Produces a colour-formatted, multi-section PDF from the data structure returned by
enterprise_report_service.build_enterprise_uptime_report().

Requires: reportlab >= 4.1.0  (already in requirements.txt)
"""
from __future__ import annotations

import io
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# reportlab — install with: pip install reportlab
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from services.core_metrics_service import (
    SLA_GOLD,
    SLA_SILVER,
    SLA_BRONZE,
    SLA_WARNING,
)
from services.report_rules import (
    MAX_ALERTS_EXECUTIVE,
    MAX_COLS_LANDSCAPE,
    MAX_EXCEPTION_ROWS,
    MAX_EXCEPTION_SHORT,
    truncate_name,
    should_render_exception_table,
)
from services.pdf_style_registry import PDFStyleRegistry

# ── Colour palette ───────────────────────────────────────────────────────────
NAVY        = "#1B2A4A"
NAVY_MID    = "#2D4A7A"
TEAL        = "#0EA5E9"
BG_LIGHT    = "#F8FAFC"
BG_ALT      = "#EEF2F7"
BORDER      = "#CBD5E0"
TEXT_DARK   = "#1A202C"
TEXT_MID    = "#4A5568"
TEXT_LIGHT  = "#718096"
WHITE       = "#FFFFFF"

# SLA tier colours
_SLA_COLORS: Dict[str, str] = {
    "Gold":     "#16A34A",   # green
    "Silver":   "#65A30D",   # lime
    "Bronze":   "#D97706",   # amber
    "Warning":  "#EA580C",   # orange
    "Critical": "#DC2626",   # red
    "Unknown":  "#6B7280",   # grey
}

# Data confidence level colours
_CONFIDENCE_COLORS: Dict[str, str] = {
    "HIGH":    "#16A34A",   # green
    "MEDIUM":  "#D97706",   # amber
    "LOW":     "#EA580C",   # orange
    "NO_DATA": "#9CA3AF",   # grey
}
_CONFIDENCE_BG: Dict[str, str] = {
    "HIGH":    "#DCFCE7",
    "MEDIUM":  "#FEF9C3",
    "LOW":     "#FFEDD5",
    "NO_DATA": "#F3F4F6",
}
_SLA_BG: Dict[str, str] = {
    "Gold":     "#DCFCE7",
    "Silver":   "#ECFCCB",
    "Bronze":   "#FEF9C3",
    "Warning":  "#FFEDD5",
    "Critical": "#FEE2E2",
    "Unknown":  "#F3F4F6",
}

# Availability status colours (text, background)
_STATUS_STYLE: Dict[str, tuple] = {
    "online":   ("#166534", "#DCFCE7"),  # dark green / light green
    "offline":  ("#991B1B", "#FEE2E2"),  # dark red / light red
    "degraded": ("#92400E", "#FEF9C3"),  # dark amber / light yellow
    "unknown":  ("#374151", "#F3F4F6"),  # dark grey / light grey
}

# Fleet labels for cover and filenames
_FLEET_TITLES: Dict[str, str] = {
    "server":      "Server & Infrastructure Fleet Report",
    "workstation": "Employee Workstation Fleet Report",
    "all":         "Enterprise Availability & Uptime Report",
}


def hex_color(h: str):
    """Convert '#RRGGBB' to a reportlab HexColor."""
    return HexColor(h)


# ── IST Timestamp Formatter ──────────────────────────────────────────────────
# Backend stores UTC. PDF renders IST (Asia/Kolkata) per user requirement.

from datetime import timezone, timedelta as _td

_IST = timezone(_td(hours=5, minutes=30))


def _fmt_ts(val) -> str:
    """Format any timestamp as IST: '23 Mar 2026 16:31 IST'.
    Accepts datetime, ISO string, or None. Never outputs raw ISO 8601."""
    if val is None:
        return "—"
    if isinstance(val, str):
        if not val or val == "—":
            return "—"
        try:
            val = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            # Last resort: try slicing but format properly
            try:
                val = datetime.fromisoformat(val[:19])
            except Exception:
                return val[:16].replace("T", " ")
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        ist = val.astimezone(_IST)
        return ist.strftime("%d %b %Y %H:%M IST")
    return str(val)


def _fmt_ts_short(val) -> str:
    """Short IST format: '23 Mar 16:31'. For table cells with limited space."""
    if val is None:
        return "—"
    if isinstance(val, str):
        if not val or val == "—":
            return "—"
        try:
            val = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            try:
                val = datetime.fromisoformat(val[:19])
            except Exception:
                return val[:10]
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        ist = val.astimezone(_IST)
        return ist.strftime("%d %b %H:%M")
    return str(val)


def _fmt_uptime(val: Optional[float]) -> str:
    if val is None:
        return "—"
    return f"{val:.2f}%"


def _fmt_hours(val: Optional[float]) -> str:
    if val is None:
        return "—"
    if val < 1.0:
        return f"{val * 60:.0f} min"
    return f"{val:.1f} h"


def _fmt_num(val: Optional[float], suffix: str = "") -> str:
    if val is None:
        return "—"
    return f"{val:.1f}{suffix}"


def _sla_badge_style(tier: str):
    """Return (text_color, bg_color) for a SLA tier cell."""
    return _SLA_COLORS.get(tier, _SLA_COLORS["Unknown"]), _SLA_BG.get(tier, _SLA_BG["Unknown"])


def _status_style(status: str):
    """Return (text_color, bg_color) for an availability status cell."""
    key = (status or "unknown").lower()
    return _STATUS_STYLE.get(key, _STATUS_STYLE["unknown"])


def _fmt_bps(val: Optional[float]) -> str:
    """Format bytes-per-second into human-readable throughput."""
    if val is None:
        return "—"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f} MB/s"
    if val >= 1_000:
        return f"{val / 1_000:.1f} KB/s"
    return f"{val:.0f} B/s"


# ── Re-usable table style builder ────────────────────────────────────────────

def base_table_style(header_rows: int = 1):
    return TableStyle([
        # Header
        ("BACKGROUND",  (0, 0), (-1, header_rows - 1), hex_color(NAVY)),
        ("TEXTCOLOR",   (0, 0), (-1, header_rows - 1), colors.white),
        ("FONTNAME",    (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, header_rows - 1), 8),
        ("TOPPADDING",  (0, 0), (-1, header_rows - 1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, header_rows - 1), 6),
        ("ALIGN",       (0, 0), (-1, header_rows - 1), "CENTER"),
        # Body
        ("FONTNAME",    (0, header_rows), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, header_rows), (-1, -1), 7.5),
        ("TOPPADDING",  (0, header_rows), (-1, -1), 4),
        ("BOTTOMPADDING", (0, header_rows), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, hex_color(BG_ALT)]),
        # Grid
        ("GRID",        (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ])


# ── Spacer rhythm constants ───────────────────────────────────────────────────
SP_CAPTION    = Spacer(1, 4)
SP_BLOCK      = Spacer(1, 8)
SP_TABLE_GAP  = Spacer(1, 10)
SP_SECTION    = Spacer(1, 14)
SP_AFTER_TITLE = Spacer(1, 6)


def sla_bar_cell(pct: float, color_hex: str, width: int = 150) -> Table:
    """Native ReportLab colour bar replacing the ASCII █░ bar.

    Renders a two-cell inner Table: a filled colour slab + a muted empty slab.
    Safe for edge cases: 0% and 100% both produce valid colWidths.
    """
    filled_w = max(0.5, int(pct * width / 100))
    empty_w  = max(0.5, width - filled_w)
    bar = Table([["", ""]], colWidths=[filled_w, empty_w], rowHeights=[7])
    bar.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, 0), HexColor(color_hex)),
        ("BACKGROUND",    (1, 0), (1, 0), HexColor("#1E293B")),
        ("BOX",           (0, 0), (-1, -1), 0.3, HexColor("#334155")),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return bar


def _kpi_color(val: float, high: float = 90.0, med: float = 70.0) -> str:
    """Return a hex color string based on value thresholds (green/amber/red)."""
    if val >= high:
        return "#16A34A"
    if val >= med:
        return "#D97706"
    return "#DC2626"


# ── 3-table layout helpers ────────────────────────────────────────────────────

def _fmt_min_max(row: dict) -> str:
    """Format 'Min / Max' latency cell. Returns '8 / 210' or '— / —'."""
    mn = row.get("min_latency_ms")
    mx = row.get("max_latency_ms")
    mn_s = f"{mn:.0f}" if mn is not None else "—"
    mx_s = f"{mx:.0f}" if mx is not None else "—"
    return f"{mn_s} / {mx_s}"


def _agent_color(row: dict):
    """Return (text_hex, bg_hex) for Agent Status badge cell."""
    status = (row.get("agent_status") or "").upper()
    if status == "INSTALLED":
        return ("#166534", "#DCFCE7")   # green
    if status == "OFFLINE":
        return ("#991B1B", "#FEE2E2")   # red
    return ("#374151", "#F3F4F6")        # grey for N/A / unknown


def _confidence_color_fn(row: dict):
    """Return (text_hex, bg_hex) using existing _CONFIDENCE_COLORS/_CONFIDENCE_BG dicts."""
    level = (row.get("data_confidence") or "NO_DATA").upper()
    text  = _CONFIDENCE_COLORS.get(level, _CONFIDENCE_COLORS["NO_DATA"])
    bg    = _CONFIDENCE_BG.get(level, _CONFIDENCE_BG["NO_DATA"])
    return (text, bg)


# ── Fleet table column specs (3-table layout) ─────────────────────────────────
# All width lists must sum to 100% to fill ReportLab landscape content width.

_COLS_AVAILABILITY = [
    {"header": "Device Name",    "width": "26%", "key": "device_name",    "max_chars": 26, "align": "LEFT"},
    {"header": "IP Address",     "width": "11%", "key": "device_ip",      "align": "LEFT"},
    {"header": "Device Role",    "width": "9%",  "key": "device_type",    "align": "CENTER"},
    {"header": "SLA Tier",       "width": "9%",  "key": "sla_tier",       "align": "CENTER",
     "color_fn": lambda r: _sla_badge_style(r.get("sla_tier", "Unknown"))},
    {"header": "Uptime %",       "width": "9%",  "fmt": lambda r: _fmt_uptime(r.get("uptime_pct")),    "align": "RIGHT"},
    {"header": "Uptime (Hrs)",   "width": "9%",  "fmt": lambda r: _fmt_hours(r.get("uptime_hours")),   "align": "RIGHT"},
    {"header": "Downtime %",     "width": "9%",  "fmt": lambda r: _fmt_uptime(r.get("downtime_pct")),  "align": "RIGHT"},
    {"header": "Downtime (Hrs)", "width": "18%", "fmt": lambda r: _fmt_hours(r.get("downtime_hours")), "align": "RIGHT"},
]  # 26+11+9+9+9+9+9+18 = 100%

_COLS_PING = [
    {"header": "Device Name",       "width": "22%", "key": "device_name",         "max_chars": 26, "align": "LEFT"},
    {"header": "Ping Interval",     "width": "10%", "key": "ping_interval_label",  "align": "CENTER"},
    {"header": "Avg Latency (ms)",  "width": "14%", "fmt": lambda r: _fmt_num(r.get("avg_latency_ms"), ""), "align": "RIGHT"},
    {"header": "Min / Max (ms)",    "width": "16%", "fmt": _fmt_min_max,            "align": "RIGHT"},
    {"header": "Packet Loss %",     "width": "12%", "fmt": lambda r: _fmt_num(r.get("avg_packet_loss_pct"), "%"), "align": "RIGHT"},
    {"header": "Total Timeouts",    "width": "12%", "fmt": lambda r: str(r.get("timeout_count") or "—"),       "align": "RIGHT"},
    {"header": "Timeout %",         "width": "14%", "fmt": lambda r: _fmt_num(r.get("timeout_pct"), "%"),       "align": "RIGHT"},
]  # 22+10+14+16+12+12+14 = 100%

_COLS_TELEMETRY = [
    {"header": "Device Name",      "width": "22%", "key": "device_name",     "max_chars": 26, "align": "LEFT"},
    {"header": "Agent Status",     "width": "12%", "key": "agent_status",    "align": "CENTER", "color_fn": _agent_color},
    {"header": "Expected Scans",   "width": "13%", "fmt": lambda r: str(r.get("expected_scans") or "—"), "align": "RIGHT"},
    {"header": "Actual Scans",     "width": "13%", "fmt": lambda r: str(r.get("actual_scans")   or "—"), "align": "RIGHT"},
    {"header": "Data Confidence",  "width": "14%", "key": "data_confidence", "align": "CENTER", "color_fn": _confidence_color_fn},
    {"header": "Top Violations",   "width": "26%", "key": "anomaly_reason",  "max_chars": 32,   "align": "LEFT"},
]  # 22+12+13+13+14+26 = 100%


def kpi_strip(items: list) -> Table:
    """Build a dark-background KPI card strip.

    items: [{"label": str, "value": str, "color": str (hex)}, ...]
    Max 5 items. Returns a full-width Table flowable.
    """
    _cell_style = ParagraphStyle(
        "_ks_cell",
        fontName="Helvetica",
        fontSize=7,
        alignment=1,      # CENTER
        leading=10,
        textColor=HexColor("#64748B"),
    )
    cells = []
    for item in items:
        text = (
            f'<font name="Helvetica-Bold" size="18" color="{item["color"]}">'
            f'{item["value"]}</font><br/>'
            f'<font name="Helvetica" size="7" color="#64748B">{item["label"]}</font>'
        )
        cells.append(Paragraph(text, _cell_style))

    n = len(items)
    col_w = [f"{100 // n}%" for _ in items]
    t = Table([cells], colWidths=col_w, rowHeights=[46])
    t.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LINEAFTER",     (0, 0), (-2, -1), 0.4, HexColor("#334155")),
        ("BACKGROUND",    (0, 0), (-1, -1), HexColor("#0F172A")),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    return t


def _render_narrative_kpis(narrative: dict) -> list:
    """Build KPI strip from narrative executive_banner.kpis list.

    Returns [kpi_strip_table, SP_BLOCK] when kpis exist, else [].
    Bridges the narrative service's typed KPI objects (with delta badges)
    into the PDF renderer — previously these were silently dropped.
    """
    banner = narrative.get("executive_banner") if narrative else None
    if not isinstance(banner, dict):
        return []
    kpis = banner.get("kpis") or []
    if not kpis:
        return []
    _STATUS_COLORS = {"ok": "#16A34A", "warning": "#D97706", "critical": "#DC2626"}
    items = []
    for kpi in kpis[:5]:
        color = _STATUS_COLORS.get(kpi.get("status", "ok"), "#94A3B8")
        value = str(kpi.get("value", "—"))
        delta = kpi.get("delta")
        if delta:
            value = f"{value}  {delta}"
        items.append({"label": kpi.get("label", ""), "value": value, "color": color})
    return [kpi_strip(items), SP_BLOCK] if items else []


def section_title_flowable(title: str, subtitle: str = "") -> list:
    """Standard section opener: rule + 11pt bold heading.

    Returns a list of flowables to extend into the story.
    """
    sub = f'  <font size="7.5" color="#64748B">{subtitle}</font>' if subtitle else ""
    heading_style = ParagraphStyle(
        "_sec_title",
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=HexColor(NAVY),
        leading=14,
    )
    return [
        SP_SECTION,
        HRFlowable(width="100%", thickness=1.5, color=HexColor(NAVY), spaceAfter=4),
        Paragraph(f"<b>{title}</b>{sub}", heading_style),
        SP_AFTER_TITLE,
    ]


# ── Section helpers ───────────────────────────────────────────────────────────

def section_heading(text: str, styles):
    style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=hex_color(NAVY),
        spaceBefore=14,
        spaceAfter=4,
        borderPadding=(4, 0, 4, 8),
        leftIndent=0,
    )
    return Paragraph(text, style)


def _subheading(text: str, styles):
    style = ParagraphStyle(
        "SubHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=hex_color(TEXT_MID),
        spaceBefore=8,
        spaceAfter=2,
    )
    return Paragraph(text, style)


def section_heading_with_meta(text: str, styles, meta_text: str = "") -> Table:
    """Section heading with optional right-aligned confidence/source metadata.

    Returns a 2-cell Table: left = bold section title, right = 7pt muted metadata.
    Falls back to plain section_heading when meta_text is empty.
    """
    title_style = ParagraphStyle(
        "SectionHeadingMeta_title",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=hex_color(NAVY),
        spaceBefore=0,
        spaceAfter=0,
    )
    meta_style = ParagraphStyle(
        "SectionHeadingMeta_meta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        textColor=hex_color(TEXT_LIGHT),
        spaceBefore=0,
        spaceAfter=0,
        alignment=2,  # RIGHT
    )
    row = [[Paragraph(text, title_style), Paragraph(meta_text or "", meta_style)]]
    tbl = Table(row, colWidths=["60%", "40%"])
    tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING",    (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (0, -1), 0),
        ("RIGHTPADDING",  (-1, 0), (-1, -1), 0),
        ("LINEBELOW",     (0, 0), (-1, -1), 1.5, hex_color(NAVY)),
    ]))
    return tbl


def _confidence_meta_text(confidence: dict, key: str, period: dict | None = None,
                           device_count: int | None = None) -> str:
    """Build the 7pt right-aligned metadata string for section headings.

    Format: 'Data: <source> (<LEVEL>) · <period> · <N> devices'
    Returns empty string when confidence data is absent.
    """
    info = confidence.get(key, {}) if isinstance(confidence, dict) else {}
    if not info:
        return ""
    level = (info.get("level") or "UNKNOWN").upper()
    source = info.get("source") or ("Daily rollup" if level == "HIGH" else "Raw scans")
    color = _CONFIDENCE_COLORS.get(level, _CONFIDENCE_COLORS["NO_DATA"])
    parts = [f'Data: {source} (<font color="{color}"><b>{level}</b></font>)']
    if period and period.get("start") and period.get("end"):
        try:
            fmt = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00")).strftime("%b %-d")
            parts.append(f'{fmt(period["start"])} – {fmt(period["end"])}')
        except Exception:
            pass
    if device_count is not None and device_count > 0:
        parts.append(f"{device_count} devices")
    return " · ".join(parts)


def _table_label(text: str, styles) -> Paragraph:
    """'TABLE N of 2 — …' caption rendered above each main executive table."""
    return Paragraph(text, ParagraphStyle(
        "TableLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        textColor=hex_color(NAVY_MID),
        spaceBefore=12,
        spaceAfter=4,
    ))


def normal_paragraph(text: str, styles, size: int = 8, color: str = TEXT_DARK):
    style = ParagraphStyle(
        "NormalCustom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=size,
        textColor=hex_color(color),
    )
    return Paragraph(text, style)


# ── Fleet table builder (P5) ─────────────────────────────────────────────────

def build_fleet_table(rows: list, col_specs: list, caption: str = "") -> list:
    """Column-spec driven fleet table builder (Step 8.9).

    Replaces per-row color loops with a declarative column spec, enforces
    the 9-column budget, and applies all styling in a single pass.

    Args:
        rows:      List of row dicts from the report service.
        col_specs: List of column spec dicts — max 9, each with:
            "header"    str   — column header text
            "width"     str   — e.g. "12%"
            "key"       str   — row dict key to read (optional if fmt provided)
            "fmt"       callable(row) → str  (optional; overrides key lookup)
            "align"     str   — "LEFT" | "CENTER" | "RIGHT" (default LEFT)
            "color_fn"  callable(row) → (text_hex, bg_hex) | None  (optional)
            "bold_fn"   callable(row) → bool  (optional; bold when True)
            "max_chars" int   — truncate cell value (optional)
        caption:   Optional footnote text shown below the table.

    Returns:
        List of flowables: [Table] + optional caption Paragraph.
    """
    assert len(col_specs) <= MAX_COLS_LANDSCAPE, (
        f"build_fleet_table: {len(col_specs)} columns exceeds budget of {MAX_COLS_LANDSCAPE}"
    )

    headers = [spec["header"] for spec in col_specs]
    col_w   = [spec["width"]  for spec in col_specs]

    # Build data rows
    table_data: List[Any] = [headers]
    for r in rows:
        cells = []
        for spec in col_specs:
            if "fmt" in spec:
                val = spec["fmt"](r)
            else:
                raw = r.get(spec.get("key", ""), None)
                val = str(raw) if raw is not None else "—"
            max_c = spec.get("max_chars")
            if max_c and len(val) > max_c:
                val = val[:max_c]
            cells.append(val)
        table_data.append(cells)

    table = Table(table_data, colWidths=col_w, repeatRows=1)
    ts = base_table_style()

    # Apply per-column alignment from spec header row
    for col_idx, spec in enumerate(col_specs):
        align = spec.get("align", "LEFT").upper()
        if align != "LEFT":
            ts.add("ALIGN", (col_idx, 1), (col_idx, -1), align)

    # Apply per-cell colors via color_fn + bold_fn
    for row_idx, r in enumerate(rows, start=1):
        for col_idx, spec in enumerate(col_specs):
            color_fn = spec.get("color_fn")
            if color_fn:
                result = color_fn(r)
                if result is not None:
                    text_c, bg_c = result
                    if bg_c:
                        ts.add("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), hex_color(bg_c))
                    if text_c:
                        ts.add("TEXTCOLOR", (col_idx, row_idx), (col_idx, row_idx), hex_color(text_c))
                        ts.add("FONTNAME",  (col_idx, row_idx), (col_idx, row_idx), "Helvetica-Bold")
            bold_fn = spec.get("bold_fn")
            if bold_fn and bold_fn(r):
                ts.add("FONTNAME", (col_idx, row_idx), (col_idx, row_idx), "Helvetica-Bold")

    table.setStyle(ts)
    result_elems: List[Any] = [table]
    if caption:
        result_elems.append(SP_CAPTION)
        result_elems.append(normal_paragraph(caption, getSampleStyleSheet(), size=6.5, color=TEXT_LIGHT))
    return result_elems


# ── Exception strip (P3) ─────────────────────────────────────────────────────

def _build_exception_strip(
    rows: list,
    col_headers: list,
    row_fn,
    label: str,
    styles,
    total_rows: Optional[int] = None,
) -> list:
    """Compact top-5 exception table shown before the full fleet table.

    Only renders when the rows contain Warning/Critical SLA-tier devices.
    Rows are sorted worst-first (ascending uptime_pct, None last).

    Args:
        rows:        All fleet rows (unsorted).
        col_headers: Column header list (max 5 — exception strip is compact).
        row_fn:      Callable(row) → list of cell values.
        label:       Section label text shown above the strip.
        styles:      ReportLab stylesheet.
        total_rows:  Full fleet row count for caption (defaults to len(rows)).
    """
    if not should_render_exception_table(rows):
        return []

    exception_rows = sorted(
        [r for r in rows if r.get("sla_tier") in ("Warning", "Critical")],
        key=lambda r: (r.get("uptime_pct") is None, r.get("uptime_pct") or 0),
    )[:MAX_EXCEPTION_SHORT]

    if not exception_rows:
        return []

    _AMBER = "#92400E"
    elems: List[Any] = [
        Spacer(1, 6),
        Paragraph(label, ParagraphStyle(
            "ExcStripLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            textColor=hex_color(_AMBER),
            spaceBefore=4,
            spaceAfter=3,
        )),
    ]

    strip_data: List[Any] = [col_headers]
    for r in exception_rows:
        strip_data.append(row_fn(r))

    strip = Table(strip_data, repeatRows=1)
    st = TableStyle([
        # Header
        ("BACKGROUND",    (0, 0), (-1, 0), hex_color("#78350F")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7.5),
        ("TOPPADDING",    (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        # Body
        ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [hex_color("#FFFBEB"), hex_color("#FEF3C7")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, hex_color("#D97706")),
        ("BOX",           (0, 0), (-1, -1), 1.5, hex_color("#D97706")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ])
    # Colour-code SLA column (assumed last col)
    sla_col = len(col_headers) - 1
    for idx, r in enumerate(exception_rows, start=1):
        tier = r.get("sla_tier", "Unknown")
        text_c, bg_c = _sla_badge_style(tier)
        st.add("BACKGROUND", (sla_col, idx), (sla_col, idx), hex_color(bg_c))
        st.add("TEXTCOLOR",  (sla_col, idx), (sla_col, idx), hex_color(text_c))
        st.add("FONTNAME",   (sla_col, idx), (sla_col, idx), "Helvetica-Bold")

    strip.setStyle(st)
    elems.append(strip)

    total = total_rows if total_rows is not None else len(rows)
    caption = (
        f"Exception strip: {len(exception_rows)} of {total} devices below SLA threshold, "
        f"ranked by uptime."
    )
    elems.append(SP_CAPTION)
    elems.append(normal_paragraph(caption, styles, size=6.5, color=TEXT_LIGHT))
    elems.append(Spacer(1, 8))
    return elems


# ── Cover page ────────────────────────────────────────────────────────────────

def _build_cover(report: dict, styles, fleet: str = "all") -> list:
    period = report.get("period", {})
    gen_at = _fmt_ts(report.get("generated_at") or datetime.utcnow())

    def _start_str():
        return _fmt_ts(period.get("start"))

    def _end_str():
        return _fmt_ts(period.get("end"))

    # Split title into two lines based on fleet
    _titles = {
        "server":      ("Server & Infrastructure", "Fleet Report"),
        "workstation": ("Employee Workstation", "Fleet Report"),
        "all":         ("Enterprise Availability &amp;", "Uptime Report"),
    }
    title_line1, title_line2 = _titles.get(fleet, _titles["all"])

    _subtitles = {
        "server":      "Server &amp; Network Devices — Uptime, Downtime &amp; SLA Compliance",
        "workstation": "Employee Devices — Uptime, Downtime, MTTR &amp; SLA Compliance",
        "all":         "Device Fleet — Uptime, Downtime &amp; SLA Compliance",
    }
    subtitle_text = _subtitles.get(fleet, _subtitles["all"])

    _reg = PDFStyleRegistry()

    # Return cover flowables directly — wrapping in a Table prevents PageBreak from working.
    # The navy background is painted by the _draw_cover_bg canvas callback in generate_enterprise_pdf.
    return [
        Spacer(1, 1.2 * inch),
        Paragraph(title_line1, _reg.cover_title),
        Paragraph(title_line2, _reg.cover_title),
        Spacer(1, 0.3 * inch),
        Paragraph(subtitle_text, _reg.cover_subtitle),
        Spacer(1, 0.6 * inch),
        HRFlowable(width="100%", thickness=1, color=hex_color(TEAL), spaceAfter=16),
        Paragraph(f"Report Period:  {_start_str()}  →  {_end_str()}", _reg.cover_meta),
        Paragraph(f"Days Covered:   {period.get('days', '—')}", _reg.cover_meta),
        Paragraph(f"Generated:      {gen_at} UTC", _reg.cover_meta),
        Spacer(1, 0.4 * inch),
        Paragraph("CONFIDENTIAL — Internal Use Only", _reg.cover_confidential),
        PageBreak(),
    ]


# ── Narrative section builder (Master Spec progressive disclosure) ────────────

def _build_narrative_section(narrative: Optional[dict], styles) -> list:
    """Build PDF elements for a narrative block.
    Returns list of ReportLab flowables (empty list if no narrative)."""
    if not narrative:
        return []

    _reg = PDFStyleRegistry(styles)
    elems = []

    # Action Required block
    action_items = narrative.get("action_required", [])
    if action_items:
        action_text = f'<font color="#ef4444"><b>Action Required:</b></font><br/>'
        for a in action_items[:5]:
            sev_color = "#ef4444" if a.get("severity") == "critical" else "#f59e0b"
            device = a.get("device", "")
            ip = a.get("ip", "")
            text = a.get("text", "")
            action_text += f'<font color="{sev_color}">&#x25CF;</font> '
            action_text += f'<font color="{TEXT_DARK}"><b>{device}</b></font> '
            if ip:
                action_text += f'<font color="{TEXT_MID}">({ip})</font> '
            action_text += f'<font color="{TEXT_DARK}">{text}</font><br/>'
        elems.append(Paragraph(action_text, _reg.narrative_action_required))
    elif narrative.get("section_intro"):
        # No action required — show green banner
        elems.append(Paragraph(
            '<font color="#22c55e">&#x2713; No immediate action required</font>',
            _reg.narrative_no_action,
        ))

    # Risk summary (executive only)
    risk = narrative.get("risk_summary")
    if risk:
        elems.append(Paragraph(
            f'<font color="{TEXT_DARK}">{risk}</font>',
            _reg.narrative_risk_summary,
        ))

    # Section intro
    intro = narrative.get("section_intro")
    if intro:
        elems.append(Paragraph(
            f'<font color="{TEXT_DARK}">{intro}</font>',
            _reg.narrative_section_intro,
        ))

    # Top findings
    findings = narrative.get("top_findings", [])
    if findings:
        findings_text = ""
        for f in findings[:5]:
            sev = f.get("severity", "info")
            sev_color = "#ef4444" if sev == "critical" else "#f59e0b" if sev == "warning" else "#22c55e"
            findings_text += f'<font color="{sev_color}">&#x25CF;</font> '
            findings_text += f'<font color="{TEXT_DARK}">{f.get("text", "")}</font>'
            if f.get("detail"):
                findings_text += f' <font color="{TEXT_MID}">({f["detail"]})</font>'
            findings_text += '<br/>'
        elems.append(Paragraph(findings_text, _reg.narrative_findings))

    # Interpretation
    interp = narrative.get("interpretation")
    if interp:
        elems.append(Paragraph(
            f'<i><font color="{TEXT_MID}">{interp}</font></i>',
            _reg.narrative_interpretation,
        ))

    # Recommended actions
    actions = narrative.get("action_items", [])
    if actions:
        actions_text = f'<font color="{TEAL}"><b>Recommended Actions:</b></font><br/>'
        for i, act in enumerate(actions[:5], 1):
            actions_text += f'<font color="{TEXT_MID}">{i}. {act}</font><br/>'
        elems.append(Paragraph(actions_text, _reg.narrative_rec_actions))

    return elems


# ── Decision banner ───────────────────────────────────────────────────────────

_BANNER_STYLE: Dict[str, tuple] = {
    # (label, text_color, bg_color, border_color)
    "ok":       ("GREEN",    "#16A34A", "#F0FDF4", "#22C55E"),
    "warning":  ("AMBER",    "#D97706", "#FFFBEB", "#F59E0B"),
    "critical": ("RED",      "#DC2626", "#FEF2F2", "#EF4444"),
}
# Usable width: landscape A4 minus 28pt margins each side
_BANNER_W = landscape(A4)[0] - 56


def _build_decision_banner(cross_report: Optional[dict], styles) -> list:
    """Full-width colored RAG banner for the executive summary page.

    Returns a list containing a single KeepTogether flowable, or an empty list
    when cross_report is absent.
    """
    if not cross_report:
        return []

    fleet_status = (cross_report.get("fleet_status") or "ok").lower()
    label, text_color, bg_color, border_color = _BANNER_STYLE.get(fleet_status, _BANNER_STYLE["ok"])

    uptime     = cross_report.get("fleet_uptime")
    crit       = cross_report.get("critical_device_count") or 0
    unres      = cross_report.get("unresolved_alert_count") or 0
    action_str = cross_report.get("composite_action") or ""
    risks      = (cross_report.get("top_risks") or [])[:2]

    # Headline sentence
    if fleet_status == "ok":
        uptime_part = f" — {uptime:.2f}% availability." if uptime is not None else "."
        headline = f"Fleet Healthy{uptime_part} No critical issues."
    elif fleet_status == "warning":
        parts = []
        if uptime is not None:
            parts.append(f"{uptime:.2f}% availability")
        if crit:
            parts.append(f"{crit} device(s) below SLA")
        if unres:
            parts.append(f"{unres} unresolved alert(s)")
        headline = "Fleet At Risk" + (" — " + ". ".join(parts) + "." if parts else ".")
    else:
        headline = f"Fleet Critical — {crit or '?'} device(s) degraded. Immediate action required."

    _reg = PDFStyleRegistry(styles)
    text_c = text_color

    lines = (
        f'<b><font color="{text_c}">{label}</font></b>'
        f'  <font color="{TEXT_DARK}">{headline}</font>'
    )
    for r in risks:
        lines += f'<br/><font color="{TEXT_MID}" size="7">• {r}</font>'
    if action_str:
        lines += f'<br/><font color="{text_c}" size="7"><i>→ {action_str}</i></font>'

    inner_style = ParagraphStyle(
        "_BannerPara",
        parent=_reg["body"],
        fontSize=8.5,
        leading=12,
    )
    para = Paragraph(lines, inner_style)

    # Single-cell table; LINEBEFORE gives the left color stripe
    tbl = Table([[para]], colWidths=[_BANNER_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), hex_color(bg_color)),
        ("LINEBEFORE",    (0, 0), (0, -1),  3, hex_color(border_color)),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    return [KeepTogether([tbl, Spacer(1, 8)])]


# ── Executive summary page ────────────────────────────────────────────────────

def _build_executive_summary(report: dict, styles) -> list:
    """
    Executive summary page — exactly two main data tables:

      TABLE 1 — Fleet Health Scorecard
        Section A: capacity KPIs (big-number row)
        Section B: SLA tier distribution with bar chart

      TABLE 2 — Devices Requiring Attention
        Ranked by downtime; rank indicator + colour-coded SLA cells
    """
    summary = report.get("summary", {})
    fleet_avg = summary.get("fleet_avg_uptime")
    sla_dist = summary.get("sla_distribution", {})

    total_devices = max(summary.get("total_devices", 1), 1)

    # ── Page heading ────────────────────────────────────────────────────────────────────────────
    elems: List[Any] = [
        section_heading("Executive Summary", styles),
        HRFlowable(width="100%", thickness=1.5, color=hex_color(NAVY), spaceAfter=10),
    ]

    # ── Decision Banner (RAG fleet status) ─────────────────────────────────────────────────────
    elems.extend(_build_decision_banner(report.get("cross_report"), styles))

    # ── Insights box ────────────────────────────────────────────────────────────────────────────
    insights = report.get("insights")
    if insights and insights.get("findings"):
        insight_text = f'<font color="{TEAL}"><b>Analysis:</b></font> '
        insight_text += f'<font color="{TEXT_DARK}">{insights.get("executive_summary", "")}</font><br/>'
        for f in insights["findings"][:3]:
            sev_c = "#ef4444" if f["severity"] == "critical" else "#f59e0b" if f["severity"] == "warning" else "#22c55e"
            insight_text += f'<font color="{sev_c}">&#x25CF;</font> <font color="{TEXT_DARK}">{f["text"]}</font><br/>'
        if insights.get("recommendations"):
            insight_text += f'<br/><font color="{TEAL}"><b>Recommendations:</b></font><br/>'
            for i, rec in enumerate(insights["recommendations"][:3], 1):
                insight_text += f'<font color="{TEXT_MID}">{i}. {rec}</font><br/>'
        _reg = PDFStyleRegistry(styles)
        elems.append(Paragraph(insight_text, _reg.insights_block))
        source_label = insights.get("insight_source", "rule_based").replace("_", " ").title()
        elems.append(Paragraph(
            f'<font color="{TEXT_LIGHT}" size="6"><i>Insights: {source_label}</i></font>',
            _reg.insights_source,
        ))

    # ── Narrative (progressive disclosure) ────────────────────────────────────────────────────────────
    narratives = report.get("narratives", {})
    exec_narrative = narratives.get("executive") or report.get("narrative")
    elems.extend(_build_narrative_section(exec_narrative, styles))

    # ── Narrative KPI strip (executive_banner.kpis — previously silently dropped) ────────────────────
    elems.extend(_render_narrative_kpis(exec_narrative))

    # ── SLA breach mini-table (8D) — only when breached devices present ─────────────────
    _worst_five = summary.get("worst_devices", [])
    _breached_rows = [
        r for r in _worst_five
        if r.get("sla_tier") not in ("Gold", "Silver", "Bronze", None)
        and r.get("uptime_pct") is not None
    ]
    if _breached_rows:
        elems.append(Spacer(1, 8))
        elems.append(_table_label("SLA Compliance — Devices Below Threshold", styles))
        _sla_mini_hdr = ["Device", "SLA Target", "Actual Uptime", "Downtime (min)", "Met?"]
        _sla_mini_data: List[List[str]] = [_sla_mini_hdr]
        _tier_thresholds = {
            "Gold": SLA_GOLD, "Silver": SLA_SILVER,
            "Bronze": SLA_BRONZE, "Warning": SLA_WARNING, "Critical": 0.0,
        }
        for r in _breached_rows[:8]:
            tier = r.get("sla_tier", "Unknown")
            target_pct = _tier_thresholds.get(tier, 95.0)
            actual = r.get("uptime_pct")
            dh = r.get("downtime_hours") or 0.0
            breach_min = f"{dh * 60:.0f}"
            met = "Yes" if tier in ("Gold", "Silver", "Bronze") else "No"
            _sla_mini_data.append([
                (r.get("device_name") or "—")[:30],
                f"≥ {target_pct}%",
                _fmt_uptime(actual),
                breach_min,
                met,
            ])
        _sla_mini_tbl = Table(
            _sla_mini_data,
            colWidths=["32%", "16%", "16%", "18%", "10%"],
        )
        _sla_mini_ts = TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), hex_color(NAVY)),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 7),
            ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, 0), 5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",      (0, 1), (-1, -1), 7),
            ("TOPPADDING",    (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, hex_color(BG_ALT)]),
            ("GRID",  (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
            ("BOX",   (0, 0), (-1, -1), 1.5, hex_color("#DC2626")),
            ("VALIGN",(0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ])
        for ri, r in enumerate(_breached_rows[:8], start=1):
            tier = r.get("sla_tier", "Unknown")
            met_bg = "#D1FAE5" if tier in ("Gold", "Silver", "Bronze") else "#FEE2E2"
            met_fg = "#065F46" if tier in ("Gold", "Silver", "Bronze") else "#991B1B"
            _sla_mini_ts.add("BACKGROUND", (4, ri), (4, ri), hex_color(met_bg))
            _sla_mini_ts.add("TEXTCOLOR",  (4, ri), (4, ri), hex_color(met_fg))
            _sla_mini_ts.add("FONTNAME",   (4, ri), (4, ri), "Helvetica-Bold")
        _sla_mini_tbl.setStyle(_sla_mini_ts)
        elems.append(_sla_mini_tbl)
        elems.append(SP_CAPTION)

    # ════════════════════════════════════════════════════════════════════════
    # TABLE 1 of 2 — Fleet Health Scorecard
    # Section A: capacity KPI big-numbers.
    # Section B: SLA tier distribution with bar chart.
    # ════════════════════════════════════════════════════════════════════════
    elems.append(_table_label("TABLE 1 of 2  —  Fleet Health Scorecard", styles))

    if fleet_avg is None:
        avg_color = TEXT_MID
    elif fleet_avg >= SLA_GOLD:
        avg_color = "#16A34A"
    elif fleet_avg >= SLA_SILVER:
        avg_color = "#65A30D"
    elif fleet_avg >= SLA_BRONZE:
        avg_color = "#D97706"
    else:
        avg_color = "#DC2626"

    fleet_uptime_str = f"{fleet_avg:.3f}%" if fleet_avg is not None else "—"

    sla_thresholds = {
        "Gold":     f"≥ {SLA_GOLD}%",
        "Silver":   f"≥ {SLA_SILVER}%",
        "Bronze":   f"≥ {SLA_BRONZE}%",
        "Warning":  f"≥ {SLA_WARNING}%",
        "Critical": f"< {SLA_WARNING}%",
        "Unknown":  "No Data",
    }

    # Row layout:
    #  0  SECTION A header (spans all 5 cols)
    #  1  KPI column labels (small, muted)
    #  2  KPI big values (18pt bold)
    #  3  SECTION B header (spans all 5 cols)
    #  4  SLA column headers
    #  5-10  SLA tier data rows
    scorecard_data: List[List[str]] = [
        ["SECTION A  —  CAPACITY OVERVIEW", "", "", "", ""],
        ["Total Devices", "With Data", "Server Fleet", "Employee Fleet", "Fleet Avg Uptime"],
        [
            str(summary.get("total_devices", 0)),
            str(summary.get("devices_with_data", 0)),
            str(summary.get("server_devices", 0)),
            str(summary.get("tracked_devices", 0)),
            fleet_uptime_str,
        ],
        ["SECTION B  —  SLA TIER DISTRIBUTION", "", "", "", ""],
        ["Tier", "Threshold", "Distribution Bar", "Count", "% of Fleet"],
    ]

    tier_row_map: Dict[str, int] = {}
    for tier in ("Gold", "Silver", "Bronze", "Warning", "Critical", "Unknown"):
        count = sla_dist.get(tier, 0)
        pct = count / total_devices * 100 if count else 0.0
        tier_color = _SLA_COLORS.get(tier, _SLA_COLORS["Unknown"])
        bar_cell = sla_bar_cell(pct, tier_color)
        scorecard_data.append([
            tier,
            sla_thresholds[tier],
            bar_cell,
            str(count) if count else "—",
            f"{pct:.1f}%" if count else "—",
        ])
        tier_row_map[tier] = len(scorecard_data) - 1

    scorecard = Table(scorecard_data, colWidths=["20%", "20%", "25%", "15%", "20%"])
    sc_style = TableStyle([
        ("BOX",           (0, 0), (-1, -1), 1.5, hex_color(NAVY)),
        ("GRID",          (0, 0), (-1, -1), 0.3, hex_color(BORDER)),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Row 0: SECTION A header
        ("SPAN",          (0, 0), (-1, 0)),
        ("BACKGROUND",    (0, 0), (-1, 0), hex_color(NAVY)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("ALIGN",         (0, 0), (-1, 0), "LEFT"),
        ("LEFTPADDING",   (0, 0), (-1, 0), 10),
        ("TOPPADDING",    (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        # Row 1: KPI column labels
        ("BACKGROUND",    (0, 1), (-1, 1), hex_color(BG_ALT)),
        ("TEXTCOLOR",     (0, 1), (-1, 1), hex_color(TEXT_MID)),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 7),
        ("ALIGN",         (0, 1), (-1, 1), "CENTER"),
        ("TOPPADDING",    (0, 1), (-1, 1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
        # Row 2: KPI big values
        ("BACKGROUND",    (0, 2), (-1, 2), hex_color(BG_LIGHT)),
        ("FONTNAME",      (0, 2), (-1, 2), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 2), (-1, 2), 18),
        ("ALIGN",         (0, 2), (-1, 2), "CENTER"),
        ("TEXTCOLOR",     (0, 2), (3, 2),  hex_color(NAVY)),
        ("TEXTCOLOR",     (4, 2), (4, 2),  hex_color(avg_color)),
        ("TOPPADDING",    (0, 2), (-1, 2), 12),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 12),
        # Row 3: SECTION B header
        ("SPAN",          (0, 3), (-1, 3)),
        ("BACKGROUND",    (0, 3), (-1, 3), hex_color(NAVY_MID)),
        ("TEXTCOLOR",     (0, 3), (-1, 3), colors.white),
        ("FONTNAME",      (0, 3), (-1, 3), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 3), (-1, 3), 9),
        ("ALIGN",         (0, 3), (-1, 3), "LEFT"),
        ("LEFTPADDING",   (0, 3), (-1, 3), 10),
        ("TOPPADDING",    (0, 3), (-1, 3), 8),
        ("BOTTOMPADDING", (0, 3), (-1, 3), 8),
        # Row 4: SLA column headers
        ("BACKGROUND",    (0, 4), (-1, 4), hex_color(NAVY)),
        ("TEXTCOLOR",     (0, 4), (-1, 4), colors.white),
        ("FONTNAME",      (0, 4), (-1, 4), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 4), (-1, 4), 7.5),
        ("ALIGN",         (0, 4), (-1, 4), "CENTER"),
        ("TOPPADDING",    (0, 4), (-1, 4), 6),
        ("BOTTOMPADDING", (0, 4), (-1, 4), 6),
        # Rows 5-10: SLA tier data
        ("ROWBACKGROUNDS",(0, 5), (-1, -1), [colors.white, hex_color(BG_ALT)]),
        ("FONTSIZE",      (0, 5), (-1, -1), 8),
        ("ALIGN",         (1, 5), (-1, -1), "CENTER"),
        ("ALIGN",         (0, 5), (0, -1),  "LEFT"),
        ("LEFTPADDING",   (0, 5), (0, -1),  8),
        # col 2 is now a native colour-bar Table — no text styling needed
        ("ALIGN",         (2, 5), (2, -1),  "CENTER"),
        ("VALIGN",        (2, 5), (2, -1),  "MIDDLE"),
    ])
    for tier, row_idx in tier_row_map.items():
        text_c, bg_c = _sla_badge_style(tier)
        sc_style.add("BACKGROUND", (0, row_idx), (0, row_idx), hex_color(bg_c))
        sc_style.add("TEXTCOLOR",  (0, row_idx), (0, row_idx), hex_color(text_c))
        sc_style.add("FONTNAME",   (0, row_idx), (0, row_idx), "Helvetica-Bold")
        if sla_dist.get(tier, 0):
            sc_style.add("FONTNAME",  (3, row_idx), (3, row_idx), "Helvetica-Bold")
            sc_style.add("TEXTCOLOR", (3, row_idx), (3, row_idx), hex_color(text_c))
            sc_style.add("TEXTCOLOR", (4, row_idx), (4, row_idx), hex_color(text_c))
    scorecard.setStyle(sc_style)
    elems += [scorecard, Spacer(1, 16)]

    # ── Teal dashed divider between the two tables ───────────────────────────────────────────────────────
    elems.append(HRFlowable(
        width="100%", thickness=2, color=hex_color(TEAL),
        dash=(5, 4), spaceAfter=12,
    ))

    # ════════════════════════════════════════════════════════════════════════
    # TABLE 2 of 2 — Devices Requiring Attention (ranked by downtime)
    # ════════════════════════════════════════════════════════════════════════
    elems.append(_table_label(
        "TABLE 2 of 2  —  Devices Requiring Attention  (Ranked by Degradation)",
        styles,
    ))

    worst = summary.get("worst_devices") or []
    _total_worst = len(worst)
    worst = worst[:10]
    if not worst:
        elems.append(normal_paragraph(
            "No device downtime data available for this period.", styles, color=TEXT_MID,
        ))
    else:
        attn_header = [
            "#", "●",
            "Device Name", "IP Address", "Type / Role",
            "Uptime %", "Downtime", "MTTR (min)", "MTBF (hrs)", "SLA Met", "SLA Tier",
        ]
        attn_data: List[List[str]] = [attn_header]

        def _fmt_mttr(val) -> str:
            if val is None:
                return "—"
            return f"{round(float(val), 0):.0f}"

        def _fmt_mtbf(val) -> str:
            if val is None:
                return "—"
            return f"{round(float(val), 1):.1f}"

        for rank, r in enumerate(worst, start=1):
            tier = r.get("sla_tier", "Unknown")
            if tier in ("Gold", "Silver"):
                indicator = "✓"
            elif tier in ("Bronze", "Warning"):
                indicator = "▲"
            else:
                indicator = "✕"
            sla_met = "Yes" if tier in ("Gold", "Silver", "Bronze") else "No"

            attn_data.append([
                str(rank),
                indicator,
                (r.get("device_name") or "—")[:28],
                r.get("device_ip", "—"),
                (r.get("device_type") or r.get("employee_name") or "—")[:18],
                _fmt_uptime(r.get("uptime_pct")),
                _fmt_hours(r.get("downtime_hours")),
                _fmt_mttr(r.get("mttr_min")),
                _fmt_mtbf(r.get("mtbf_hours")),
                sla_met,
                tier,
            ])

        attn_table = Table(
            attn_data,
            colWidths=["4%", "4%", "20%", "11%", "11%", "9%", "9%", "8%", "8%", "7%", "9%"],
        )
        attn_ts = TableStyle([
            # Header
            ("BACKGROUND",    (0, 0), (-1, 0), hex_color(NAVY)),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, 0), 7),
            ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            # Body
            ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",      (0, 1), (-1, -1), 7),
            ("TOPPADDING",    (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, hex_color(BG_ALT)]),
            # Grid + outer border
            ("GRID",  (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
            ("BOX",   (0, 0), (-1, -1), 1.5, hex_color(NAVY)),
            ("VALIGN",(0, 0), (-1, -1), "MIDDLE"),
            # Rank col: centred, muted
            ("ALIGN",     (0, 0), (0, -1), "CENTER"),
            ("TEXTCOLOR", (0, 1), (0, -1), hex_color(TEXT_LIGHT)),
            ("FONTNAME",  (0, 1), (0, -1), "Helvetica-Bold"),
            # Indicator col: centred, larger glyph
            ("ALIGN",    (1, 0), (1, -1), "CENTER"),
            ("FONTSIZE", (1, 1), (1, -1), 9),
            ("FONTNAME", (1, 1), (1, -1), "Helvetica-Bold"),
            # Uptime col: right-aligned, bold
            ("ALIGN",    (5, 0), (5, -1), "RIGHT"),
            ("FONTNAME", (5, 1), (5, -1), "Helvetica-Bold"),
            # Downtime col: right-aligned
            ("ALIGN",    (6, 0), (6, -1), "RIGHT"),
            # MTTR / MTBF: right-aligned
            ("ALIGN",    (7, 0), (7, -1), "RIGHT"),
            ("ALIGN",    (8, 0), (8, -1), "RIGHT"),
            # SLA Met col: centred
            ("ALIGN",    (9, 0), (9, -1), "CENTER"),
            ("FONTNAME", (9, 1), (9, -1), "Helvetica-Bold"),
            # SLA tier col: centred
            ("ALIGN",    (10, 0), (10, -1), "CENTER"),
        ])

        for row_idx, r in enumerate(worst, start=1):
            tier = r.get("sla_tier", "Unknown")
            text_c, bg_c = _sla_badge_style(tier)
            # SLA tier cell (col 10): coloured badge
            attn_ts.add("BACKGROUND", (10, row_idx), (10, row_idx), hex_color(bg_c))
            attn_ts.add("TEXTCOLOR",  (10, row_idx), (10, row_idx), hex_color(text_c))
            attn_ts.add("FONTNAME",   (10, row_idx), (10, row_idx), "Helvetica-Bold")
            # Uptime cell: same colour as SLA tier
            attn_ts.add("TEXTCOLOR",  (5, row_idx), (5, row_idx), hex_color(text_c))
            # SLA Met cell (col 9): green Yes / red No
            sla_met_bg = "#D1FAE5" if tier in ("Gold", "Silver", "Bronze") else "#FEE2E2"
            sla_met_fg = "#065F46" if tier in ("Gold", "Silver", "Bronze") else "#991B1B"
            attn_ts.add("BACKGROUND", (9, row_idx), (9, row_idx), hex_color(sla_met_bg))
            attn_ts.add("TEXTCOLOR",  (9, row_idx), (9, row_idx), hex_color(sla_met_fg))
            # Indicator glyph colour
            if tier in ("Gold", "Silver"):
                ind_c = "#16A34A"
            elif tier in ("Bronze", "Warning"):
                ind_c = "#D97706"
            else:
                ind_c = "#DC2626"
            attn_ts.add("TEXTCOLOR", (1, row_idx), (1, row_idx), hex_color(ind_c))
            # Rank-1 row: subtle red tint on rank cell to flag worst device
            if row_idx == 1:
                attn_ts.add("BACKGROUND", (0, 1), (0, 1), hex_color("#FEE2E2"))
                attn_ts.add("TEXTCOLOR",  (0, 1), (0, 1), hex_color("#991B1B"))

        attn_table.setStyle(attn_ts)
        elems.append(attn_table)
        if _total_worst > 10:
            elems.append(SP_CAPTION)
            elems.append(normal_paragraph(
                f"Showing top 10 of {_total_worst} devices requiring attention.",
                styles, size=6.5, color=TEXT_LIGHT,
            ))
        # Fleet downtime summary line (8A)
        _total_with_outages = sum(1 for r in worst if (r.get("downtime_hours") or 0) > 0)
        _total_downtime_h = sum(float(r.get("downtime_hours") or 0) for r in worst)
        if _total_with_outages > 0:
            elems.append(SP_CAPTION)
            elems.append(normal_paragraph(
                f"{_total_with_outages} device{'s' if _total_with_outages > 1 else ''} had outages this period; "
                f"total fleet downtime (top 10): {_total_downtime_h:.1f} hrs.",
                styles, size=6.5, color=TEXT_MID,
            ))

    # ── Chronically offline footnote ─────────────────────────────────────────────
    _offline_summary = summary.get("chronically_offline") or report.get("chronically_offline")
    if isinstance(_offline_summary, dict) and _offline_summary.get("count", 0) > 0:
        _off_count = _offline_summary["count"]
        _off_names = ", ".join(
            d.get("name", "—") for d in (_offline_summary.get("devices") or [])[:3]
        )
        _off_text = (
            f'<font color="{TEXT_MID}"><i>'
            f'Note: {_off_count} device(s) with 0% uptime for the entire period '
            f'are excluded from the attention table above '
            f'(e.g. {_off_names}{"..." if _off_count > 3 else ""}). '
            f'Consider decommission review or physical inspection.'
            f'</i></font>'
        )
        elems.append(SP_CAPTION)
        elems.append(Paragraph(_off_text, ParagraphStyle(
            "_OfflineFootnote", parent=styles["Normal"],
            fontName="Helvetica", fontSize=7, leading=9,
            textColor=hex_color(TEXT_MID),
        )))

    # ── Top Alert Sources (compact, capped at MAX_ALERTS_EXECUTIVE = 5) ─────────
    # Pulls total_alerts from existing fleet rows — no additional query needed.
    all_alert_rows = (
        (report.get("server_rows") or []) + (report.get("tracked_rows") or [])
    )
    alert_sources = sorted(
        [r for r in all_alert_rows if r.get("total_alerts", 0) > 0],
        key=lambda r: r.get("total_alerts", 0),
        reverse=True,
    )[:MAX_ALERTS_EXECUTIVE]

    if alert_sources:
        elems.append(Spacer(1, 10))
        elems.append(_table_label(
            "Top Alert Sources  (devices with most alerts this period)",
            styles,
        ))
        alert_hdr = ["Device", "IP", "Type", "SLA Tier", "Alerts"]
        alert_data: List[Any] = [alert_hdr]
        for r in alert_sources:
            alert_data.append([
                truncate_name(r.get("device_name"), 28),
                r.get("device_ip", "—"),
                (r.get("device_type") or r.get("employee_name") or "—")[:16],
                r.get("sla_tier", "Unknown"),
                str(r.get("total_alerts", 0)),
            ])
        alert_tbl = Table(alert_data, colWidths=["28%", "18%", "20%", "18%", "16%"])
        alert_ts = TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), hex_color(NAVY)),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("GRID",          (0, 0), (-1, -1), 0.3, hex_color(BORDER)),
            ("BOX",           (0, 0), (-1, -1), 1.0, hex_color(NAVY)),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, hex_color(BG_ALT)]),
            ("ALIGN",         (4, 0), (4, -1),  "CENTER"),
            ("FONTNAME",      (4, 1), (4, -1),  "Helvetica-Bold"),
        ])
        for idx, r in enumerate(alert_sources, start=1):
            tier = r.get("sla_tier", "Unknown")
            text_c, bg_c = _sla_badge_style(tier)
            alert_ts.add("BACKGROUND", (3, idx), (3, idx), hex_color(bg_c))
            alert_ts.add("TEXTCOLOR",  (3, idx), (3, idx), hex_color(text_c))
            alert_ts.add("FONTNAME",   (3, idx), (3, idx), "Helvetica-Bold")
        alert_tbl.setStyle(alert_ts)
        elems.append(alert_tbl)

    elems.append(PageBreak())
    return elems


# ── Per-device server card (KeepTogether block) ───────────────────────────────

def _build_server_device_card(row: dict, styles) -> List[Any]:
    """Two-or-three-row KeepTogether block per server/network device.

    Row 0 — identity (navy header): name | IP | type | status | SLA tier
    Row 1 — ICMP metrics: uptime %/hrs | downtime %/hrs | latency | pkt loss | timeout
    Row 2 — agent telemetry if available, else a full-width "not available" note.
    """
    col_widths = ["28%", "15%", "14%", "13%", "30%"]

    # Row 0: device identity
    sla_tier = row.get("sla_tier", "Unknown")
    sla_tc, sla_bg = _sla_badge_style(sla_tier)
    status_str = (row.get("availability_status") or "unknown").capitalize()
    status_tc, status_bg = _status_style(status_str.lower())

    id_row = [
        truncate_name(row.get("device_name") or "—", 26),
        row.get("device_ip") or "—",
        (row.get("device_type") or "—")[:12],
        status_str,
        sla_tier,
    ]

    # Row 1: ICMP availability metrics — all four availability figures
    up = row.get("uptime_pct")
    dn_pct = row.get("downtime_pct")
    uptime_str = (
        f"{_fmt_uptime(up)} / {_fmt_hours(row.get('uptime_hours'))}"
        if up is not None else "—"
    )
    downtime_str = (
        f"{_fmt_uptime(dn_pct)} / {_fmt_hours(row.get('downtime_hours'))}"
        if dn_pct is not None else "—"
    )
    timeout_cnt = row.get("timeout_count") or 0
    metrics_row = [
        f"Up: {uptime_str}",
        f"Down: {downtime_str}",
        _fmt_num(row.get("avg_latency_ms"), " ms"),
        _fmt_num(row.get("avg_packet_loss_pct"), "%"),
        f"Timeout: {timeout_cnt}",
    ]

    # Row 2: agent telemetry or explicit absence note
    avg_cpu = row.get("avg_cpu")
    has_agent = avg_cpu is not None
    if has_agent:
        agent_row = [
            f"CPU: {_fmt_num(avg_cpu, '%')} / {_fmt_num(row.get('max_cpu'), '%')} pk",
            f"Mem: {_fmt_num(row.get('avg_mem'), '%')}",
            f"Disk: {_fmt_num(row.get('avg_disk'), '%')}",
            f"In: {_fmt_bps(row.get('avg_net_in_bps'))}",
            f"Out: {_fmt_bps(row.get('avg_net_out_bps'))}",
        ]
    else:
        gap_reason = (row.get("_data_gaps") or {}).get("telemetry", "no_agent_data")
        gap_label = {
            "device_type_unsupported_for_agent": "N/A — network device",
            "no_agent_data": "not available",
        }.get(gap_reason, "not available")
        agent_row = [f"Agent: {gap_label}", "", "", "", ""]

    tbl = Table([id_row, metrics_row, agent_row], colWidths=col_widths)
    ts = TableStyle([
        # Row 0 — identity header (navy)
        ("BACKGROUND",    (0, 0), (-1, 0), hex_color(NAVY)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("TOPPADDING",    (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        # Row 1 — ICMP metrics
        ("BACKGROUND",    (0, 1), (-1, 1), colors.white),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, 1), 7.5),
        ("TOPPADDING",    (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
        # Row 2 — agent (light blue if data, muted grey if absent)
        ("BACKGROUND",    (0, 2), (-1, 2),
         hex_color("#EFF6FF") if has_agent else hex_color("#F9FAFB")),
        ("FONTNAME",      (0, 2), (-1, 2), "Helvetica"),
        ("FONTSIZE",      (0, 2), (-1, 2), 7.5),
        ("TOPPADDING",    (0, 2), (-1, 2), 4),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 4),
        # Grid + alignment
        ("GRID",          (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ])

    # Per-cell colours in identity row
    ts.add("BACKGROUND", (3, 0), (3, 0), hex_color(status_bg))
    ts.add("TEXTCOLOR",  (3, 0), (3, 0), hex_color(status_tc))
    ts.add("BACKGROUND", (4, 0), (4, 0), hex_color(sla_bg))
    ts.add("TEXTCOLOR",  (4, 0), (4, 0), hex_color(sla_tc))
    ts.add("FONTNAME",   (4, 0), (4, 0), "Helvetica-Bold")

    # Highlight non-zero timeouts in metrics row
    if timeout_cnt > 0:
        ts.add("TEXTCOLOR", (4, 1), (4, 1), hex_color("#EA580C"))
        ts.add("FONTNAME",  (4, 1), (4, 1), "Helvetica-Bold")

    if not has_agent:
        # Span the absence note across all five columns
        ts.add("SPAN",      (0, 2), (-1, 2))
        ts.add("FONTNAME",  (0, 2), (-1, 2), "Helvetica-Oblique")
        ts.add("TEXTCOLOR", (0, 2), (-1, 2), hex_color(TEXT_LIGHT))
    else:
        # Heat-colour high CPU / memory readings
        if avg_cpu is not None and avg_cpu > 80:
            ts.add("TEXTCOLOR", (0, 2), (0, 2), hex_color("#DC2626"))
            ts.add("FONTNAME",  (0, 2), (0, 2), "Helvetica-Bold")
        avg_mem = row.get("avg_mem")
        if avg_mem is not None and avg_mem > 80:
            ts.add("TEXTCOLOR", (1, 2), (1, 2), hex_color("#DC2626"))
            ts.add("FONTNAME",  (1, 2), (1, 2), "Helvetica-Bold")

    tbl.setStyle(ts)
    return [KeepTogether([tbl, Spacer(1, 5)])]


# ── Server fleet section ──────────────────────────────────────────────────────

def _build_server_fleet(report: dict, styles) -> list:
    from services.pdf_section_builder import ReportSectionBuilder
    rows = report.get("server_rows", [])
    narratives = report.get("narratives", {})
    elems: List[Any] = (
        ReportSectionBuilder(f"Server & Network Fleet  ({len(rows)} devices)", styles)
        .confidence_meta(report.get("_confidence", {}), "server_fleet", report.get("period"), len(rows))
        .description(
            "Inventory devices managed via SNMP / ICMP scanning and server_agent telemetry. "
            "Uptime from DailyDeviceStats rollups or raw scan history. "
            "Latency & packet-loss from DailyDeviceStats."
        )
        .narrative(narratives.get("server_fleet"))
        .build()
    )

    if not rows:
        elems.append(normal_paragraph("No inventory devices found for this period.", styles))
        return elems

    # ── Server Fleet KPI strip ────────────────────────────────────────────────
    total_srv = len(rows)
    critical_count = sum(1 for r in rows if (r.get("uptime_pct") or 100.0) < 70.0)
    avg_uptime = (
        sum(r.get("uptime_pct") or 0 for r in rows) / total_srv
        if total_srv else 0.0
    )
    good_sla = sum(1 for r in rows if r.get("sla_tier") in ("Gold", "Silver"))
    good_sla_pct = good_sla / total_srv * 100 if total_srv else 0.0

    elems.append(SP_BLOCK)
    elems.append(kpi_strip([
        {"label": "Total Servers",   "value": str(total_srv),
         "color": "#94A3B8"},
        {"label": "Critical (<70%)", "value": str(critical_count),
         "color": "#DC2626" if critical_count > 0 else "#16A34A"},
        {"label": "Avg Uptime",      "value": f"{avg_uptime:.1f}%",
         "color": _kpi_color(avg_uptime)},
        {"label": "SLA Compliant",   "value": f"{good_sla_pct:.0f}%",
         "color": _kpi_color(good_sla_pct)},
    ]))
    elems.append(SP_BLOCK)

    # ── Exception strip — top 5 worst servers before full table (P3) ─────────
    elems.extend(_build_exception_strip(
        rows,
        col_headers=["Device Name", "IP Address", "Uptime %", "SLA Tier", "Downtime"],
        row_fn=lambda r: [
            truncate_name(r.get("device_name"), 28),
            r.get("device_ip", "—"),
            _fmt_uptime(r.get("uptime_pct")),
            r.get("sla_tier", "Unknown"),
            _fmt_hours(r.get("downtime_hours")),
        ],
        label="Exception Strip — Servers Below SLA Threshold",
        styles=styles,
        total_rows=len(rows),
    ))

    styles_ref = getSampleStyleSheet()

    # ── TABLE 1 of 3 — Availability & SLA Ledger ─────────────────────────────
    elems.append(_table_label("TABLE 1 of 3 — Availability & SLA Ledger", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_AVAILABILITY,
        caption="Uptime and downtime for the reporting period. SLA tier based on uptime %."
    ))

    # ── TABLE 2 of 3 — Ping, Latency & Packet Health ─────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 2 of 3 — Ping, Latency & Packet Health", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_PING,
        caption="ICMP health metrics. Timeout % = timeouts / expected pings x 100."
    ))

    # ── TABLE 3 of 3 — Telemetry & Diagnostic Context ────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 3 of 3 — Telemetry & Diagnostic Context", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_TELEMETRY,
        caption="LOW CONFIDENCE = actual scans < 70% of expected. Violations = anomaly_reason."
    ))

    return elems


# ── Employee / tracking fleet section ────────────────────────────────────────

def _build_tracked_fleet(report: dict, styles) -> list:
    from services.pdf_section_builder import ReportSectionBuilder
    rows = report.get("tracked_rows", [])
    narratives = report.get("narratives", {})
    elems: List[Any] = (
        ReportSectionBuilder(f"Employee Device Fleet  ({len(rows)} devices)", styles)
        .confidence_meta(report.get("_confidence", {}), "tracked_fleet", report.get("period"), len(rows))
        .description(
            "Employee / workstation devices managed via the tracking agent. "
            "Uptime from tracked_device_availability_events stream. "
            "Status reflects current availability."
        )
        .narrative(narratives.get("tracked_fleet"))
        .build()
    )

    if not rows:
        elems.append(normal_paragraph("No tracked devices found for this period.", styles))
        return elems

    # ── Tracked Fleet KPI strip ───────────────────────────────────────────────
    total_ws = len(rows)
    offline_count = sum(
        1 for r in rows if (r.get("availability_status") or "").lower() == "offline"
    )
    avg_avail = (
        sum(r.get("uptime_pct") or 0 for r in rows) / total_ws
        if total_ws else 0.0
    )
    high_risk = sum(1 for r in rows if r.get("sla_tier") in ("Warning", "Critical"))

    elems.append(SP_BLOCK)
    elems.append(kpi_strip([
        {"label": "Total Tracked",     "value": str(total_ws),
         "color": "#94A3B8"},
        {"label": "Offline",           "value": str(offline_count),
         "color": "#DC2626" if offline_count > 0 else "#16A34A"},
        {"label": "Avg Availability",  "value": f"{avg_avail:.1f}%",
         "color": _kpi_color(avg_avail)},
        {"label": "High Risk",         "value": str(high_risk),
         "color": "#DC2626" if high_risk > 0 else "#16A34A"},
    ]))
    elems.append(SP_BLOCK)

    # ── Exception strip — top 5 worst workstations before full table (P3) ────
    elems.extend(_build_exception_strip(
        rows,
        col_headers=["Device Name", "Employee", "Status", "Uptime %", "SLA Tier"],
        row_fn=lambda r: [
            truncate_name(r.get("device_name"), 24),
            (r.get("employee_name") or "—")[:16],
            (r.get("availability_status") or "unknown").capitalize(),
            _fmt_uptime(r.get("uptime_pct")),
            r.get("sla_tier", "Unknown"),
        ],
        label="Exception Strip — Workstations Below SLA Threshold",
        styles=styles,
        total_rows=len(rows),
    ))

    styles_ref = getSampleStyleSheet()

    # ── TABLE 1 of 3 — Availability & SLA Ledger ─────────────────────────────
    elems.append(_table_label("TABLE 1 of 3 — Availability & SLA Ledger", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_AVAILABILITY,
        caption="Uptime and downtime for the reporting period. SLA tier based on uptime %."
    ))

    # ── TABLE 2 of 3 — Ping, Latency & Packet Health ─────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 2 of 3 — Ping, Latency & Packet Health", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_PING,
        caption="Workstation devices use event-based availability — ICMP columns show — by design."
    ))

    # ── TABLE 3 of 3 — Telemetry & Diagnostic Context ────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 3 of 3 — Telemetry & Diagnostic Context", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_TELEMETRY,
        caption="LOW CONFIDENCE = actual scans < 70% of expected. Violations = anomaly_reason."
    ))

    # Workstation behavioral metrics — second table, shown when agent data exists
    beh_rows = [
        r for r in rows
        if r.get("productivity_score") is not None
        or r.get("avg_active_hours_day") is not None
    ]
    if beh_rows:
        elems.append(SP_BLOCK)
        elems.append(section_heading("Workstation Behavioral Metrics", styles))
        elems.append(SP_AFTER_TITLE)
        beh_col_specs = [
            {"header": "Device Name",    "width": "18%",
             "fmt": lambda r: (r.get("device_name") or "—")[:22]},
            {"header": "Employee",       "width": "11%",
             "fmt": lambda r: (r.get("employee_name") or "—")[:14]},
            {"header": "Prod. Score",    "width": "10%",
             "fmt": lambda r: _fmt_num(r.get("productivity_score"), "%"),
             "align": "RIGHT"},
            {"header": "Focus Score",    "width": "10%",
             "fmt": lambda r: _fmt_num(r.get("focus_score"), "%"),
             "align": "RIGHT"},
            {"header": "Active Hrs/Day", "width": "11%",
             "fmt": lambda r: _fmt_num(r.get("avg_active_hours_day"), " h"),
             "align": "RIGHT"},
            {"header": "Policy Viol.",   "width": "10%",
             "fmt": lambda r: str(r.get("policy_violations") or 0),
             "color_fn": lambda r: ("#DC2626", None) if (r.get("policy_violations") or 0) > 0 else None,
             "align": "CENTER"},
            {"header": "MTTR (min)",     "width": "10%",
             "fmt": lambda r: _fmt_num(r.get("mttr_min")),
             "align": "RIGHT"},
            {"header": "MTBF (hrs)",     "width": "10%",
             "fmt": lambda r: _fmt_num(r.get("mtbf_hours")),
             "align": "RIGHT"},
            {"header": "Top App",        "width": "10%",
             "fmt": lambda r: (r.get("top_app") or "—")[:12]},
        ]
        elems.extend(build_fleet_table(beh_rows, beh_col_specs))

    return elems


# ── Page-number footer callback ───────────────────────────────────────────────

class PageFooter:
    def __init__(self, report_title: str, gen_at: str, insight_source: str = "rule_based"):
        self.title = report_title
        self.gen_at = gen_at
        self.insight_source = insight_source

    def __call__(self, canvas, doc):
        canvas.saveState()
        w, h = doc.pagesize
        # ── Inner-page header (pages 2+) ────────────────────────────────────
        if doc.page >= 2:
            top_y = h - doc.topMargin + 2
            canvas.setFillColor(hex_color(NAVY))
            canvas.rect(doc.leftMargin, top_y, doc.width, 16, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica", 6.5)
            canvas.drawString(doc.leftMargin + 6, top_y + 5, "CONFIDENTIAL")
            canvas.drawRightString(doc.leftMargin + doc.width - 6, top_y + 5, self.title)
        # ── Page footer ─────────────────────────────────────────────────────
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(hex_color(TEXT_LIGHT))
        # Classification marking · title
        canvas.drawString(doc.leftMargin, doc.bottomMargin - 10,
                          f"CONFIDENTIAL \u00b7 Internal Use Only \u00b7 {self.title}")
        canvas.drawRightString(
            w - doc.rightMargin, doc.bottomMargin - 10,
            f"Generated {self.gen_at} UTC \u00b7 Rule-Based Insights Engine \u00b7 Page {doc.page}",
        )
        canvas.restoreState()


# ── Cover-page background painter ────────────────────────────────────────────

def _draw_cover_bg(canvas, doc):
    """Paint the full-page navy background for the cover page."""
    canvas.saveState()
    w, h = doc.pagesize
    canvas.setFillColor(hex_color(NAVY))
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    canvas.restoreState()


# ── Violations section ────────────────────────────────────────────────────────

def _build_violations_section(report: dict, styles) -> list:
    """Security Summary — aggregate-only, no per-event detail tables."""
    violations = report.get("violations")
    if not violations:
        return []

    total_site  = violations.get("total_site_violations", 0)
    total_typed = violations.get("total_typed_text_alerts", 0)
    total       = total_site + total_typed

    if total == 0:
        return []

    story = [PageBreak()]
    story.append(section_heading("Security Summary", styles))
    story.append(HRFlowable(width="100%", thickness=1, color=hex_color(BORDER), spaceAfter=8))

    # ── KPI row ───────────────────────────────────────────────────────────────
    top_offenders = violations.get("top_offenders", [])
    affected_devices = len(top_offenders)
    top_device = top_offenders[0] if top_offenders else None

    story.append(kpi_strip([
        {"label": "Total Violations",
         "value": str(total),
         "color": "#DC2626" if total > 0 else "#16A34A"},
        {"label": "Affected Devices",
         "value": str(affected_devices),
         "color": "#DC2626" if affected_devices > 0 else "#16A34A"},
        {"label": "Top Offender",
         "value": (top_device.get("device_name", "—") if top_device else "—"),
         "color": "#94A3B8"},
    ]))
    story.append(SP_BLOCK)

    # ── Top violating domains ─────────────────────────────────────────────────
    # Aggregate domain counts from tracked_rows violation data
    domain_totals: Dict[str, int] = {}
    for row in report.get("tracked_rows", []):
        for td in row.get("top_domains", []):
            d = td.get("domain", "")
            domain_totals[d] = domain_totals.get(d, 0) + td.get("count", 0)

    if domain_totals:
        story.append(_subheading("Top Violating Domains", styles))
        top_domains = sorted(domain_totals.items(), key=lambda x: x[1], reverse=True)[:5]
        d_header = ["Domain", "Violation Count"]
        d_rows = [d_header] + [[dom, str(cnt)] for dom, cnt in top_domains]
        d_table = Table(d_rows, colWidths=["70%", "30%"])
        d_table.setStyle(base_table_style())
        story += [d_table, Spacer(1, 0.3 * cm)]

    # ── Top offending device ──────────────────────────────────────────────────
    if top_device:
        story.append(_subheading("Top Offending Device", styles))
        total_offender = top_device.get("site_violations", 0) + top_device.get("typed_text_alerts", 0)
        o_data = [
            ["Device", "Employee", "Violations"],
            [
                top_device.get("device_name", "—"),
                top_device.get("employee_name", "—"),
                str(total_offender),
            ],
        ]
        o_table = Table(o_data, colWidths=["40%", "40%", "20%"])
        o_table.setStyle(base_table_style())
        story.append(o_table)

    return story


def _build_confidence_footnotes(report: dict, styles) -> list:
    """Build data confidence footnotes for the end of the report."""
    confidence = report.get("_confidence", {})
    if not confidence:
        return []

    story = [Spacer(1, 0.5 * cm)]
    story.append(HRFlowable(width="100%", thickness=0.5, color=hex_color(BORDER)))

    legend = (
        f'<font color="{TEXT_LIGHT}" size="7">'
        '<b>Data Confidence:</b> '
        '<font color="#16A34A">HIGH</font> = aggregated rollup data &nbsp;|&nbsp; '
        '<font color="#D97706">MEDIUM</font> = raw scan data &nbsp;|&nbsp; '
        '<font color="#EA580C">LOW</font> = sparse/interpolated &nbsp;|&nbsp; '
        '<font color="#9CA3AF">N/A</font> = no data available'
        '</font>'
    )
    _reg = PDFStyleRegistry(styles)
    story.append(Paragraph(legend, _reg.confidence_legend))

    # Per-section confidence
    for key, info in confidence.items():
        level = info.get("level", "NO_DATA") if isinstance(info, dict) else "NO_DATA"
        source = info.get("source") if isinstance(info, dict) else None
        color = _CONFIDENCE_COLORS.get(level, _CONFIDENCE_COLORS["NO_DATA"])
        label = key.replace("_", " ").title()
        text = f'<font color="{TEXT_LIGHT}" size="6">{label}: <font color="{color}"><b>{level}</b></font>'
        if source:
            text += f' ({source})'
        text += '</font>'
        story.append(Paragraph(text, _reg.confidence_item))

    return story


# ── Public entry point ────────────────────────────────────────────────────────

def generate_enterprise_pdf(report: dict, fleet: str = "all") -> io.BytesIO:
    """
    Render the enterprise uptime/downtime report as a PDF.

    Args:
        report: dict from enterprise_report_service.build_enterprise_uptime_report()
        fleet:  "all"         — both server and workstation sections (default)
                "server"      — cover + exec summary + server fleet only
                "workstation" — cover + exec summary + workstation fleet only

    Returns:
        BytesIO positioned at 0, ready for send_file().
    """
    fleet = fleet if fleet in _FLEET_TITLES else "all"
    logger.info("[EnterprisePDF] Generating PDF: fleet=%s, devices=%d",
                fleet, report.get("summary", {}).get("total_devices", 0))
    period = report.get("period", {})
    gen_at = _fmt_ts(report.get("generated_at") or datetime.utcnow())
    start_str = _fmt_ts(period.get("start"))
    end_str = _fmt_ts(period.get("end"))

    fleet_label = _FLEET_TITLES.get(fleet, _FLEET_TITLES["all"])
    report_title = f"{fleet_label}  |  {start_str} — {end_str}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=36,
        title=report_title,
        author="Device Monitoring Tactical",
    )

    styles = getSampleStyleSheet()
    insight_source = (report.get("insights") or {}).get("insight_source", "rule_based")
    footer = PageFooter(report_title, gen_at, insight_source)

    def _first_page(canvas, doc):
        _draw_cover_bg(canvas, doc)
        footer(canvas, doc)

    # ── Gemini Layer-2 narrative enhancement (optional) ─────────────────────
    import os as _os
    if _os.environ.get("GEMINI_API_KEY"):
        try:
            from services.gemini_pdf_narrative import enhance_pdf_narratives
            report = dict(report)
            report["narratives"] = enhance_pdf_narratives(
                report.get("narratives") or {}, report
            )
            logger.debug("[EnterprisePDF] Gemini narrative enhancement applied")
        except Exception as _e:
            logger.warning("[EnterprisePDF] Gemini narrative enhancement skipped: %s", _e)

    from services.report_context import ReportContext
    ctx = ReportContext(report)

    story = []
    story += _build_cover(report, styles, fleet=fleet)
    story += _build_executive_summary(report, styles)

    # Data quality advisory — shown when any device rows have incomplete data
    _dq = (report.get("summary") or {}).get("data_quality") or {}
    _gap_count = _dq.get("devices_with_gaps", 0)
    if _gap_count > 0:
        _gap_reasons = _dq.get("gap_reasons") or {}
        _reason_parts = [
            f"{v} — {k.replace('_', ' ')}"
            for k, v in sorted(_gap_reasons.items(), key=lambda x: -x[1])[:3]
        ]
        _reason_str = ("; ".join(_reason_parts) + ".") if _reason_parts else ""
        story.append(normal_paragraph(
            f"Data Quality: {_gap_count} device(s) have incomplete data in this period."
            + (f"  Breakdown: {_reason_str}" if _reason_str else ""),
            styles, color="#D97706",
        ))
        story.append(SP_BLOCK)

    if fleet in ("all", "server") and ctx.should_render_server_fleet():
        story += _build_server_fleet(report, styles)
    if fleet in ("all", "workstation") and ctx.should_render_tracked_fleet():
        story += _build_tracked_fleet(report, styles)
    if ctx.should_render_violations():
        story += _build_violations_section(report, styles)
    story += _build_confidence_footnotes(report, styles)

    doc.build(story, onFirstPage=_first_page, onLaterPages=footer)
    buf.seek(0)
    logger.info("[EnterprisePDF] PDF complete: %d bytes", len(buf.getvalue()))
    return buf


def generate_alert_report_pdf(report_data: dict) -> io.BytesIO:
    """Structured Alert History Report PDF.

    Follows the enterprise 8-step section pipeline:
      Cover → KPI strip → Narrative → Exception strip (top 5) →
      Severity breakdown → Top alerted devices → Unresolved aging →
      Confidence footnotes
    """
    period = report_data.get("period", {})
    gen_at = _fmt_ts(report_data.get("generated_at") or datetime.utcnow())
    start_str = _fmt_ts(period.get("start"))
    end_str   = _fmt_ts(period.get("end"))
    report_title = f"Alert History Report  |  {start_str} — {end_str}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=28, rightMargin=28,
        topMargin=28,  bottomMargin=36,
        title=report_title,
        author="Device Monitoring Tactical",
    )
    styles = getSampleStyleSheet()
    _reg = PDFStyleRegistry(styles)
    footer = PageFooter(report_title, gen_at)

    def _first_page(canvas, doc):
        _draw_cover_bg(canvas, doc)
        footer(canvas, doc)

    # ── Cover ──────────────────────────────────────────────────────────────────
    story: List[Any] = [
        Spacer(1, 1.2 * inch),
        Paragraph("Alert History", _reg.cover_title),
        Paragraph("Report", _reg.cover_title),
        Spacer(1, 0.3 * inch),
        Paragraph("System Alerts — Severity, Response Times &amp; Unresolved Aging", _reg.cover_subtitle),
        Spacer(1, 0.2 * inch),
        Paragraph(f"Period:  {start_str} — {end_str}", _reg.cover_meta),
        Paragraph(f"Generated:  {gen_at}", _reg.cover_meta),
        Spacer(1, 0.15 * inch),
        Paragraph("CONFIDENTIAL — INTERNAL USE ONLY", _reg.cover_confidential),
        PageBreak(),
    ]

    # ── KPI strip ──────────────────────────────────────────────────────────────
    sev = report_data.get("severity_breakdown") or {}
    alerts_total   = report_data.get("alerts_total_count") or len(report_data.get("alerts") or [])
    critical_count = sev.get("CRITICAL", 0)
    unresolved     = sum(
        v for k, v in (report_data.get("unresolved_aging") or {}).items()
    )
    tta_human = (report_data.get("tta") or {}).get("human") or "—"
    tta_human = tta_human.split(",")[0] if tta_human != "—" else "—"

    _alert_meta = _confidence_meta_text(
        {"alerts": {"level": "HIGH", "source": "DashboardEvent log"}},
        "alerts", period, alerts_total or None,
    )
    story.append(section_heading_with_meta("Alert Summary", styles, _alert_meta))
    story.append(kpi_strip([
        {"label": "Total Alerts",   "value": str(alerts_total),
         "color": "#DC2626" if alerts_total > 0 else "#16A34A"},
        {"label": "Critical",       "value": str(critical_count),
         "color": "#DC2626" if critical_count > 0 else "#16A34A"},
        {"label": "Unresolved",     "value": str(unresolved),
         "color": "#DC2626" if unresolved > 0 else "#16A34A"},
        {"label": "Avg TTA",        "value": tta_human,
         "color": "#D97706" if tta_human != "—" else "#94A3B8"},
    ]))
    story.append(SP_BLOCK)

    # ── Narrative ──────────────────────────────────────────────────────────────
    story.extend(_build_narrative_section(report_data.get("narrative"), styles))

    # ── Exception strip — top 5 oldest unresolved critical alerts ─────────────
    all_alerts = report_data.get("alerts") or []
    unresolved_alerts = sorted(
        [a for a in all_alerts if not a.get("resolved") and a.get("severity") == "CRITICAL"],
        key=lambda a: a.get("timestamp") or "",
    )[:5]
    if unresolved_alerts:
        story.append(SP_BLOCK)
        story.append(_table_label("Exception Strip — Oldest Unresolved Critical Alerts", styles))
        exc_headers = ["Device", "IP", "Type", "Since", "Age"]
        exc_data: List[Any] = [exc_headers]
        for a in unresolved_alerts:
            ts_raw = a.get("timestamp", "")
            since_str = _fmt_ts_short(ts_raw) if ts_raw else "—"
            # Compute age from timestamp to now
            try:
                from datetime import timezone as _tz
                ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if ts_raw else None
                age_h = round((datetime.now(_tz.utc) - ts_dt).total_seconds() / 3600) if ts_dt else None
                age_str = f"{age_h}h" if age_h is not None else "—"
            except Exception:
                age_str = "—"
            exc_data.append([
                truncate_name(a.get("device_name"), 24),
                a.get("device_ip", "—"),
                (a.get("event_type") or "—")[:14],
                since_str,
                age_str,
            ])
        exc_tbl = Table(exc_data, colWidths=["28%", "18%", "16%", "22%", "16%"])
        exc_ts = TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), hex_color("#78350F")),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [hex_color("#FFFBEB"), hex_color("#FEF3C7")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, hex_color("#D97706")),
            ("BOX",           (0, 0), (-1, -1), 1.5, hex_color("#D97706")),
        ])
        exc_tbl.setStyle(exc_ts)
        story.append(exc_tbl)
        story.append(SP_CAPTION)

    # ── Severity breakdown ─────────────────────────────────────────────────────
    if sev:
        story.append(SP_BLOCK)
        story.append(_subheading("Severity Breakdown", styles))
        sev_data: List[Any] = [["Severity", "Count"]]
        _SEV_ORDER = ["CRITICAL", "WARNING", "INFO", "LOW"]
        for s in _SEV_ORDER:
            if s in sev:
                sev_data.append([s, str(sev[s])])
        for s, v in sev.items():
            if s not in _SEV_ORDER:
                sev_data.append([s, str(v)])
        sev_tbl = Table(sev_data, colWidths=["50%", "50%"])
        sev_ts = base_table_style()
        for idx, s_key in enumerate(_SEV_ORDER, start=1):
            if idx < len(sev_data):
                color_map = {"CRITICAL": "#DC2626", "WARNING": "#D97706", "INFO": "#3B82F6", "LOW": "#6B7280"}
                c = color_map.get(s_key, "#94A3B8")
                sev_ts.add("TEXTCOLOR", (0, idx), (0, idx), hex_color(c))
                sev_ts.add("FONTNAME",  (0, idx), (0, idx), "Helvetica-Bold")
        sev_tbl.setStyle(sev_ts)
        story.append(sev_tbl)

    # ── Top alerted devices ────────────────────────────────────────────────────
    top_devices = (report_data.get("top_alerted_devices") or [])[:MAX_EXCEPTION_ROWS]
    if top_devices:
        story.append(SP_BLOCK)
        story.append(_subheading("Most Alerted Devices", styles))
        td_data: List[Any] = [["Device", "IP", "Alert Count"]]
        for d in top_devices:
            td_data.append([
                truncate_name(d.get("device_name"), 28),
                d.get("device_ip", "—"),
                str(d.get("alert_count", 0)),
            ])
        td_tbl = Table(td_data, colWidths=["45%", "30%", "25%"])
        td_ts = base_table_style()
        for idx in range(1, len(td_data)):
            td_ts.add("ALIGN", (2, idx), (2, idx), "CENTER")
            td_ts.add("FONTNAME", (2, idx), (2, idx), "Helvetica-Bold")
        td_tbl.setStyle(td_ts)
        story.append(td_tbl)

    # ── Unresolved aging ───────────────────────────────────────────────────────
    aging = report_data.get("unresolved_aging") or {}
    if aging and any(v > 0 for v in aging.values()):
        story.append(SP_BLOCK)
        story.append(_subheading("Unresolved Alert Aging", styles))
        ag_data: List[Any] = [["Age Bucket", "Count"]]
        for bucket, count in aging.items():
            ag_data.append([str(bucket), str(count)])
        ag_tbl = Table(ag_data, colWidths=["60%", "40%"])
        ag_ts = base_table_style()
        ag_tbl.setStyle(ag_ts)
        story.append(ag_tbl)

    # ── Export note ────────────────────────────────────────────────────────────
    if report_data.get("alerts_truncated"):
        story.append(SP_BLOCK)
        story.append(normal_paragraph(
            report_data.get("alerts_export_note") or "Full alert list available via CSV/XLSX export.",
            styles, size=6.5, color=TEXT_LIGHT,
        ))

    # ── Confidence footnotes ───────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(section_heading("Data Source", styles))
    story.append(normal_paragraph(
        "Alert data sourced from DashboardEvent table (dashboard_events). "
        "Timestamps in IST (Asia/Kolkata). TTA/TTR computed from acknowledged_at and resolved_at fields.",
        styles, color=TEXT_MID,
    ))

    doc.build(story, onFirstPage=_first_page, onLaterPages=footer)
    buf.seek(0)
    logger.info("[AlertPDF] PDF complete: %d bytes", len(buf.getvalue()))
    return buf


def generate_device_health_pdf(report_data: dict) -> io.BytesIO:
    """Structured Device Health Report PDF.

    Uses peaks_and_breaches ONLY — raw time_series is never passed to this function.
    Follows the enterprise section pipeline:
      Cover → KPI strip → Narrative → Capacity Runway table →
      Breach Summary table → Per-device avg/max table → Confidence footnote
    """
    period = report_data.get("period", {})
    gen_at = _fmt_ts(report_data.get("generated_at") or datetime.utcnow())
    start_str = _fmt_ts(period.get("start"))
    end_str   = _fmt_ts(period.get("end"))
    report_title = f"Device Health Report  |  {start_str} — {end_str}"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=28, rightMargin=28,
        topMargin=28,  bottomMargin=36,
        title=report_title,
        author="Device Monitoring Tactical",
    )
    styles = getSampleStyleSheet()
    _reg = PDFStyleRegistry(styles)
    footer = PageFooter(report_title, gen_at)

    def _first_page(canvas, doc):
        _draw_cover_bg(canvas, doc)
        footer(canvas, doc)

    # ── Cover ──────────────────────────────────────────────────────────────────
    story: List[Any] = [
        Spacer(1, 1.2 * inch),
        Paragraph("Device Health", _reg.cover_title),
        Paragraph("Report", _reg.cover_title),
        Spacer(1, 0.3 * inch),
        Paragraph("CPU, Memory &amp; Disk Capacity Risks — Threshold Breaches &amp; Runway Estimates", _reg.cover_subtitle),
        Spacer(1, 0.2 * inch),
        Paragraph(f"Period:  {start_str} — {end_str}", _reg.cover_meta),
        Paragraph(f"Generated:  {gen_at}", _reg.cover_meta),
        PageBreak(),
    ]

    # ── Data from report ───────────────────────────────────────────────────────
    peaks_and_breaches  = report_data.get("peaks_and_breaches") or {}
    fleet_correlation   = report_data.get("fleet_correlation") or {}
    data_note           = report_data.get("data_note")
    granularity         = report_data.get("granularity", "hourly")
    total_samples       = report_data.get("total_samples", 0)
    narrative           = report_data.get("narrative")

    # KPI strip values
    device_count    = len(peaks_and_breaches)
    capacity_at_risk = sum(
        1 for dev in peaks_and_breaches.values()
        if any(r.get("estimated_days_to_breach", 999) < 60 for r in dev.get("capacity_runway", []))
    )
    breach_count = sum(len(dev.get("breaches", [])) for dev in peaks_and_breaches.values())
    data_src_label = {
        "raw": "Raw Logs",
        "hourly": "Hourly Rollup",
        "daily": "Daily Rollup",
    }.get(granularity, granularity.capitalize())

    # ── Section opener via ReportSectionBuilder ────────────────────────────────
    from services.pdf_section_builder import ReportSectionBuilder
    _health_confidence = {"device_health": {
        "level": "HIGH" if granularity in ("hourly", "daily") else "MEDIUM",
        "source": data_src_label,
    }}
    story.extend(
        ReportSectionBuilder("Device Health Overview", styles)
        .no_page_break()
        .confidence_meta(_health_confidence, "device_health", period, device_count)
        .narrative(narrative)
        .build()
    )
    story.append(SP_BLOCK)
    story.append(kpi_strip([
        {"label": "Devices with Telemetry", "value": str(device_count),
         "color": "#94A3B8"},
        {"label": "Capacity at Risk (<60d)", "value": str(capacity_at_risk),
         "color": "#DC2626" if capacity_at_risk > 0 else "#16A34A"},
        {"label": "Breach Events", "value": str(breach_count),
         "color": "#EA580C" if breach_count > 0 else "#16A34A"},
        {"label": "Data Source", "value": data_src_label,
         "color": "#94A3B8"},
        {"label": "Total Samples", "value": str(total_samples),
         "color": "#94A3B8"},
    ]))
    story.append(SP_BLOCK)

    # ── No-data guard ──────────────────────────────────────────────────────────
    if data_note == "no_data" or not peaks_and_breaches:
        story.append(normal_paragraph(
            "No telemetry data was found for this period. "
            "Verify agent connectivity and that server health logging is active.",
            styles, color=TEXT_MID,
        ))
        doc.build(story, onFirstPage=_first_page, onLaterPages=footer)
        buf.seek(0)
        return buf

    # ── Sparse data note ───────────────────────────────────────────────────────
    if data_note == "sparse":
        story.append(normal_paragraph(
            "Note: telemetry data for this period is sparse. "
            "Runway estimates and breach analysis may not be representative.",
            styles, color="#D97706",
        ))
        story.append(SP_BLOCK)

    # ── Fleet Incident Correlation ────────────────────────────────────────────
    _fc_total = fleet_correlation.get("total_incidents", 0)
    if _fc_total > 0:
        story.append(section_heading("Fleet Incident Correlation", styles))
        story.append(SP_AFTER_TITLE)
        cpu_n   = fleet_correlation.get("cpu_spike_count", 0)
        mem_n   = fleet_correlation.get("mem_spike_count", 0)
        cpu_pct = round(cpu_n / _fc_total * 100) if _fc_total else 0
        mem_pct = round(mem_n / _fc_total * 100) if _fc_total else 0
        corr_headers = ["Metric", "Incidents with Spike", "Out of Total", "Coincidence %"]
        corr_data: List[Any] = [corr_headers]
        corr_data.append(["CPU > 80%",    str(cpu_n), str(_fc_total), f"{cpu_pct}%"])
        corr_data.append(["Memory > 85%", str(mem_n), str(_fc_total), f"{mem_pct}%"])
        corr_tbl = Table(corr_data, colWidths=["30%", "22%", "22%", "26%"])
        corr_ts = base_table_style()
        if cpu_pct > 50:
            corr_ts.add("TEXTCOLOR", (3, 1), (3, 1), hex_color("#DC2626"))
            corr_ts.add("FONTNAME",  (3, 1), (3, 1), "Helvetica-Bold")
        if mem_pct > 50:
            corr_ts.add("TEXTCOLOR", (3, 2), (3, 2), hex_color("#DC2626"))
            corr_ts.add("FONTNAME",  (3, 2), (3, 2), "Helvetica-Bold")
        corr_tbl.setStyle(corr_ts)
        story.append(corr_tbl)
        insight = fleet_correlation.get("insight")
        if insight:
            story.append(SP_CAPTION)
            story.append(normal_paragraph(insight, styles, size=8, color=TEXT_MID))
        story.append(SP_BLOCK)

    # ── Capacity Runway table ──────────────────────────────────────────────────
    runway_rows: List[dict] = []
    for dev in peaks_and_breaches.values():
        for r in dev.get("capacity_runway", []):
            if r.get("estimated_days_to_breach", 999) < 60:
                runway_rows.append({
                    "device_name": dev.get("device_name", "—"),
                    **r,
                })
    runway_rows.sort(key=lambda r: r.get("estimated_days_to_breach", 999))

    if runway_rows:
        story.append(section_heading("Capacity Runway — At-Risk Devices", styles))
        story.append(normal_paragraph(
            "Devices estimated to breach the warning threshold within 60 days (linear regression, R²≥0.3, growth≥0.1%/day).",
            styles, color=TEXT_MID,
        ))
        story.append(SP_BLOCK)
        rw_headers = ["Device", "Metric", "Current Avg", "Days to Breach", "Threshold", "Confidence"]
        rw_data: List[Any] = [rw_headers]
        for r in runway_rows[:10]:
            days = r.get("estimated_days_to_breach")
            urgency_color = "#DC2626" if (days or 999) < 14 else ("#D97706" if (days or 999) < 30 else "#16A34A")
            rw_data.append([
                truncate_name(r.get("device_name"), 28),
                r.get("metric", "—"),
                f"{r.get('current_avg', 0):.1f}%",
                str(days) + " days" if days is not None else "—",
                f"{r.get('threshold', 0):.0f}%",
                (r.get("confidence") or "—").capitalize(),
            ])
        rw_tbl = Table(rw_data, colWidths=["25%", "15%", "13%", "16%", "12%", "14%"])
        rw_ts = base_table_style()
        for idx in range(1, len(rw_data)):
            days_val = runway_rows[idx - 1].get("estimated_days_to_breach", 999)
            c = "#DC2626" if days_val < 14 else ("#D97706" if days_val < 30 else "#16A34A")
            rw_ts.add("TEXTCOLOR", (3, idx), (3, idx), hex_color(c))
            rw_ts.add("FONTNAME",  (3, idx), (3, idx), "Helvetica-Bold")
        rw_tbl.setStyle(rw_ts)
        story.append(rw_tbl)
        story.append(SP_BLOCK)
    else:
        story.append(normal_paragraph(
            "No capacity risks detected within 60 days. All monitored metrics are within safe growth thresholds.",
            styles, color="#16A34A",
        ))
        story.append(SP_BLOCK)

    # ── Breach Summary table ───────────────────────────────────────────────────
    breach_rows: List[dict] = []
    for dev in peaks_and_breaches.values():
        for b in dev.get("breaches", []):
            if b.get("sustained_hours", 0) >= 2:
                breach_rows.append({
                    "device_name": dev.get("device_name", "—"),
                    **b,
                })
    breach_rows.sort(key=lambda r: r.get("sustained_hours", 0), reverse=True)

    if breach_rows:
        story.append(section_heading("Sustained Threshold Breaches (≥2h)", styles))
        story.append(SP_BLOCK)
        br_headers = ["Device", "Metric", "Level", "Sustained (h)", "Breach Count", "First Breach"]
        br_data: List[Any] = [br_headers]
        for b in breach_rows[:10]:
            br_data.append([
                truncate_name(b.get("device_name"), 28),
                b.get("metric", "—"),
                (b.get("level") or "—").upper(),
                f"{b.get('sustained_hours', 0):.1f}h",
                str(b.get("breach_count", 0)),
                _fmt_ts_short(b.get("first_breach_at")),
            ])
        br_tbl = Table(br_data, colWidths=["25%", "15%", "10%", "13%", "13%", "24%"])
        br_ts = base_table_style()
        _LEVEL_COLORS = {"CRITICAL": "#DC2626", "WARNING": "#D97706"}
        for idx in range(1, len(br_data)):
            lvl = (breach_rows[idx - 1].get("level") or "").upper()
            c = _LEVEL_COLORS.get(lvl, TEXT_MID)
            br_ts.add("TEXTCOLOR", (2, idx), (2, idx), hex_color(c))
            br_ts.add("FONTNAME",  (2, idx), (2, idx), "Helvetica-Bold")
        br_tbl.setStyle(br_ts)
        story.append(br_tbl)
        story.append(SP_BLOCK)

    # ── Per-device avg/peak summary ────────────────────────────────────────────
    story.append(section_heading("Per-Device Peak Metrics", styles))
    story.append(SP_BLOCK)
    pk_headers = ["Device", "CPU Avg", "CPU Peak", "Mem Avg", "Mem Peak", "Disk Avg", "Disk Peak"]
    pk_data: List[Any] = [pk_headers]
    PEAK_THRESHOLD = 85.0

    for dev_id, dev in list(peaks_and_breaches.items())[:30]:
        peaks_map = {p["metric"]: p for p in dev.get("peaks", [])}
        row = [truncate_name(dev.get("device_name"), 26)]
        for m in ("CPU", "Memory", "Disk"):
            p = peaks_map.get(m, {})
            avg_v = p.get("avg_value")
            peak_v = p.get("peak_value")
            row.append(f"{avg_v:.0f}%" if avg_v is not None else "—")
            row.append(f"{peak_v:.0f}%" if peak_v is not None else "—")
        pk_data.append(row)

    pk_tbl = Table(pk_data, colWidths=["22%", "10%", "10%", "10%", "10%", "10%", "10%"])
    pk_ts = base_table_style()
    for idx in range(1, len(pk_data)):
        dev_id_key = list(peaks_and_breaches.keys())[idx - 1]
        dev = peaks_and_breaches.get(dev_id_key, {})
        peaks_map = {p["metric"]: p for p in dev.get("peaks", [])}
        for col_idx, metric in enumerate(("CPU", "Memory", "Disk")):
            p = peaks_map.get(metric, {})
            peak_v = p.get("peak_value")
            if peak_v is not None and peak_v >= PEAK_THRESHOLD:
                pk_ts.add("TEXTCOLOR", (2 * col_idx + 2, idx), (2 * col_idx + 2, idx), hex_color("#DC2626"))
                pk_ts.add("FONTNAME",  (2 * col_idx + 2, idx), (2 * col_idx + 2, idx), "Helvetica-Bold")
    pk_tbl.setStyle(pk_ts)
    story.append(pk_tbl)
    if len(peaks_and_breaches) > 30:
        story.append(SP_CAPTION)
        story.append(normal_paragraph(
            f"Showing 30 of {len(peaks_and_breaches)} devices. Export CSV for full dataset.",
            styles, size=6.5, color=TEXT_LIGHT,
        ))

    # ── Confidence footnote ────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(section_heading("Data Source", styles))
    story.append(normal_paragraph(
        f"Health metrics sourced from server_health_logs (TimescaleDB hypertable). "
        f"Granularity: {data_src_label}. "
        f"Capacity runway estimated via linear regression (R²≥0.3, growth≥0.1%/day). "
        f"Timestamps in IST (Asia/Kolkata). "
        f"Total samples in period: {total_samples}.",
        styles, color=TEXT_MID,
    ))

    doc.build(story, onFirstPage=_first_page, onLaterPages=footer)
    buf.seek(0)
    logger.info("[HealthPDF] PDF complete: %d bytes", len(buf.getvalue()))
    return buf


# ── Inspector PDF private helpers ────────────────────────────────────────────

def _confidence_label(coverage_pct: float) -> str:
    """HIGH / MEDIUM / LOW based on scan coverage %."""
    if coverage_pct >= 80:
        return "HIGH"
    if coverage_pct >= 40:
        return "MEDIUM"
    return "LOW"


def _build_availability_bar(scan_series: List[dict], content_w: float, n_bins: int = 60) -> list:
    """Single-row colored availability bar.  Green=online, red=offline, amber=no_response."""
    if not scan_series:
        return []
    _BAR = {'online': '#16A34A', 'offline': '#DC2626', 'no_response': '#D97706'}

    bin_lists: List[List[str]] = [[] for _ in range(n_bins)]
    total = len(scan_series)
    for i, s in enumerate(scan_series):
        idx = min(int(i * n_bins / total), n_bins - 1)
        bin_lists[idx].append(s.get('status', 'unknown'))

    def _dominant(sl: List[str]) -> str:
        if not sl:
            return 'unknown'
        if 'offline' in sl:
            return 'offline'
        if 'no_response' in sl:
            return 'no_response'
        return 'online'

    cell_w = content_w / n_bins
    data = [['' for _ in range(n_bins)]]
    ts = TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ])
    for j, sl in enumerate(bin_lists):
        d = _dominant(sl)
        ts.add('BACKGROUND', (j, 0), (j, 0), hex_color(_BAR.get(d, '#CBD5E0')))
    tbl = Table(data, colWidths=[cell_w] * n_bins, rowHeights=[14])
    tbl.setStyle(ts)
    return [tbl]


def _extract_incidents(scan_series: List[dict]) -> List[dict]:
    """Detect offline/no_response runs and return incident records."""
    incidents: List[dict] = []
    start: Optional[str] = None
    inc_type: Optional[str] = None
    for s in scan_series:
        status = s.get('status', 'unknown')
        is_down = status in ('offline', 'no_response')
        if is_down and start is None:
            start = s['ts']
            inc_type = status
        elif not is_down and start is not None:
            incidents.append({'start': start, 'end': s['ts'], 'type': inc_type})
            start = None
            inc_type = None
    if start is not None:
        incidents.append({'start': start, 'end': None, 'type': inc_type})
    return incidents


def _fmt_duration(start_iso: str, end_iso: Optional[str]) -> str:
    """Format an incident duration as '2h 35m' or '45m'."""
    from datetime import datetime as _dt
    try:
        s = _dt.fromisoformat(start_iso)
        e = _dt.fromisoformat(end_iso) if end_iso else _dt.utcnow()
        mins = max(0, int((e - s).total_seconds() / 60))
        if mins < 60:
            return f"{mins}m"
        return f"{mins // 60}h {mins % 60}m"
    except Exception:
        return "—"


def _build_downtime_timeline(incidents: List[dict], styles, content_w: float) -> list:
    """Incident table: Start | End | Duration | Type.  Returns story elements."""
    if not incidents:
        return []

    _cs = ParagraphStyle('tlC', fontName='Helvetica', fontSize=7.5, leading=10, wordWrap='CJK')
    _hd = ParagraphStyle('tlH', fontName='Helvetica-Bold', fontSize=8, leading=10,
                         wordWrap='CJK', textColor=colors.white)

    rows = [[
        Paragraph("Start (IST)", _hd), Paragraph("End (IST)", _hd),
        Paragraph("Duration", _hd), Paragraph("Type", _hd),
    ]]
    for inc in incidents[:20]:
        end_label = _fmt_ts_short(inc['end']) if inc['end'] else "Ongoing"
        rows.append([
            Paragraph(_fmt_ts_short(inc['start']), _cs),
            Paragraph(end_label, _cs),
            Paragraph(_fmt_duration(inc['start'], inc['end']), _cs),
            Paragraph(inc['type'].replace('_', ' ').title(), _cs),
        ])

    widths = [content_w * 0.30, content_w * 0.30, content_w * 0.20, content_w * 0.20]
    ts_tl = base_table_style()
    for i, inc in enumerate(incidents[:20], start=1):
        bg = '#FEE2E2' if inc['type'] == 'offline' else '#FEF9C3'
        ts_tl.add('BACKGROUND', (0, i), (-1, i), hex_color(bg))
        ts_tl.add('TEXTCOLOR',  (0, i), (-1, i), hex_color(
            '#991B1B' if inc['type'] == 'offline' else '#92400E'))

    return [
        Paragraph('<b>Downtime Timeline</b>',
            ParagraphStyle('dts', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=10, spaceAfter=5,
                           textColor=hex_color(NAVY))),
        Table(rows, colWidths=widths, hAlign='LEFT', style=ts_tl, repeatRows=1),
        Spacer(1, 0.4 * cm),
    ]


def _build_notable_events(scan_series: List[dict], styles, content_w: float) -> list:
    """Table of status transitions + high-latency / high-loss events."""
    events: List[dict] = []
    prev: Optional[str] = None
    for s in scan_series:
        status = s.get('status', 'unknown')
        ping = s.get('ping_ms')
        loss = s.get('pkt_loss')
        notable = (
            (prev is not None and status != prev)
            or (ping is not None and ping > 100)
            or (loss is not None and loss > 10)
        )
        if notable:
            events.append(s)
        prev = status

    events = events[:15]
    if not events:
        return []

    _cs = ParagraphStyle('evC', fontName='Helvetica', fontSize=7.5, leading=10, wordWrap='CJK')
    _hd = ParagraphStyle('evH', fontName='Helvetica-Bold', fontSize=8, leading=10,
                         wordWrap='CJK', textColor=colors.white)

    rows = [[
        Paragraph("Time (IST)", _hd), Paragraph("Status", _hd),
        Paragraph("Latency", _hd), Paragraph("Pkt Loss", _hd),
    ]]
    for ev in events:
        rows.append([
            Paragraph(_fmt_ts_short(ev['ts']), _cs),
            Paragraph((ev.get('status') or 'unknown').replace('_', ' ').title(), _cs),
            Paragraph(_fmt_num(ev.get('ping_ms'), ' ms'), _cs),
            Paragraph(_fmt_num(ev.get('pkt_loss'), '%'), _cs),
        ])

    widths = [content_w * 0.35, content_w * 0.22, content_w * 0.22, content_w * 0.21]
    return [
        Paragraph('<b>Notable Events</b>',
            ParagraphStyle('nevs', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=10, spaceAfter=5,
                           textColor=hex_color(NAVY))),
        Table(rows, colWidths=widths, hAlign='LEFT', style=base_table_style(), repeatRows=1),
        Spacer(1, 0.4 * cm),
    ]


def generate_device_inspector_pdf(
    stats: dict,
    device_name: str,
    device_ip: str,
    period_label: str,
    period_hours: int = 24,
) -> io.BytesIO:
    """Single-device performance report PDF (Portrait A4)."""
    buf = io.BytesIO()
    # A4 portrait: 21cm wide, 1.5cm margins → 18cm content width
    _L_MARGIN = 1.5 * cm
    _R_MARGIN = 1.5 * cm
    _CONTENT_W = 21 * cm - _L_MARGIN - _R_MARGIN   # 18 cm

    # Per-scan series for timeline / bar — may be empty
    scan_series: List[dict] = stats.get('scan_series') or []

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=_L_MARGIN, rightMargin=_R_MARGIN,
        topMargin=2.0*cm, bottomMargin=1.8*cm,
    )
    styles = getSampleStyleSheet()
    gen_at = datetime.utcnow().strftime("%d-%m-%Y %H:%M UTC")
    footer = PageFooter(f"Device Inspector — {device_ip}", gen_at)
    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph(
        f'<font color="{TEAL}"><b>{device_name}</b></font>',
        ParagraphStyle('h1', parent=styles['Normal'], fontSize=20, spaceAfter=4,
                       fontName='Helvetica-Bold'),
    ))
    story.append(Paragraph(
        f'<font color="{TEXT_MID}">{device_ip} &#xB7; {period_label}</font>',
        ParagraphStyle('sub', parent=styles['Normal'], fontSize=10, spaceAfter=4),
    ))
    story.append(Paragraph(
        f'<font color="{TEXT_LIGHT}">Generated: {gen_at}</font>',
        ParagraphStyle('gen', parent=styles['Normal'], fontSize=8, spaceAfter=10),
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=hex_color(TEAL), spaceAfter=6))

    # Cell style with word-wrap for data values (prevents overflow on long strings)
    _cs = ParagraphStyle('inspCell', fontName='Helvetica', fontSize=7.5,
                         leading=10, wordWrap='CJK')
    def _cell(val):
        return Paragraph(str(val if val not in (None, '') else '—'), _cs)

    # Header cell style — white bold text, word-wraps in narrow columns
    _hdr = ParagraphStyle('inspHdr', fontName='Helvetica-Bold', fontSize=8,
                          leading=10, wordWrap='CJK', textColor=colors.white)
    def _h(val):
        return Paragraph(str(val), _hdr)

    # ── Availability KPI table — explicit column widths to fill page ──────────
    uptime = stats.get('uptime_percentage', 0.0) or 0.0
    downtime_h = stats.get('downtime_hours')
    if downtime_h is None:
        downtime_h = round((100.0 - uptime) / 100.0 * period_hours, 2)
    total_scans    = stats.get('total_scans', 0)
    expected_scans = int(period_hours * 12)   # 5-min interval = 12/hr
    coverage_pct   = round(total_scans / expected_scans * 100, 1) if expected_scans else 0

    # ── Data Confidence Banner ────────────────────────────────────────────────
    _conf_level = _confidence_label(coverage_pct)
    _conf_txt_clr = _CONFIDENCE_COLORS.get(_conf_level, _CONFIDENCE_COLORS["NO_DATA"])
    _conf_bg_clr  = _CONFIDENCE_BG.get(_conf_level, _CONFIDENCE_BG["NO_DATA"])
    _cpar = ParagraphStyle('_cpar', fontName='Helvetica', fontSize=8.5, leading=12)
    _ctxt = (
        f'<font color="{_conf_txt_clr}"><b>Data Confidence: {_conf_level}</b></font>'
        f' &nbsp;·&nbsp; {total_scans:,} of {expected_scans:,} expected scans'
        f' ({coverage_pct}%)'
    )
    story.append(Table(
        [[Paragraph(_ctxt, _cpar)]],
        colWidths=[_CONTENT_W], hAlign='LEFT',
        style=TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), hex_color(_conf_bg_clr)),
            ('TOPPADDING',    (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ('LEFTPADDING',   (0, 0), (-1, -1), 10),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
            ('BOX',           (0, 0), (-1, -1), 0.5, hex_color(BORDER)),
        ]),
    ))
    story.append(Spacer(1, 0.25 * cm))

    scan_display   = f"{total_scans:,} ({coverage_pct}% of {expected_scans:,} expected)"
    tier = (
        "Gold"    if uptime >= SLA_GOLD    else
        "Silver"  if uptime >= SLA_SILVER  else
        "Bronze"  if uptime >= SLA_BRONZE  else
        "Warning" if uptime >= SLA_WARNING else "Critical"
    )
    tc, tbg = _sla_badge_style(tier)
    uptime_h = round(uptime / 100.0 * period_hours, 2)
    avail_data = [
        [
            _h("Total Scans"), _h("Online"), _h("Offline"), _h("No Response"),
            _h("Uptime %"), _h("Uptime (hrs)"), _h("Downtime %"), _h("Downtime (hrs)"),
        ],
        [
            _cell(scan_display),
            _cell(stats.get('online_count', 0)),
            _cell(stats.get('offline_count', 0)),
            _cell(stats.get('no_response_count', 0)),
            _cell(_fmt_uptime(uptime)),
            _cell(f"{uptime_h:.2f} hrs"),
            _cell(f"{(100.0 - uptime):.2f}%"),
            _cell(f"{downtime_h:.2f} hrs"),
        ],
    ]
    # 8 equal columns totalling _CONTENT_W
    _col6 = [_CONTENT_W / 8] * 8
    ts_avail = base_table_style()
    ts_avail.add('VALIGN',     (0, 0), (-1, 0), 'TOP')
    ts_avail.add('BACKGROUND', (4, 1), (4, 1), hex_color(tbg))
    ts_avail.add('TEXTCOLOR',  (4, 1), (4, 1), hex_color(tc))
    ts_avail.add('FONTNAME',   (4, 1), (4, 1), 'Helvetica-Bold')
    story.append(Paragraph('<b>Availability</b>',
        ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                       fontSize=11, spaceBefore=10, spaceAfter=5,
                       textColor=hex_color(NAVY))))
    story.append(Table(avail_data, colWidths=_col6, hAlign='LEFT',
                       style=ts_avail, repeatRows=1))
    story.append(Spacer(1, 0.3 * cm))

    # ── Availability Bar ──────────────────────────────────────────────────────
    if scan_series:
        story.append(Paragraph(
            f'<font color="{TEXT_LIGHT}" size="7"><i>'
            f'Availability over period — '
            f'<font color="#16A34A">■</font> Online  '
            f'<font color="#DC2626">■</font> Offline  '
            f'<font color="#D97706">■</font> No Response'
            f'</i></font>',
            ParagraphStyle('bar_leg', parent=styles['Normal'], fontSize=7,
                           spaceBefore=0, spaceAfter=3),
        ))
        story.extend(_build_availability_bar(scan_series, _CONTENT_W))
    story.append(Spacer(1, 0.5 * cm))

    # ── Latency & Packet Loss ──────────────────────────────────────────────────
    if stats.get('avg_latency') is not None:
        # 5 columns, equal widths
        _col5 = [_CONTENT_W / 5] * 5
        lat_data = [
            [_h("Avg Latency"), _h("Min Latency"), _h("Max Latency"), _h("Std Dev"), _h("Avg Pkt Loss")],
            [
                _cell(_fmt_num(stats.get('avg_latency'),     ' ms')),
                _cell(_fmt_num(stats.get('min_latency'),     ' ms')),
                _cell(_fmt_num(stats.get('max_latency'),     ' ms')),
                _cell(_fmt_num(stats.get('latency_std_dev'), ' ms')),
                _cell(_fmt_num(stats.get('avg_packet_loss'), '%')),
            ],
        ]
        story.append(Paragraph('<b>Latency &amp; Packet Loss</b>',
            ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=8, spaceAfter=5,
                           textColor=hex_color(NAVY))))
        story.append(Table(lat_data, colWidths=_col5, hAlign='LEFT',
                           style=base_table_style(), repeatRows=1))
        story.append(Spacer(1, 0.5*cm))

    # ── Agent Telemetry ────────────────────────────────────────────────────────
    agent = stats.get('agent_data', {})
    if agent.get('available') and agent.get('latest'):
        l = agent['latest']
        # Proportional widths: give Net In/Out more room than pct columns
        _ag_widths = [
            _CONTENT_W * 0.14,  # CPU %
            _CONTENT_W * 0.14,  # Memory %
            _CONTENT_W * 0.14,  # Disk %
            _CONTENT_W * 0.20,  # Net In
            _CONTENT_W * 0.20,  # Net Out
            _CONTENT_W * 0.18,  # Uptime
        ]
        ag_data = [
            [_h("CPU %"), _h("Memory %"), _h("Disk %"), _h("Net In"), _h("Net Out"), _h("Uptime")],
            [
                _cell(_fmt_num(l.get('cpu_percent'),    '%')),
                _cell(_fmt_num(l.get('memory_percent'), '%')),
                _cell(_fmt_num(l.get('disk_percent'),   '%')),
                _cell(_fmt_bps(l.get('network_in_bps'))),
                _cell(_fmt_bps(l.get('network_out_bps'))),
                _cell(_fmt_hours(float(l.get('uptime_seconds') or 0) / 3600)),
            ],
        ]
        ts_ag = base_table_style()
        ts_ag.add('VALIGN', (0, 0), (-1, 0), 'TOP')
        story.append(Paragraph('<b>Agent Telemetry (Latest Sample)</b>',
            ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=8, spaceAfter=5,
                           textColor=hex_color(NAVY))))
        story.append(Table(ag_data, colWidths=_ag_widths, hAlign='LEFT',
                           style=ts_ag, repeatRows=1))
        story.append(Spacer(1, 0.5*cm))
    else:
        # Explicit message when agent data is absent — never silently omit the section
        gap_reason = (stats.get("_data_gaps") or {}).get("telemetry", "no_agent_data")
        _gap_msgs = {
            "device_type_unsupported_for_agent": (
                "Agent telemetry: not applicable for this device type."
            ),
            "no_agent_data": (
                "Agent telemetry: not available — "
                "the server agent may not be installed or has not reported recently."
            ),
        }
        gap_msg = _gap_msgs.get(gap_reason, "Agent telemetry: not available.")
        story.append(Paragraph('<b>Agent Telemetry</b>',
            ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=8, spaceAfter=4,
                           textColor=hex_color(NAVY))))
        story.append(Paragraph(
            f'<i><font color="{TEXT_LIGHT}">{gap_msg}</font></i>',
            ParagraphStyle('agent_na', parent=styles['Normal'],
                           fontName='Helvetica-Oblique', fontSize=8, spaceBefore=2),
        ))
        story.append(Spacer(1, 0.5*cm))

    # ── Health Diagnosis (rule-based narrative) ───────────────────────────────
    try:
        from services.report_narrative_service import ReportNarrativeService as _NarrSvc
        _narr = _NarrSvc()._narrate_device_inspector({
            "device_name": device_name,
            "device_ip": device_ip,
            "device_type": stats.get("device_type", "unknown"),
            "uptime_pct": uptime,
            "avg_latency_ms": stats.get("avg_latency"),
            "avg_packet_loss_pct": stats.get("avg_packet_loss"),
        })
        # Optional Gemini prose enhancement
        import os as _os
        if _os.environ.get("GEMINI_API_KEY"):
            try:
                from services.gemini_pdf_narrative import enhance_pdf_narratives as _enh
                _enhanced = _enh({"device_inspector": _narr}, stats)
                _narr = _enhanced.get("device_inspector", _narr)
            except Exception as _ge:
                logger.warning("[InspectorPDF] Gemini enhancement skipped: %s", _ge)

        _findings = _narr.get("top_findings") or []
        _interp   = _narr.get("interpretation") or ""
        _actions  = _narr.get("action_items") or []

        _SEV_TXT = {"critical": "#991B1B", "warning": "#92400E", "ok": "#166534"}
        _SEV_BG  = {"critical": "#FEE2E2", "warning": "#FEF3C7", "ok": "#DCFCE7"}
        _fn_par  = ParagraphStyle('_fn', fontName='Helvetica', fontSize=8.5, leading=12)

        story.append(Paragraph('<b>Health Diagnosis</b>',
            ParagraphStyle('hd_sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=10, spaceAfter=5,
                           textColor=hex_color(NAVY))))
        for _f in _findings:
            _sev = _f.get("severity", "ok")
            _msg = _f.get("message", "")
            _tc  = _SEV_TXT.get(_sev, "#374151")
            _bg  = _SEV_BG.get(_sev, "#F3F4F6")
            story.append(Table(
                [[Paragraph(
                    f'<font color="{_tc}"><b>{_sev.upper()}</b></font>'
                    f' &nbsp;—&nbsp; {_msg}', _fn_par,
                )]],
                colWidths=[_CONTENT_W], hAlign='LEFT',
                style=TableStyle([
                    ('BACKGROUND',    (0, 0), (-1, -1), hex_color(_bg)),
                    ('TOPPADDING',    (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('LEFTPADDING',   (0, 0), (-1, -1), 8),
                    ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
                    ('BOX',           (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
                ]),
            ))
            story.append(Spacer(1, 2))
        if _interp:
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                f'<i>{_interp}</i>',
                ParagraphStyle('_interp', parent=styles['Normal'],
                               fontName='Helvetica-Oblique', fontSize=8.5, leading=13,
                               spaceBefore=4, textColor=hex_color(TEXT_MID)),
            ))
        if _actions and _actions != ["No immediate action required"]:
            story.append(Spacer(1, 4))
            story.append(Paragraph('<b>Recommended Actions</b>',
                ParagraphStyle('_act_h', parent=styles['Normal'], fontName='Helvetica-Bold',
                               fontSize=9, spaceBefore=4, spaceAfter=3,
                               textColor=hex_color(NAVY_MID))))
            for _act in _actions:
                story.append(Paragraph(
                    f'• {_act}',
                    ParagraphStyle('_act', parent=styles['Normal'],
                                   fontName='Helvetica', fontSize=8.5, leading=13,
                                   leftIndent=10),
                ))
        story.append(Spacer(1, 0.4 * cm))
    except Exception as _narr_err:
        logger.warning("[InspectorPDF] Health diagnosis skipped: %s", _narr_err)

    # ── Downtime Timeline ─────────────────────────────────────────────────────
    if scan_series:
        _incidents = _extract_incidents(scan_series)
        story.extend(_build_downtime_timeline(_incidents, styles, _CONTENT_W))
        story.extend(_build_notable_events(scan_series, styles, _CONTENT_W))

    # ── SLA tier summary row ───────────────────────────────────────────────────
    tier_ts = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), hex_color(tbg)),
        ('TEXTCOLOR',  (0, 0), (-1, 0), hex_color(tc)),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, 0), 9),
        ('ALIGN',      (0, 0), (-1, 0), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID',       (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
    ])
    tier_data = [[f"SLA Tier: {tier}", f"Uptime: {_fmt_uptime(uptime)}  |  Downtime: {downtime_h:.2f} hrs", f"Period: {period_label}"]]
    story.append(Table(tier_data,
                       colWidths=[_CONTENT_W * 0.35, _CONTENT_W * 0.30, _CONTENT_W * 0.35],
                       hAlign='LEFT', style=tier_ts))
    story.append(Spacer(1, 0.4*cm))

    # ── Footer note ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=hex_color(BORDER), spaceBefore=4))
    story.append(Paragraph(
        f'<i><font color="{TEXT_LIGHT}">'
        f'ICMP scan interval: 5 min &nbsp;·&nbsp; Agent telemetry requires on-device agent'
        f' &nbsp;·&nbsp; All times in IST (Asia/Kolkata)'
        f'</font></i>',
        ParagraphStyle('note', parent=styles['Normal'], fontSize=7, spaceBefore=6),
    ))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    logger.info("[DeviceInspectorPDF] Generated for %s (%s): %d bytes",
                device_ip, period_label, len(buf.getvalue()))
    return buf
