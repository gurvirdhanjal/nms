from datetime import datetime

from extensions import db


class ServerMetricThresholdState(db.Model):
    __tablename__ = "server_metric_threshold_state"
    __table_args__ = (
        db.UniqueConstraint("device_id", "metric_key", name="uq_server_metric_threshold_state_device_metric"),
        db.Index("ix_server_metric_threshold_state_device_metric", "device_id", "metric_key"),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False)
    metric_key = db.Column(db.String(100), nullable=False)
    breach_streak = db.Column(db.Integer, nullable=False, default=0)
    recovery_streak = db.Column(db.Integer, nullable=False, default=0)
    last_state = db.Column(db.String(20), nullable=True)
    last_value = db.Column(db.Float, nullable=True)
    last_evaluated_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": int(self.id),
            "device_id": int(self.device_id),
            "metric_key": self.metric_key,
            "breach_streak": int(self.breach_streak or 0),
            "recovery_streak": int(self.recovery_streak or 0),
            "last_state": self.last_state,
            "last_value": self.last_value,
            "last_evaluated_at": self.last_evaluated_at.isoformat() if self.last_evaluated_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
