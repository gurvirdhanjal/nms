"""
Property-based tests for Site management.

Tests universal correctness properties for site CRUD operations, deletion protection,
and statistics accuracy using Hypothesis.
"""
import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from tests.strategies.site_strategies import site_strategy, site_with_devices_strategy
from services.sites_service import SitesService
from models.site import Site
from models.device import Device
from extensions import db


# Feature: phase-1-verification-testing, Property 10: Site CRUD Round Trip
@given(site_data=site_strategy())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_site_crud_round_trip(db_session, site_data):
    """
    For any site with valid fields, creating the site and then retrieving it
    should return an equivalent site with all fields preserved and a unique ID assigned.
    
    **Validates: Requirements 5.1**
    """
    # Create service
    sites_service = SitesService()
    
    # Create site
    created_site = sites_service.create_site(
        name=site_data['name'],
        address=site_data['address'],
        timezone=site_data['timezone'],
        contact_info=site_data['contact_info']
    )
    
    # Verify site was created with ID
    assert created_site is not None
    assert created_site.id is not None
    assert created_site.id > 0
    
    # Retrieve site
    retrieved_site = sites_service.get_site(created_site.id)
    
    # Verify all fields preserved
    assert retrieved_site is not None
    assert retrieved_site.id == created_site.id
    assert retrieved_site.site_name == site_data['name']
    assert retrieved_site.address == site_data['address']
    assert retrieved_site.timezone == site_data['timezone']
    assert retrieved_site.contact_name == site_data['contact_info'].get('contact_name')
    assert retrieved_site.contact_email == site_data['contact_info'].get('contact_email')
    assert retrieved_site.contact_phone == site_data['contact_info'].get('contact_phone')


# Feature: phase-1-verification-testing, Property 11: Site Deletion Protection
@given(site_data=site_strategy())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_site_deletion_protection(db_session, site_data):
    """
    For any site that has at least one device assigned to it, attempting to delete
    that site should raise an error, and the site should remain in the database unchanged.
    
    **Validates: Requirements 5.2**
    """
    # Create service
    sites_service = SitesService()
    
    # Create site
    site = sites_service.create_site(
        name=site_data['name'],
        address=site_data['address'],
        timezone=site_data['timezone'],
        contact_info=site_data['contact_info']
    )
    
    # Create a device assigned to this site
    device = Device(
        device_name=f"TestDevice_{site.id}",
        device_type='printer',
        ip_address='192.168.1.100',
        site_id=site.id
    )
    db_session.add(device)
    db_session.commit()
    
    # Attempt to delete site should raise error
    with pytest.raises(ValueError) as exc_info:
        sites_service.delete_site(site.id)
    
    # Verify error message mentions devices
    assert 'device' in str(exc_info.value).lower()
    
    # Verify site still exists
    retrieved_site = sites_service.get_site(site.id)
    assert retrieved_site is not None
    assert retrieved_site.id == site.id


# Feature: phase-1-verification-testing, Property 12: Empty Site Deletion
@given(site_data=site_strategy())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_empty_site_deletion(db_session, site_data):
    """
    For any site that has no devices assigned to it, deleting that site should succeed,
    and subsequent attempts to retrieve that site should return None.
    
    **Validates: Requirements 5.3**
    """
    # Create service
    sites_service = SitesService()
    
    # Create site
    site = sites_service.create_site(
        name=site_data['name'],
        address=site_data['address'],
        timezone=site_data['timezone'],
        contact_info=site_data['contact_info']
    )
    
    site_id = site.id
    
    # Verify site has no devices
    device_count = Device.query.filter_by(site_id=site_id).count()
    assert device_count == 0
    
    # Delete site should succeed
    result = sites_service.delete_site(site_id)
    assert result is True
    
    # Verify site no longer exists
    retrieved_site = sites_service.get_site(site_id)
    assert retrieved_site is None


