"""
Print Jobs Service for Print Job Audit Trail Management.
Handles print job record creation, filtering, and export.
"""
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from extensions import db
from models.printer import PrintJobAudit
from models.device import Device
import csv
from io import StringIO


class PrintJobsService:
    """Service for managing print job audit records."""

    def create_print_job(self, data: Dict) -> PrintJobAudit:
        """
        Create a print job record.
        
        Args:
            data: Dict with required fields:
                - user_account: Username
                - document_name: Document name
                - printer_name: Printer name
                - submission_time: Timestamp
                - page_count: Number of pages
                - collection_source: Source (wef, syslog, snmp)
                Optional fields:
                - device_id: Printer device ID
                - print_server_id: Print server device ID
                - job_id: Job identifier
                - source_ip: Source IP address
                - size_bytes: Document size
                - completion_time: Completion timestamp
                - status: Job status
                
        Returns:
            Created PrintJobAudit object
            
        Raises:
            ValueError: If required fields are missing
        """
        try:
            # Validate required fields
            required_fields = ['user_account', 'document_name', 'printer_name', 
                             'submission_time', 'page_count', 'collection_source']
            for field in required_fields:
                if field not in data:
                    raise ValueError(f"Missing required field: {field}")
            
            # If device_id not provided, try to find printer by name
            device_id = data.get('device_id')
            if not device_id:
                printer = Device.query.filter_by(
                    device_name=data['printer_name']
                ).first()
                if printer:
                    device_id = printer.device_id
                else:
                    # Create a placeholder device_id or raise error
                    raise ValueError(f"Printer '{data['printer_name']}' not found in devices")
            
            # Create print job record
            print_job = PrintJobAudit(
                device_id=device_id,
                print_server_id=data.get('print_server_id'),
                job_id=data.get('job_id', f"job_{datetime.utcnow().timestamp()}"),
                document_name=data['document_name'],
                user_account=data['user_account'],
                source_ip=data.get('source_ip'),
                printer_name=data['printer_name'],
                page_count=data['page_count'],
                size_bytes=data.get('size_bytes'),
                submission_time=data['submission_time'],
                completion_time=data.get('completion_time'),
                status=data.get('status', 'completed'),
                collection_source=data['collection_source']
            )
            
            db.session.add(print_job)
            db.session.commit()
            
            return print_job
            
        except Exception as e:
            db.session.rollback()
            raise

    def list_print_jobs(self, filters: Dict = None, page: int = 1, page_size: int = 50) -> Dict:
        """
        List print jobs with filtering and pagination.
        
        Args:
            filters: Dict with optional filters:
                - start_date: Start date filter
                - end_date: End date filter
                - user: Username filter
                - printer_id: Printer device ID filter
                - site_id: Site ID filter
                - department_id: Department ID filter
            page: Page number (1-indexed)
            page_size: Number of records per page (default: 50)
            
        Returns:
            Dict with:
                - jobs: List of PrintJobAudit objects
                - total: Total number of matching records
                - total_pages: Total number of pages
                - page: Current page number
                - page_size: Records per page
        """
        filters = filters or {}
        
        # Build query
        query = PrintJobAudit.query
        
        # Apply filters
        if filters.get('start_date'):
            query = query.filter(PrintJobAudit.submission_time >= filters['start_date'])
        
        if filters.get('end_date'):
            query = query.filter(PrintJobAudit.submission_time <= filters['end_date'])
        
        if filters.get('user'):
            query = query.filter(PrintJobAudit.user_account.ilike(f"%{filters['user']}%"))
        
        if filters.get('printer_id'):
            query = query.filter(PrintJobAudit.device_id == filters['printer_id'])
        
        # Site and department filtering requires joining with Device
        if filters.get('site_id') or filters.get('department_id'):
            query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
            
            if filters.get('site_id'):
                query = query.filter(Device.site_id == filters['site_id'])
            
            if filters.get('department_id'):
                query = query.filter(Device.department_id == filters['department_id'])
        
        # Get total count
        total = query.count()
        
        # Calculate pagination
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        offset = (page - 1) * page_size
        
        # Get paginated results
        jobs = query.order_by(PrintJobAudit.submission_time.desc()).offset(offset).limit(page_size).all()
        
        return {
            'jobs': jobs,
            'total': total,
            'total_pages': total_pages,
            'page': page,
            'page_size': page_size
        }

    def get_total_pages(self, filters: Dict = None) -> int:
        """
        Get total page count for filtered print jobs.
        
        Args:
            filters: Dict with optional filters (same as list_print_jobs)
            
        Returns:
            Total page count
        """
        filters = filters or {}
        
        # Build query
        query = db.session.query(db.func.sum(PrintJobAudit.page_count))
        
        # Apply filters
        if filters.get('start_date'):
            query = query.filter(PrintJobAudit.submission_time >= filters['start_date'])
        
        if filters.get('end_date'):
            query = query.filter(PrintJobAudit.submission_time <= filters['end_date'])
        
        if filters.get('user'):
            query = query.filter(PrintJobAudit.user_account.ilike(f"%{filters['user']}%"))
        
        if filters.get('printer_id'):
            query = query.filter(PrintJobAudit.device_id == filters['printer_id'])
        
        # Site and department filtering
        if filters.get('site_id') or filters.get('department_id'):
            query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
            
            if filters.get('site_id'):
                query = query.filter(Device.site_id == filters['site_id'])
            
            if filters.get('department_id'):
                query = query.filter(Device.department_id == filters['department_id'])
        
        result = query.scalar()
        return result if result else 0

    def export_to_csv(self, filters: Dict = None) -> str:
        """
        Export filtered print jobs to CSV format.
        
        Args:
            filters: Dict with optional filters (same as list_print_jobs)
            
        Returns:
            CSV string
        """
        # Get all matching jobs (no pagination)
        result = self.list_print_jobs(filters=filters, page=1, page_size=100000)
        jobs = result['jobs']
        
        # Create CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'Job ID',
            'User',
            'Document Name',
            'Printer Name',
            'Submission Time',
            'Completion Time',
            'Page Count',
            'Size (bytes)',
            'Status',
            'Source IP',
            'Collection Source'
        ])
        
        # Write data
        for job in jobs:
            writer.writerow([
                job.job_id,
                job.user_account,
                job.document_name,
                job.printer_name,
                job.submission_time.isoformat() if job.submission_time else '',
                job.completion_time.isoformat() if job.completion_time else '',
                job.page_count,
                job.size_bytes or '',
                job.status or '',
                job.source_ip or '',
                job.collection_source or ''
            ])
        
        return output.getvalue()

    def cleanup_old_jobs(self, days: int = 90) -> Dict:
        """
        Delete print jobs older than retention period.
        
        Args:
            days: Retention period in days (default: 90)
            
        Returns:
            Dict with success status and deleted count
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            
            # Count jobs to delete
            count = PrintJobAudit.query.filter(
                PrintJobAudit.submission_time < cutoff
            ).count()
            
            if count > 0:
                # Delete old jobs
                PrintJobAudit.query.filter(
                    PrintJobAudit.submission_time < cutoff
                ).delete()
                
                db.session.commit()
            
            return {
                'success': True,
                'deleted_count': count,
                'cutoff_date': cutoff.isoformat(),
                'retention_days': days
            }
            
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'error': str(e)
            }
