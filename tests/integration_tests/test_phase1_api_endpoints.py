"""
Integration tests for Phase 1 MVP API endpoints.
Tests Sites, Departments, Printers, and Print Jobs REST APIs.
"""
import pytest
from datetime import datetime


class TestSitesAPI:
    """Test Sites REST API endpoints."""
    
    def test_list_sites(self, client):
        """Test GET /api/sites returns list of sites."""
        response = client.get('/api/sites')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'data' in data
        assert isinstance(data['data'], list)
    
    def test_create_site(self, client):
        """Test POST /api/sites creates a new site."""
        site_data = {
            'site_name': 'Test Site',
            'site_code': 'TST',
            'address': '123 Test St',
            'timezone': 'UTC',
            'contact_name': 'Test Contact',
            'contact_email': 'test@example.com'
        }
        response = client.post('/api/sites', json=site_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['site_name'] == 'Test Site'
        assert data['data']['site_code'] == 'TST'
    
    def test_get_site(self, client, sample_site):
        """Test GET /api/sites/<id> returns site details."""
        response = client.get(f'/api/sites/{sample_site.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['id'] == sample_site.id
    
    def test_update_site(self, client, sample_site):
        """Test PUT /api/sites/<id> updates site."""
        update_data = {'site_name': 'Updated Site Name'}
        response = client.put(f'/api/sites/{sample_site.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['site_name'] == 'Updated Site Name'
    
    def test_delete_site_without_devices(self, client, sample_site):
        """Test DELETE /api/sites/<id> deletes site with no devices."""
        response = client.delete(f'/api/sites/{sample_site.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'


class TestDepartmentsAPI:
    """Test Departments REST API endpoints."""
    
    def test_list_departments(self, client):
        """Test GET /api/departments returns list of departments."""
        response = client.get('/api/departments')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'data' in data
        assert isinstance(data['data'], list)
    
    def test_create_department(self, client):
        """Test POST /api/departments creates a new department."""
        dept_data = {
            'name': 'Test Department',
            'description': 'Test description'
        }
        response = client.post('/api/departments', json=dept_data)
        assert response.status_code == 201
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['name'] == 'Test Department'
    
    def test_get_department(self, client, sample_department):
        """Test GET /api/departments/<id> returns department details."""
        response = client.get(f'/api/departments/{sample_department.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['id'] == sample_department.id
    
    def test_update_department(self, client, sample_department):
        """Test PUT /api/departments/<id> updates department."""
        update_data = {'name': 'Updated Department'}
        response = client.put(f'/api/departments/{sample_department.id}', json=update_data)
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['name'] == 'Updated Department'
    
    def test_delete_department_without_devices(self, client, sample_department):
        """Test DELETE /api/departments/<id> deletes department with no devices."""
        response = client.delete(f'/api/departments/{sample_department.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
    
    def test_delete_department_with_devices_fails(self, client, sample_department, sample_device):
        """Test DELETE /api/departments/<id> returns 409 when devices exist."""
        # Assign device to department
        sample_device.department_id = sample_department.id
        from extensions import db
        db.session.commit()
        
        response = client.delete(f'/api/departments/{sample_department.id}')
        assert response.status_code == 409
        data = response.get_json()
        assert data['status'] == 'error'


class TestPrintersAPI:
    """Test Printers REST API endpoints."""
    
    def test_list_printers(self, client):
        """Test GET /api/printers returns list of printers."""
        response = client.get('/api/printers')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'data' in data
        assert isinstance(data['data'], list)
    
    def test_get_printer_details(self, client, sample_printer):
        """Test GET /api/printers/<id> returns printer details."""
        response = client.get(f'/api/printers/{sample_printer.device_id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['data']['device_id'] == sample_printer.device_id
    
    def test_list_printers_filtered_by_site(self, client, sample_printer, sample_site):
        """Test GET /api/printers?site_id=<id> filters by site."""
        sample_printer.site_id = sample_site.id
        from extensions import db
        db.session.commit()
        
        response = client.get(f'/api/printers?site_id={sample_site.id}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        # Should only return printers from this site
        for printer in data['data']:
            assert printer.get('site_id') == sample_site.id


class TestPrintJobsAPI:
    """Test Print Jobs REST API endpoints."""
    
    def test_list_print_jobs(self, client):
        """Test GET /api/print-jobs returns list of print jobs."""
        response = client.get('/api/print-jobs')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'data' in data
        assert 'meta' in data
        assert isinstance(data['data'], list)
    
    def test_list_print_jobs_with_pagination(self, client):
        """Test GET /api/print-jobs supports pagination."""
        response = client.get('/api/print-jobs?page=1&page_size=10')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['meta']['page'] == 1
        assert data['meta']['page_size'] == 10
    
    def test_list_print_jobs_filtered_by_user(self, client, sample_print_job):
        """Test GET /api/print-jobs?user=<name> filters by user."""
        response = client.get(f'/api/print-jobs?user={sample_print_job.user_account}')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        # All returned jobs should match the user filter
        for job in data['data']:
            assert sample_print_job.user_account.lower() in job['user_account'].lower()
    
    def test_get_print_job_stats(self, client):
        """Test GET /api/print-jobs/stats returns statistics."""
        response = client.get('/api/print-jobs/stats')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'total_jobs' in data['data']
        assert 'total_pages' in data['data']
        assert 'unique_users' in data['data']
