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
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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


def normal_paragraph(text: str, styles, size: int = 8, color: str = TEXT_DARK):
    style = ParagraphStyle(
        "NormalCustom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=size,
        textColor=hex_color(color),
    )
    return Paragraph(text, style)


# ── Cover page ────────────────────────────────────────────────────────────────

def _build_cover(report: dict, styles, fleet: str = "all") -> list:
    period = report.get("period", {})
    gen_at = report.get("generated_at", "")[:16].replace("T", " ")

    def _start_str():
        try:
            return datetime.fromisoformat(period["start"]).strftime("%d %b %Y")
        except Exception:
            return period.get("start", "")[:10]

    def _end_str():
        try:
            return datetime.fromisoformat(period["end"]).strftime("%d %b %Y")
        except Exception:
            return period.get("end", "")[:10]

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

    title_style = ParagraphStyle(
        "CoverTitle",
        fontName="Helvetica-Bold",
        fontSize=26,
        textColor=hex_color(WHITE),
        leading=32,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        fontName="Helvetica",
        fontSize=13,
        textColor=hex_color(TEAL),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "CoverMeta",
        fontName="Helvetica",
        fontSize=9,
        textColor=hex_color(BG_ALT),
        spaceAfter=3,
    )

    # Return cover flowables directly — wrapping in a Table prevents PageBreak from working.
    # The navy background is painted by the _draw_cover_bg canvas callback in generate_enterprise_pdf.
    return [
        Spacer(1, 1.2 * inch),
        Paragraph(title_line1, title_style),
        Paragraph(title_line2, title_style),
        Spacer(1, 0.3 * inch),
        Paragraph(subtitle_text, subtitle_style),
        Spacer(1, 0.6 * inch),
        HRFlowable(width="100%", thickness=1, color=hex_color(TEAL), spaceAfter=16),
        Paragraph(f"Report Period:  {_start_str()}  →  {_end_str()}", meta_style),
        Paragraph(f"Days Covered:   {period.get('days', '—')}", meta_style),
        Paragraph(f"Generated:      {gen_at} UTC", meta_style),
        Spacer(1, 0.4 * inch),
        Paragraph("CONFIDENTIAL — Internal Use Only", ParagraphStyle(
            "CoverConfidential",
            fontName="Helvetica-Oblique",
            fontSize=8,
            textColor=hex_color(TEXT_LIGHT),
        )),
        PageBreak(),
    ]


# ── Executive summary page ────────────────────────────────────────────────────

def _build_executive_summary(report: dict, styles) -> list:
    summary = report.get("summary", {})
    period = report.get("period", {})
    fleet_avg = summary.get("fleet_avg_uptime")
    sla_dist = summary.get("sla_distribution", {})

    # ── Header ────────────────────────────────────────────────────────────────
    elems = [
        section_heading("Executive Summary", styles),
        HRFlowable(width="100%", thickness=1, color=hex_color(BORDER), spaceAfter=8),
    ]

    # ── Fleet KPI row ─────────────────────────────────────────────────────────
    fleet_uptime_str = f"{fleet_avg:.3f}%" if fleet_avg is not None else "—"
    kpi_data = [
        ["Total Devices", "With Data", "Server Fleet", "Employee Fleet", "Fleet Avg Uptime"],
        [
            str(summary.get("total_devices", 0)),
            str(summary.get("devices_with_data", 0)),
            str(summary.get("server_devices", 0)),
            str(summary.get("tracked_devices", 0)),
            fleet_uptime_str,
        ],
    ]
    kpi_table = Table(kpi_data, colWidths=["20%"] * 5)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), hex_color(NAVY_MID)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 14),
        ("TEXTCOLOR",     (0, 1), (-1, 1), hex_color(NAVY)),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.4, hex_color(BORDER)),
        ("ROWBACKGROUNDS", (0, 1), (-1, 1), [hex_color(BG_LIGHT)]),
    ]))
    elems += [kpi_table, Spacer(1, 12)]

    # ── SLA distribution table ────────────────────────────────────────────────
    elems.append(_subheading("SLA Tier Distribution", styles))
    sla_header = ["SLA Tier", "Threshold", "Devices", "% of Fleet"]
    total_devices = max(summary.get("total_devices", 1), 1)
    sla_thresholds = {
        "Gold": "≥ 99.9%", "Silver": "≥ 99.5%", "Bronze": "≥ 99.0%",
        "Warning": "≥ 95.0%", "Critical": "< 95.0%", "Unknown": "No Data",
    }
    sla_rows = [sla_header]
    for tier in ("Gold", "Silver", "Bronze", "Warning", "Critical", "Unknown"):
        count = sla_dist.get(tier, 0)
        pct = f"{count / total_devices * 100:.1f}%" if count else "—"
        sla_rows.append([tier, sla_thresholds[tier], str(count), pct])

    sla_table = Table(sla_rows, colWidths=["25%", "25%", "25%", "25%"])
    sla_ts = base_table_style()
    for i, tier in enumerate(("Gold", "Silver", "Bronze", "Warning", "Critical", "Unknown"), start=1):
        text_c, bg_c = _sla_badge_style(tier)
        sla_ts.add("BACKGROUND", (0, i), (0, i), hex_color(bg_c))
        sla_ts.add("TEXTCOLOR",  (0, i), (0, i), hex_color(text_c))
        sla_ts.add("FONTNAME",   (0, i), (0, i), "Helvetica-Bold")
    sla_table.setStyle(sla_ts)
    elems += [sla_table, Spacer(1, 12)]

    # ── Worst 5 devices ───────────────────────────────────────────────────────
    worst = summary.get("worst_devices") or []
    if worst:
        elems.append(_subheading("Top 5 Devices by Downtime", styles))
        w_header = ["Device", "IP", "Type / Role", "Uptime %", "Downtime Hours", "SLA Tier"]
        w_rows = [w_header]
        for r in worst:
            tier = r.get("sla_tier", "Unknown")
            w_rows.append([
                r.get("device_name", "—")[:30],
                r.get("device_ip", "—"),
                r.get("device_type", r.get("employee_name", "Employee"))[:20],
                _fmt_uptime(r.get("uptime_pct")),
                _fmt_hours(r.get("downtime_hours")),
                tier,
            ])
        w_table = Table(w_rows, colWidths=["25%", "13%", "16%", "12%", "16%", "18%"])
        w_ts = base_table_style()
        for i, r in enumerate(worst, start=1):
            tier = r.get("sla_tier", "Unknown")
            text_c, bg_c = _sla_badge_style(tier)
            w_ts.add("BACKGROUND", (5, i), (5, i), hex_color(bg_c))
            w_ts.add("TEXTCOLOR",  (5, i), (5, i), hex_color(text_c))
            w_ts.add("FONTNAME",   (5, i), (5, i), "Helvetica-Bold")
        w_table.setStyle(w_ts)
        elems += [w_table, Spacer(1, 12)]

    elems.append(PageBreak())
    return elems


