"""
Unit tests for Phase 1 MVP services.
Tests SitesService, DepartmentsService, and PrintJobsService.
"""
import pytest
from datetime import datetime, timedelta
from services.sites_service import SitesService
from services.departments_service import DepartmentsService
from services.print_jobs_service import PrintJobsService
from models.site import Site
from models.department import Department
from models.device import Device
from models.printer import PrintJobAudit
from extensions import db


class TestSitesService:
    """Test cases for SitesService."""
    
    def test_create_site(self, app):
        """Test creating a site."""
        with app.app_context():
            service = SitesService()
            
            site = service.create_site(
                name="Test Site",
                address="123 Test St",
                timezone="America/New_York",
                contact_info={
                    'contact_name': 'John Doe',
                    'contact_email': 'john@example.com',
                    'contact_phone': '555-1234'
                }
            )
            
            assert site.id is not None
            assert site.site_name == "Test Site"
            assert site.address == "123 Test St"
            assert site.timezone == "America/New_York"
            assert site.contact_name == "John Doe"
            assert site.contact_email == "john@example.com"
            assert site.contact_phone == "555-1234"
    
    def test_create_duplicate_site(self, app):
        """Test creating a site with duplicate name fails."""
        with app.app_context():
            service = SitesService()
            
            service.create_site(name="Duplicate Site")
            
            with pytest.raises(ValueError, match="already exists"):
                service.create_site(name="Duplicate Site")
    
    def test_get_site(self, app):
        """Test retrieving a site by ID."""
        with app.app_context():
            service = SitesService()
            
            created = service.create_site(name="Get Test Site")
            retrieved = service.get_site(created.id)
            
            assert retrieved is not None
            assert retrieved.id == created.id
            assert retrieved.site_name == "Get Test Site"
    
    def test_list_sites(self, app):
        """Test listing all sites."""
        with app.app_context():
            service = SitesService()
            
            service.create_site(name="Site A")
            service.create_site(name="Site B")
            
            sites = service.list_sites()
            
            assert len(sites) >= 2
            site_names = [s.site_name for s in sites]
            assert "Site A" in site_names
            assert "Site B" in site_names
    
    def test_update_site(self, app):
        """Test updating a site."""
        with app.app_context():
            service = SitesService()
            
            site = service.create_site(name="Update Test")
            updated = service.update_site(
                site.id,
                name="Updated Name",
                address="New Address"
            )
            
            assert updated.site_name == "Updated Name"
            assert updated.address == "New Address"
    
    def test_delete_site_without_devices(self, app):
        """Test deleting a site with no devices."""
        with app.app_context():
            service = SitesService()
            
            site = service.create_site(name="Delete Test")
            result = service.delete_site(site.id)
            
            assert result is True
            assert service.get_site(site.id) is None
    
    def test_delete_site_with_devices(self, app):
        """Test deleting a site with devices fails."""
        with app.app_context():
            service = SitesService()
            
            site = service.create_site(name="Site With Devices")
            
            # Create a device assigned to this site
            device = Device(
                device_name="Test Device",
                device_type="server",
                device_ip="192.168.1.100",
                site_id=site.id
            )
            db.session.add(device)
            db.session.commit()
            
            with pytest.raises(ValueError, match="device\\(s\\) are assigned"):
                service.delete_site(site.id)
    
    def test_get_site_devices(self, app):
        """Test getting devices for a site."""
        with app.app_context():
            service = SitesService()
            
            site = service.create_site(name="Device Test Site")
            
            # Create devices
            device1 = Device(
                device_name="Device 1",
                device_type="server",
                device_ip="192.168.1.101",
                site_id=site.id
            )
            device2 = Device(
                device_name="Device 2",
                device_type="printer",
                device_ip="192.168.1.102",
                site_id=site.id
            )
            db.session.add_all([device1, device2])
            db.session.commit()
            
            devices = service.get_site_devices(site.id)
            
            assert len(devices) == 2
            device_names = [d.device_name for d in devices]
            assert "Device 1" in device_names
            assert "Device 2" in device_names


