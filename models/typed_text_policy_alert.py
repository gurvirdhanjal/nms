from datetime import datetime
from sqlalchemy import UniqueConstraint
from extensions import db


class TypedTextPolicyAlert(db.Model):
    """
    Server-side record of a typed-text policy hit detected on a tracked device agent.

    The agent scans locally; only the SHA-256 hash of the flagged snippet is sent —
    raw keystroke text is never transmitted.  Ingested via the tracking sync endpoint.
    """
    __tablename__ = 'typed_text_policy_alerts'
    __table_args__ = (
        UniqueConstraint(
            "device_id", "evidence_hash", "detected_at",
            name="uq_typed_text_alert_device_hash_time",
        ),
    )

    id            = db.Column(db.Integer, primary_key=True)
    device_id     = db.Column(db.Integer, db.ForeignKey('tracked_devices.id'),
                              nullable=False, index=True)
    pattern_type  = db.Column(db.String(50))   # credit_card, ssn, profanity, …
    severity      = db.Column(db.String(20))   # high | medium | low
    evidence_hash = db.Column(db.String(64))   # SHA-256 hex of the flagged snippet
    ai_risk_level = db.Column(db.String(20))   # high/medium/low from Claude (optional)
    ai_category   = db.Column(db.String(100))  # e.g. financial_data (optional)
    detected_at   = db.Column(db.DateTime, nullable=False, index=True)
    received_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':            self.id,
            'device_id':     self.device_id,
            'pattern_type':  self.pattern_type,
            'severity':      self.severity,
            'evidence_hash': self.evidence_hash,
            'ai_risk_level': self.ai_risk_level,
            'ai_category':   self.ai_category,
            'detected_at':   self.detected_at.isoformat() if self.detected_at else None,
            'received_at':   self.received_at.isoformat() if self.received_at else None,
        }
