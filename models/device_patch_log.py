from extensions import db
from datetime import datetime

VALID_PACKAGE_MANAGERS = frozenset({
    'chocolatey', 'winget', 'homebrew', 'apt', 'yum', 'dnf', 'pacman', 'snap',
})


class DevicePatchLog(db.Model):
    __tablename__ = 'device_patch_logs'
    __table_args__ = (
        db.UniqueConstraint('tracked_device_id', 'package_manager', 'package_name', name='uq_device_patch'),
        db.Index('ix_device_patch_logs_device_pending', 'tracked_device_id', 'is_pending_update'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    tracked_device_id = db.Column(
        db.Integer,
        db.ForeignKey('tracked_devices.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    package_manager = db.Column(db.String(32))
    package_name = db.Column(db.String(255), nullable=False)
    installed_version = db.Column(db.String(64))
    available_version = db.Column(db.String(64))
    is_pending_update = db.Column(db.Boolean, default=False, nullable=False)
    last_checked_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tracked_device_id': self.tracked_device_id,
            'package_manager': self.package_manager,
            'package_name': self.package_name,
            'installed_version': self.installed_version,
            'available_version': self.available_version,
            'is_pending_update': self.is_pending_update,
            'last_checked_at': self.last_checked_at.isoformat() if self.last_checked_at else None,
        }
