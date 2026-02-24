from extensions import db
from datetime import datetime

class TrackedDevice(db.Model):
    __tablename__ = 'tracked_devices'
    
    id = db.Column(db.Integer, primary_key=True)
    mac_address = db.Column(db.String(17), unique=True, nullable=False, index=True)
    unique_client_id = db.Column(db.String(36), unique=True, index=True) # UUID is 36 chars
    device_name = db.Column(db.String(100), nullable=False)
    employee_name = db.Column(db.String(100))
    hostname = db.Column(db.String(100))
    ip_address = db.Column(db.String(15))
    department = db.Column(db.String(100))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    maintenance_mode = db.Column(db.Boolean, default=False)
    
    # Live Tracking Cache Data
    availability_status = db.Column(db.String(20), default='offline')
    tracking_data = db.Column(db.Text)  # JSON dump of tracking stats
    metrics_available = db.Column(db.Boolean, default=False)
    probe_error_code = db.Column(db.String(50))
    probe_method = db.Column(db.String(50))
    last_probe_at = db.Column(db.DateTime)

    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    activity_logs = db.relationship('DeviceActivityLog', backref='device', lazy=True, cascade='all, delete-orphan')
    resource_logs = db.relationship('DeviceResourceLog', backref='device', lazy=True, cascade='all, delete-orphan')
    application_logs = db.relationship('DeviceApplicationLog', backref='device', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'mac_address': self.mac_address,
            'unique_client_id': self.unique_client_id,
            'device_name': self.device_name,
            'employee_name': self.employee_name,
            'hostname': self.hostname,
            'ip_address': self.ip_address,
            'department': self.department,
            'notes': self.notes,
            'is_active': self.is_active,
            'maintenance_mode': self.maintenance_mode,
            'availability_status': self.availability_status,
            'tracking_data': self.tracking_data,
            'metrics_available': self.metrics_available,
            'probe_error_code': self.probe_error_code,
            'probe_method': self.probe_method,
            'last_probe_at': self.last_probe_at.isoformat() if self.last_probe_at else None,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f'<TrackedDevice {self.device_name} ({self.mac_address})>'

class DeviceScanHistory(db.Model):
    __tablename__ = 'device_scan_history_remote'
    
    id = db.Column(db.Integer, primary_key=True)
    mac_address = db.Column(db.String(17), nullable=False)
    scan_timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(15))
    status = db.Column(db.String(20))  # online, offline, port_open
    response_data = db.Column(db.Text)  # JSON response from device
    
    def to_dict(self):
        return {
            'id': self.id,
            'mac_address': self.mac_address,
            'scan_timestamp': self.scan_timestamp.isoformat() if self.scan_timestamp else None,
            'ip_address': self.ip_address,
            'status': self.status,
            'response_data': self.response_data
        }

class DeviceActivityLog(db.Model):
    __tablename__ = 'device_activity_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    activity_type = db.Column(db.String(20), nullable=False)  # keyboard, mouse, scroll, idle, active
    event_count = db.Column(db.Integer, default=0)
    details = db.Column(db.Text)  # JSON details
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'activity_type': self.activity_type,
            'event_count': self.event_count,
            'details': self.details
        }

class DeviceResourceLog(db.Model):
    __tablename__ = 'device_resource_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    cpu_usage = db.Column(db.Float)  # percentage
    memory_usage = db.Column(db.Float)  # percentage
    disk_usage = db.Column(db.Float)  # percentage
    network_usage = db.Column(db.Float)  # MB/s (Total)
    upload_kbps = db.Column(db.Float)  # KB/s
    download_kbps = db.Column(db.Float)  # KB/s
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'disk_usage': self.disk_usage,
            'network_usage': self.network_usage,
            'upload_kbps': self.upload_kbps,
            'download_kbps': self.download_kbps
        }

class DeviceApplicationLog(db.Model):
    __tablename__ = 'device_application_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    application_name = db.Column(db.String(200), nullable=False)
    window_title = db.Column(db.String(500))
    duration = db.Column(db.Integer)  # seconds
    status = db.Column(db.String(20))  # opened, closed, active
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'application_name': self.application_name,
            'window_title': self.window_title,
            'duration': self.duration,
            'status': self.status}