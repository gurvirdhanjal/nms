"""
Dashboard-specific database models.
These are ADDITIVE tables that do not modify any existing schema.
"""
from extensions import db
from datetime import datetime, date

class DashboardEvent(db.Model):
    """
    Persists events for the dashboard timeline and alert history.
    Replaces the in-memory EventManager storage.
    """
    __tablename__ = 'dashboard_events'
    
    event_id = db.Column(db.String(36), primary_key=True)  # UUID
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=True)
    device_ip = db.Column(db.String(50), nullable=True)  # For quick lookups
    event_type = db.Column(db.String(50), default='THRESHOLD')  # STATUS_CHANGE, THRESHOLD, SYSTEM
    severity = db.Column(db.String(20), default='INFO')  # CRITICAL, WARNING, INFO, OK
    metric_name = db.Column(db.String(100), nullable=True)
    message = db.Column(db.Text, nullable=True)
    value = db.Column(db.Float, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    resolved = db.Column(db.Boolean, default=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    # Phase 1D: Acknowledgment
    is_acknowledged = db.Column(db.Boolean, default=False)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    acknowledged_by = db.Column(db.String(100), nullable=True)
    
    def __repr__(self):
        return f'<DashboardEvent {self.event_id[:8]} - {self.severity}>'
    
    def to_dict(self):
        return {
            'event_id': self.event_id,
            'device_id': self.device_id,
            'device_ip': self.device_ip,
            'event_type': self.event_type,
            'severity': self.severity,
            'metric_name': self.metric_name,
            'message': self.message,
            'value': self.value,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'resolved': self.resolved,
            'is_acknowledged': self.is_acknowledged,
            'acknowledged_at': self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            'acknowledged_by': self.acknowledged_by
        }


class DailyDeviceStats(db.Model):
    """
    Pre-aggregated daily statistics for fast historical queries.
    Populated by a nightly rollup job from DeviceScanHistory.
    """
    __tablename__ = 'daily_device_stats'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    
    # Aggregated metrics
    uptime_percent = db.Column(db.Float, default=0.0)
    avg_latency_ms = db.Column(db.Float, nullable=True)
    max_latency_ms = db.Column(db.Float, nullable=True)
    min_latency_ms = db.Column(db.Float, nullable=True)
    avg_packet_loss_pct = db.Column(db.Float, default=0.0)
    total_scans = db.Column(db.Integer, default=0)
    online_scans = db.Column(db.Integer, default=0)
    total_alerts = db.Column(db.Integer, default=0)
    
    # Composite index for fast lookups
    __table_args__ = (
        db.Index('idx_daily_stats_device_date', 'device_id', 'date'),
    )
    
    def __repr__(self):
        return f'<DailyDeviceStats device={self.device_id} date={self.date}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'date': self.date.isoformat() if self.date else None,
            'uptime_percent': self.uptime_percent,
            'avg_latency_ms': self.avg_latency_ms,
            'max_latency_ms': self.max_latency_ms,
            'min_latency_ms': self.min_latency_ms,
            'avg_packet_loss_pct': self.avg_packet_loss_pct,
            'total_scans': self.total_scans,
            'online_scans': self.online_scans,
            'total_alerts': self.total_alerts
        }