class TestDepartmentsService:
    """Test cases for DepartmentsService."""
    
    def test_create_department(self, app):
        """Test creating a department."""
        with app.app_context():
            service = DepartmentsService()
            
            dept = service.create_department(
                name="IT Department",
                description="Information Technology"
            )
            
            assert dept.id is not None
            assert dept.name == "IT Department"
            assert dept.description == "Information Technology"
    
    def test_create_duplicate_department(self, app):
        """Test creating a department with duplicate name fails."""
        with app.app_context():
            service = DepartmentsService()
            
            service.create_department(name="Duplicate Dept")
            
            with pytest.raises(ValueError, match="already exists"):
                service.create_department(name="Duplicate Dept")
    
    def test_get_department(self, app):
        """Test retrieving a department by ID."""
        with app.app_context():
            service = DepartmentsService()
            
            created = service.create_department(name="Get Test Dept")
            retrieved = service.get_department(created.id)
            
            assert retrieved is not None
            assert retrieved.id == created.id
            assert retrieved.name == "Get Test Dept"
    
    def test_list_departments(self, app):
        """Test listing all departments."""
        with app.app_context():
            service = DepartmentsService()
            
            service.create_department(name="Dept A")
            service.create_department(name="Dept B")
            
            depts = service.list_departments()
            
            assert len(depts) >= 2
            dept_names = [d.name for d in depts]
            assert "Dept A" in dept_names
            assert "Dept B" in dept_names
    
    def test_update_department(self, app):
        """Test updating a department."""
        with app.app_context():
            service = DepartmentsService()
            
            dept = service.create_department(name="Update Test Dept")
            updated = service.update_department(
                dept.id,
                name="Updated Dept Name",
                description="New Description"
            )
            
            assert updated.name == "Updated Dept Name"
            assert updated.description == "New Description"
    
    def test_delete_department_without_devices(self, app):
        """Test deleting a department with no devices."""
        with app.app_context():
            service = DepartmentsService()
            
            dept = service.create_department(name="Delete Test Dept")
            result = service.delete_department(dept.id)
            
            assert result is True
            assert service.get_department(dept.id) is None
    
    def test_delete_department_with_devices(self, app):
        """Test deleting a department with devices fails."""
        with app.app_context():
            service = DepartmentsService()
            
            dept = service.create_department(name="Dept With Devices")
            
            # Create a device assigned to this department
            device = Device(
                device_name="Test Device",
                device_type="server",
                device_ip="192.168.1.200",
                department_id=dept.id
            )
            db.session.add(device)
            db.session.commit()
            
            with pytest.raises(ValueError, match="device\\(s\\) are assigned"):
                service.delete_department(dept.id)


