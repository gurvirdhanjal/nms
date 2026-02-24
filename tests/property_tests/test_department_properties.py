"""
Property-based tests for Department management.

Tests universal correctness properties for department CRUD operations, deletion protection,
and filtering using Hypothesis.
"""
import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from tests.strategies.department_strategies import department_strategy
from services.departments_service import DepartmentsService
from models.department import Department
from models.device import Device
from models.user import User
from extensions import db


# Feature: phase-1-verification-testing, Property 15: Department CRUD Round Trip
@given(dept_data=department_strategy())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_department_crud_round_trip(db_session, dept_data):
    """
    For any department with valid name and description, creating the department and
    then retrieving it should return an equivalent department with all fields preserved
    and a unique ID assigned.
    
    **Validates: Requirements 6.1**
    """
    # Create service
    departments_service = DepartmentsService()
    
    # Create department
    created_dept = departments_service.create_department(
        name=dept_data['name'],
        description=dept_data['description'],
        site_id=dept_data['site_id']
    )
    
    # Verify department was created with ID
    assert created_dept is not None
    assert created_dept.id is not None
    assert created_dept.id > 0
    
    # Retrieve department
    retrieved_dept = departments_service.get_department(created_dept.id)
    
    # Verify all fields preserved
    assert retrieved_dept is not None
    assert retrieved_dept.id == created_dept.id
    assert retrieved_dept.name == dept_data['name']
    assert retrieved_dept.description == dept_data['description']
    assert retrieved_dept.site_id == dept_data['site_id']


# Feature: phase-1-verification-testing, Property 16: Department Deletion Protection
@given(dept_data=department_strategy(), has_devices=st.booleans())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_department_deletion_protection(db_session, dept_data, has_devices):
    """
    For any department that has at least one device or user assigned to it, attempting
    to delete that department should raise an error, and the department should remain
    in the database unchanged.
    
    **Validates: Requirements 6.2**
    """
    # Create service
    departments_service = DepartmentsService()
    
    # Create department
    dept = departments_service.create_department(
        name=dept_data['name'],
        description=dept_data['description'],
        site_id=dept_data['site_id']
    )
    
    # Assign either a device or a user to the department
    if has_devices:
        # Create a device assigned to this department
        device = Device(
            device_name=f"TestDevice_{dept.id}",
            device_type='printer',
            ip_address='192.168.1.100',
            department_id=dept.id
        )
        db_session.add(device)
    else:
        # Create a user assigned to this department
        user = User(
            username=f"testuser_{dept.id}",
            email=f"test_{dept.id}@example.com",
            department_id=dept.id
        )
        db_session.add(user)
    
    db_session.commit()
    
    # Attempt to delete department should raise error
    with pytest.raises(ValueError) as exc_info:
        departments_service.delete_department(dept.id)
    
    # Verify error message mentions devices or users
    error_msg = str(exc_info.value).lower()
    assert 'device' in error_msg or 'user' in error_msg
    
    # Verify department still exists
    retrieved_dept = departments_service.get_department(dept.id)
    assert retrieved_dept is not None
    assert retrieved_dept.id == dept.id


# Feature: phase-1-verification-testing, Property 17: Empty Department Deletion
@given(dept_data=department_strategy())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_empty_department_deletion(db_session, dept_data):
    """
    For any department that has no devices or users assigned to it, deleting that
    department should succeed, and subsequent attempts to retrieve that department
    should return None.
    
    **Validates: Requirements 6.3**
    """
    # Create service
    departments_service = DepartmentsService()
    
    # Create department
    dept = departments_service.create_department(
        name=dept_data['name'],
        description=dept_data['description'],
        site_id=dept_data['site_id']
    )
    
    dept_id = dept.id
    
    # Verify department has no devices or users
    device_count = Device.query.filter_by(department_id=dept_id).count()
    user_count = User.query.filter_by(department_id=dept_id).count()
    assert device_count == 0
    assert user_count == 0
    
    # Delete department should succeed
    result = departments_service.delete_department(dept_id)
    assert result is True
    
    # Verify department no longer exists
    retrieved_dept = departments_service.get_department(dept_id)
    assert retrieved_dept is None


# Feature: phase-1-verification-testing, Property 18: Department Filtering Correctness
@given(
    dept1_data=department_strategy(),
    dept2_data=department_strategy(),
    devices_per_dept=st.integers(min_value=1, max_value=5)
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_department_filtering_correctness(db_session, dept1_data, dept2_data, devices_per_dept):
    """
    For any department filter applied to device lists, the results should contain only
    data for devices assigned to the specified department, and should not include data
    from other departments.
    
    **Validates: Requirements 6.4**
    """
    # Create service
    departments_service = DepartmentsService()
    
    # Ensure department names are unique
    if dept1_data['name'] == dept2_data['name']:
        dept2_data['name'] = dept2_data['name'] + '_2'
    
    # Create two departments
    dept1 = departments_service.create_department(
        name=dept1_data['name'],
        description=dept1_data['description'],
        site_id=dept1_data['site_id']
    )
    
    dept2 = departments_service.create_department(
        name=dept2_data['name'],
        description=dept2_data['description'],
        site_id=dept2_data['site_id']
    )
    
    # Create devices for dept1
    dept1_device_ids = []
    for i in range(devices_per_dept):
        device = Device(
            device_name=f"Dept1_Device_{i}",
            device_type='printer',
            ip_address=f'192.168.1.{100 + i}',
            department_id=dept1.id
        )
        db_session.add(device)
        db_session.flush()
        dept1_device_ids.append(device.device_id)
    
    # Create devices for dept2
    dept2_device_ids = []
    for i in range(devices_per_dept):
        device = Device(
            device_name=f"Dept2_Device_{i}",
            device_type='printer',
            ip_address=f'192.168.2.{100 + i}',
            department_id=dept2.id
        )
        db_session.add(device)
        db_session.flush()
        dept2_device_ids.append(device.device_id)
    
    db_session.commit()
    
    # Get devices for dept1
    dept1_devices = departments_service.get_department_devices(dept1.id)
    
    # Verify all devices belong to dept1
    assert len(dept1_devices) == devices_per_dept
    for device in dept1_devices:
        assert device.department_id == dept1.id
        assert device.device_id in dept1_device_ids
        assert device.device_id not in dept2_device_ids
    
    # Get devices for dept2
    dept2_devices = departments_service.get_department_devices(dept2.id)
    
    # Verify all devices belong to dept2
    assert len(dept2_devices) == devices_per_dept
    for device in dept2_devices:
        assert device.department_id == dept2.id
        assert device.device_id in dept2_device_ids
        assert device.device_id not in dept1_device_ids
