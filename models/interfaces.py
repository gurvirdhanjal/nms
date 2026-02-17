from extensions import db
from datetime import datetime

class DeviceInterface(db.Model):
    __tablename__ = 'device_interfaces'
    
    interface_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id'), nullable=False)
    
    # SNMP Data
    if_index = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=True) # ifDescr
    alias = db.Column(db.String(100), nullable=True) # ifAlias
    canonical_name = db.Column(db.String(100), nullable=True) # Phase 3: Stable ID (e.g. Gi0/1)
    if_type = db.Column(db.Integer, nullable=True)
    speed_bps = db.Column(db.BigInteger, nullable=True)
    high_speed_bps = db.Column(db.BigInteger, nullable=True) # Phase 3: 64-bit capacity
    mac_address = db.Column(db.String(20), nullable=True)
    admin_status = db.Column(db.String(20), nullable=True)
    oper_status = db.Column(db.String(20), nullable=True)
    
    # Last Poll Data (for Rate Calculation)
    last_poll_time = db.Column(db.DateTime, nullable=True)
    last_counter_reset = db.Column(db.DateTime, nullable=True) # Phase 3: Detect reboots
    last_in_octets = db.Column(db.BigInteger, nullable=True)
    last_out_octets = db.Column(db.BigInteger, nullable=True)
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    device = db.relationship(
        'Device',
        backref=db.backref('interfaces', lazy=True, cascade='all, delete-orphan'),
        foreign_keys=[device_id]
    )

    traffic_history = db.relationship(
        'InterfaceTrafficHistory',
        backref=db.backref('interface', lazy=True),
        cascade='all, delete-orphan',
        lazy=True
    )
    
    __table_args__ = (
        db.UniqueConstraint('device_id', 'if_index', name='uq_device_interface'),
        db.Index('idx_device_interfaces_device_id', 'device_id'),
    )

    def __repr__(self):
        return f'<Interface {self.name} on Device {self.device_id}>'

class InterfaceTrafficHistory(db.Model):
    __tablename__ = 'interface_traffic_history'
    
    history_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    interface_id = db.Column(db.Integer, db.ForeignKey('device_interfaces.interface_id'), nullable=False)
    
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Rates (Calculated)
    rx_bps = db.Column(db.Float, default=0.0) # Received Bits/sec
    tx_bps = db.Column(db.Float, default=0.0) # Transmitted Bits/sec
    
    rx_utilization_pct = db.Column(db.Float, nullable=True)
    tx_utilization_pct = db.Column(db.Float, nullable=True)

    __table_args__ = (
        db.Index('idx_interface_traffic_interface_timestamp', 'interface_id', 'timestamp'),
    )
    
    def __repr__(self):
        return f'<TrafficHistory IF-{self.interface_id} @ {self.timestamp}>'
