from datetime import datetime

from extensions import db


class ServerThresholdConfig(db.Model):
    __tablename__ = "server_threshold_config"

    id = db.Column(db.Integer, primary_key=True, default=1)
    version = db.Column(db.Integer, nullable=False, default=1)
    thresholds_json = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(100), nullable=True)
    change_reason = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": int(self.id),
            "version": int(self.version or 1),
            "thresholds_json": self.thresholds_json or {},
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
            "change_reason": self.change_reason,
        }
