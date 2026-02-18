"""
SNMP Configuration model - stores SNMP credentials per device.
This is an ADDITIVE table that does not modify the existing Device table.
"""
from extensions import db
from datetime import datetime

class DeviceSnmpConfig(db.Model):
    """
    Stores SNMP configuration for devices that support SNMP polling.
    One-to-one relationship with Device (by device_id).
    """
    __tablename__ = 'device_snmp_config'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='CASCADE'),
        unique=True,
        nullable=False
    )
    
    # SNMP v1/v2c settings
    community_string = db.Column(db.String(100), default='public')
    snmp_version = db.Column(db.String(10), default='2c')  # '1', '2c', '3'
    snmp_port = db.Column(db.Integer, default=161)
    
    # SNMP v3 settings (optional)
    security_name = db.Column(db.String(100), nullable=True)
    auth_protocol = db.Column(db.String(20), nullable=True)  # MD5, SHA
    auth_password = db.Column(db.String(100), nullable=True)
    priv_protocol = db.Column(db.String(20), nullable=True)  # DES, AES
    priv_password = db.Column(db.String(100), nullable=True)
    
    # Polling settings
    poll_interval_seconds = db.Column(db.Integer, default=300)  # 5 minutes
    is_enabled = db.Column(db.Boolean, default=True)
    last_successful_poll = db.Column(db.DateTime, nullable=True)
    last_poll_error = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<DeviceSnmpConfig device_id={self.device_id} version={self.snmp_version}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'community_string': '***',  # Hide in API responses
            'snmp_version': self.snmp_version,
            'snmp_port': self.snmp_port,
            'poll_interval_seconds': self.poll_interval_seconds,
            'is_enabled': self.is_enabled,
            'last_successful_poll': self.last_successful_poll.isoformat() if self.last_successful_poll else None,
            'last_poll_error': self.last_poll_error
        }
