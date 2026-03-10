from datetime import datetime

from extensions import db


class AlertFanoutTask(db.Model):
    __tablename__ = 'alert_fanout_tasks'
    __table_args__ = (
        db.Index('ix_alert_fanout_tasks_status_next_run', 'status', 'next_run_at'),
        db.UniqueConstraint('delivery_key', name='uq_alert_fanout_delivery_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dashboard_event_id = db.Column(db.String(36), nullable=False, index=True)
    tracked_device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False)
    channel = db.Column(db.String(16), nullable=False, index=True)
    delivery_key = db.Column(db.String(128), nullable=False)
    payload_json = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    priority = db.Column(db.Integer, nullable=False, default=100)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    next_run_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    error_code = db.Column(db.String(64))
    error_message = db.Column(db.Text)
    claim_token = db.Column(db.String(64), index=True)
    claim_expires_at = db.Column(db.DateTime, index=True)
    provider_message_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': int(self.id),
            'dashboard_event_id': self.dashboard_event_id,
            'tracked_device_id': int(self.tracked_device_id),
            'channel': self.channel,
            'delivery_key': self.delivery_key,
            'status': self.status,
            'priority': int(self.priority or 0),
            'retry_count': int(self.retry_count or 0),
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
            'claim_token': self.claim_token,
            'claim_expires_at': self.claim_expires_at.isoformat() if self.claim_expires_at else None,
            'provider_message_id': self.provider_message_id,
        }
