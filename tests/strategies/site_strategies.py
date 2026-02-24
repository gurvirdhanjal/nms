"""
Hypothesis strategies for generating Site test data.

Provides strategies for creating valid Site objects with randomized fields
for property-based testing.
"""
from hypothesis import strategies as st
from hypothesis.strategies import composite
import string


# Valid timezone samples (subset of common timezones)
VALID_TIMEZONES = [
    'UTC',
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Los_Angeles',
    'Europe/London',
    'Europe/Paris',
    'Europe/Berlin',
    'Asia/Tokyo',
    'Asia/Shanghai',
    'Asia/Dubai',
    'Australia/Sydney',
]


@composite
def site_strategy(draw):
    """
    Generate valid Site objects with random fields.
    
    Returns a dictionary with site data that can be used to create a Site object.
    
    Fields:
        - name: 1-100 chars, no control characters
        - site_code: Optional 1-50 chars alphanumeric code
        - address: Optional text up to 500 chars
        - timezone: Valid timezone string
        - contact_name: Optional 1-200 chars
        - contact_email: Optional email format
        - contact_phone: Optional phone number format
    
    Usage:
        @given(site_data=site_strategy())
        def test_something(site_data):
            site = Site(**site_data)
    """
    # Generate site name (1-100 chars, printable ASCII, no control chars)
    name = draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd', 'Pd', 'Zs'),
            blacklist_characters='\n\r\t'
        )
    ).filter(lambda x: x.strip()))  # Ensure not just whitespace
    
    # Generate optional site code (alphanumeric, 1-50 chars)
    site_code = draw(st.one_of(
        st.none(),
        st.text(
            min_size=1,
            max_size=50,
            alphabet=string.ascii_uppercase + string.digits + '-_'
        )
    ))
    
    # Generate optional address
    address = draw(st.one_of(
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
    
    # Select valid timezone
    timezone = draw(st.sampled_from(VALID_TIMEZONES))
    
    # Generate optional contact name
    contact_name = draw(st.one_of(
        st.none(),
        st.text(
            min_size=1,
            max_size=200,
            alphabet=st.characters(
                whitelist_categories=('Lu', 'Ll', 'Zs'),
                blacklist_characters='\n\r\t'
            )
        ).filter(lambda x: x.strip())
    ))
    
    # Generate optional contact email
    contact_email = draw(st.one_of(
        st.none(),
        st.emails()
    ))
    
    # Generate optional contact phone
    contact_phone = draw(st.one_of(
        st.none(),
        st.from_regex(r'\+?[0-9]{1,3}[-.\s]?(\([0-9]{3}\)|[0-9]{3})[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}', fullmatch=True)
    ))
    
    return {
        'name': name,
        'site_code': site_code,
        'address': address,
        'timezone': timezone,
        'contact_info': {
            'contact_name': contact_name,
            'contact_email': contact_email,
            'contact_phone': contact_phone,
        }
    }


@composite
def site_with_devices_strategy(draw, min_devices=1, max_devices=10):
    """
    Generate Site data with indication that devices should be assigned.
    
    Args:
        min_devices: Minimum number of devices to assign
        max_devices: Maximum number of devices to assign
    
    Returns:
        Dictionary with site data and device_count
    
    Usage:
        @given(site_data=site_with_devices_strategy(min_devices=1, max_devices=5))
        def test_something(site_data):
            # Create site and assign site_data['device_count'] devices
    """
    site_data = draw(site_strategy())
    device_count = draw(st.integers(min_value=min_devices, max_value=max_devices))
    
    site_data['device_count'] = device_count
    return site_data
