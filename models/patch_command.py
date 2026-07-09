from extensions import db
from datetime import datetime

VALID_STATUSES = frozenset({'queued', 'sent', 'success', 'failed', 'cancelled'})

ALLOWED_MANAGERS = frozenset({
    'chocolatey', 'winget', 'homebrew', 'apt', 'yum', 'dnf',
})


class PatchCommand(db.Model):
    __tablename__ = 'patch_commands'
    __table_args__ = (
        db.Index('ix_patch_commands_device_status', 'tracked_device_id', 'status'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    tracked_device_id = db.Column(
        db.Integer,
        db.ForeignKey('tracked_devices.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    package_manager = db.Column(db.String(32), nullable=False)
    package_name = db.Column(db.String(255), nullable=False)
    target_version = db.Column(db.String(64), nullable=True)

    # lifecycle: queued → sent → success | failed | cancelled
    status = db.Column(db.String(16), nullable=False, default='queued', index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)
    result_at = db.Column(db.DateTime, nullable=True)

    result_success = db.Column(db.Boolean, nullable=True)
    result_output = db.Column(db.Text, nullable=True)

    # who queued the command
    created_by = db.Column(db.String(100), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'tracked_device_id': self.tracked_device_id,
            'package_manager': self.package_manager,
            'package_name': self.package_name,
            'target_version': self.target_version,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'result_at': self.result_at.isoformat() if self.result_at else None,
            'result_success': self.result_success,
            'result_output': self.result_output,
            'created_by': self.created_by,
        }
