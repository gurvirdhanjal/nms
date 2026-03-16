"""
DeviceConfigSnapshot — stores raw configuration captures from devices.

One snapshot per capture event; a device can have many snapshots over time.
config_hash is computed automatically whenever config_text is assigned,
enabling cheap change-detection without re-reading the full text.
"""
import hashlib
from datetime import datetime
from extensions import db
from sqlalchemy.orm import validates


class DeviceConfigSnapshot(db.Model):
    __tablename__ = 'device_config_snapshots'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    captured_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )

    config_text = db.Column(db.Text, nullable=True)

    # SHA-256 hex digest of config_text — auto-computed via @validates below.
    # String(64) exactly fits a SHA-256 hex string.
    config_hash = db.Column(db.String(64), nullable=True, index=True)

    # 'scheduled' (snmp_worker / scheduler) or 'manual' (user-initiated)
    source = db.Column(db.String(20), nullable=False, default='manual')

    # Set only for manual captures; NULL for scheduled captures.
    captured_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
    )

    # Relationships
    device = db.relationship(
        'Device',
        backref=db.backref('config_snapshots', lazy='dynamic', cascade='all, delete-orphan'),
        foreign_keys=[device_id],
    )

    captured_by = db.relationship(
        'User',
        backref=db.backref('config_snapshots', lazy='dynamic'),
        foreign_keys=[captured_by_user_id],
    )

    # Composite index for history queries: all snapshots for a device, newest first.
    __table_args__ = (
        db.Index(
            'ix_device_config_snapshots_device_captured',
            'device_id',
            captured_at.desc(),
        ),
    )

    # ── Auto-hash ─────────────────────────────────────────────────────────────

    @validates('config_text')
    def _compute_hash(self, key, value):
        """Recompute config_hash whenever config_text is assigned."""
        if value is not None:
            self.config_hash = hashlib.sha256(
                value.encode('utf-8')
            ).hexdigest()
        else:
            self.config_hash = None
        return value

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_changed_from(self, other: 'DeviceConfigSnapshot') -> bool:
        """Return True if this snapshot differs from another (hash comparison)."""
        if self.config_hash is None or other.config_hash is None:
            return True
        return self.config_hash != other.config_hash

    def to_dict(self, include_text: bool = False) -> dict:
        return {
            'id': self.id,
            'device_id': self.device_id,
            'captured_at': self.captured_at.isoformat() if self.captured_at else None,
            'config_hash': self.config_hash,
            'source': self.source,
            'captured_by_user_id': self.captured_by_user_id,
            **(({'config_text': self.config_text}) if include_text else {}),
        }

    def __repr__(self):
        return (
            f'<DeviceConfigSnapshot id={self.id} device_id={self.device_id} '
            f'source={self.source} captured_at={self.captured_at}>'
        )
