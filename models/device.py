from extensions import db
from datetime import datetime

class Device(db.Model):
    device_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_name = db.Column(db.String(100), nullable=False)
    device_type = db.Column(db.String(100), nullable=False)
    device_ip = db.Column(db.String(50), nullable=False)
    port = db.Column(db.String(50), nullable=True)
    rstplink = db.Column(db.String(100), nullable=True)
    macaddress = db.Column(db.String(50), nullable=True)
    hostname = db.Column(db.String(100), nullable=True)
    manufacturer = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    is_monitored = db.Column(db.Boolean, default=True)

    # Switch Specific
    switch_brand = db.Column(db.String(50), nullable=True) # Cisco, Juniper, Aruba, etc.

    # Classification fields
    confidence_score = db.Column(db.Integer, default=0)
    classification_confidence = db.Column(db.String(20), default="Low") # High, Medium, Low
    classification_details = db.Column(db.Text, nullable=True) # JSON reasoning
    
    # Phase 3: Transport & Capability
    transport_type = db.Column(db.String(20), default='SNMP') # SNMP, API, SSH
    if_index_map = db.Column(db.JSON, nullable=True) # Stores ifIndex -> canonical_name mapping
    # ssh_profile_id = db.Column(db.Integer, db.ForeignKey('ssh_profiles.profile_id'), nullable=True)
    
    # Phase 3: Infrastructure Mapping & Topology
    parent_switch_id = db.Column(db.Integer, db.ForeignKey('device.device_id'), nullable=True)
    parent_port_id = db.Column(db.Integer, db.ForeignKey('device_interfaces.interface_id'), nullable=True)
    last_discovery_method = db.Column(db.String(50), nullable=True) # LLDP, CDP, SSH-CAM, etc.
    
    # Intelligence & Classification
    cos_tier = db.Column(db.String(20), default='Standard') # Critical, Standard, Low
    
    # Relationships
    # ssh_profile = db.relationship('SSHProfile', backref=db.backref('devices', lazy=True))
    
    # Explicitly specify foreign_keys to resolve AmbiguousForeignKeysError
    child_devices = db.relationship(
        'Device',
        backref=db.backref('parent_switch', remote_side=[device_id]),
        foreign_keys=[parent_switch_id]
    )
    
    # Relationship to parent port (The specific interface on the parent switch)
    parent_port = db.relationship(
        'DeviceInterface',
        foreign_keys=[parent_port_id],
        backref=db.backref('connected_downstream_devices', lazy=True)
    )

    def __repr__(self):
        return f'<Device {self.device_name} ({self.device_ip})>'
    
    def to_dict(self):
        return {
            'device_id': self.device_id,
            'device_name': self.device_name,
            'device_type': self.device_type,
            'device_ip': self.device_ip,
            'port': self.port,
            'macaddress': self.macaddress,
            'hostname': self.hostname,
            'manufacturer': self.manufacturer,
            'is_monitored': self.is_monitored,
            'confidence_score': self.confidence_score,
            'classification_confidence': self.classification_confidence,
            'classification_details': self.classification_details,
            'switch_brand': self.switch_brand,
            'cos_tier': self.cos_tier,
            'parent_switch_id': self.parent_switch_id,
            'parent_port_id': self.parent_port_id
        }