import pytest
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.restricted_site_policy import RestrictedSiteDomainMeta
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.unit


def _make_device(mac='AA:BB:CC:DD:EE:01'):
    device = TrackedDevice(mac_address=mac, device_name='Endpoint-01')
    db.session.add(device)
    db.session.commit()
    return device


def test_restricted_site_domain_meta_insert_and_update():
    device = _make_device()
    row = RestrictedSiteDomainMeta(
        device_id=device.id,
        domain='youtube.com',
        category='Productivity',
        reason='Streaming media block',
        created_by='tester',
        updated_by='tester',
    )
    db.session.add(row)
    db.session.commit()

    stored = RestrictedSiteDomainMeta.query.filter_by(device_id=device.id, domain='youtube.com').first()
    assert stored is not None
    assert stored.category == 'Productivity'

    stored.reason = 'Updated reason'
    stored.updated_by = 'tester-2'
    db.session.commit()

    updated = RestrictedSiteDomainMeta.query.filter_by(device_id=device.id, domain='youtube.com').first()
    assert updated.reason == 'Updated reason'
    assert updated.updated_by == 'tester-2'


def test_restricted_site_domain_meta_unique_constraint_per_device_domain():
    device = _make_device(mac='AA:BB:CC:DD:EE:02')
    first = RestrictedSiteDomainMeta(device_id=device.id, domain='example.com', category='Custom')
    duplicate = RestrictedSiteDomainMeta(device_id=device.id, domain='example.com', category='Security')

    db.session.add(first)
    db.session.commit()

    db.session.add(duplicate)
    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()
