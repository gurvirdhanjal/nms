"""
Export Service — Server-side CSV and Excel export.

Rule 3 (AGENTS.md §7): Export on the server, not the browser.
All exports are generated in Flask and sent via send_file().
"""
import csv
import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


# ── Dark/professional Excel styling ─────────────────────────────
HEADER_FONT = Font(name='Calibri', bold=True, color='FFFFFF', size=11)
HEADER_FILL = PatternFill(start_color='1B2A4A', end_color='1B2A4A', fill_type='solid')
HEADER_ALIGNMENT = Alignment(horizontal='center', vertical='center', wrap_text=True)
HEADER_BORDER = Border(
    bottom=Side(border_style='thin', color='4A5568'),
)
DATA_FONT = Font(name='Calibri', size=10)
ALT_ROW_FILL = PatternFill(start_color='F7FAFC', end_color='F7FAFC', fill_type='solid')


def _flatten_report_rows(report_data, report_type):
    """
    Convert report JSON into a flat list of dicts suitable for tabular export.
    Returns (headers: list[str], rows: list[dict]).
    """
    if report_type == 'device-health':
        return _flatten_device_health(report_data)
    elif report_type == 'productivity':
        return _flatten_productivity(report_data)
    elif report_type == 'network':
        return _flatten_network(report_data)
    elif report_type == 'alerts':
        return _flatten_alerts(report_data)
    elif report_type == 'executive':
        return _flatten_executive(report_data)
    elif report_type == 'operational':
        return _flatten_operational(report_data)
    else:
        return [], []


def _flatten_device_health(data):
    headers = ['Device', 'Timestamp', 'CPU %', 'Memory %', 'Disk %', 'Net In (bps)', 'Net Out (bps)']
    rows = []
    for device_id, info in (data.get('time_series') or {}).items():
        name = info.get('device_name', device_id)
        for pt in info.get('points', []):
            rows.append({
                'Device': name,
                'Timestamp': pt.get('ts', ''),
                'CPU %': pt.get('cpu'),
                'Memory %': pt.get('mem'),
                'Disk %': pt.get('disk'),
                'Net In (bps)': pt.get('net_in'),
                'Net Out (bps)': pt.get('net_out'),
            })
    return headers, rows


def _flatten_productivity(data):
    headers = ['Employee', 'Device', 'Application', 'Category', 'Duration (sec)', 'Sessions']
    rows = []
    for device_id, info in (data.get('app_breakdown') or {}).items():
        for app in info.get('apps', []):
            rows.append({
                'Employee': info.get('employee_name', ''),
                'Device': info.get('device_name', ''),
                'Application': app.get('name', ''),
                'Category': app.get('category', ''),
                'Duration (sec)': app.get('total_seconds', 0),
                'Sessions': app.get('sessions', 0),
            })
    return headers, rows


def _flatten_network(data):
    headers = ['Device', 'Interface', 'Timestamp', 'RX (bps)', 'TX (bps)', 'RX Util %', 'TX Util %']
    rows = []
    for key, info in (data.get('bandwidth') or {}).items():
        for pt in info.get('points', []):
            rows.append({
                'Device': info.get('device_name', ''),
                'Interface': info.get('interface', ''),
                'Timestamp': pt.get('ts', ''),
                'RX (bps)': pt.get('rx_bps'),
                'TX (bps)': pt.get('tx_bps'),
                'RX Util %': pt.get('rx_util'),
                'TX Util %': pt.get('tx_util'),
            })
    return headers, rows


def _flatten_alerts(data):
    headers = ['Timestamp', 'Device', 'Severity', 'Type', 'Message', 'Acknowledged', 'Resolved']
    rows = []
    for a in (data.get('alerts') or []):
        rows.append({
            'Timestamp': a.get('timestamp', ''),
            'Device': a.get('device_name', a.get('device_ip', '')),
            'Severity': a.get('severity', ''),
            'Type': a.get('event_type', ''),
            'Message': a.get('message', ''),
            'Acknowledged': 'Yes' if a.get('is_acknowledged') else 'No',
            'Resolved': 'Yes' if a.get('resolved') else 'No',
        })
    return headers, rows


def _flatten_executive(data):
    headers = ['Metric', 'Value']
    rows = [
        {'Metric': 'Uptime Score (%)', 'Value': data.get('uptime_score', '')},
        {'Metric': 'Avg Latency (ms)', 'Value': data.get('avg_latency', '')},
        {'Metric': 'MTTA', 'Value': data.get('sla_metrics', {}).get('mtta_human', '')},
        {'Metric': 'Total Devices', 'Value': data.get('total_devices', '')},
    ]
    for d in data.get('top_problematic', []):
        rows.append({'Metric': f"Problematic: {d['name']}", 'Value': f"{d['uptime']}%"})
    return headers, rows


def _flatten_operational(data):
    headers = ['Type', 'Detail', 'Timestamp']
    rows = []
    for e in data.get('audit_log', []):
        rows.append({
            'Type': e.get('event_type', ''),
            'Detail': e.get('message', ''),
            'Timestamp': e.get('timestamp', ''),
        })
    return headers, rows


# ── Public API ───────────────────────────────────────────────────

def export_to_csv(report_data, report_type):
    """
    Generate CSV from report data.
    Returns a BytesIO buffer ready for Flask send_file().
    """
    headers, rows = _flatten_report_rows(report_data, report_type)
    text_buf = io.StringIO()
    writer = csv.DictWriter(text_buf, fieldnames=headers, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    # Flask send_file requires bytes
    buf = io.BytesIO(text_buf.getvalue().encode('utf-8'))
    buf.seek(0)
    return buf


def export_to_excel(report_data, report_type):
    """
    Generate styled .xlsx workbook from report data.
    Returns a BytesIO buffer ready for Flask send_file().
    """
    headers, rows = _flatten_report_rows(report_data, report_type)
    wb = Workbook()

    # ── Summary sheet ──
    ws_summary = wb.active
    ws_summary.title = 'Summary'
    period = report_data.get('period', {})
    ws_summary.append(['Report Type', report_type.replace('-', ' ').title()])
    ws_summary.append(['Period', f"{period.get('start', '')} to {period.get('end', '')}"])
    ws_summary.append(['Generated', datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')])
    ws_summary.append(['Rows', len(rows)])

    # Style summary
    for row in ws_summary.iter_rows(min_row=1, max_row=4, max_col=2):
        for cell in row:
            cell.font = DATA_FONT
    ws_summary.column_dimensions['A'].width = 20
    ws_summary.column_dimensions['B'].width = 50

    # ── Data sheet ──
    ws_data = wb.create_sheet(title='Data')

    # Headers
    for col_idx, header in enumerate(headers, 1):
        cell = ws_data.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = HEADER_BORDER

    # Data rows
    for row_idx, row_data in enumerate(rows, 2):
        for col_idx, header in enumerate(headers, 1):
            cell = ws_data.cell(row=row_idx, column=col_idx, value=row_data.get(header, ''))
            cell.font = DATA_FONT
            if row_idx % 2 == 0:
                cell.fill = ALT_ROW_FILL

    # Auto-width columns
    for col_idx, header in enumerate(headers, 1):
        max_len = len(header)
        for row_idx in range(2, min(len(rows) + 2, 100)):  # sample first 100 rows
            val = ws_data.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, len(str(val)))
        ws_data.column_dimensions[ws_data.cell(row=1, column=col_idx).column_letter].width = min(max_len + 3, 50)

    # Freeze header row
    ws_data.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
