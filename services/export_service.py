"""
Server-side report export helpers.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill(start_color="1B2A4A", end_color="1B2A4A", fill_type="solid")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
HEADER_BORDER = Border(bottom=Side(border_style="thin", color="4A5568"))
DATA_FONT = Font(name="Calibri", size=10)
ALT_ROW_FILL = PatternFill(start_color="F7FAFC", end_color="F7FAFC", fill_type="solid")
_ILLEGAL_XLSX_CHARS_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_label() -> str:
    return _utcnow().strftime("%Y-%m-%d %H:%M UTC")


def _sanitize_export_value(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")

    text = _ILLEGAL_XLSX_CHARS_RE.sub("", str(value))
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def _flatten_report_rows(report_data, report_type):
    flatteners = {
        "device-health": _flatten_device_health,
        "productivity": _flatten_productivity,
        "network": _flatten_network,
        "alerts": _flatten_alerts,
        "executive": _flatten_executive,
        "operational": _flatten_operational,
        "maintenance-availability": _flatten_maintenance_availability,
        "security-compliance": _flatten_security_compliance,
        "inventory-assets": _flatten_inventory_assets,
        "tracking-operations": _flatten_tracking_operations,
        "printer-operations": _flatten_printer_operations,
    }
    flattener = flatteners.get(report_type)
    if flattener is None:
        return ["Section", "Key", "Value"], []
    return flattener(report_data or {})


def _flatten_executive(data):
    headers = ["Section", "Label", "Value", "Device", "IP", "Type", "Uptime %"]
    rows = [
        {"Section": "summary", "Label": "Uptime Score (%)", "Value": data.get("uptime_score")},
        {"Section": "summary", "Label": "Avg Latency (ms)", "Value": data.get("avg_latency")},
        {"Section": "summary", "Label": "MTTA", "Value": (data.get("sla_metrics") or {}).get("mtta_human")},
        {"Section": "summary", "Label": "Total Devices", "Value": data.get("total_devices")},
    ]
    for label, count in (data.get("health_distribution") or {}).items():
        rows.append({"Section": "health_distribution", "Label": label, "Value": count})
    for device in data.get("top_problematic") or []:
        rows.append(
            {
                "Section": "top_problematic",
                "Device": device.get("name"),
                "IP": device.get("ip"),
                "Type": device.get("type"),
                "Uptime %": device.get("uptime"),
            }
        )
    return headers, rows


def _flatten_operational(data):
    headers = ["Section", "Day", "Hour", "Count", "Type", "Detail", "Timestamp", "Device", "IP"]
    rows = []
    for day, hour, count in data.get("heatmap") or []:
        rows.append({"Section": "heatmap", "Day": day, "Hour": hour, "Count": count})
    for event in data.get("audit_log") or []:
        rows.append(
            {
                "Section": "audit_log",
                "Type": event.get("event_type"),
                "Detail": event.get("message"),
                "Timestamp": event.get("timestamp"),
            }
        )
    for device in data.get("new_devices") or []:
        rows.append(
            {
                "Section": "new_devices",
                "Device": device.get("device_name"),
                "IP": device.get("device_ip"),
                "Type": device.get("device_type"),
                "Timestamp": device.get("created_at"),
            }
        )
    return headers, rows


def _flatten_device_health(data):
    headers = [
        "Section",
        "Device",
        "Timestamp",
        "CPU %",
        "Memory %",
        "Disk %",
        "Net In (bps)",
        "Net Out (bps)",
        "Avg CPU %",
        "Max CPU %",
        "Avg Memory %",
        "Max Memory %",
        "Avg Disk %",
        "Samples",
    ]
    rows = []
    for item in data.get("summary") or []:
        rows.append(
            {
                "Section": "summary",
                "Device": item.get("device_name"),
                "Avg CPU %": item.get("avg_cpu"),
                "Max CPU %": item.get("max_cpu"),
                "Avg Memory %": item.get("avg_mem"),
                "Max Memory %": item.get("max_mem"),
                "Avg Disk %": item.get("avg_disk"),
                "Samples": item.get("samples"),
            }
        )
    for _, info in (data.get("time_series") or {}).items():
        for point in info.get("points") or []:
            rows.append(
                {
                    "Section": "time_series",
                    "Device": info.get("device_name"),
                    "Timestamp": point.get("ts"),
                    "CPU %": point.get("cpu"),
                    "Memory %": point.get("mem"),
                    "Disk %": point.get("disk"),
                    "Net In (bps)": point.get("net_in"),
                    "Net Out (bps)": point.get("net_out"),
                }
            )
    return headers, rows


def _flatten_productivity(data):
    headers = [
        "Section",
        "Device",
        "Employee",
        "Application",
        "Category",
        "Duration (sec)",
        "Sessions",
        "Data Basis",
        "Metric",
        "Value",
        "Freshness State",
        "Last Sample At",
        "Coverage %",
        "Sample Count",
        "Report Eligible",
    ]
    rows = []
    freshness_summary = data.get("freshness_summary") or {}
    freshness_devices = freshness_summary.get("devices") or {}
    source_basis = freshness_summary.get("source_basis", "persisted_samples")
    for device_id, info in (data.get("app_breakdown") or {}).items():
        freshness = freshness_devices.get(str(device_id)) or freshness_devices.get(device_id) or {}
        for app in info.get("apps") or []:
            rows.append(
                {
                    "Section": "applications",
                    "Device": info.get("device_name"),
                    "Employee": info.get("employee_name"),
                    "Application": app.get("name"),
                    "Category": app.get("category"),
                    "Duration (sec)": app.get("total_seconds"),
                    "Sessions": app.get("sessions"),
                    "Data Basis": source_basis,
                    "Freshness State": freshness.get("freshness_state"),
                    "Last Sample At": freshness.get("last_sample_at"),
                    "Coverage %": freshness.get("coverage_pct"),
                    "Sample Count": freshness.get("sample_count"),
                    "Report Eligible": "Yes" if freshness.get("report_eligible") else "No",
                }
            )
    for category, total_seconds in (data.get("category_totals") or {}).items():
        rows.append({"Section": "category_totals", "Category": category, "Value": total_seconds})
    for device_id, metrics in (data.get("activity_summary") or {}).items():
        for metric_name, metric_value in metrics.items():
            rows.append(
                {
                    "Section": "activity_summary",
                    "Device": device_id,
                    "Metric": metric_name,
                    "Value": metric_value,
                }
            )
    totals = freshness_summary.get("totals") or {}
    for metric_name, metric_value in totals.items():
        rows.append({"Section": "freshness_totals", "Metric": metric_name, "Value": metric_value})
    return headers, rows


def _flatten_network(data):
    headers = [
        "Section",
        "Device",
        "Interface",
        "Timestamp",
        "RX (bps)",
        "TX (bps)",
        "RX Util %",
        "TX Util %",
        "Metric",
        "Value",
    ]
    rows = []
    for _, info in (data.get("bandwidth") or {}).items():
        for point in info.get("points") or []:
            rows.append(
                {
                    "Section": "bandwidth",
                    "Device": info.get("device_name"),
                    "Interface": info.get("interface"),
                    "Timestamp": point.get("ts"),
                    "RX (bps)": point.get("rx_bps"),
                    "TX (bps)": point.get("tx_bps"),
                    "RX Util %": point.get("rx_util"),
                    "TX Util %": point.get("tx_util"),
                }
            )
    for summary in data.get("uptime_summary") or []:
        rows.append(
            {
                "Section": "uptime_summary",
                "Device": summary.get("device_name"),
                "Metric": "avg_uptime",
                "Value": summary.get("avg_uptime"),
            }
        )
        rows.append(
            {
                "Section": "uptime_summary",
                "Device": summary.get("device_name"),
                "Metric": "avg_latency_ms",
                "Value": summary.get("avg_latency_ms"),
            }
        )
        rows.append(
            {
                "Section": "uptime_summary",
                "Device": summary.get("device_name"),
                "Metric": "avg_packet_loss",
                "Value": summary.get("avg_packet_loss"),
            }
        )
    mttr = data.get("mttr") or {}
    rows.append({"Section": "mttr", "Metric": "seconds", "Value": mttr.get("seconds")})
    rows.append({"Section": "mttr", "Metric": "human", "Value": mttr.get("human")})
    rows.append({"Section": "mttr", "Metric": "total_incidents", "Value": mttr.get("total_incidents")})
    return headers, rows


def _flatten_alerts(data):
    headers = [
        "Section",
        "Timestamp",
        "Device",
        "IP",
        "Severity",
        "Type",
        "Message",
        "Acknowledged",
        "Resolved",
        "Label",
        "Value",
    ]
    rows = []
    for alert in data.get("alerts") or []:
        rows.append(
            {
                "Section": "alerts",
                "Timestamp": alert.get("timestamp"),
                "Device": alert.get("device_name"),
                "IP": alert.get("device_ip"),
                "Severity": alert.get("severity"),
                "Type": alert.get("event_type"),
                "Message": alert.get("message"),
                "Acknowledged": "Yes" if alert.get("is_acknowledged") else "No",
                "Resolved": "Yes" if alert.get("resolved") else "No",
            }
        )
    for day, severities in (data.get("daily_trend") or {}).items():
        for severity, count in (severities or {}).items():
            rows.append({"Section": "daily_trend", "Timestamp": day, "Severity": severity, "Value": count})
    for severity, count in (data.get("severity_breakdown") or {}).items():
        rows.append({"Section": "severity_breakdown", "Severity": severity, "Value": count})
    for device in data.get("top_alerted_devices") or []:
        rows.append(
            {
                "Section": "top_alerted_devices",
                "Device": device.get("device_name"),
                "IP": device.get("device_ip"),
                "Value": device.get("alert_count"),
            }
        )
    tta = data.get("tta") or {}
    ttr = data.get("ttr") or {}
    rows.append({"Section": "tta", "Label": "seconds", "Value": tta.get("seconds")})
    rows.append({"Section": "tta", "Label": "human", "Value": tta.get("human")})
    rows.append({"Section": "ttr", "Label": "seconds", "Value": ttr.get("seconds")})
    rows.append({"Section": "ttr", "Label": "human", "Value": ttr.get("human")})
    return headers, rows


def _flatten_maintenance_availability(data):
    headers = ["Section", "Device", "IP", "Start", "End", "Metric", "Value", "Status", "Reason"]
    rows = []
    for window in data.get("scheduled_windows") or []:
        rows.append(
            {
                "Section": "scheduled_windows",
                "Device": window.get("device_name"),
                "IP": window.get("device_ip"),
                "Start": window.get("start_time"),
                "End": window.get("end_time"),
                "Reason": window.get("reason"),
                "Status": "active" if window.get("is_active") else "inactive",
            }
        )
    for device in data.get("maintenance_devices") or []:
        rows.append({"Section": "maintenance_devices", "Device": device.get("device_name"), "IP": device.get("device_ip"), "Status": "maintenance"})
    for item in data.get("downtime_leaders") or []:
        rows.append(
            {
                "Section": "downtime_leaders",
                "Device": item.get("device_name"),
                "IP": item.get("device_ip"),
                "Metric": "availability_pct",
                "Value": item.get("availability_pct"),
            }
        )
    for item in data.get("tracked_instability") or []:
        rows.append(
            {
                "Section": "tracked_instability",
                "Device": item.get("device_name"),
                "Metric": "offline_events",
                "Value": item.get("offline_events"),
            }
        )
        rows.append(
            {
                "Section": "tracked_instability",
                "Device": item.get("device_name"),
                "Metric": "degraded_events",
                "Value": item.get("degraded_events"),
            }
        )
    for metric_name, metric_value in (data.get("summary") or {}).items():
        rows.append({"Section": "summary", "Metric": metric_name, "Value": metric_value})
    return headers, rows


def _flatten_security_compliance(data):
    headers = ["Section", "Timestamp", "Device", "Severity", "Type", "Metric", "Value", "Detail"]
    rows = []
    for metric_name, metric_value in (data.get("summary") or {}).items():
        rows.append({"Section": "summary", "Metric": metric_name, "Value": metric_value})
    for event in data.get("recent_alerts") or []:
        rows.append(
            {
                "Section": "recent_alerts",
                "Timestamp": event.get("timestamp"),
                "Device": event.get("device_name"),
                "Severity": event.get("severity"),
                "Type": event.get("event_type"),
                "Detail": event.get("message"),
            }
        )
    for entry in data.get("recent_audit_log") or []:
        rows.append(
            {
                "Section": "audit_log",
                "Timestamp": entry.get("timestamp"),
                "Type": entry.get("action"),
                "Detail": entry.get("description"),
                "Value": entry.get("entity_id"),
            }
        )
    for item in data.get("restricted_site_violations") or []:
        rows.append(
            {
                "Section": "restricted_site_violations",
                "Timestamp": item.get("observed_at_utc"),
                "Device": item.get("device_name"),
                "Metric": item.get("domain"),
                "Value": item.get("count"),
            }
        )
    for severity, count in (data.get("integrity_breakdown") or {}).items():
        rows.append({"Section": "integrity_breakdown", "Severity": severity, "Value": count})
    for item in data.get("threshold_breaches") or []:
        rows.append(
            {
                "Section": "threshold_breaches",
                "Device": item.get("device_name"),
                "Metric": item.get("metric_key"),
                "Value": item.get("breach_streak"),
                "Detail": item.get("last_state"),
            }
        )
    return headers, rows


def _flatten_inventory_assets(data):
    headers = ["Section", "Name", "Device", "IP", "Metric", "Value", "Type", "Status"]
    rows = []
    for metric_name, metric_value in (data.get("summary") or {}).items():
        rows.append({"Section": "summary", "Metric": metric_name, "Value": metric_value})
    for item in data.get("inventory_devices") or []:
        rows.append(
            {
                "Section": "inventory_devices",
                "Name": item.get("device_name"),
                "IP": item.get("device_ip"),
                "Type": item.get("device_type"),
            }
        )
    for item in data.get("tracked_devices") or []:
        rows.append(
            {
                "Section": "tracked_devices",
                "Name": item.get("device_name"),
                "IP": item.get("ip_address"),
                "Status": item.get("availability_status"),
            }
        )
    for item in data.get("active_links") or []:
        rows.append(
            {
                "Section": "active_links",
                "Device": item.get("device_name"),
                "Name": item.get("tracked_device_name"),
                "Metric": "confidence",
                "Value": item.get("confidence"),
                "Status": item.get("link_source"),
            }
        )
    for item in data.get("pending_candidates") or []:
        rows.append(
            {
                "Section": "pending_candidates",
                "Device": item.get("device_name"),
                "Name": item.get("tracked_device_name"),
                "Metric": "candidate_score",
                "Value": item.get("candidate_score"),
                "Status": item.get("status"),
            }
        )
    return headers, rows


def _flatten_tracking_operations(data):
    headers = ["Section", "Device", "Metric", "Value", "Application", "Category", "Timestamp", "Status"]
    rows = []
    for metric_name, metric_value in (data.get("summary") or {}).items():
        rows.append({"Section": "summary", "Metric": metric_name, "Value": metric_value})
    for item in data.get("device_freshness") or []:
        rows.append(
            {
                "Section": "device_freshness",
                "Device": item.get("device_name"),
                "Metric": "coverage_pct",
                "Value": item.get("coverage_pct"),
                "Status": item.get("freshness_state"),
            }
        )
    for item in data.get("top_applications") or []:
        rows.append(
            {
                "Section": "top_applications",
                "Device": item.get("device_name"),
                "Application": item.get("application_name"),
                "Category": item.get("category"),
                "Value": item.get("total_seconds"),
            }
        )
    for item in data.get("activity_totals") or []:
        rows.append(
            {
                "Section": "activity_totals",
                "Device": item.get("device_name"),
                "Metric": item.get("activity_type"),
                "Value": item.get("total_events"),
            }
        )
    for item in data.get("availability_breakdown") or []:
        rows.append(
            {
                "Section": "availability_breakdown",
                "Device": item.get("device_name"),
                "Metric": "offline_events",
                "Value": item.get("offline_events"),
            }
        )
        rows.append(
            {
                "Section": "availability_breakdown",
                "Device": item.get("device_name"),
                "Metric": "degraded_events",
                "Value": item.get("degraded_events"),
            }
        )
    for severity, count in (data.get("integrity_breakdown") or {}).items():
        rows.append({"Section": "integrity_breakdown", "Metric": severity, "Value": count})
    return headers, rows


def _flatten_printer_operations(data):
    headers = ["Section", "Device", "Metric", "Value", "Timestamp", "Status", "User"]
    rows = []
    for metric_name, metric_value in (data.get("summary") or {}).items():
        rows.append({"Section": "summary", "Metric": metric_name, "Value": metric_value})
    for metric_name, metric_value in (data.get("promotion_triggers") or {}).items():
        rows.append({"Section": "promotion_triggers", "Metric": metric_name, "Value": metric_value})
    for item in data.get("printer_status") or []:
        rows.append(
            {
                "Section": "printer_status",
                "Device": item.get("device_name"),
                "Timestamp": item.get("timestamp"),
                "Status": item.get("status"),
                "Metric": "page_count_total",
                "Value": item.get("page_count_total"),
            }
        )
    for item in data.get("print_volume") or []:
        rows.append(
            {
                "Section": "print_volume",
                "Device": item.get("printer_name"),
                "Metric": "job_count",
                "Value": item.get("job_count"),
                "User": item.get("user_account"),
            }
        )
        rows.append(
            {
                "Section": "print_volume",
                "Device": item.get("printer_name"),
                "Metric": "total_pages",
                "Value": item.get("total_pages"),
                "User": item.get("user_account"),
            }
        )
    return headers, rows


def export_to_csv(report_data, report_type):
    headers, rows = _flatten_report_rows(report_data, report_type)
    text_buf = io.StringIO()
    writer = csv.DictWriter(text_buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({header: _sanitize_export_value(row.get(header, "")) for header in headers})
    buf = io.BytesIO(text_buf.getvalue().encode("utf-8"))
    buf.seek(0)
    return buf


def export_to_excel(report_data, report_type):
    headers, rows = _flatten_report_rows(report_data, report_type)
    wb = Workbook()

    ws_summary = wb.active
    ws_summary.title = "Summary"
    period = report_data.get("period", {})
    ws_summary.append(["Report Type", report_type.replace("-", " ").title()])
    ws_summary.append(["Period", f"{period.get('start', '')} to {period.get('end', '')}"])
    ws_summary.append(["Generated", _utc_label()])
    ws_summary.append(["Rows", len(rows)])
    meta = report_data.get("meta") or {}
    if meta:
        ws_summary.append(["Scope Type", meta.get("scope_type", "")])
        ws_summary.append(["Scope ID", meta.get("scope_id", "")])
        ws_summary.append(["Freshness State", meta.get("freshness_state", "")])
        ws_summary.append(["Data As Of", meta.get("data_as_of", "")])
    if report_type == "productivity":
        freshness_summary = report_data.get("freshness_summary") or {}
        totals = freshness_summary.get("totals") or {}
        ws_summary.append(["Data Basis", freshness_summary.get("source_basis", "persisted_samples")])
        ws_summary.append(["Fresh Devices", totals.get("fresh_devices", 0)])
        ws_summary.append(["Stale Devices", totals.get("stale_devices", 0)])
        ws_summary.append(["Empty Devices", totals.get("empty_devices", 0)])
    for row in ws_summary.iter_rows(min_row=1, max_row=ws_summary.max_row, max_col=2):
        for cell in row:
            cell.font = DATA_FONT
    ws_summary.column_dimensions["A"].width = 24
    ws_summary.column_dimensions["B"].width = 60

    ws_data = wb.create_sheet(title="Data")
    for col_idx, header in enumerate(headers, 1):
        cell = ws_data.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = HEADER_BORDER

    for row_idx, row_data in enumerate(rows, 2):
        for col_idx, header in enumerate(headers, 1):
            cell = ws_data.cell(
                row=row_idx,
                column=col_idx,
                value=_sanitize_export_value(row_data.get(header, "")),
            )
            cell.font = DATA_FONT
            if row_idx % 2 == 0:
                cell.fill = ALT_ROW_FILL

    for col_idx, header in enumerate(headers, 1):
        max_len = len(header)
        for row_idx in range(2, min(len(rows) + 2, 200)):
            value = ws_data.cell(row=row_idx, column=col_idx).value
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws_data.column_dimensions[ws_data.cell(row=1, column=col_idx).column_letter].width = min(max_len + 3, 40)
    ws_data.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _export_to_pdf_fallback(report_data, report_type):
    headers, rows = _flatten_report_rows(report_data, report_type)
    preview_rows = rows[:35]
    lines = [
        f"{report_type.replace('-', ' ').title()} Report",
        f"Generated {_utc_label()}",
    ]
    meta = report_data.get("meta") or {}
    if meta:
        lines.append(f"Scope {meta.get('scope_type') or 'global'}:{meta.get('scope_id')}")
        lines.append(f"Freshness {meta.get('freshness_state')}")
    lines.append("")
    lines.append(" | ".join(headers))
    for row in preview_rows:
        lines.append(" | ".join(str(_sanitize_export_value(row.get(header, "")))[:40] for header in headers))

    content_lines = []
    for idx, line in enumerate(lines):
        operator = "Td" if idx == 0 else "T*"
        if idx == 0:
            content_lines.append(f"BT /F1 10 Tf 36 800 {operator} ({_escape_pdf_text(line)}) Tj")
        else:
            content_lines.append(f"({_escape_pdf_text(line)}) Tj {operator}")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj")
    objects.append(b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj")
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj")
    objects.append(f"5 0 obj << /Length {len(content)} >> stream\n".encode("ascii") + content + b"\nendstream endobj")

    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj)
        pdf.write(b"\n")
    xref_offset = pdf.tell()
    pdf.write(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(
        (
            "trailer << /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".format(
                size=len(offsets),
                xref=xref_offset,
            )
        ).encode("ascii")
    )
    pdf.seek(0)
    return pdf


def export_to_pdf(report_data, report_type):
    headers, rows = _flatten_report_rows(report_data, report_type)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except Exception:
        return _export_to_pdf_fallback(report_data, report_type)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{report_type.replace('-', ' ').title()} Report", styles["Title"]),
        Spacer(1, 8),
    ]
    period = report_data.get("period") or {}
    meta = report_data.get("meta") or {}
    story.append(Paragraph(f"Period: {period.get('start', '')} to {period.get('end', '')}", styles["Normal"]))
    story.append(Paragraph(f"Generated: {_utc_label()}", styles["Normal"]))
    if meta:
        story.append(Paragraph(f"Scope: {meta.get('scope_type', 'global')} / {meta.get('scope_id', '')}", styles["Normal"]))
        story.append(Paragraph(f"Freshness: {meta.get('freshness_state', '')}", styles["Normal"]))
    story.append(Spacer(1, 12))

    table_rows = [headers]
    for row in rows[:250]:
        table_rows.append([str(_sanitize_export_value(row.get(header, ""))) for header in headers])

    table = Table(table_rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B2A4A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    buf.seek(0)
    return buf


def export_report_buffer(report_data, report_type, export_format):
    export_format = str(export_format or "csv").strip().lower()
    if export_format == "csv":
        return export_to_csv(report_data, report_type)
    if export_format == "xlsx":
        return export_to_excel(report_data, report_type)
    if export_format == "pdf":
        return export_to_pdf(report_data, report_type)
    raise ValueError(f"Unsupported export format: {export_format}")
