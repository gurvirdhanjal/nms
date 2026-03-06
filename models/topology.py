from extensions import db
from datetime import datetime

class SwitchTopology(db.Model):
    """
    Tracks neighbors between switches (Phase 1).
    Used to build the core network map.
    """
    __tablename__ = 'switch_topology'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    
    # Local side
    local_device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False)
    local_interface_id = db.Column(db.Integer, db.ForeignKey('device_interfaces.interface_id', ondelete='CASCADE'), nullable=True)
    
    # Remote side (Neighbor)
    remote_device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='SET NULL'),
        nullable=True
    )  # None if unknown switch
    remote_hostname = db.Column(db.String(100), nullable=True)
    remote_ip = db.Column(db.String(50), nullable=True)
    remote_port_desc = db.Column(db.String(100), nullable=True) # e.g. "GigabitEthernet0/1"
    
    # Discovery Metadata
    protocol = db.Column(db.String(20), nullable=True) # LLDP, CDP
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    local_device = db.relationship(
        'Device', 
        foreign_keys=[local_device_id], 
        backref=db.backref('local_neighbors', lazy=True, cascade='all, delete-orphan')
    )
    remote_device = db.relationship(
        'Device', 
        foreign_keys=[remote_device_id],
        backref=db.backref('remote_neighbors', lazy=True)
    )

    __table_args__ = (
        db.UniqueConstraint('local_device_id', 'local_interface_id', 'remote_hostname', name='uq_topology_neighbor'),
    )

    def __repr__(self):
        return f'<Topology {self.local_device_id} -> {self.remote_hostname}>'
