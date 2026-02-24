"""
Pytest configuration and fixtures for Phase 1 MVP testing.

This module provides:
- Test database setup and isolation
- Session and transaction fixtures
- Common test data fixtures
"""

import pytest
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from extensions import db as _db
from app import create_app


@pytest.fixture(scope='session')
def app():
    """Create Flask application for testing."""
    app = create_app()
    app.config['TESTING'] = True
    # Use the configured database (PostgreSQL)
    # Don't override SQLALCHEMY_DATABASE_URI - use the one from config
    app.config['WTF_CSRF_ENABLED'] = False
    
    # Don't create/drop all tables - use existing database
    yield app


@pytest.fixture(scope='function')
def db_session(app):
    """
    Provide database session with automatic rollback.
    
    Each test gets a fresh session that is rolled back after the test completes,
    ensuring test isolation and preventing production data contamination.
    """
    with app.app_context():
        connection = _db.engine.connect()
        transaction = connection.begin()
        
        # Create a session bound to the connection
        session = scoped_session(
            sessionmaker(bind=connection)
        )
        
        # Override the db.session with our test session
        _db.session = session
        
        yield session
        
        # Rollback and cleanup
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope='function')
def client(app):
    """Provide Flask test client."""
    return app.test_client()


@pytest.fixture(scope='function')
def runner(app):
    """Provide Flask CLI test runner."""
    return app.test_cli_runner()


# ============================================================================
# Phase 1 MVP Fixtures
# ============================================================================

@pytest.fixture
def sample_site(db_session):
    """Create a sample site for testing."""
    from models.site import Site
    site = Site(
        site_name='Test Site',
        site_code='TST',
        address='123 Test Street',
        timezone='UTC',
        contact_name='Test Contact',
        contact_email='test@example.com'
    )
    db_session.add(site)
    db_session.commit()
    return site


@pytest.fixture
def sample_department(db_session):
    """Create a sample department for testing."""
    from models.department import Department
    dept = Department(
        name='Test Department',
        description='Test department description'
    )
    db_session.add(dept)
    db_session.commit()
    return dept


@pytest.fixture
def sample_device(db_session):
    """Create a sample device for testing."""
    from models.device import Device
    device = Device(
        device_name='Test Device',
        device_ip='192.168.1.100',
        device_type='workstation',
        status='online'
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def sample_printer(db_session):
    """Create a sample printer device for testing."""
    from models.device import Device
    printer = Device(
        device_name='Test Printer',
        device_ip='192.168.1.200',
        device_type='printer',
        status='online'
    )
    db_session.add(printer)
    db_session.commit()
    return printer


@pytest.fixture
def sample_print_job(db_session, sample_printer):
    """Create a sample print job for testing."""
    from models.printer import PrintJobAudit
    from datetime import datetime
    
    job = PrintJobAudit(
        device_id=sample_printer.device_id,
        job_id='TEST-JOB-001',
        document_name='test_document.pdf',
        user_account='testuser',
        printer_name='Test Printer',
        page_count=5,
        submission_time=datetime.utcnow(),
        status='completed',
        collection_source='test'
    )
    db_session.add(job)
    db_session.commit()
    return job
