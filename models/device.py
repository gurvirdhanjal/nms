from extensions import db
from datetime import datetime

class Device(db.Model):
    device_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_name = db.Column(db.String(100), nullable=False)
    device_type = db.Column(db.String(100), nullable=False)
    device_ip = db.Column(db.String(50), nullable=False, index=True)
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
    parent_switch_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='SET NULL'), nullable=True)
    parent_port_id = db.Column(db.Integer, db.ForeignKey('device_interfaces.interface_id', ondelete='SET NULL'), nullable=True)
    last_discovery_method = db.Column(db.String(50), nullable=True) # LLDP, CDP, SSH-CAM, etc.
    
    # Intelligence & Classification
    cos_tier = db.Column(db.String(20), default='Standard') # Critical, Standard, Low

    # Maintenance & Health Alert Tracking
    maintenance_mode = db.Column(db.Boolean, default=False)  # Suppress alerts when True
    health_alert_strikes = db.Column(db.Integer, default=0)   # Consecutive health threshold breaches
    offline_strikes = db.Column(db.Integer, default=0)        # Consecutive offline checks (for 3-strike rule)
    latency_strikes = db.Column(db.Integer, default=0)        # Consecutive high-latency scans
    packet_loss_strikes = db.Column(db.Integer, default=0)    # Consecutive high-packet-loss scans
    
    # Enhanced Identity
    subnet_cidr = db.Column(db.String(50), nullable=True, index=True)  # e.g. "172.16.1.0/24"
    location = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    
    # Monitoring Configuration
    monitoring_mode = db.Column(db.String(20), default='ping') # ping, snmp, agent, wmi
    
    # SNMP Configuration
    snmp_version = db.Column(db.String(10), default='v2c') # v2c, v3
    snmp_port = db.Column(db.Integer, default=161)
    snmp_timeout = db.Column(db.Integer, default=2)
    snmp_retries = db.Column(db.Integer, default=1)
    snmp_community = db.Column(db.String(100), nullable=True) # v2c
    snmp_username = db.Column(db.String(100), nullable=True) # v3
    snmp_auth_proto = db.Column(db.String(10), nullable=True) # SHA, MD5
    snmp_auth_password = db.Column(db.String(100), nullable=True)
    snmp_priv_proto = db.Column(db.String(10), nullable=True) # AES, DES
    snmp_priv_password = db.Column(db.String(100), nullable=True)
    
    # Agent Configuration
    agent_token = db.Column(db.String(100), nullable=True)
    agent_interval = db.Column(db.Integer, default=300) # seconds
    agent_os_type = db.Column(db.String(20), nullable=True) # windows, linux
    hardware_specs = db.Column(db.JSON, nullable=True)
    
    # WMI Configuration
    wmi_username = db.Column(db.String(100), nullable=True)
    wmi_password = db.Column(db.String(100), nullable=True)
    wmi_domain = db.Column(db.String(100), nullable=True)
    
    # Device Credentials (for SSH/API/general access)
    device_username = db.Column(db.String(100), nullable=True)
    device_password_hash = db.Column(db.String(256), nullable=True)  # werkzeug pbkdf2 hash
    
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

    server_health_logs = db.relationship(
        'ServerHealthLog',
        backref=db.backref('device', lazy=True),
        cascade='all, delete-orphan'
    )

    snmp_config = db.relationship(
        'DeviceSnmpConfig',
        backref='device',
        uselist=False,
        cascade='all, delete-orphan'
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
            'parent_port_id': self.parent_port_id,
            'maintenance_mode': self.maintenance_mode,
            'health_alert_strikes': self.health_alert_strikes,
            'latency_strikes': self.latency_strikes,
            'packet_loss_strikes': self.packet_loss_strikes,
            'hardware_specs': self.hardware_specs,
            'device_username': self.device_username,
            'subnet_cidr': self.subnet_cidr,
            # device_password_hash intentionally excluded for security
        }
