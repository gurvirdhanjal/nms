from extensions import db
from datetime import datetime

class DeviceScanHistory(db.Model):
    scan_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_ip = db.Column(db.String(50), nullable=False, index=True)
    device_name = db.Column(db.String(100), nullable=True)
    ping_time_ms = db.Column(db.Float, nullable=True)   # avg RTT across probes
    min_rtt = db.Column(db.Float, nullable=True)         # min RTT across probes
    max_rtt = db.Column(db.Float, nullable=True)         # max RTT across probes
    status = db.Column(db.String(20), nullable=False, index=True)  # online, offline
    status_detail = db.Column(db.String(100), nullable=True)
    packet_loss = db.Column(db.Float, default=0.0)
    jitter = db.Column(db.Float, nullable=True)
    scan_timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    scan_type = db.Column(db.String(20), default='scheduled')  # scheduled, manual

    __table_args__ = (
        db.Index('idx_device_scan_history_status_time', 'status', 'scan_timestamp'),
        db.Index('idx_device_scan_history_ip_time', 'device_ip', 'scan_timestamp'),
    )
    
    def __repr__(self):
        return f'<DeviceScanHistory {self.device_ip} - {self.status}>'

class NetworkScan(db.Model):
    scan_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ip_range = db.Column(db.String(50), nullable=False)
    total_devices_found = db.Column(db.Integer, default=0)
    online_devices = db.Column(db.Integer, default=0)
    scan_duration = db.Column(db.Float)  # in seconds
    initiated_by = db.Column(db.String(80), nullable=False)
    scan_timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_network_scan_timestamp', 'scan_timestamp'),
        db.Index('idx_network_scan_ip_range_timestamp', 'ip_range', 'scan_timestamp'),
    )

    def __repr__(self):
        return f'<NetworkScan {self.ip_range} - {self.total_devices_found} devices>'

class PortScanResult(db.Model):
    port_scan_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_ip = db.Column(db.String(50), nullable=False)
    port_number = db.Column(db.Integer, nullable=False)
    protocol = db.Column(db.String(10), default='TCP')
    status = db.Column(db.String(20), nullable=False)  # open, closed, filtered
    service_name = db.Column(db.String(50), nullable=True)
    banner = db.Column(db.Text, nullable=True)
    scan_timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_port_scan_result_device_ip', 'device_ip'),
        db.Index('idx_port_scan_result_device_ip_timestamp', 'device_ip', 'scan_timestamp'),
    )

    def __repr__(self):
        return f'<PortScanResult {self.device_ip}:{self.port_number} - {self.status}>'
