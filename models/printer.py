from extensions import db
from datetime import datetime


class PrinterMetrics(db.Model):
    """Per-poll SNMP snapshot of printer health and consumables."""
    __tablename__ = 'printer_metrics'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Status (RFC 3805 hrPrinterStatus)
    status = db.Column(db.String(50), nullable=True)       # idle, printing, error, offline, warmup
    status_code = db.Column(db.Integer, nullable=True)      # Raw hrPrinterStatus integer

    # Consumables (percentage 0–100, -1 = unknown, -2 = not applicable)
    toner_black = db.Column(db.Integer, nullable=True)
    toner_cyan = db.Column(db.Integer, nullable=True)
    toner_magenta = db.Column(db.Integer, nullable=True)
    toner_yellow = db.Column(db.Integer, nullable=True)

    # Paper trays  (JSON: [{tray_id, name, status, capacity_pct}])
    paper_tray_status = db.Column(db.JSON, nullable=True)

    # Page counters
    page_count_total = db.Column(db.BigInteger, nullable=True)
    page_count_color = db.Column(db.BigInteger, nullable=True)
    page_count_bw = db.Column(db.BigInteger, nullable=True)

    # Queue
    job_queue_length = db.Column(db.Integer, nullable=True)

    # Relationships
    device = db.relationship('Device', backref=db.backref('printer_metrics', lazy='dynamic', cascade='all, delete-orphan'))

    __table_args__ = (
        db.Index('idx_printer_metrics_device_ts', 'device_id', 'timestamp'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'status': self.status,
            'status_code': self.status_code,
            'toner_black': self.toner_black,
            'toner_cyan': self.toner_cyan,
            'toner_magenta': self.toner_magenta,
            'toner_yellow': self.toner_yellow,
            'paper_tray_status': self.paper_tray_status,
            'page_count_total': self.page_count_total,
            'page_count_color': self.page_count_color,
            'page_count_bw': self.page_count_bw,
            'job_queue_length': self.job_queue_length,
        }


class PrintJobAudit(db.Model):
    """Parsed print job records from Print Server event logs or Syslog."""
    __tablename__ = 'print_job_audit'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='CASCADE'), nullable=False, index=True)
    print_server_id = db.Column(db.Integer, db.ForeignKey('device.device_id', ondelete='SET NULL'), nullable=True, index=True)

    # Job Identity
    job_id = db.Column(db.String(100), nullable=False, index=True)
    document_name = db.Column(db.String(500), nullable=True)

    # User Context
    user_account = db.Column(db.String(200), nullable=True, index=True)
    source_ip = db.Column(db.String(50), nullable=True, index=True)

    # Printer
    printer_name = db.Column(db.String(200), nullable=False, index=True)

    # Metrics
    page_count = db.Column(db.Integer, nullable=True)
    size_bytes = db.Column(db.BigInteger, nullable=True)

    # Timestamps
    submission_time = db.Column(db.DateTime, nullable=False, index=True)
    completion_time = db.Column(db.DateTime, nullable=True)

    # Status
    status = db.Column(db.String(50), nullable=True)  # submitted, printing, completed, failed, cancelled

    # Source of this record
    collection_source = db.Column(db.String(50), nullable=True)  # wef, syslog, snmp

    # Relationships
    device = db.relationship('Device', foreign_keys=[device_id], backref=db.backref('print_jobs', lazy='dynamic', cascade='all, delete-orphan'))
    print_server = db.relationship('Device', foreign_keys=[print_server_id])

    __table_args__ = (
        db.Index('idx_print_job_user_time', 'user_account', 'submission_time'),
        db.Index('idx_print_job_ip_time', 'source_ip', 'submission_time'),
        db.Index('idx_print_job_printer_time', 'printer_name', 'submission_time'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'print_server_id': self.print_server_id,
            'job_id': self.job_id,
            'document_name': self.document_name,
            'user_account': self.user_account,
            'source_ip': self.source_ip,
            'printer_name': self.printer_name,
            'page_count': self.page_count,
            'size_bytes': self.size_bytes,
            'submission_time': self.submission_time.isoformat() if self.submission_time else None,
            'completion_time': self.completion_time.isoformat() if self.completion_time else None,
            'status': self.status,
            'collection_source': self.collection_source,
        }