# Feature: phase-1-verification-testing, Property 13: Site Statistics Accuracy
@given(site_data=site_strategy(), device_count=st.integers(min_value=0, max_value=10))
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_site_statistics_accuracy(db_session, site_data, device_count):
    """
    For any site, the computed statistics (device count, online count, offline count,
    warning count) returned by get_site_stats should equal the actual counts of devices
    in those states assigned to that site.
    
    **Validates: Requirements 5.4**
    """
    # Create service
    sites_service = SitesService()
    
    # Create site
    site = sites_service.create_site(
        name=site_data['name'],
        address=site_data['address'],
        timezone=site_data['timezone'],
        contact_info=site_data['contact_info']
    )
    
    # Create devices for this site
    for i in range(device_count):
        device = Device(
            device_name=f"TestDevice_{site.id}_{i}",
            device_type='printer',
            ip_address=f'192.168.1.{100 + i}',
            site_id=site.id
        )
        db_session.add(device)
    db_session.commit()
    
    # Get site statistics
    stats = sites_service.get_site_stats(site.id)
    
    # Verify device count matches
    assert stats['device_count'] == device_count
    
    # Verify counts are non-negative
    assert stats['online_count'] >= 0
    assert stats['offline_count'] >= 0
    assert stats['warning_count'] >= 0
    
    # Verify online + offline = total
    assert stats['online_count'] + stats['offline_count'] == device_count


# Feature: phase-1-verification-testing, Property 14: Site Filtering Correctness
@given(
    site1_data=site_strategy(),
    site2_data=site_strategy(),
    devices_per_site=st.integers(min_value=1, max_value=5)
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_site_filtering_correctness(db_session, site1_data, site2_data, devices_per_site):
    """
    For any site filter applied to device lists, the results should contain only data
    for devices assigned to the specified site, and should not include data from devices
    at other sites.
    
    **Validates: Requirements 5.5**
    """
    # Create service
    sites_service = SitesService()
    
    # Ensure site names are unique
    if site1_data['name'] == site2_data['name']:
        site2_data['name'] = site2_data['name'] + '_2'
    
    # Create two sites
    site1 = sites_service.create_site(
        name=site1_data['name'],
        address=site1_data['address'],
        timezone=site1_data['timezone'],
        contact_info=site1_data['contact_info']
    )
    
    site2 = sites_service.create_site(
        name=site2_data['name'],
        address=site2_data['address'],
        timezone=site2_data['timezone'],
        contact_info=site2_data['contact_info']
    )
    
    # Create devices for site1
    site1_device_ids = []
    for i in range(devices_per_site):
        device = Device(
            device_name=f"Site1_Device_{i}",
            device_type='printer',
            ip_address=f'192.168.1.{100 + i}',
            site_id=site1.id
        )
        db_session.add(device)
        db_session.flush()
        site1_device_ids.append(device.device_id)
    
    # Create devices for site2
    site2_device_ids = []
    for i in range(devices_per_site):
        device = Device(
            device_name=f"Site2_Device_{i}",
            device_type='printer',
            ip_address=f'192.168.2.{100 + i}',
            site_id=site2.id
        )
        db_session.add(device)
        db_session.flush()
        site2_device_ids.append(device.device_id)
    
    db_session.commit()
    
    # Get devices for site1
    site1_devices = sites_service.get_site_devices(site1.id)
    
    # Verify all devices belong to site1
    assert len(site1_devices) == devices_per_site
    for device in site1_devices:
        assert device.site_id == site1.id
        assert device.device_id in site1_device_ids
        assert device.device_id not in site2_device_ids
    
    # Get devices for site2
    site2_devices = sites_service.get_site_devices(site2.id)
    
    # Verify all devices belong to site2
    assert len(site2_devices) == devices_per_site
    for device in site2_devices:
        assert device.site_id == site2.id
        assert device.device_id in site2_device_ids
        assert device.device_id not in site1_device_ids
