"""Tests for reports blueprint auth guard and export RBAC.

Verifies:
  - Unauthenticated requests are redirected (302) to login
  - Viewer can access reports.view endpoints (200)
  - Viewer cannot access reports.export endpoints (403)
  - Admin and Manager can access reports.export endpoints (200)
"""
import pytest

pytestmark = pytest.mark.integration


# ── Unauthenticated access → redirect to login ──────────────────────────────

class TestUnauthenticatedAccess:

    def test_reports_page_redirects_to_login(self, client):
        resp = client.get('/reports')
        assert resp.status_code == 302
        assert '/login' in resp.headers.get('Location', '')

    def test_api_executive_report_redirects(self, client):
        resp = client.get('/api/reports/executive')
        assert resp.status_code in (302, 401)

    def test_api_export_redirects(self, client):
        resp = client.get('/api/reports/executive/export')
        assert resp.status_code in (302, 401)

    def test_api_export_job_create_redirects(self, client):
        resp = client.post('/api/reports/executive/export-jobs')
        assert resp.status_code in (302, 401, 403)

    def test_api_enterprise_uptime_pdf_redirects(self, client):
        resp = client.get('/api/reports/enterprise-uptime/pdf')
        assert resp.status_code in (302, 401)


# ── Viewer role — has reports.view, lacks reports.export ────────────────────

class TestViewerAccess:

    def test_viewer_can_access_reports_page(self, viewer_client):
        resp = viewer_client.get('/reports')
        assert resp.status_code == 200

    def test_viewer_can_access_executive_api(self, viewer_client):
        resp = viewer_client.get('/api/reports/executive')
        assert resp.status_code == 200

    def test_viewer_cannot_export(self, viewer_client):
        resp = viewer_client.get('/api/reports/executive/export')
        assert resp.status_code == 403

    def test_viewer_cannot_create_export_job(self, viewer_client):
        resp = viewer_client.post('/api/reports/executive/export-jobs')
        assert resp.status_code == 403

    def test_viewer_cannot_access_enterprise_pdf(self, viewer_client):
        resp = viewer_client.get('/api/reports/enterprise-uptime/pdf')
        assert resp.status_code == 403


# ── Admin role — full access ────────────────────────────────────────────────

class TestAdminAccess:

    def test_admin_can_access_reports_page(self, admin_client):
        resp = admin_client.get('/reports')
        assert resp.status_code == 200

    def test_admin_can_access_executive_api(self, admin_client):
        resp = admin_client.get('/api/reports/executive')
        assert resp.status_code == 200

    def test_admin_can_export(self, admin_client):
        resp = admin_client.get('/api/reports/executive/export')
        assert resp.status_code == 200
        assert resp.mimetype == 'application/pdf'

    def test_admin_can_access_enterprise_pdf(self, admin_client):
        resp = admin_client.get('/api/reports/enterprise-uptime/pdf')
        assert resp.status_code == 200
        assert resp.data.startswith(b'%PDF-')


# ── Manager role — has reports.export ───────────────────────────────────────

class TestManagerAccess:

    def test_manager_can_export(self, manager_client):
        resp = manager_client.get('/api/reports/executive/export')
        assert resp.status_code == 200
        assert resp.mimetype == 'application/pdf'

    def test_manager_can_create_export_job(self, manager_client):
        resp = manager_client.post('/api/reports/executive/export-jobs')
        assert resp.status_code in (200, 201, 202)
