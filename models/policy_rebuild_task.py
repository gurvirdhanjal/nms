from datetime import datetime

from extensions import db


class PolicyRebuildTask(db.Model):
    __tablename__ = 'policy_rebuild_tasks'
    __table_args__ = (
        db.Index('ix_policy_rebuild_tasks_status_next_run', 'status', 'next_run_at'),
        db.Index('ix_policy_rebuild_tasks_device_status', 'tracked_device_id', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    tracked_device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False)
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
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': int(self.id),
            'tracked_device_id': int(self.tracked_device_id),
            'status': self.status,
            'priority': int(self.priority or 0),
            'retry_count': int(self.retry_count or 0),
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'error_code': self.error_code,
            'error_message': self.error_message,
            'claim_token': self.claim_token,
            'claim_expires_at': self.claim_expires_at.isoformat() if self.claim_expires_at else None,
        }
