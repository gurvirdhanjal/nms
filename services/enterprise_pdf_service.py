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


# ── Narrative section builder (Master Spec progressive disclosure) ────────────

def _build_narrative_section(narrative: Optional[dict], styles) -> list:
    """Build PDF elements for a narrative block.
    Returns list of ReportLab flowables (empty list if no narrative)."""
    if not narrative:
        return []

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
        elems.append(Paragraph(action_text, ParagraphStyle(
            'action_required', parent=styles['Normal'], fontSize=8, spaceBefore=4, spaceAfter=6,
            leading=12, borderWidth=1, borderColor=hex_color("#ef4444"), borderPadding=6,
            backColor=hex_color("#FEF2F2"),
        )))
    elif narrative.get("section_intro"):
        # No action required — show green banner
        elems.append(Paragraph(
            f'<font color="#22c55e">&#x2713; No immediate action required</font>',
            ParagraphStyle('no_action', parent=styles['Normal'], fontSize=8,
                           spaceBefore=2, spaceAfter=4, textColor=hex_color("#22c55e")),
        ))

    # Risk summary (executive only)
    risk = narrative.get("risk_summary")
    if risk:
        elems.append(Paragraph(
            f'<font color="{TEXT_DARK}">{risk}</font>',
            ParagraphStyle('risk_summary', parent=styles['Normal'], fontSize=8,
                           spaceBefore=2, spaceAfter=6, leading=12,
                           borderWidth=1, borderColor=hex_color("#ef4444"),
                           borderPadding=5, leftIndent=3,
                           backColor=hex_color("#FEF2F2")),
        ))

    # Section intro
    intro = narrative.get("section_intro")
    if intro:
        elems.append(Paragraph(
            f'<font color="{TEXT_DARK}">{intro}</font>',
            ParagraphStyle('section_intro', parent=styles['Normal'], fontSize=9,
                           spaceBefore=2, spaceAfter=4, leading=13),
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
        elems.append(Paragraph(findings_text, ParagraphStyle(
            'findings', parent=styles['Normal'], fontSize=8, spaceBefore=2,
            spaceAfter=4, leading=11,
        )))

    # Interpretation
    interp = narrative.get("interpretation")
    if interp:
        elems.append(Paragraph(
            f'<i><font color="{TEXT_MID}">{interp}</font></i>',
            ParagraphStyle('interpretation', parent=styles['Normal'], fontSize=8,
                           spaceBefore=2, spaceAfter=4, leading=11,
                           backColor=hex_color("#F0F9FF"), borderPadding=4),
        ))

    # Recommended actions
    actions = narrative.get("action_items", [])
    if actions:
        actions_text = f'<font color="{TEAL}"><b>Recommended Actions:</b></font><br/>'
        for i, act in enumerate(actions[:5], 1):
            actions_text += f'<font color="{TEXT_MID}">{i}. {act}</font><br/>'
        elems.append(Paragraph(actions_text, ParagraphStyle(
            'rec_actions', parent=styles['Normal'], fontSize=8, spaceBefore=2,
            spaceAfter=6, leading=11,
        )))

    return elems


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

    # ── Insights box (rule-based + optional Gemini) ──────────────────────────
    insights = report.get("insights")
    if insights and insights.get("findings"):
        insight_text = f'<font color="{TEAL}"><b>Analysis:</b></font> '
        insight_text += f'<font color="{TEXT_DARK}">{insights.get("executive_summary", "")}</font><br/>'
        for f in insights["findings"][:3]:
            sev_color = "#ef4444" if f["severity"] == "critical" else "#f59e0b" if f["severity"] == "warning" else "#22c55e"
            insight_text += f'<font color="{sev_color}">&#x25CF;</font> <font color="{TEXT_DARK}">{f["text"]}</font><br/>'
        if insights.get("recommendations"):
            insight_text += f'<br/><font color="{TEAL}"><b>Recommendations:</b></font><br/>'
            for i, rec in enumerate(insights["recommendations"][:3], 1):
                insight_text += f'<font color="{TEXT_MID}">{i}. {rec}</font><br/>'
        elems.append(Paragraph(insight_text, ParagraphStyle(
            'insights', parent=styles['Normal'], fontSize=8, spaceBefore=4, spaceAfter=8,
            leading=12, borderWidth=1, borderColor=hex_color(TEAL), borderPadding=6,
            backColor=hex_color("#F0F9FF"),
        )))
        source_label = insights.get("insight_source", "rule_based").replace("_", " ").title()
        elems.append(Paragraph(
            f'<font color="{TEXT_LIGHT}" size="6"><i>Insights: {source_label}</i></font>',
            ParagraphStyle('insight_src', parent=styles['Normal'], fontSize=6, spaceAfter=6),
        ))

    # ── Narrative section (Master Spec progressive disclosure) ───────────────
    narratives = report.get("narratives", {})
    exec_narrative = narratives.get("executive") or report.get("narrative")
    elems.extend(_build_narrative_section(exec_narrative, styles))

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
    sla_header = ["SLA Tier", "Threshold", "Devices", "Distribution"]
    total_devices = max(summary.get("total_devices", 1), 1)
    sla_thresholds = {
        "Gold": "\u2265 99.9%", "Silver": "\u2265 99.5%", "Bronze": "\u2265 99.0%",
        "Warning": "\u2265 95.0%", "Critical": "< 95.0%", "Unknown": "No Data",
    }
    sla_rows = [sla_header]
    for tier in ("Gold", "Silver", "Bronze", "Warning", "Critical", "Unknown"):
        count = sla_dist.get(tier, 0)
        pct_val = count / total_devices * 100 if count else 0
        # Unicode progress bar: 10 chars, filled proportional to pct
        filled = round(pct_val / 10)
        bar = "\u2588" * filled + "\u2591" * (10 - filled)
        pct_str = f"{pct_val:.1f}%  ({count})" if count else "\u2014"
        sla_rows.append([tier, sla_thresholds[tier], f"{bar}", pct_str])

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
            "Latency & packet-loss from DailyDeviceStats.",
            styles, color=TEXT_MID,
        ),
        HRFlowable(width="100%", thickness=0.5, color=hex_color(BORDER), spaceAfter=6),
        Spacer(1, 4),
    ]

    # Narrative section (Master Spec)
    narratives = report.get("narratives", {})
    srv_narrative = narratives.get("server_fleet")
    elems.extend(_build_narrative_section(srv_narrative, styles))

    if not rows:
        elems.append(normal_paragraph("No inventory devices found for this period.", styles))
        elems.append(PageBreak())
        return elems

    # 9 columns — landscape A4 (773pt wide after 28pt margins each side)
    headers = [
        "Device Name", "IP Address", "Type",
        "Uptime %", "Downtime", "Timeout",
        "Latency", "Pkt Loss", "SLA Tier",
    ]
    col_w = ["20%", "13%", "10%", "10%", "10%", "7%", "10%", "9%", "11%"]

    table_data = [headers]
    for r in rows:
        tier = r.get("sla_tier", "Unknown")
        no_resp = r.get("timeout_count", 0)
        table_data.append([
            (r.get("device_name") or "—")[:24],
            r.get("device_ip", "—"),
            (r.get("device_type") or "—")[:12],
            _fmt_uptime(r.get("uptime_pct")),
            _fmt_hours(r.get("downtime_hours")),
            str(no_resp) if no_resp else "—",
            _fmt_num(r.get("avg_latency_ms"), " ms"),
            _fmt_num(r.get("avg_packet_loss_pct"), "%"),
            tier,
        ])

    table = Table(table_data, colWidths=col_w, repeatRows=1)
    ts = base_table_style()

    # Colour uptime col (3) and SLA col (8)
    for i, r in enumerate(rows, start=1):
        tier = r.get("sla_tier", "Unknown")
        text_c, bg_c = _sla_badge_style(tier)
        ts.add("BACKGROUND", (8, i), (8, i), hex_color(bg_c))
        ts.add("TEXTCOLOR",  (8, i), (8, i), hex_color(text_c))
        ts.add("FONTNAME",   (8, i), (8, i), "Helvetica-Bold")
        ts.add("TEXTCOLOR",  (3, i), (3, i), hex_color(text_c))
        ts.add("FONTNAME",   (3, i), (3, i), "Helvetica-Bold")
        # Highlight timeout column if non-zero
        no_resp = r.get("timeout_count", 0)
        if no_resp and no_resp > 0:
            ts.add("TEXTCOLOR", (5, i), (5, i), hex_color("#EA580C"))
            ts.add("FONTNAME",  (5, i), (5, i), "Helvetica-Bold")
        # Severity colors for latency (col 6) and packet loss (col 7)
        lat = r.get("avg_latency_ms")
        if lat is not None and lat > 200:
            ts.add("TEXTCOLOR", (6, i), (6, i), hex_color("#DC2626"))
            ts.add("FONTNAME",  (6, i), (6, i), "Helvetica-Bold")
        elif lat is not None and lat > 100:
            ts.add("TEXTCOLOR", (6, i), (6, i), hex_color("#D97706"))
        pkt = r.get("avg_packet_loss_pct")
        if pkt is not None and pkt > 15:
            ts.add("TEXTCOLOR", (7, i), (7, i), hex_color("#DC2626"))
            ts.add("FONTNAME",  (7, i), (7, i), "Helvetica-Bold")
        elif pkt is not None and pkt > 5:
            ts.add("TEXTCOLOR", (7, i), (7, i), hex_color("#D97706"))

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

    # Narrative section (Master Spec)
    narratives = report.get("narratives", {})
    ws_narrative = narratives.get("tracked_fleet")
    elems.extend(_build_narrative_section(ws_narrative, styles))

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
        last_seen = _fmt_ts_short(r.get("last_seen"))
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
    def __init__(self, report_title: str, gen_at: str, insight_source: str = "rule_based"):
        self.title = report_title
        self.gen_at = gen_at
        self.insight_source = insight_source

    def __call__(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(hex_color(TEXT_LIGHT))
        w, h = doc.pagesize
        # Master Spec: classification marking + generated by
        canvas.drawString(doc.leftMargin, doc.bottomMargin - 10,
                          f"CONFIDENTIAL \u2014 Internal Use Only   |   {self.title}")
        source_label = self.insight_source.replace("_", " ").title()
        canvas.drawRightString(
            w - doc.rightMargin, doc.bottomMargin - 10,
            f"Generated {self.gen_at} UTC   |   Rule-Based Insights Engine   |   Page {doc.page}",
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

    kpi_data = [
        ["Total Violations", "Affected Devices", "Top Offender"],
        [
            str(total),
            str(affected_devices),
            (top_device.get("device_name", "—") if top_device else "—"),
        ],
    ]
    kpi_table = Table(kpi_data, colWidths=["33%", "33%", "34%"])
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
    story += [kpi_table, Spacer(1, 0.4 * cm)]

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
    story.append(Paragraph(legend, ParagraphStyle(
        'confidence_legend', parent=styles['Normal'], fontSize=7, spaceBefore=4,
    )))

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
        story.append(Paragraph(text, ParagraphStyle(
            f'conf_{key}', parent=styles['Normal'], fontSize=6, spaceBefore=1,
        )))

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

    story = []
    story += _build_cover(report, styles, fleet=fleet)
    story += _build_executive_summary(report, styles)
    if fleet in ("all", "server"):
        story += _build_server_fleet(report, styles)
    if fleet in ("all", "workstation"):
        story += _build_tracked_fleet(report, styles)
    story += _build_violations_section(report, styles)
    story += _build_confidence_footnotes(report, styles)

    doc.build(story, onFirstPage=_first_page, onLaterPages=footer)
    buf.seek(0)
    logger.info("[EnterprisePDF] PDF complete: %d bytes", len(buf.getvalue()))
    return buf


def generate_device_inspector_pdf(
    stats: dict,
    device_name: str,
    device_ip: str,
    period_label: str,
) -> io.BytesIO:
    """Single-device performance report PDF (Portrait A4)."""
    buf = io.BytesIO()
    # A4 portrait: 21cm wide, 1.5cm margins → 18cm content width
    _L_MARGIN = 1.5 * cm
    _R_MARGIN = 1.5 * cm
    _CONTENT_W = 21 * cm - _L_MARGIN - _R_MARGIN   # 18 cm

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
        f'<font color="{TEXT_MID}">{device_ip} &nbsp;&middot;&nbsp; {period_label}</font>',
        ParagraphStyle('sub', parent=styles['Normal'], fontSize=10, spaceAfter=4),
    ))
    story.append(Paragraph(
        f'<font color="{TEXT_LIGHT}">Generated: {gen_at}</font>',
        ParagraphStyle('gen', parent=styles['Normal'], fontSize=8, spaceAfter=10),
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=hex_color(TEAL), spaceAfter=6))

    # ── Availability KPI table — explicit column widths to fill page ──────────
    uptime = stats.get('uptime_percentage', 0.0) or 0.0
    downtime = round(100.0 - uptime, 2)
    tier = (
        "Gold"    if uptime >= 99.5 else
        "Silver"  if uptime >= 99.0 else
        "Bronze"  if uptime >= 95.0 else
        "Warning" if uptime >= 90.0 else "Critical"
    )
    tc, tbg = _sla_badge_style(tier)
    avail_data = [
        ["Total Scans", "Online", "Offline", "No Response", "Uptime %", "Downtime %"],
        [
            str(stats.get('total_scans', 0)),
            str(stats.get('online_count', 0)),
            str(stats.get('offline_count', 0)),
            str(stats.get('no_response_count', 0)),
            _fmt_uptime(uptime),
            _fmt_uptime(downtime),
        ],
    ]
    # 6 equal columns totalling _CONTENT_W
    _col6 = [_CONTENT_W / 6] * 6
    ts_avail = base_table_style()
    ts_avail.add('BACKGROUND', (4, 1), (4, 1), hex_color(tbg))
    ts_avail.add('TEXTCOLOR',  (4, 1), (4, 1), hex_color(tc))
    ts_avail.add('FONTNAME',   (4, 1), (4, 1), 'Helvetica-Bold')
    story.append(Paragraph('<b>Availability</b>',
        ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                       fontSize=11, spaceBefore=10, spaceAfter=5,
                       textColor=hex_color(NAVY))))
    story.append(Table(avail_data, colWidths=_col6, hAlign='LEFT',
                       style=ts_avail, repeatRows=1))
    story.append(Spacer(1, 0.5*cm))

    # ── Latency & Packet Loss ──────────────────────────────────────────────────
    if stats.get('avg_latency') is not None:
        # 5 columns, equal widths
        _col5 = [_CONTENT_W / 5] * 5
        lat_data = [
            ["Avg Latency", "Min Latency", "Max Latency", "Std Dev", "Avg Pkt Loss"],
            [
                _fmt_num(stats.get('avg_latency'),     ' ms'),
                _fmt_num(stats.get('min_latency'),     ' ms'),
                _fmt_num(stats.get('max_latency'),     ' ms'),
                _fmt_num(stats.get('latency_std_dev'), ' ms'),
                _fmt_num(stats.get('avg_packet_loss'), '%'),
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
        ag_data = [
            ["CPU %", "Memory %", "Disk %", "Net In", "Net Out", "Uptime"],
            [
                _fmt_num(l.get('cpu_percent'),    '%'),
                _fmt_num(l.get('memory_percent'), '%'),
                _fmt_num(l.get('disk_percent'),   '%'),
                _fmt_bps(l.get('network_in_bps')),
                _fmt_bps(l.get('network_out_bps')),
                _fmt_hours(float(l.get('uptime_seconds') or 0) / 3600),
            ],
        ]
        story.append(Paragraph('<b>Agent Telemetry (Latest Sample)</b>',
            ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=8, spaceAfter=5,
                           textColor=hex_color(NAVY))))
        story.append(Table(ag_data, colWidths=_col6, hAlign='LEFT',
                           style=base_table_style(), repeatRows=1))
        story.append(Spacer(1, 0.5*cm))

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
    tier_data = [[f"SLA Tier: {tier}", f"Uptime: {_fmt_uptime(uptime)}", f"Period: {period_label}"]]
    story.append(Table(tier_data,
                       colWidths=[_CONTENT_W * 0.35, _CONTENT_W * 0.30, _CONTENT_W * 0.35],
                       hAlign='LEFT', style=tier_ts))
    story.append(Spacer(1, 0.4*cm))

    # ── Footer note ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=hex_color(BORDER), spaceBefore=4))
    story.append(Paragraph(
        f'<i><font color="{TEXT_LIGHT}">Read-only report. '
        f'Data sourced from ICMP scan history (5-min interval) and agent telemetry. '
        f'All times in IST (Asia/Kolkata).</font></i>',
        ParagraphStyle('note', parent=styles['Normal'], fontSize=7, spaceBefore=6),
    ))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buf.seek(0)
    logger.info("[DeviceInspectorPDF] Generated for %s (%s): %d bytes",
                device_ip, period_label, len(buf.getvalue()))
    return buf
