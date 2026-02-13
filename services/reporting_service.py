from extensions import db
from models.device import Device
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from sqlalchemy import func, case, desc, and_, extract
from datetime import datetime, timedelta

class ReportingService:
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
        # Aggregate from DailyDeviceStats for the period
        uptime_stats = db.session.query(
            func.avg(DailyDeviceStats.uptime_percent).label('avg_uptime'),
            func.avg(DailyDeviceStats.avg_latency_ms).label('avg_latency')
        ).filter(
            DailyDeviceStats.date >= start_date.date(),
            DailyDeviceStats.date <= end_date.date()
        ).first()

        uptime_score = round(uptime_stats.avg_uptime, 2) if uptime_stats.avg_uptime else 100.0
        avg_latency = round(uptime_stats.avg_latency, 2) if uptime_stats.avg_latency else 0.0

        # 2. Health Distribution (Snapshot of CURRENT state)
        # We can use the latest scan status or the 'Device' table status if we trust it
        # Let's use Device table for speed
        total_devices = Device.query.count()
        # For simplicity, we'll categorize based on recent availability if possible, 
        # but 'Device' model doesn't store 'status' directly in a simple enum often.
        # We'll use the latest scan history for accuracy if needed, or simple query if 'is_active' implies health.
        # Actually, let's use the Dashboard logic for health counts (Online, Offline, Warning)
        # We will assume 'Online' is healthy for now.
        
        # We need a quick way to get current status. 
        # Let's look at the latest scan for each device.
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

        health_counts = {
            'Healthy': 0,
            'Critical': 0, # Offline
            'Warning': 0   # High latency/packet loss (implied)
        }
        
        # Simple classification
        for scan in latest_scans:
            status = (scan.status or '').lower()
            if status == 'online':
                health_counts['Healthy'] += 1
            else:
                health_counts['Critical'] += 1
        
        # 3. SLA Tracking (MTTA - Mean Time To Acknowledge)
        # Avg time between timestamp and acknowledged_at for CRITICAL events
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
        
        # 4. Top 10 Problematic Devices (Lowest Uptime)
        # rank by average uptime_percent ascending
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

    def get_operational_report(self, start_date=None, end_date=None):
        """
        Generates the Operational Activity & Compliance Report.
        Target metrics:
        1. Employee Activity Heatmap (derived from ServerHealthLog timestamps)
        2. Configuration/System Audit Log (DashboardEvents of type SYSTEM/CONFIG)
        3. Recently Added Devices (Device.created_at)
        """
        if not end_date:
            end_date = datetime.utcnow()
        if not start_date:
            start_date = end_date - timedelta(days=30)
            
        # 1. Employee Activity Heatmap (Day x Hour)
        # We aggregate ServerHealthLogs to see when devices are reporting data
        # Using SQLAlchemy extract for DB agnostic (works for Postgres 'dow' and 'hour')
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
        
        # Format for frontend: [day, hour, value]
        formatted_heatmap = []
        for row in heatmap_data:
            if row.day_of_week is not None and row.hour_of_day is not None:
                formatted_heatmap.append([
                    int(row.day_of_week),
                    int(row.hour_of_day),
                    row.activity_count
                ])

        # 2. Audit Log (Configuration & System Changes)
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
