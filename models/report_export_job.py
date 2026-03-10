from datetime import datetime

from extensions import db


class ReportExportJob(db.Model):
    __tablename__ = "report_export_jobs"
    __table_args__ = (
        db.Index("ix_report_export_jobs_owner_created", "owner_key", "created_at"),
        db.Index("ix_report_export_jobs_status_created", "status", "created_at"),
        db.Index("ix_report_export_jobs_report_created", "report_type", "created_at"),
        db.Index("ix_report_export_jobs_scope_created", "scope_type", "scope_id", "created_at"),
    )

    job_id = db.Column(db.String(32), primary_key=True)
    owner_key = db.Column(db.String(100), nullable=False, index=True)
    scope_type = db.Column(db.String(20), nullable=False, default="global", index=True)
    scope_id = db.Column(db.Integer, nullable=True, index=True)
    report_type = db.Column(db.String(50), nullable=False, index=True)
    export_format = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    params_json = db.Column(db.Text, nullable=False, default="{}")
    payload_cache_key = db.Column(db.String(128), nullable=True)
    row_count = db.Column(db.Integer, nullable=True)
    filename = db.Column(db.String(255), nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "owner_key": self.owner_key,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "report_type": self.report_type,
            "format": self.export_format,
            "status": self.status,
            "params_json": self.params_json,
            "payload_cache_key": self.payload_cache_key,
            "row_count": self.row_count,
            "filename": self.filename,
            "file_path": self.file_path,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
