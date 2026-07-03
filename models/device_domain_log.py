from extensions import db
from datetime import datetime


class DeviceDomainLog(db.Model):
    __tablename__ = 'device_domain_logs'
    __table_args__ = (
        db.UniqueConstraint('tracked_device_id', 'domain', name='uq_device_domain'),
        db.Index('ix_device_domain_logs_device_last', 'tracked_device_id', 'last_seen_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    tracked_device_id = db.Column(
        db.Integer,
        db.ForeignKey('tracked_devices.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    domain = db.Column(db.String(255), nullable=False)
    visit_count = db.Column(db.Integer, default=1, nullable=False)
    first_seen_at = db.Column(db.DateTime(timezone=True))
    last_seen_at = db.Column(db.DateTime(timezone=True))
    category = db.Column(db.String(64))
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tracked_device_id': self.tracked_device_id,
            'domain': self.domain,
            'visit_count': self.visit_count,
            'first_seen_at': self.first_seen_at.isoformat() if self.first_seen_at else None,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'category': self.category,
            'is_blocked': self.is_blocked,
        }
