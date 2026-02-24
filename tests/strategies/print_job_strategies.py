"""
Hypothesis strategies for generating PrintJobAudit test data.

Provides strategies for creating valid PrintJobAudit objects with randomized fields
for property-based testing.
"""
from hypothesis import strategies as st
from hypothesis.strategies import composite
from datetime import datetime, timedelta
import string


# Valid collection sources
COLLECTION_SOURCES = ['wef', 'syslog', 'snmp']

# Valid job statuses
JOB_STATUSES = ['submitted', 'printing', 'completed', 'failed', 'cancelled']


@composite
def print_job_strategy(draw):
    """
    Generate valid PrintJobAudit objects with random fields.
    
    Returns a dictionary with print job data that can be used to create a PrintJobAudit object.
    
    Fields:
        - user_account: 1-100 chars username
        - document_name: 1-255 chars document name
        - printer_name: 1-100 chars printer name
        - submission_time: Valid datetime between 2020-2025
        - page_count: 1-10000 pages
        - collection_source: One of: wef, syslog, snmp
        - job_id: Optional job identifier
        - source_ip: Optional IP address
        - size_bytes: Optional file size
        - completion_time: Optional completion datetime
        - status: Optional job status
    
    Usage:
        @given(job_data=print_job_strategy())
        def test_something(job_data):
            job = PrintJobAudit(**job_data)
    """
    # Generate username (1-100 chars, alphanumeric + common chars)
    user_account = draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=string.ascii_letters + string.digits + '._-@'
    ).filter(lambda x: x and not x.isspace()))
    
    # Generate document name (1-255 chars)
    document_name = draw(st.text(
        min_size=1,
        max_size=255,
        alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd', 'Pd', 'Po', 'Zs'),
            blacklist_characters='\n\r\t'
        )
    ).filter(lambda x: x.strip()))
    
    # Generate printer name (1-100 chars)
    printer_name = draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=string.ascii_letters + string.digits + '._-'
    ).filter(lambda x: x and not x.isspace()))
    
    # Generate submission time (between 2020 and 2025)
    min_date = datetime(2020, 1, 1)
    max_date = datetime(2025, 12, 31)
    days_between = (max_date - min_date).days
    random_days = draw(st.integers(min_value=0, max_value=days_between))
    submission_time = min_date + timedelta(days=random_days)
    
    # Generate page count (1-10000)
    page_count = draw(st.integers(min_value=1, max_value=10000))
    
    # Select collection source
    collection_source = draw(st.sampled_from(COLLECTION_SOURCES))
    
    # Generate optional job ID
    job_id = draw(st.one_of(
        st.none(),
        st.text(
            min_size=1,
            max_size=100,
            alphabet=string.ascii_letters + string.digits + '-_'
        )
    ))
    
    # Generate optional source IP
    source_ip = draw(st.one_of(
        st.none(),
        st.ip_addresses(v=4).map(str),
        st.ip_addresses(v=6).map(str)
    ))
    
    # Generate optional file size (0-1GB)
    size_bytes = draw(st.one_of(
        st.none(),
        st.integers(min_value=0, max_value=1073741824)  # 1GB
    ))
    
    # Generate optional completion time (after submission)
    completion_time = draw(st.one_of(
        st.none(),
        st.integers(min_value=1, max_value=3600).map(
            lambda seconds: submission_time + timedelta(seconds=seconds)
        )
    ))
    
    # Select optional status
    status = draw(st.one_of(
        st.none(),
        st.sampled_from(JOB_STATUSES)
    ))
    
    return {
        'user_account': user_account,
        'document_name': document_name,
        'printer_name': printer_name,
        'submission_time': submission_time,
        'page_count': page_count,
        'collection_source': collection_source,
        'job_id': job_id or f"job_{int(submission_time.timestamp())}",
        'source_ip': source_ip,
        'size_bytes': size_bytes,
        'completion_time': completion_time,
        'status': status or 'completed',
    }
