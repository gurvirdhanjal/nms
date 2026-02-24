"""
Print Log Collector Service — Phase 1 MVP

Collects print job audit records from:
  1. Windows Print Server event logs (Event ID 307 — Document Printed)
  2. Syslog messages (future — Linux CUPS servers)

These records are stored in the PrintJobAudit table for IP-to-Printer tracking.

Usage:
  from services.print_log_collector import PrintLogCollector
  collector = PrintLogCollector(app)
  collector.collect_from_windows_events(print_server_ip)
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class PrintLogCollector:
    """
    Collects print job records from external sources (Windows Event Logs,
    Syslog) and stores them in the PrintJobAudit table.

    This service does NOT require an agent on the printer — it reads logs
    from the Print Server or the NMS's syslog receiver.
    """

    def __init__(self, app=None):
        self.app = app

    def collect_from_windows_events(self, print_server_device_id: int,
                                     events: list) -> int:
        """
        Process a batch of Windows Print Server events (Event ID 307).

        Args:
            print_server_device_id: Device ID of the print server in our DB.
            events: List of dicts with the following keys:
                - job_id: str
                - document_name: str
                - user_account: str
                - source_ip: str (optional)
                - printer_name: str
                - page_count: int (optional)
                - size_bytes: int (optional)
                - timestamp: ISO 8601 string
                - status: str (default "completed")

        Returns:
            Number of records inserted.
        """
        from extensions import db
        from models.printer import PrintJobAudit
        from models.device import Device

        inserted = 0

        for event in events:
            # Resolve the printer device by name if possible
            printer_name = event.get('printer_name', 'Unknown')
            printer_device = Device.query.filter(
                Device.device_name.ilike(f'%{printer_name}%'),
                Device.device_type.ilike('%printer%')
            ).first()

            device_id = printer_device.device_id if printer_device else None
            if not device_id:
                log.warning(f"[PrintLog] Could not resolve printer '{printer_name}' to a device. Skipping.")
                continue

            # Parse timestamp
            ts_str = event.get('timestamp')
            try:
                submission_time = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                submission_time = datetime.now(timezone.utc)

            # Deduplicate by job_id + printer_name
            job_id = event.get('job_id', '')
            existing = PrintJobAudit.query.filter_by(
                job_id=job_id, printer_name=printer_name
            ).first()
            if existing:
                continue

            audit = PrintJobAudit(
                device_id=device_id,
                print_server_id=print_server_device_id,
                job_id=job_id,
                document_name=event.get('document_name'),
                user_account=event.get('user_account'),
                source_ip=event.get('source_ip'),
                printer_name=printer_name,
                page_count=event.get('page_count'),
                size_bytes=event.get('size_bytes'),
                submission_time=submission_time,
                completion_time=submission_time,  # Event 307 fires on completion
                status=event.get('status', 'completed'),
                collection_source='wef',
            )
            db.session.add(audit)
            inserted += 1

        if inserted > 0:
            db.session.commit()
            log.info(f"[PrintLog] Inserted {inserted} print job record(s) from server {print_server_device_id}")

        return inserted

    def collect_from_syslog(self, syslog_entries: list) -> int:
        """
        Process a batch of parsed syslog entries from CUPS or network printers.

        Args:
            syslog_entries: List of dicts with:
                - job_id, user_account, printer_name, page_count, timestamp, source_ip

        Returns:
            Number of records inserted.
        """
        from extensions import db
        from models.printer import PrintJobAudit
        from models.device import Device

        inserted = 0

        for entry in syslog_entries:
            printer_name = entry.get('printer_name', 'Unknown')
            printer_device = Device.query.filter(
                Device.device_name.ilike(f'%{printer_name}%'),
                Device.device_type.ilike('%printer%')
            ).first()

            device_id = printer_device.device_id if printer_device else None
            if not device_id:
                log.warning(f"[PrintLog] Could not resolve printer '{printer_name}'. Skipping syslog entry.")
                continue

            ts_str = entry.get('timestamp')
            try:
                submission_time = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                submission_time = datetime.now(timezone.utc)

            job_id = entry.get('job_id', '')
            existing = PrintJobAudit.query.filter_by(
                job_id=job_id, printer_name=printer_name
            ).first()
            if existing:
                continue

            audit = PrintJobAudit(
                device_id=device_id,
                job_id=job_id,
                document_name=entry.get('document_name'),
                user_account=entry.get('user_account'),
                source_ip=entry.get('source_ip'),
                printer_name=printer_name,
                page_count=entry.get('page_count'),
                submission_time=submission_time,
                status='completed',
                collection_source='syslog',
            )
            db.session.add(audit)
            inserted += 1

        if inserted > 0:
            db.session.commit()
            log.info(f"[PrintLog] Inserted {inserted} syslog print job record(s)")

        return inserted
