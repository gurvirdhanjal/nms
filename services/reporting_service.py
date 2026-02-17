"""
Reporting Service — Read-only report generators.

Rules (AGENTS.md §7):
  1. Never query raw tables for ranges > 24h; use rollup tables.
  2. No report method writes to the database.
  3. Export handled by export_service.py server-side.
"""
from extensions import db
from models.device import Device
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.server_health_rollups import (
    ServerHealthHourlyRollup,
    ServerHealthDailyRollup,
)
from models.tracked_device import (
    TrackedDevice,
    DeviceActivityLog,
    DeviceApplicationLog,
)
from models.interfaces import DeviceInterface, InterfaceTrafficHistory
from sqlalchemy import func, case, desc, and_, extract
from datetime import datetime, timedelta


# ── Runtime app-category mapping ──────────────────────────────────
# Used by productivity report to classify apps without a DB column.
APP_CATEGORIES = {
    # Productivity
    'Microsoft Word': 'Productivity', 'Microsoft Excel': 'Productivity',
    'Microsoft PowerPoint': 'Productivity', 'Google Docs': 'Productivity',
    'LibreOffice': 'Productivity', 'Notepad++': 'Productivity',
    'Microsoft Outlook': 'Productivity', 'Thunderbird': 'Productivity',
    # Communication
    'Microsoft Teams': 'Communication', 'Slack': 'Communication',
    'Zoom': 'Communication', 'Discord': 'Communication',
    'Skype': 'Communication',
    # Development
    'Visual Studio Code': 'Development', 'PyCharm': 'Development',
    'IntelliJ': 'Development', 'Eclipse': 'Development',
    'Terminal': 'Development', 'cmd': 'Development',
    'powershell': 'Development', 'Git': 'Development',
    # Browser
    'Google Chrome': 'Browser', 'Mozilla Firefox': 'Browser',
    'Microsoft Edge': 'Browser', 'Opera': 'Browser', 'Safari': 'Browser',
    # Entertainment
    'Spotify': 'Entertainment', 'VLC': 'Entertainment',
    'Netflix': 'Entertainment', 'YouTube': 'Entertainment',
}


def _classify_app(app_name):
    """Classify an application name to a category via fuzzy match."""
    if not app_name:
        return 'Other'
    name_lower = app_name.lower()
    for known, category in APP_CATEGORIES.items():
        if known.lower() in name_lower:
            return category
    return 'Other'


def _safe_round(value, digits=2):
    return round(value, digits) if value is not None else None


