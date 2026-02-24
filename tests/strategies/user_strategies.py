"""
Hypothesis strategies for generating User test data with RBAC permissions.

Provides strategies for creating valid User objects with randomized RBAC fields
for property-based testing.
"""
from hypothesis import strategies as st
from hypothesis.strategies import composite
import string


@composite
def user_rbac_strategy(draw):
    """
    Generate users with RBAC permissions.
    
    Returns a dictionary with user data including RBAC permissions.
    
    Fields:
        - username: 1-100 chars username
        - department_id: Optional department ID (or None for no department)
        - view_own_department: Boolean permission
        - view_all_departments: Boolean permission
    
    Note: If view_all_departments is True, view_own_department is typically False
    
    Usage:
        @given(user_data=user_rbac_strategy())
        def test_something(user_data):
            user = User(**user_data)
    """
    # Generate username (1-100 chars, alphanumeric + common chars)
    username = draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=string.ascii_letters + string.digits + '._-'
    ).filter(lambda x: x and not x.isspace()))
    
    # Generate optional department_id (None or 1-100)
    department_id = draw(st.one_of(
        st.none(),
        st.integers(min_value=1, max_value=100)
    ))
    
    # Generate RBAC permissions
    # If user has no department, they can't have view_own_department
    if department_id is None:
        view_own_department = False
        view_all_departments = draw(st.booleans())
    else:
        # User has department - generate permissions
        # Typically either view_own or view_all, not both
        view_all_departments = draw(st.booleans())
        if view_all_departments:
            view_own_department = False
        else:
            view_own_department = draw(st.booleans())
    
    return {
        'username': username,
        'department_id': department_id,
        'view_own_department': view_own_department,
        'view_all_departments': view_all_departments,
    }
