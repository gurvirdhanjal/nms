from datetime import datetime

from extensions import db


class MaintenanceWindow(db.Model):
    __tablename__ = 'maintenance_window'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey('device.device_id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    start_time = db.Column(db.DateTime, nullable=False, index=True)
    end_time = db.Column(db.DateTime, nullable=False, index=True)
    reason = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    device = db.relationship('Device', backref=db.backref('maintenance_windows', lazy=True))

    def __repr__(self):
        return (
            f'<MaintenanceWindow id={self.id} device_id={self.device_id} '
            f'{self.start_time} -> {self.end_time} active={self.is_active}>'
        )

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'device_name': self.device.device_name if self.device else None,
            'device_ip': self.device.device_ip if self.device else None,
            'device_type': self.device.device_type if self.device else None,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'reason': self.reason,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_active': self.is_active,
        }