class ReportingService:
    """Pure read-only report generators.  No method writes to the DB."""

    # ─────────────────────────────────────────────────────────────
    # EXISTING: Executive Fleet Health
    # ─────────────────────────────────────────────────────────────
    def get_executive_fleet_health(self, start_date=None, end_date=None):
        """
        Generates the Executive Fleet Health Report.
        Target metrics:
        1. Overall Uptime Score (Avg of DailyStats.uptime_percent)
        2. Health Distribution (Count of devices by current status)
        3. SLA Metrics (Avg time to acknowledge critical alerts)
        4. Top 10 Problematic Devices (Lowest availability)
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # 1. Overall Uptime Score
        uptime_stats = db.session.query(
            func.avg(DailyDeviceStats.uptime_percent).label('avg_uptime'),
            func.avg(DailyDeviceStats.avg_latency_ms).label('avg_latency')
        ).filter(
            DailyDeviceStats.date >= start_date.date(),
            DailyDeviceStats.date <= end_date.date()
        ).first()

        uptime_score = round(uptime_stats.avg_uptime, 2) if uptime_stats.avg_uptime else 100.0
        avg_latency = round(uptime_stats.avg_latency, 2) if uptime_stats.avg_latency else 0.0

        # 2. Health Distribution
        total_devices = Device.query.count()

        latest_scans_subq = db.session.query(
            DeviceScanHistory.device_ip,
            func.max(DeviceScanHistory.scan_id).label('max_id')
        ).group_by(DeviceScanHistory.device_ip).subquery()

        latest_scans = db.session.query(DeviceScanHistory.status).join(
            latest_scans_subq,
            and_(
                DeviceScanHistory.device_ip == latest_scans_subq.c.device_ip,
                DeviceScanHistory.scan_id == latest_scans_subq.c.max_id
            )
        ).all()

        health_counts = {'Healthy': 0, 'Critical': 0, 'Warning': 0}
        for scan in latest_scans:
            status = (scan.status or '').lower()
            if status == 'online':
                health_counts['Healthy'] += 1
            else:
                health_counts['Critical'] += 1

        # 3. SLA Tracking (MTTA)
        sla_stats = db.session.query(
            func.avg(
                func.extract('epoch', DashboardEvent.acknowledged_at) -
                func.extract('epoch', DashboardEvent.timestamp)
            ).label('avg_ack_seconds')
        ).filter(
            DashboardEvent.severity == 'CRITICAL',
            DashboardEvent.is_acknowledged == True,
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date
        ).first()

        mtta_seconds = round(sla_stats.avg_ack_seconds) if sla_stats.avg_ack_seconds else 0

        # 4. Top 10 Problematic Devices
        problematic_devices = db.session.query(
            Device.device_name,
            Device.device_ip,
            Device.device_type,
            func.avg(DailyDeviceStats.uptime_percent).label('avg_uptime')
        ).join(
            DailyDeviceStats, DailyDeviceStats.device_id == Device.device_id
        ).filter(
            DailyDeviceStats.date >= start_date.date(),
            DailyDeviceStats.date <= end_date.date()
        ).group_by(
            Device.device_id, Device.device_name, Device.device_ip, Device.device_type
        ).order_by(
            func.avg(DailyDeviceStats.uptime_percent).asc()
        ).limit(10).all()

        top_problematic = [{
            'name': d.device_name,
            'ip': d.device_ip,
            'type': d.device_type,
            'uptime': round(d.avg_uptime, 2)
        } for d in problematic_devices]

        return {
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            },
            'uptime_score': uptime_score,
            'avg_latency': avg_latency,
            'health_distribution': health_counts,
            'sla_metrics': {
                'mtta_seconds': mtta_seconds,
                'mtta_human': str(timedelta(seconds=mtta_seconds))
            },
            'top_problematic': top_problematic,
            'total_devices': total_devices
        }

    # ─────────────────────────────────────────────────────────────
    # EXISTING: Operational Activity
    # ─────────────────────────────────────────────────────────────
    def get_operational_report(self, start_date=None, end_date=None):
        """
        Generates the Operational Activity & Compliance Report.
        1. Employee Activity Heatmap
        2. Audit Log (system/config events)
        3. Recently Added Devices
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # 1. Activity Heatmap
        heatmap_data = db.session.query(
            extract('dow', ServerHealthLog.timestamp).label('day_of_week'),
            extract('hour', ServerHealthLog.timestamp).label('hour_of_day'),
            func.count(ServerHealthLog.id).label('activity_count')
        ).filter(
            ServerHealthLog.timestamp >= start_date,
            ServerHealthLog.timestamp <= end_date
        ).group_by(
            'day_of_week', 'hour_of_day'
        ).all()

        formatted_heatmap = []
        for row in heatmap_data:
            if row.day_of_week is not None and row.hour_of_day is not None:
                formatted_heatmap.append([
                    int(row.day_of_week),
                    int(row.hour_of_day),
                    row.activity_count
                ])

        # 2. Audit Log
        audit_logs = DashboardEvent.query.filter(
            DashboardEvent.event_type.in_(['SYSTEM', 'CONFIG', 'SECURITY']),
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date
        ).order_by(DashboardEvent.timestamp.desc()).limit(50).all()

        # 3. New Devices
        new_devices = Device.query.filter(
            Device.created_at >= start_date,
            Device.created_at <= end_date
        ).order_by(Device.created_at.desc()).limit(20).all()

        return {
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            },
            'heatmap': formatted_heatmap,
            'audit_log': [e.to_dict() for e in audit_logs],
            'new_devices': [d.to_dict() for d in new_devices]
        }

    # ─────────────────────────────────────────────────────────────
    # NEW: 1. Device Health Report
    # ─────────────────────────────────────────────────────────────
    def get_device_health_report(self, device_ids=None, start_date=None, end_date=None):
        """
        Time-series CPU / Memory / Disk / Network per device.
        Enforces Rule 1: ≤24h → raw, ≤30d → hourly rollup, >30d → daily rollup.
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(hours=24)

        span = end_date - start_date

        # --- pick the right source table ----
        if span <= timedelta(hours=24):
            time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
            granularity = 'raw'
        elif span <= timedelta(days=30):
            time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
            granularity = 'hourly'
        else:
            time_series, summary = self._health_from_daily(device_ids, start_date, end_date)
            granularity = 'daily'

        return {
            'period': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
            'granularity': granularity,
            'time_series': time_series,
            'summary': summary,
        }

    # -- Device Health helpers --
    def _health_from_raw(self, device_ids, start_dt, end_dt):
        q = db.session.query(
            ServerHealthLog.device_id,
            Device.device_name,
            ServerHealthLog.timestamp,
            ServerHealthLog.cpu_usage,
            ServerHealthLog.memory_usage,
            ServerHealthLog.disk_usage,
            ServerHealthLog.network_in_bps,
            ServerHealthLog.network_out_bps,
        ).join(Device, Device.device_id == ServerHealthLog.device_id).filter(
            ServerHealthLog.timestamp >= start_dt,
            ServerHealthLog.timestamp <= end_dt,
        )
        if device_ids:
            q = q.filter(ServerHealthLog.device_id.in_(device_ids))
        q = q.order_by(ServerHealthLog.timestamp)

        rows = q.all()
        series = self._build_time_series(rows, ts_field='timestamp')

        # Summary KPIs per device
        summary_q = db.session.query(
            ServerHealthLog.device_id,
            Device.device_name,
            func.avg(ServerHealthLog.cpu_usage).label('avg_cpu'),
            func.max(ServerHealthLog.cpu_usage).label('max_cpu'),
            func.avg(ServerHealthLog.memory_usage).label('avg_mem'),
            func.max(ServerHealthLog.memory_usage).label('max_mem'),
            func.avg(ServerHealthLog.disk_usage).label('avg_disk'),
            func.count(ServerHealthLog.id).label('samples'),
        ).join(Device, Device.device_id == ServerHealthLog.device_id).filter(
            ServerHealthLog.timestamp >= start_dt,
            ServerHealthLog.timestamp <= end_dt,
        )
        if device_ids:
            summary_q = summary_q.filter(ServerHealthLog.device_id.in_(device_ids))
        summary_q = summary_q.group_by(ServerHealthLog.device_id, Device.device_name)

        summary = [{
            'device_id': r.device_id,
            'device_name': r.device_name,
            'avg_cpu': _safe_round(r.avg_cpu),
            'max_cpu': _safe_round(r.max_cpu),
            'avg_mem': _safe_round(r.avg_mem),
            'max_mem': _safe_round(r.max_mem),
            'avg_disk': _safe_round(r.avg_disk),
            'samples': r.samples,
        } for r in summary_q.all()]

        return series, summary

    def _health_from_hourly(self, device_ids, start_dt, end_dt):
        q = db.session.query(
            ServerHealthHourlyRollup.device_id,
            Device.device_name,
            ServerHealthHourlyRollup.bucket_hour.label('timestamp'),
            ServerHealthHourlyRollup.avg_cpu_usage.label('cpu_usage'),
            ServerHealthHourlyRollup.avg_memory_usage.label('memory_usage'),
            ServerHealthHourlyRollup.avg_disk_usage.label('disk_usage'),
            ServerHealthHourlyRollup.avg_network_in_bps.label('network_in_bps'),
            ServerHealthHourlyRollup.avg_network_out_bps.label('network_out_bps'),
        ).join(Device, Device.device_id == ServerHealthHourlyRollup.device_id).filter(
            ServerHealthHourlyRollup.bucket_hour >= start_dt,
            ServerHealthHourlyRollup.bucket_hour <= end_dt,
        )
        if device_ids:
            q = q.filter(ServerHealthHourlyRollup.device_id.in_(device_ids))
        q = q.order_by(ServerHealthHourlyRollup.bucket_hour)

        rows = q.all()
        series = self._build_time_series(rows, ts_field='timestamp')

        summary_q = db.session.query(
            ServerHealthHourlyRollup.device_id,
            Device.device_name,
            func.avg(ServerHealthHourlyRollup.avg_cpu_usage).label('avg_cpu'),
            func.max(ServerHealthHourlyRollup.max_cpu_usage).label('max_cpu'),
            func.avg(ServerHealthHourlyRollup.avg_memory_usage).label('avg_mem'),
            func.max(ServerHealthHourlyRollup.max_memory_usage).label('max_mem'),
            func.avg(ServerHealthHourlyRollup.avg_disk_usage).label('avg_disk'),
            func.sum(ServerHealthHourlyRollup.sample_count).label('samples'),
        ).join(Device, Device.device_id == ServerHealthHourlyRollup.device_id).filter(
            ServerHealthHourlyRollup.bucket_hour >= start_dt,
            ServerHealthHourlyRollup.bucket_hour <= end_dt,
        )
        if device_ids:
            summary_q = summary_q.filter(ServerHealthHourlyRollup.device_id.in_(device_ids))
        summary_q = summary_q.group_by(ServerHealthHourlyRollup.device_id, Device.device_name)

        summary = [{
            'device_id': r.device_id,
            'device_name': r.device_name,
            'avg_cpu': _safe_round(r.avg_cpu),
            'max_cpu': _safe_round(r.max_cpu),
            'avg_mem': _safe_round(r.avg_mem),
            'max_mem': _safe_round(r.max_mem),
            'avg_disk': _safe_round(r.avg_disk),
            'samples': r.samples,
        } for r in summary_q.all()]

        return series, summary

    def _health_from_daily(self, device_ids, start_dt, end_dt):
        q = db.session.query(
            ServerHealthDailyRollup.device_id,
            Device.device_name,
            ServerHealthDailyRollup.bucket_day.label('timestamp'),
            ServerHealthDailyRollup.avg_cpu_usage.label('cpu_usage'),
            ServerHealthDailyRollup.avg_memory_usage.label('memory_usage'),
            ServerHealthDailyRollup.avg_disk_usage.label('disk_usage'),
            ServerHealthDailyRollup.avg_network_in_bps.label('network_in_bps'),
            ServerHealthDailyRollup.avg_network_out_bps.label('network_out_bps'),
        ).join(Device, Device.device_id == ServerHealthDailyRollup.device_id).filter(
            ServerHealthDailyRollup.bucket_day >= start_dt.date(),
            ServerHealthDailyRollup.bucket_day <= end_dt.date(),
        )
        if device_ids:
            q = q.filter(ServerHealthDailyRollup.device_id.in_(device_ids))
        q = q.order_by(ServerHealthDailyRollup.bucket_day)

        rows = q.all()
        series = self._build_time_series(rows, ts_field='timestamp')

        summary_q = db.session.query(
            ServerHealthDailyRollup.device_id,
            Device.device_name,
            func.avg(ServerHealthDailyRollup.avg_cpu_usage).label('avg_cpu'),
            func.max(ServerHealthDailyRollup.max_cpu_usage).label('max_cpu'),
            func.avg(ServerHealthDailyRollup.avg_memory_usage).label('avg_mem'),
            func.max(ServerHealthDailyRollup.max_memory_usage).label('max_mem'),
            func.avg(ServerHealthDailyRollup.avg_disk_usage).label('avg_disk'),
            func.sum(ServerHealthDailyRollup.sample_count).label('samples'),
        ).join(Device, Device.device_id == ServerHealthDailyRollup.device_id).filter(
            ServerHealthDailyRollup.bucket_day >= start_dt.date(),
            ServerHealthDailyRollup.bucket_day <= end_dt.date(),
        )
        if device_ids:
            summary_q = summary_q.filter(ServerHealthDailyRollup.device_id.in_(device_ids))
        summary_q = summary_q.group_by(ServerHealthDailyRollup.device_id, Device.device_name)

        summary = [{
            'device_id': r.device_id,
            'device_name': r.device_name,
            'avg_cpu': _safe_round(r.avg_cpu),
            'max_cpu': _safe_round(r.max_cpu),
            'avg_mem': _safe_round(r.avg_mem),
            'max_mem': _safe_round(r.max_mem),
            'avg_disk': _safe_round(r.avg_disk),
            'samples': r.samples,
        } for r in summary_q.all()]

        return series, summary

    @staticmethod
    def _build_time_series(rows, ts_field='timestamp'):
        """Group query rows into {device_id: [{timestamp, cpu, mem, ...}]}"""
        by_device = {}
        for r in rows:
            did = r.device_id
            ts_val = getattr(r, ts_field)
            if by_device.get(did) is None:
                by_device[did] = {'device_name': r.device_name, 'points': []}
            by_device[did]['points'].append({
                'ts': ts_val.isoformat() if hasattr(ts_val, 'isoformat') else str(ts_val),
                'cpu': _safe_round(r.cpu_usage),
                'mem': _safe_round(r.memory_usage),
                'disk': _safe_round(r.disk_usage),
                'net_in': _safe_round(r.network_in_bps),
                'net_out': _safe_round(r.network_out_bps),
            })
        return by_device

    # ─────────────────────────────────────────────────────────────
    # NEW: 2. Employee Productivity Report
    # ─────────────────────────────────────────────────────────────
    def get_productivity_report(self, device_ids=None, start_date=None, end_date=None):
        """
        App usage breakdown, active/idle time, per employee/device.
        Uses DeviceApplicationLog + DeviceActivityLog from tracked_device models.
        Categories assigned at query-time via APP_CATEGORIES dict.
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(hours=24)

        # --- App usage ---
        app_q = db.session.query(
            DeviceApplicationLog.device_id,
            TrackedDevice.device_name,
            TrackedDevice.employee_name,
            DeviceApplicationLog.application_name,
            func.sum(DeviceApplicationLog.duration).label('total_seconds'),
            func.count(DeviceApplicationLog.id).label('session_count'),
        ).join(
            TrackedDevice, TrackedDevice.id == DeviceApplicationLog.device_id
        ).filter(
            DeviceApplicationLog.timestamp >= start_date,
            DeviceApplicationLog.timestamp <= end_date,
        )
        if device_ids:
            app_q = app_q.filter(DeviceApplicationLog.device_id.in_(device_ids))
        app_q = app_q.group_by(
            DeviceApplicationLog.device_id,
            TrackedDevice.device_name,
            TrackedDevice.employee_name,
            DeviceApplicationLog.application_name,
        )
        app_rows = app_q.all()

        # Build per-device app breakdown with runtime category
        app_breakdown = {}
        category_totals = {}
        for r in app_rows:
            cat = _classify_app(r.application_name)
            secs = r.total_seconds or 0
            category_totals[cat] = category_totals.get(cat, 0) + secs

            key = r.device_id
            if key not in app_breakdown:
                app_breakdown[key] = {
                    'device_name': r.device_name,
                    'employee_name': r.employee_name,
                    'apps': [],
                }
            app_breakdown[key]['apps'].append({
                'name': r.application_name,
                'category': cat,
                'total_seconds': secs,
                'sessions': r.session_count,
            })

        # --- Active / idle time ---
        activity_q = db.session.query(
            DeviceActivityLog.device_id,
            DeviceActivityLog.activity_type,
            func.sum(DeviceActivityLog.event_count).label('total_events'),
            func.count(DeviceActivityLog.id).label('log_count'),
        ).filter(
            DeviceActivityLog.timestamp >= start_date,
            DeviceActivityLog.timestamp <= end_date,
        )
        if device_ids:
            activity_q = activity_q.filter(DeviceActivityLog.device_id.in_(device_ids))
        activity_q = activity_q.group_by(
            DeviceActivityLog.device_id,
            DeviceActivityLog.activity_type,
        )

        activity_summary = {}
        for r in activity_q.all():
            did = r.device_id
            if did not in activity_summary:
                activity_summary[did] = {'active': 0, 'idle': 0, 'keyboard': 0, 'mouse': 0}
            atype = (r.activity_type or '').lower()
            if atype in activity_summary[did]:
                activity_summary[did][atype] = r.total_events or 0
            elif atype in ('scroll',):
                activity_summary[did]['active'] += (r.total_events or 0)

        return {
            'period': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
            'app_breakdown': app_breakdown,
            'category_totals': category_totals,
            'activity_summary': activity_summary,
        }

    # ─────────────────────────────────────────────────────────────
    # NEW: 3. Network Performance Report
    # ─────────────────────────────────────────────────────────────
    def get_network_performance_report(self, device_ids=None, start_date=None, end_date=None):
        """
        Bandwidth trends, uptime %, MTTR per device.
        Sources: InterfaceTrafficHistory, DailyDeviceStats, DashboardEvent.
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(hours=24)

        # --- Bandwidth trends from InterfaceTrafficHistory ---
        bw_q = db.session.query(
            DeviceInterface.device_id,
            Device.device_name,
            DeviceInterface.name.label('interface_name'),
            InterfaceTrafficHistory.timestamp,
            InterfaceTrafficHistory.rx_bps,
            InterfaceTrafficHistory.tx_bps,
            InterfaceTrafficHistory.rx_utilization_pct,
            InterfaceTrafficHistory.tx_utilization_pct,
        ).join(
            DeviceInterface,
            DeviceInterface.interface_id == InterfaceTrafficHistory.interface_id,
        ).join(
            Device, Device.device_id == DeviceInterface.device_id,
        ).filter(
            InterfaceTrafficHistory.timestamp >= start_date,
            InterfaceTrafficHistory.timestamp <= end_date,
        )
        if device_ids:
            bw_q = bw_q.filter(DeviceInterface.device_id.in_(device_ids))
        bw_q = bw_q.order_by(InterfaceTrafficHistory.timestamp)

        bandwidth = {}
        for r in bw_q.all():
            key = f"{r.device_id}_{r.interface_name}"
            if key not in bandwidth:
                bandwidth[key] = {
                    'device_id': r.device_id,
                    'device_name': r.device_name,
                    'interface': r.interface_name,
                    'points': [],
                }
            bandwidth[key]['points'].append({
                'ts': r.timestamp.isoformat(),
                'rx_bps': _safe_round(r.rx_bps),
                'tx_bps': _safe_round(r.tx_bps),
                'rx_util': _safe_round(r.rx_utilization_pct),
                'tx_util': _safe_round(r.tx_utilization_pct),
            })

        # --- Uptime from DailyDeviceStats ---
        uptime_q = db.session.query(
            DailyDeviceStats.device_id,
            Device.device_name,
            func.avg(DailyDeviceStats.uptime_percent).label('avg_uptime'),
            func.avg(DailyDeviceStats.avg_latency_ms).label('avg_latency'),
            func.avg(DailyDeviceStats.avg_packet_loss_pct).label('avg_packet_loss'),
        ).join(
            Device, Device.device_id == DailyDeviceStats.device_id,
        ).filter(
            DailyDeviceStats.date >= start_date.date(),
            DailyDeviceStats.date <= end_date.date(),
        )
        if device_ids:
            uptime_q = uptime_q.filter(DailyDeviceStats.device_id.in_(device_ids))
        uptime_q = uptime_q.group_by(DailyDeviceStats.device_id, Device.device_name)

        uptime_summary = [{
            'device_id': r.device_id,
            'device_name': r.device_name,
            'avg_uptime': _safe_round(r.avg_uptime),
            'avg_latency_ms': _safe_round(r.avg_latency),
            'avg_packet_loss': _safe_round(r.avg_packet_loss),
        } for r in uptime_q.all()]

        # --- MTTR (Mean Time To Resolve) from DashboardEvent ---
        mttr_q = db.session.query(
            func.avg(
                func.extract('epoch', DashboardEvent.resolved_at) -
                func.extract('epoch', DashboardEvent.timestamp)
            ).label('avg_resolve_seconds'),
            func.count(DashboardEvent.event_id).label('total_incidents'),
        ).filter(
            DashboardEvent.resolved == True,
            DashboardEvent.resolved_at.isnot(None),
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date,
        )
        if device_ids:
            mttr_q = mttr_q.filter(DashboardEvent.device_id.in_(device_ids))
        mttr = mttr_q.first()

        mttr_seconds = round(mttr.avg_resolve_seconds) if mttr.avg_resolve_seconds else 0

        return {
            'period': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
            'bandwidth': bandwidth,
            'uptime_summary': uptime_summary,
            'mttr': {
                'seconds': mttr_seconds,
                'human': str(timedelta(seconds=mttr_seconds)),
                'total_incidents': mttr.total_incidents or 0,
            },
        }

    # ─────────────────────────────────────────────────────────────
    # NEW: 4. Alert History Report
    # ─────────────────────────────────────────────────────────────
    def get_alert_history_report(self, start_date=None, end_date=None,
                                  severity=None, device_ids=None):
        """
        Alert list + per-day trend + TTA/TTR stats + top-10 alerted devices.
        Source: DashboardEvent (the unified alert/event table).
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(days=7)

        base_filter = [
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date,
        ]
        if severity:
            base_filter.append(DashboardEvent.severity == severity.upper())
        if device_ids:
            base_filter.append(DashboardEvent.device_id.in_(device_ids))

        # --- Alert list (paginated to 200) ---
        alerts = DashboardEvent.query.filter(
            *base_filter
        ).order_by(DashboardEvent.timestamp.desc()).limit(200).all()

        alert_list = []
        for a in alerts:
            d = a.to_dict()
            # enrichment: add device name
            device = Device.query.get(a.device_id) if a.device_id else None
            d['device_name'] = device.device_name if device else a.device_ip
            d['resolved_at'] = a.resolved_at.isoformat() if a.resolved_at else None
            alert_list.append(d)

        # --- Alerts per day ---
        daily_q = db.session.query(
            func.date(DashboardEvent.timestamp).label('day'),
            DashboardEvent.severity,
            func.count(DashboardEvent.event_id).label('count'),
        ).filter(*base_filter).group_by(
            func.date(DashboardEvent.timestamp),
            DashboardEvent.severity,
        ).order_by(func.date(DashboardEvent.timestamp))

        daily_trend = {}
        for r in daily_q.all():
            day_str = str(r.day)
            if day_str not in daily_trend:
                daily_trend[day_str] = {}
            daily_trend[day_str][r.severity] = r.count

        # --- TTA / TTR ---
        tta_q = db.session.query(
            func.avg(
                func.extract('epoch', DashboardEvent.acknowledged_at) -
                func.extract('epoch', DashboardEvent.timestamp)
            ).label('avg_tta'),
        ).filter(
            *base_filter,
            DashboardEvent.is_acknowledged == True,
        ).first()

        ttr_q = db.session.query(
            func.avg(
                func.extract('epoch', DashboardEvent.resolved_at) -
                func.extract('epoch', DashboardEvent.timestamp)
            ).label('avg_ttr'),
        ).filter(
            *base_filter,
            DashboardEvent.resolved == True,
            DashboardEvent.resolved_at.isnot(None),
        ).first()

        tta_sec = round(tta_q.avg_tta) if tta_q.avg_tta else 0
        ttr_sec = round(ttr_q.avg_ttr) if ttr_q.avg_ttr else 0

        # --- Top 10 most alerted devices ---
        top_q = db.session.query(
            DashboardEvent.device_id,
            Device.device_name,
            Device.device_ip,
            func.count(DashboardEvent.event_id).label('alert_count'),
        ).outerjoin(
            Device, Device.device_id == DashboardEvent.device_id,
        ).filter(*base_filter).group_by(
            DashboardEvent.device_id, Device.device_name, Device.device_ip,
        ).order_by(desc('alert_count')).limit(10)

        top_devices = [{
            'device_id': r.device_id,
            'device_name': r.device_name or 'Unknown',
            'device_ip': r.device_ip or '',
            'alert_count': r.alert_count,
        } for r in top_q.all()]

        # --- Severity breakdown ---
        sev_q = db.session.query(
            DashboardEvent.severity,
            func.count(DashboardEvent.event_id).label('count'),
        ).filter(*base_filter).group_by(DashboardEvent.severity)

        severity_breakdown = {r.severity: r.count for r in sev_q.all()}

        return {
            'period': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
            'alerts': alert_list,
            'daily_trend': daily_trend,
            'tta': {'seconds': tta_sec, 'human': str(timedelta(seconds=tta_sec))},
            'ttr': {'seconds': ttr_sec, 'human': str(timedelta(seconds=ttr_sec))},
            'top_alerted_devices': top_devices,
            'severity_breakdown': severity_breakdown,
        }

