"""
Hypothesis strategies for generating Department test data.

Provides strategies for creating valid Department objects with randomized fields
for property-based testing.
"""
from hypothesis import strategies as st
from hypothesis.strategies import composite


@composite
def department_strategy(draw):
    """
    Generate valid Department objects with random fields.
    
    Returns a dictionary with department data that can be used to create a Department object.
    
    Fields:
        - name: 1-100 chars, no control characters
        - description: Optional text up to 500 chars
        - site_id: Optional integer (will be set by test if needed)
    
    Usage:
        @given(dept_data=department_strategy())
        def test_something(dept_data):
            dept = Department(**dept_data)
    """
    # Generate department name (1-100 chars, printable ASCII, no control chars)
    name = draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd', 'Pd', 'Zs'),
            blacklist_characters='\n\r\t'
        )
    ).filter(lambda x: x.strip()))  # Ensure not just whitespace
    
    # Generate optional description
    description = draw(st.one_of(
        st.none(),
        st.text(
            min_size=0,
            max_size=500,
            alphabet=st.characters(
                whitelist_categories=('Lu', 'Ll', 'Nd', 'Pd', 'Zs', 'Po'),
                blacklist_characters='\n\r\t'
            )
        )
    ))
    
    return {
        'name': name,
        'description': description,
        'site_id': None  # Will be set by test if needed
    }


@composite
def department_with_users_strategy(draw, min_users=1, max_users=10):
    """
    Generate Department data with indication that users should be assigned.
    
    Args:
        min_users: Minimum number of users to assign
        max_users: Maximum number of users to assign
    
    Returns:
        Dictionary with department data and user_count
    
    Usage:
        @given(dept_data=department_with_users_strategy(min_users=1, max_users=5))
        def test_something(dept_data):
            # Create department and assign dept_data['user_count'] users
    """
    dept_data = draw(department_strategy())
    user_count = draw(st.integers(min_value=min_users, max_value=max_users))
    
    dept_data['user_count'] = user_count
    return dept_data


@composite
def department_with_devices_strategy(draw, min_devices=1, max_devices=10):
    """
    Generate Department data with indication that devices should be assigned.
    
    Args:
        min_devices: Minimum number of devices to assign
        max_devices: Maximum number of devices to assign
    
    Returns:
        Dictionary with department data and device_count
    
    Usage:
        @given(dept_data=department_with_devices_strategy(min_devices=1, max_devices=5))
        def test_something(dept_data):
            # Create department and assign dept_data['device_count'] devices
    """
    dept_data = draw(department_strategy())
    device_count = draw(st.integers(min_value=min_devices, max_value=max_devices))
    
    dept_data['device_count'] = device_count
    return dept_data