class TestPrintJobsService:
    """Test cases for PrintJobsService."""
    
    def test_create_print_job(self, app):
        """Test creating a print job."""
        with app.app_context():
            service = PrintJobsService()
            
            # Create a printer device first
            printer = Device(
                device_name="Test Printer",
                device_type="printer",
                device_ip="192.168.1.50"
            )
            db.session.add(printer)
            db.session.commit()
            
            job_data = {
                'user_account': 'testuser',
                'document_name': 'test.pdf',
                'printer_name': 'Test Printer',
                'submission_time': datetime.utcnow(),
                'page_count': 5,
                'collection_source': 'wef',
                'device_id': printer.device_id
            }
            
            job = service.create_print_job(job_data)
            
            assert job.id is not None
            assert job.user_account == 'testuser'
            assert job.document_name == 'test.pdf'
            assert job.page_count == 5
            assert job.collection_source == 'wef'
    
    def test_list_print_jobs(self, app):
        """Test listing print jobs with pagination."""
        with app.app_context():
            service = PrintJobsService()
            
            # Create a printer device
            printer = Device(
                device_name="List Test Printer",
                device_type="printer",
                device_ip="192.168.1.51"
            )
            db.session.add(printer)
            db.session.commit()
            
            # Create multiple print jobs
            for i in range(5):
                job_data = {
                    'user_account': f'user{i}',
                    'document_name': f'doc{i}.pdf',
                    'printer_name': 'List Test Printer',
                    'submission_time': datetime.utcnow() - timedelta(hours=i),
                    'page_count': i + 1,
                    'collection_source': 'syslog',
                    'device_id': printer.device_id
                }
                service.create_print_job(job_data)
            
            result = service.list_print_jobs(page=1, page_size=3)
            
            assert len(result['jobs']) == 3
            assert result['total'] >= 5
            assert result['page'] == 1
            assert result['page_size'] == 3
    
    def test_list_print_jobs_with_filters(self, app):
        """Test listing print jobs with filters."""
        with app.app_context():
            service = PrintJobsService()
            
            # Create a printer device
            printer = Device(
                device_name="Filter Test Printer",
                device_type="printer",
                device_ip="192.168.1.52"
            )
            db.session.add(printer)
            db.session.commit()
            
            # Create print jobs
            now = datetime.utcnow()
            job_data1 = {
                'user_account': 'alice',
                'document_name': 'report.pdf',
                'printer_name': 'Filter Test Printer',
                'submission_time': now - timedelta(hours=1),
                'page_count': 10,
                'collection_source': 'wef',
                'device_id': printer.device_id
            }
            job_data2 = {
                'user_account': 'bob',
                'document_name': 'invoice.pdf',
                'printer_name': 'Filter Test Printer',
                'submission_time': now - timedelta(hours=2),
                'page_count': 5,
                'collection_source': 'wef',
                'device_id': printer.device_id
            }
            service.create_print_job(job_data1)
            service.create_print_job(job_data2)
            
            # Filter by user
            result = service.list_print_jobs(filters={'user': 'alice'})
            
            assert len(result['jobs']) >= 1
            assert all('alice' in job.user_account.lower() for job in result['jobs'])
    
    def test_get_total_pages(self, app):
        """Test getting total page count."""
        with app.app_context():
            service = PrintJobsService()
            
            # Create a printer device
            printer = Device(
                device_name="Pages Test Printer",
                device_type="printer",
                device_ip="192.168.1.53"
            )
            db.session.add(printer)
            db.session.commit()
            
            # Create print jobs with different page counts
            for page_count in [5, 10, 15]:
                job_data = {
                    'user_account': 'testuser',
                    'document_name': 'test.pdf',
                    'printer_name': 'Pages Test Printer',
                    'submission_time': datetime.utcnow(),
                    'page_count': page_count,
                    'collection_source': 'snmp',
                    'device_id': printer.device_id
                }
                service.create_print_job(job_data)
            
            total_pages = service.get_total_pages()
            
            assert total_pages >= 30  # 5 + 10 + 15
    
    def test_export_to_csv(self, app):
        """Test exporting print jobs to CSV."""
        with app.app_context():
            service = PrintJobsService()
            
            # Create a printer device
            printer = Device(
                device_name="CSV Test Printer",
                device_type="printer",
                device_ip="192.168.1.54"
            )
            db.session.add(printer)
            db.session.commit()
            
            # Create a print job
            job_data = {
                'user_account': 'csvuser',
                'document_name': 'export.pdf',
                'printer_name': 'CSV Test Printer',
                'submission_time': datetime.utcnow(),
                'page_count': 3,
                'collection_source': 'wef',
                'device_id': printer.device_id
            }
            service.create_print_job(job_data)
            
            csv_output = service.export_to_csv()
            
            assert 'Job ID' in csv_output
            assert 'csvuser' in csv_output
            assert 'export.pdf' in csv_output
    
    def test_cleanup_old_jobs(self, app):
        """Test cleaning up old print jobs."""
        with app.app_context():
            service = PrintJobsService()
            
            # Create a printer device
            printer = Device(
                device_name="Cleanup Test Printer",
                device_type="printer",
                device_ip="192.168.1.55"
            )
            db.session.add(printer)
            db.session.commit()
            
            # Create an old print job
            old_job_data = {
                'user_account': 'olduser',
                'document_name': 'old.pdf',
                'printer_name': 'Cleanup Test Printer',
                'submission_time': datetime.utcnow() - timedelta(days=100),
                'page_count': 1,
                'collection_source': 'wef',
                'device_id': printer.device_id
            }
            service.create_print_job(old_job_data)
            
            # Run cleanup with 90-day retention
            result = service.cleanup_old_jobs(days=90)
            
            assert result['success'] is True
            assert result['deleted_count'] >= 1