# ── Server fleet section ──────────────────────────────────────────────────────

def _build_server_fleet(report: dict, styles) -> list:
    rows = report.get("server_rows", [])
    elems = [
        section_heading(f"Server & Network Fleet  ({len(rows)} devices)", styles),
        normal_paragraph(
            "Inventory devices managed via SNMP / ICMP scanning and server_agent telemetry. "
            "Uptime from DailyDeviceStats rollups or raw scan history. "
            "Latency & packet-loss from DailyDeviceStats. CPU/Mem/Disk from ServerHealthLog.",
            styles, color=TEXT_MID,
        ),
        HRFlowable(width="100%", thickness=0.5, color=hex_color(BORDER), spaceAfter=6),
        Spacer(1, 4),
    ]

    if not rows:
        elems.append(normal_paragraph("No inventory devices found for this period.", styles))
        elems.append(PageBreak())
        return elems

    # 13 columns — fits landscape A4 (773pt wide after 28pt margins each side)
    headers = [
        "Device Name", "IP Address", "Type",
        "Uptime %", "Downtime",
        "Avg CPU", "Avg Mem", "Avg Disk",
        "Latency", "Pkt Loss", "Load 1m",
        "Samples", "SLA Tier",
    ]
    col_w = ["16%", "10%", "8%", "8%", "8%", "6%", "6%", "6%", "7%", "6%", "6%", "6%", "7%"]

    table_data = [headers]
    for r in rows:
        tier = r.get("sla_tier", "Unknown")
        table_data.append([
            (r.get("device_name") or "—")[:24],
            r.get("device_ip", "—"),
            (r.get("device_type") or "—")[:12],
            _fmt_uptime(r.get("uptime_pct")),
            _fmt_hours(r.get("downtime_hours")),
            _fmt_num(r.get("avg_cpu"), "%"),
            _fmt_num(r.get("avg_mem"), "%"),
            _fmt_num(r.get("avg_disk"), "%"),
            _fmt_num(r.get("avg_latency_ms"), " ms"),
            _fmt_num(r.get("avg_packet_loss_pct"), "%"),
            _fmt_num(r.get("avg_load_1m"), ""),
            str(r.get("sample_count") or "—"),
            tier,
        ])

    table = Table(table_data, colWidths=col_w, repeatRows=1)
    ts = base_table_style()

    # Colour uptime col (3) and SLA col (12)
    for i, r in enumerate(rows, start=1):
        tier = r.get("sla_tier", "Unknown")
        text_c, bg_c = _sla_badge_style(tier)
        ts.add("BACKGROUND", (12, i), (12, i), hex_color(bg_c))
        ts.add("TEXTCOLOR",  (12, i), (12, i), hex_color(text_c))
        ts.add("FONTNAME",   (12, i), (12, i), "Helvetica-Bold")
        ts.add("TEXTCOLOR",  (3, i), (3, i), hex_color(text_c))
        ts.add("FONTNAME",   (3, i), (3, i), "Helvetica-Bold")

    table.setStyle(ts)
    elems.append(table)
    elems.append(PageBreak())
    return elems


# ── Employee / tracking fleet section ────────────────────────────────────────

