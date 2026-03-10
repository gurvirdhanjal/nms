from datetime import datetime

from extensions import db


class DeviceIdentityLink(db.Model):
    __tablename__ = 'device_identity_links'
    __table_args__ = (
        db.Index('ix_device_identity_links_device_active', 'device_id', 'is_active'),
        db.Index('ix_device_identity_links_tracked_active', 'tracked_device_id', 'is_active'),
        db.Index('ix_device_identity_links_mac_active', 'normalized_mac', 'is_active'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False)
    tracked_device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False)
    normalized_mac = db.Column(db.String(17), nullable=False, index=True)
    link_source = db.Column(db.String(32), nullable=False, default='manual')
    confidence = db.Column(db.Integer, nullable=False, default=100)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_by = db.Column(db.String(100))
    resolution_reason = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': int(self.id),
            'device_id': int(self.device_id),
            'tracked_device_id': int(self.tracked_device_id),
            'normalized_mac': self.normalized_mac,
            'link_source': self.link_source,
            'confidence': int(self.confidence or 0),
            'is_active': bool(self.is_active),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'resolved_by': self.resolved_by,
            'resolution_reason': self.resolution_reason,
        }
