from datetime import datetime

from extensions import db


class TrackingSyncEnvelope(db.Model):
    __tablename__ = 'tracking_sync_envelopes'
    __table_args__ = (
        db.Index('ix_tracking_sync_envelopes_core_status_next', 'core_status', 'received_at'),
        db.Index('ix_tracking_sync_envelopes_violation_status_next', 'violation_status', 'received_at'),
        db.Index('ix_tracking_sync_envelopes_domain_status_next', 'domain_status', 'received_at'),
        db.Index('ix_tracking_sync_envelopes_shadow_status_received', 'shadow_status', 'received_at'),
        db.Index('ix_tracking_sync_envelopes_mac_received', 'normalized_mac', 'received_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    normalized_mac = db.Column(db.String(17), nullable=False, index=True)
    unique_client_id = db.Column(db.String(36), index=True)
    tracked_device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='SET NULL'), index=True)
    payload_json = db.Column(db.JSON, nullable=False, default=dict)
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    inline_summary_json = db.Column(db.JSON)
    shadow_summary_json = db.Column(db.JSON)
    shadow_mismatches_json = db.Column(db.JSON)
    shadow_status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    core_status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    violation_status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    domain_status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    core_retry_count = db.Column(db.Integer, nullable=False, default=0)
    violation_retry_count = db.Column(db.Integer, nullable=False, default=0)
    domain_retry_count = db.Column(db.Integer, nullable=False, default=0)
    core_next_run_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    violation_next_run_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    domain_next_run_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    core_started_at = db.Column(db.DateTime)
    violation_started_at = db.Column(db.DateTime)
    domain_started_at = db.Column(db.DateTime)
    core_finished_at = db.Column(db.DateTime)
    violation_finished_at = db.Column(db.DateTime)
    domain_finished_at = db.Column(db.DateTime)
    core_error_code = db.Column(db.String(64))
    violation_error_code = db.Column(db.String(64))
    domain_error_code = db.Column(db.String(64))
    core_claim_token = db.Column(db.String(64), index=True)
    violation_claim_token = db.Column(db.String(64), index=True)
    domain_claim_token = db.Column(db.String(64), index=True)
    core_claim_expires_at = db.Column(db.DateTime, index=True)
    violation_claim_expires_at = db.Column(db.DateTime, index=True)
    domain_claim_expires_at = db.Column(db.DateTime, index=True)
    dedupe_key = db.Column(db.String(255), index=True)

    def to_dict(self):
        return {
            'id': int(self.id),
            'normalized_mac': self.normalized_mac,
            'unique_client_id': self.unique_client_id,
            'tracked_device_id': self.tracked_device_id,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'shadow_status': self.shadow_status,
            'core_status': self.core_status,
            'violation_status': self.violation_status,
            'domain_status': self.domain_status,
            'core_retry_count': int(self.core_retry_count or 0),
            'violation_retry_count': int(self.violation_retry_count or 0),
            'domain_retry_count': int(self.domain_retry_count or 0),
            'core_next_run_at': self.core_next_run_at.isoformat() if self.core_next_run_at else None,
            'violation_next_run_at': self.violation_next_run_at.isoformat() if self.violation_next_run_at else None,
            'domain_next_run_at': self.domain_next_run_at.isoformat() if self.domain_next_run_at else None,
            'dedupe_key': self.dedupe_key,
        }
