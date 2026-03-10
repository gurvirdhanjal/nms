import pytest

from extensions import db
from models.audit_log import AuditLog
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.tracked_device import TrackedDevice
from services.device_link_service import DeviceLinkService


pytestmark = pytest.mark.unit


def _device(name, mac):
    row = Device(device_name=name, device_type='workstation', device_ip=f'10.0.0.{len(name) + 10}', macaddress=mac)
    db.session.add(row)
    db.session.flush()
    return row


def _tracked(name, mac):
    row = TrackedDevice(device_name=name, mac_address=mac, availability_status='online')
    db.session.add(row)
    db.session.flush()
    return row


def test_backfill_exact_mac_links_creates_active_link():
    device = _device('Inventory-1', 'AA-BB-CC-DD-EE-01')
    tracked = _tracked('Tracked-1', 'aa:bb:cc:dd:ee:01')
    db.session.commit()

    result = DeviceLinkService.backfill_exact_mac_links()

    assert result['created'] == 1
    link = DeviceLinkService.resolve_link_for_device(device.device_id)
    assert link is not None
    assert link.tracked_device_id == tracked.id
    assert link.normalized_mac == 'AA:BB:CC:DD:EE:01'
    assert DeviceLinkService.link_status_for_device(device.device_id).status == 'linked'
    assert DeviceLinkService.link_status_for_tracked_device(tracked.id).status == 'linked'


def test_backfill_ambiguous_candidates_creates_pending_review_candidates():
    device_one = _device('Inventory-A', 'AA:BB:CC:DD:EE:10')
    _device('Inventory-B', 'AA:BB:CC:DD:EE:10')
    tracked = _tracked('Tracked-A', 'AA:BB:CC:DD:EE:10')
    db.session.commit()

    result = DeviceLinkService.backfill_ambiguous_candidates()

    assert result['created'] == 2
    candidates = DeviceIdentityLinkCandidate.query.order_by(DeviceIdentityLinkCandidate.id.asc()).all()
    assert len(candidates) == 2
    assert all(row.status == 'pending' for row in candidates)
    status = DeviceLinkService.link_status_for_device(device_one.device_id)
    assert status.status == 'pending_review'
    assert status.candidate_count == 1
    assert DeviceLinkService.resolve_link_for_device(device_one.device_id) is None
    assert DeviceLinkService.resolve_inventory_device_for_tracked_device(tracked.id) is None


def test_decide_candidate_confirm_creates_link_rejects_competitors_and_audits():
    device = _device('Inventory-C', 'AA:BB:CC:DD:EE:20')
    tracked_one = _tracked('Tracked-C1', 'AA:BB:CC:DD:EE:20')
    tracked_two = _tracked('Tracked-C2', 'AA:BB:CC:DD:EE:21')
    candidate_one = DeviceIdentityLinkCandidate(
        device_id=device.device_id,
        tracked_device_id=tracked_one.id,
        normalized_mac='AA:BB:CC:DD:EE:20',
        ambiguity_group_key='mac:AA:BB:CC:DD:EE:20',
        status='pending',
    )
    candidate_two = DeviceIdentityLinkCandidate(
        device_id=device.device_id,
        tracked_device_id=tracked_two.id,
        normalized_mac='AA:BB:CC:DD:EE:20',
        ambiguity_group_key='mac:AA:BB:CC:DD:EE:20',
        status='pending',
    )
    db.session.add_all([candidate_one, candidate_two])
    db.session.commit()

    link = DeviceLinkService.decide_candidate(candidate_one.id, 'confirm', 'test-admin', 'exact match')

    assert isinstance(link, DeviceIdentityLink)
    assert link.device_id == device.device_id
    assert link.tracked_device_id == tracked_one.id
    db.session.refresh(candidate_one)
    db.session.refresh(candidate_two)
    assert candidate_one.status == 'confirmed'
    assert candidate_two.status == 'rejected'
    assert AuditLog.query.filter_by(entity_type='device_identity_link').count() == 1


def test_decide_candidate_reject_marks_candidate_only():
    device = _device('Inventory-D', 'AA:BB:CC:DD:EE:30')
    tracked = _tracked('Tracked-D', 'AA:BB:CC:DD:EE:30')
    candidate = DeviceIdentityLinkCandidate(
        device_id=device.device_id,
        tracked_device_id=tracked.id,
        normalized_mac='AA:BB:CC:DD:EE:30',
        ambiguity_group_key='mac:AA:BB:CC:DD:EE:30',
        status='pending',
    )
    db.session.add(candidate)
    db.session.commit()

    result = DeviceLinkService.decide_candidate(candidate.id, 'reject', 'test-admin', 'duplicate asset')

    assert result.status == 'rejected'
    assert DeviceIdentityLink.query.count() == 0
    assert AuditLog.query.filter_by(entity_type='device_identity_link').count() == 1


