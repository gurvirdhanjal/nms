from extensions import db
from datetime import datetime


class DeviceLocationLog(db.Model):
    __tablename__ = 'device_location_logs'
    __table_args__ = (
        db.Index('ix_device_location_logs_device_recorded', 'tracked_device_id', 'recorded_at'),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    tracked_device_id = db.Column(
        db.Integer,
        db.ForeignKey('tracked_devices.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # Stable per-sample UUID set by the agent — used as idempotency key so
    # relay redeliveries (visibility-timeout expiry before ack) never produce
    # duplicate rows.  NULL for samples delivered via the normal LAN path that
    # pre-dates the relay (those have no uuid).
    sample_uuid = db.Column(db.String(36), nullable=True, unique=True, index=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    accuracy_meters = db.Column(db.Float)
    source = db.Column(db.String(32))  # 'gps' | 'wifi' | 'ip'
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tracked_device_id': self.tracked_device_id,
            'sample_uuid': self.sample_uuid,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'accuracy_meters': self.accuracy_meters,
            'source': self.source,
            'recorded_at': self.recorded_at.isoformat() if self.recorded_at else None,
        }