def _build_tracked_fleet(report: dict, styles) -> list:
    rows = report.get("tracked_rows", [])
    elems = [
        section_heading(f"Employee Device Fleet  ({len(rows)} devices)", styles),
        normal_paragraph(
            "Employee / workstation devices managed via the service.py tracking agent. "
            "Uptime from tracked_device_availability_events stream. "
            "Status reflects current availability. MTTR = mean time to recover (minutes).",
            styles, color=TEXT_MID,
        ),
        HRFlowable(width="100%", thickness=0.5, color=hex_color(BORDER), spaceAfter=6),
        Spacer(1, 4),
    ]

    if not rows:
        elems.append(normal_paragraph("No tracked devices found for this period.", styles))
        return elems

    # 12 columns
    headers = [
        "Device Name", "Employee", "Department",
        "IP Address", "Status",
        "Uptime %", "Downtime",
        "Incidents", "MTTR", "MTBF",
        "Last Seen", "SLA Tier",
    ]
    col_w = ["14%", "11%", "10%", "9%", "7%", "8%", "7%", "6%", "6%", "6%", "9%", "7%"]

    table_data = [headers]
    for r in rows:
        tier = r.get("sla_tier", "Unknown")
        last_seen = "—"
        if r.get("last_seen"):
            try:
                last_seen = datetime.fromisoformat(r["last_seen"]).strftime("%d %b %y %H:%M")
            except Exception:
                last_seen = r["last_seen"][:16]
        mttr = r.get("mttr_min")
        mtbf = r.get("mtbf_hours")
        status_raw = (r.get("availability_status") or "unknown").lower()
        table_data.append([
            (r.get("device_name") or "—")[:20],
            (r.get("employee_name") or "—")[:16],
            (r.get("department") or "—")[:14],
            r.get("device_ip", "—"),
            status_raw.capitalize(),
            _fmt_uptime(r.get("uptime_pct")),
            _fmt_hours(r.get("downtime_hours")),
            str(r.get("incident_count") or "0"),
            f"{mttr:.0f} min" if mttr is not None else "—",
            f"{mtbf:.1f} h" if mtbf is not None else "—",
            last_seen,
            tier,
        ])

    table = Table(table_data, colWidths=col_w, repeatRows=1)
    ts = base_table_style()

    for i, r in enumerate(rows, start=1):
        tier = r.get("sla_tier", "Unknown")
        status_raw = (r.get("availability_status") or "unknown").lower()
        sla_text_c, sla_bg_c = _sla_badge_style(tier)
        st_text_c, st_bg_c = _status_style(status_raw)
        # SLA col (11)
        ts.add("BACKGROUND", (11, i), (11, i), hex_color(sla_bg_c))
        ts.add("TEXTCOLOR",  (11, i), (11, i), hex_color(sla_text_c))
        ts.add("FONTNAME",   (11, i), (11, i), "Helvetica-Bold")
        # Uptime col (5)
        ts.add("TEXTCOLOR",  (5, i), (5, i), hex_color(sla_text_c))
        ts.add("FONTNAME",   (5, i), (5, i), "Helvetica-Bold")
        # Status col (4)
        ts.add("BACKGROUND", (4, i), (4, i), hex_color(st_bg_c))
        ts.add("TEXTCOLOR",  (4, i), (4, i), hex_color(st_text_c))
        ts.add("FONTNAME",   (4, i), (4, i), "Helvetica-Bold")

    table.setStyle(ts)
    elems.append(table)
    return elems


# ── Page-number footer callback ───────────────────────────────────────────────

class PageFooter:
    def __init__(self, report_title: str, gen_at: str):
        self.title = report_title
        self.gen_at = gen_at

    def __call__(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(hex_color(TEXT_LIGHT))
        w, h = doc.pagesize
        canvas.drawString(doc.leftMargin, doc.bottomMargin - 10, self.title)
        canvas.drawRightString(
            w - doc.rightMargin, doc.bottomMargin - 10,
            f"Generated {self.gen_at} UTC   |   Page {doc.page}",
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
    gen_at = report.get("generated_at", "")[:16].replace("T", " ")
    try:
        start_str = datetime.fromisoformat(period["start"]).strftime("%d %b %Y")
        end_str = datetime.fromisoformat(period["end"]).strftime("%d %b %Y")
    except Exception:
        start_str = period.get("start", "")[:10]
        end_str = period.get("end", "")[:10]

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
    footer = PageFooter(report_title, gen_at)

    def _first_page(canvas, doc):
        _draw_cover_bg(canvas, doc)
        footer(canvas, doc)

    story = []
    story += _build_cover(report, styles, fleet=fleet)
    story += _build_executive_summary(report, styles)
    if fleet in ("all", "server"):
        story += _build_server_fleet(report, styles)
    if fleet in ("all", "workstation"):
        story += _build_tracked_fleet(report, styles)

    doc.build(story, onFirstPage=_first_page, onLaterPages=footer)
    buf.seek(0)
    logger.info("[EnterprisePDF] PDF complete: %d bytes", len(buf.getvalue()))
    return buf
