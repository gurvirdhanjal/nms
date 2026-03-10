from datetime import datetime

from extensions import db


class DeviceEffectivePolicyCache(db.Model):
    __tablename__ = 'device_effective_policy_cache'

    tracked_device_id = db.Column(
        db.Integer,
        db.ForeignKey('tracked_devices.id', ondelete='CASCADE'),
        primary_key=True,
    )
    global_domains_json = db.Column(db.JSON, nullable=False, default=list)
    device_domains_json = db.Column(db.JSON, nullable=False, default=list)
    effective_domains_json = db.Column(db.JSON, nullable=False, default=list)
    effective_policy_version = db.Column(db.String(128), nullable=False, default='', index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'tracked_device_id': int(self.tracked_device_id),
            'global_restricted_sites': list(self.global_domains_json or []),
            'device_restricted_sites': list(self.device_domains_json or []),
            'effective_restricted_sites': list(self.effective_domains_json or []),
            'effective_policy_version': self.effective_policy_version or '',
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
