from datetime import datetime

from extensions import db


class DeviceIdentityLinkCandidate(db.Model):
    __tablename__ = 'device_identity_link_candidates'
    __table_args__ = (
        db.Index('ix_device_identity_candidates_status_detected', 'status', 'detected_at'),
        db.Index('ix_device_identity_candidates_mac_status', 'normalized_mac', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False)
    tracked_device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False)
    normalized_mac = db.Column(db.String(17), nullable=False, index=True)
    ambiguity_group_key = db.Column(db.String(64), nullable=False, index=True)
    candidate_source = db.Column(db.String(32), nullable=False, default='mac')
    candidate_score = db.Column(db.Integer, nullable=False, default=100)
    status = db.Column(db.String(16), nullable=False, default='pending', index=True)
    detected_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)
    decided_by = db.Column(db.String(100))
    decision_reason = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': int(self.id),
            'device_id': int(self.device_id),
            'tracked_device_id': int(self.tracked_device_id),
            'normalized_mac': self.normalized_mac,
            'ambiguity_group_key': self.ambiguity_group_key,
            'candidate_source': self.candidate_source,
            'candidate_score': int(self.candidate_score or 0),
            'status': self.status,
            'detected_at': self.detected_at.isoformat() if self.detected_at else None,
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'decided_by': self.decided_by,
            'decision_reason': self.decision_reason,
        }
