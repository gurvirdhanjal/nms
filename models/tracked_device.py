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
    site_id = db.Column(db.Integer, db.ForeignKey('sites.id', ondelete='SET NULL'), nullable=True, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
    department = db.Column(db.String(100))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    maintenance_mode = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False, index=True)
    archived_at = db.Column(db.DateTime)
    archived_reason = db.Column(db.Text)
    archived_by = db.Column(db.String(100))
    
    # Live Tracking Cache Data
    availability_status = db.Column(db.String(20), default='offline')
    tracking_data = db.Column(db.Text)  # JSON dump of tracking stats
    metrics_available = db.Column(db.Boolean, default=False)
    probe_error_code = db.Column(db.String(50))
    probe_method = db.Column(db.String(50))
    last_probe_at = db.Column(db.DateTime)
    last_agent_sync_at = db.Column(db.DateTime, index=True)
    last_agent_sync_ip = db.Column(db.String(45))
    last_policy_version_seen = db.Column(db.String(128), index=True)
    last_policy_sync_at = db.Column(db.DateTime, index=True)

    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    activity_logs = db.relationship('DeviceActivityLog', backref='device', lazy=True, cascade='all, delete-orphan')
    resource_logs = db.relationship('DeviceResourceLog', backref='device', lazy=True, cascade='all, delete-orphan')
    application_logs = db.relationship('DeviceApplicationLog', backref='device', lazy=True, cascade='all, delete-orphan')
    tracking_samples = db.relationship('TrackingSample', backref='device', lazy=True, cascade='all, delete-orphan')
    availability_events = db.relationship(
        'TrackedDeviceAvailabilityEvent',
        backref='device',
        lazy=True,
        cascade='all, delete-orphan',
    )
    ip_history_entries = db.relationship(
        'TrackedDeviceIpHistory',
        backref='device',
        lazy=True,
        cascade='all, delete-orphan',
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'mac_address': self.mac_address,
            'unique_client_id': self.unique_client_id,
            'device_name': self.device_name,
            'employee_name': self.employee_name,
            'hostname': self.hostname,
            'ip_address': self.ip_address,
            'site_id': self.site_id,
            'department_id': self.department_id,
            'department': self.department,
            'notes': self.notes,
            'is_active': self.is_active,
            'maintenance_mode': self.maintenance_mode,
            'is_archived': self.is_archived,
            'archived_at': self.archived_at.isoformat() if self.archived_at else None,
            'archived_reason': self.archived_reason,
            'archived_by': self.archived_by,
            'availability_status': self.availability_status,
            'tracking_data': self.tracking_data,
            'metrics_available': self.metrics_available,
            'probe_error_code': self.probe_error_code,
            'probe_method': self.probe_method,
            'last_probe_at': self.last_probe_at.isoformat() if self.last_probe_at else None,
            'last_agent_sync_at': self.last_agent_sync_at.isoformat() if self.last_agent_sync_at else None,
            'last_agent_sync_ip': self.last_agent_sync_ip,
            'last_policy_version_seen': self.last_policy_version_seen,
            'last_policy_sync_at': self.last_policy_sync_at.isoformat() if self.last_policy_sync_at else None,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f'<TrackedDevice {self.device_name} ({self.mac_address})>'

class RemoteDeviceScanHistory(db.Model):
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


class TrackedDeviceIpHistory(db.Model):
    __tablename__ = 'tracked_device_ip_history'
    __table_args__ = (
        db.Index('ix_tracking_ip_history_device_changed', 'device_id', 'changed_at_utc'),
        db.Index('ix_tracking_ip_history_agent_key_changed', 'agent_key_id', 'changed_at_utc'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False, index=True)
    old_ip = db.Column(db.String(45))
    new_ip = db.Column(db.String(45))
    resolved_ip = db.Column(db.String(45))
    payload_ip = db.Column(db.String(45))
    payload_candidates_json = db.Column(db.Text)
    transport_remote_ip = db.Column(db.String(64))
    transport_forwarded_for = db.Column(db.String(255))
    agent_key_id = db.Column(db.String(64))
    reason = db.Column(db.String(40), nullable=False, default='SYNC_PAYLOAD_UPDATE')
    ip_source = db.Column(db.String(40))
    network_signature = db.Column(db.String(128))
    changed_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    received_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'old_ip': self.old_ip,
            'new_ip': self.new_ip,
            'resolved_ip': self.resolved_ip,
            'payload_ip': self.payload_ip,
            'payload_candidates_json': self.payload_candidates_json,
            'transport_remote_ip': self.transport_remote_ip,
            'transport_forwarded_for': self.transport_forwarded_for,
            'agent_key_id': self.agent_key_id,
            'reason': self.reason,
            'ip_source': self.ip_source,
            'network_signature': self.network_signature,
            'changed_at_utc': self.changed_at_utc.isoformat() if self.changed_at_utc else None,
            'received_at_utc': self.received_at_utc.isoformat() if self.received_at_utc else None,
        }


class TrackingSample(db.Model):
    __tablename__ = 'tracking_samples'
    __table_args__ = (
        db.UniqueConstraint('device_id', 'idempotency_key', name='uq_tracking_samples_device_idempotency'),
        db.Index('ix_tracking_samples_device_sampled_id', 'device_id', 'sampled_at', 'id'),
        db.Index('ix_tracking_samples_device_received_id', 'device_id', 'received_at', 'id'),
        db.Index('ix_tracking_samples_source_received', 'source', 'received_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False, index=True)
    sample_uuid = db.Column(db.String(64), index=True)
    idempotency_key = db.Column(db.String(255), nullable=False)
    sampled_at = db.Column(db.DateTime)
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    source = db.Column(db.String(20), nullable=False, default='sync')
    schema_version = db.Column(db.String(20), nullable=False, default='1')
    integrity_status = db.Column(db.String(20), nullable=False, default='verified')
    integrity_notes = db.Column(db.JSON)
    received_minute_bucket = db.Column(db.DateTime, index=True)
    payload_hash = db.Column(db.String(64))
    previous_sample_id = db.Column(db.Integer, db.ForeignKey('tracking_samples.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    previous_sample = db.relationship('TrackingSample', remote_side=[id], uselist=False)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'sample_uuid': self.sample_uuid,
            'idempotency_key': self.idempotency_key,
            'sampled_at': self.sampled_at.isoformat() if self.sampled_at else None,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'source': self.source,
            'schema_version': self.schema_version,
            'integrity_status': self.integrity_status,
            'integrity_notes': self.integrity_notes,
            'received_minute_bucket': self.received_minute_bucket.isoformat() if self.received_minute_bucket else None,
            'payload_hash': self.payload_hash,
            'previous_sample_id': self.previous_sample_id,
        }


class TrackingHistoryIntegrityAudit(db.Model):
    __tablename__ = 'tracking_history_integrity_audit'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.String(64), nullable=False, index=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=True, index=True)
    check_name = db.Column(db.String(100), nullable=False, index=True)
    severity = db.Column(db.String(20), nullable=False, index=True)
    details = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            'id': self.id,
            'run_id': self.run_id,
            'device_id': self.device_id,
            'check_name': self.check_name,
            'severity': self.severity,
            'details': self.details,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class TrackedDeviceAvailabilityEvent(db.Model):
    __tablename__ = 'tracked_device_availability_events'
    __table_args__ = (
        db.Index(
            'ix_tracking_availability_device_observed_id',
            'device_id',
            'observed_at',
            'id',
        ),
        db.Index(
            'ix_tracking_availability_device_status_observed',
            'device_id',
            'status',
            'observed_at',
        ),
        db.Index('ix_tracking_availability_observed_at', 'observed_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False, index=True)
    sample_id = db.Column(db.Integer, db.ForeignKey('tracking_samples.id'), index=True)
    observed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    status = db.Column(db.String(20), nullable=False, index=True)
    event_type = db.Column(db.String(20), nullable=False, default='status_change')
    source = db.Column(db.String(20), nullable=False, default='unknown')
    probe_method = db.Column(db.String(50))
    probe_error_code = db.Column(db.String(50))
    metrics_available = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'sample_id': self.sample_id,
            'observed_at': self.observed_at.isoformat() if self.observed_at else None,
            'status': self.status,
            'event_type': self.event_type,
            'source': self.source,
            'probe_method': self.probe_method,
            'probe_error_code': self.probe_error_code,
            'metrics_available': bool(self.metrics_available),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class TrackingHourlyRollup(db.Model):
    __tablename__ = 'tracking_hourly_rollups'
    __table_args__ = (
        db.UniqueConstraint('device_id', 'bucket_hour', name='uq_tracking_hourly_rollups_device_bucket'),
        db.Index('ix_tracking_hourly_rollups_bucket', 'bucket_hour'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False, index=True)
    bucket_hour = db.Column(db.DateTime, nullable=False)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    active_seconds = db.Column(db.Integer, nullable=False, default=0)
    keyboard_events = db.Column(db.Integer, nullable=False, default=0)
    mouse_events = db.Column(db.Integer, nullable=False, default=0)
    cpu_avg = db.Column(db.Float)
    memory_avg = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrackingDailyRollup(db.Model):
    __tablename__ = 'tracking_daily_rollups'
    __table_args__ = (
        db.UniqueConstraint('device_id', 'bucket_day', name='uq_tracking_daily_rollups_device_bucket'),
        db.Index('ix_tracking_daily_rollups_bucket', 'bucket_day'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False, index=True)
    bucket_day = db.Column(db.Date, nullable=False)
    sample_count = db.Column(db.Integer, nullable=False, default=0)
    active_seconds = db.Column(db.Integer, nullable=False, default=0)
    keyboard_events = db.Column(db.Integer, nullable=False, default=0)
    mouse_events = db.Column(db.Integer, nullable=False, default=0)
    cpu_avg = db.Column(db.Float)
    memory_avg = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DeviceActivityLog(db.Model):
    __tablename__ = 'device_activity_logs'

    __table_args__ = (
        db.Index('idx_device_activity_logs_device_timestamp', 'device_id', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False)
    sample_id = db.Column(db.Integer, db.ForeignKey('tracking_samples.id'), index=True)
    # primary_key=True matches composite PK (id, timestamp) required by TimescaleDB hypertable.
    timestamp = db.Column(db.DateTime, primary_key=True, default=datetime.utcnow)
    activity_type = db.Column(db.String(20), nullable=False)  # keyboard, mouse, scroll, idle, active
    event_count = db.Column(db.Integer, default=0)
    details = db.Column(db.Text)  # JSON details
    current_application = db.Column(db.Text)  # active window/app name at sample time
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'sample_id': self.sample_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'activity_type': self.activity_type,
            'event_count': self.event_count,
            'details': self.details
        }

class DeviceResourceLog(db.Model):
    __tablename__ = 'device_resource_logs'

    __table_args__ = (
        db.Index('idx_device_resource_logs_device_timestamp', 'device_id', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False)
    sample_id = db.Column(db.Integer, db.ForeignKey('tracking_samples.id'), index=True)
    # primary_key=True matches composite PK (id, timestamp) required by TimescaleDB hypertable.
    timestamp = db.Column(db.DateTime, primary_key=True, default=datetime.utcnow)
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
            'sample_id': self.sample_id,
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

    __table_args__ = (
        db.Index('idx_device_application_logs_device_timestamp', 'device_id', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'), nullable=False)
    sample_id = db.Column(db.Integer, db.ForeignKey('tracking_samples.id'), index=True)
    # primary_key=True matches composite PK (id, timestamp) required by TimescaleDB hypertable.
    timestamp = db.Column(db.DateTime, primary_key=True, default=datetime.utcnow)
    application_name = db.Column(db.String(200), nullable=False)
    window_title = db.Column(db.String(500))
    duration = db.Column(db.Integer)  # seconds
    status = db.Column(db.String(20))  # opened, closed, active
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'sample_id': self.sample_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'application_name': self.application_name,
            'window_title': self.window_title,
            'duration': self.duration,
            'status': self.status}