def test_resolve_helpers_return_rows_and_pending_tracked_status():
    device = _device('Inventory-E', 'AA:BB:CC:DD:EE:31')
    tracked = _tracked('Tracked-E', 'AA:BB:CC:DD:EE:31')
    pending_device = _device('Inventory-F', 'AA:BB:CC:DD:EE:32')
    pending_tracked = _tracked('Tracked-F', 'AA:BB:CC:DD:EE:32')
    db.session.add(
        DeviceIdentityLink(
            device_id=device.device_id,
            tracked_device_id=tracked.id,
            normalized_mac='AA:BB:CC:DD:EE:31',
            link_source='manual',
            is_active=True,
        )
    )
    db.session.add(
        DeviceIdentityLinkCandidate(
            device_id=pending_device.device_id,
            tracked_device_id=pending_tracked.id,
            normalized_mac='AA:BB:CC:DD:EE:32',
            ambiguity_group_key='mac:AA:BB:CC:DD:EE:32',
            status='pending',
        )
    )
    db.session.commit()

    assert DeviceLinkService.resolve_inventory_device_for_tracked_device(tracked.id).device_id == device.device_id
    assert DeviceLinkService.resolve_tracked_device_for_device(device.device_id).id == tracked.id
    pending_status = DeviceLinkService.link_status_for_tracked_device(pending_tracked.id)
    assert pending_status.status == 'pending_review'
    assert pending_status.candidate_count == 1


def test_backfill_exact_mac_links_updates_existing_link_and_counts_ambiguous_group():
    device = _device('Inventory-G', 'AA:BB:CC:DD:EE:33')
    tracked = _tracked('Tracked-G', 'AA:BB:CC:DD:EE:33')
    existing = DeviceIdentityLink(
        device_id=device.device_id,
        tracked_device_id=tracked.id,
        normalized_mac='OLD',
        link_source=None,
        confidence=20,
        is_active=False,
    )
    db.session.add(existing)
    _device('Inventory-H1', 'AA:BB:CC:DD:EE:34')
    _device('Inventory-H2', 'AA:BB:CC:DD:EE:34')
    _tracked('Tracked-H', 'AA:BB:CC:DD:EE:34')
    db.session.commit()

    result = DeviceLinkService.backfill_exact_mac_links()

    db.session.refresh(existing)
    assert result == {'created': 0, 'skipped': 1, 'ambiguous_groups': 1}
    assert existing.normalized_mac == 'AA:BB:CC:DD:EE:33'
    assert existing.confidence == 100
    assert existing.is_active is True


def test_backfill_ambiguous_candidates_skips_exact_pairs_and_existing_candidates():
    _device('Inventory-I', 'AA:BB:CC:DD:EE:35')
    _tracked('Tracked-I', 'AA:BB:CC:DD:EE:35')
    device_one = _device('Inventory-J1', 'AA:BB:CC:DD:EE:36')
    device_two = _device('Inventory-J2', 'AA:BB:CC:DD:EE:36')
    tracked = _tracked('Tracked-J', 'AA:BB:CC:DD:EE:36')
    db.session.add(
        DeviceIdentityLinkCandidate(
            device_id=device_one.device_id,
            tracked_device_id=tracked.id,
            normalized_mac='AA:BB:CC:DD:EE:36',
            ambiguity_group_key='mac:AA:BB:CC:DD:EE:36',
            status='pending',
        )
    )
    db.session.commit()

    result = DeviceLinkService.backfill_ambiguous_candidates()

    assert result['created'] == 1
    candidates = DeviceIdentityLinkCandidate.query.filter_by(ambiguity_group_key='mac:AA:BB:CC:DD:EE:36').all()
    assert len(candidates) == 2
    assert {row.device_id for row in candidates} == {device_one.device_id, device_two.device_id}


def test_decide_candidate_validates_missing_candidate_and_invalid_action():
    with pytest.raises(ValueError, match='candidate not found'):
        DeviceLinkService.decide_candidate(999999, 'confirm', 'test-admin')

    device = _device('Inventory-K', 'AA:BB:CC:DD:EE:37')
    tracked = _tracked('Tracked-K', 'AA:BB:CC:DD:EE:37')
    candidate = DeviceIdentityLinkCandidate(
        device_id=device.device_id,
        tracked_device_id=tracked.id,
        normalized_mac='AA:BB:CC:DD:EE:37',
        ambiguity_group_key='mac:AA:BB:CC:DD:EE:37',
        status='pending',
    )
    db.session.add(candidate)
    db.session.commit()

    with pytest.raises(ValueError, match='action must be confirm or reject'):
        DeviceLinkService.decide_candidate(candidate.id, 'merge', 'test-admin')


def test_decide_candidate_confirm_updates_existing_link():
    device = _device('Inventory-L', 'AA:BB:CC:DD:EE:38')
    tracked = _tracked('Tracked-L', 'AA:BB:CC:DD:EE:38')
    link = DeviceIdentityLink(
        device_id=device.device_id,
        tracked_device_id=tracked.id,
        normalized_mac='OLD',
        link_source='exact_mac',
        confidence=5,
        is_active=False,
    )
    candidate = DeviceIdentityLinkCandidate(
        device_id=device.device_id,
        tracked_device_id=tracked.id,
        normalized_mac='AA:BB:CC:DD:EE:38',
        ambiguity_group_key='mac:AA:BB:CC:DD:EE:38',
        candidate_score=87,
        status='pending',
    )
    db.session.add_all([link, candidate])
    db.session.commit()

    result = DeviceLinkService.decide_candidate(candidate.id, 'confirm', 'test-admin', 'manual review')

    db.session.refresh(link)
    assert result.id == link.id
    assert link.normalized_mac == 'AA:BB:CC:DD:EE:38'
    assert link.link_source == 'manual'
    assert link.confidence == 87
    assert link.is_active is True
    assert link.resolved_by == 'test-admin'
    assert link.resolution_reason == 'manual review'
