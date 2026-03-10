from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_

from extensions import db
from models.audit_log import AuditLog
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.tracked_device import TrackedDevice
from services.tracking_reconcile import normalize_mac


@dataclass(frozen=True)
class IdentityStatus:
    status: str
    linked_inventory_device_id: int | None = None
    linked_tracked_device_id: int | None = None
    candidate_count: int = 0

    def to_dict(self) -> dict:
        return {
            'identity_link_status': self.status,
            'linked_inventory_device_id': self.linked_inventory_device_id,
            'linked_tracked_device_id': self.linked_tracked_device_id,
            'identity_candidate_count': int(self.candidate_count or 0),
        }


class DeviceLinkService:
    @staticmethod
    def normalized_mac(value) -> str | None:
        return normalize_mac(value)

    @staticmethod
    def resolve_link_for_device(device_id: int) -> DeviceIdentityLink | None:
        return DeviceIdentityLink.query.filter_by(device_id=int(device_id), is_active=True).order_by(DeviceIdentityLink.id.desc()).first()

    @staticmethod
    def resolve_link_for_tracked_device(tracked_device_id: int) -> DeviceIdentityLink | None:
        return DeviceIdentityLink.query.filter_by(tracked_device_id=int(tracked_device_id), is_active=True).order_by(DeviceIdentityLink.id.desc()).first()

    @staticmethod
    def resolve_inventory_device_for_tracked_device(tracked_device_id: int) -> Device | None:
        link = DeviceLinkService.resolve_link_for_tracked_device(int(tracked_device_id))
        if link is None:
            return None
        return Device.query.get(int(link.device_id))

    @staticmethod
    def resolve_tracked_device_for_device(device_id: int) -> TrackedDevice | None:
        link = DeviceLinkService.resolve_link_for_device(int(device_id))
        if link is None:
            return None
        return TrackedDevice.query.get(int(link.tracked_device_id))

    @staticmethod
    def link_status_for_tracked_device(tracked_device_id: int) -> IdentityStatus:
        link = DeviceLinkService.resolve_link_for_tracked_device(int(tracked_device_id))
        if link is not None:
            return IdentityStatus(
                status='linked',
                linked_inventory_device_id=int(link.device_id),
                linked_tracked_device_id=int(link.tracked_device_id),
            )
        candidate_count = DeviceIdentityLinkCandidate.query.filter_by(tracked_device_id=int(tracked_device_id), status='pending').count()
        if candidate_count > 0:
            return IdentityStatus(status='pending_review', linked_tracked_device_id=int(tracked_device_id), candidate_count=candidate_count)
        return IdentityStatus(status='unlinked', linked_tracked_device_id=int(tracked_device_id), candidate_count=0)

    @staticmethod
    def link_status_for_device(device_id: int) -> IdentityStatus:
        link = DeviceLinkService.resolve_link_for_device(int(device_id))
        if link is not None:
            return IdentityStatus(
                status='linked',
                linked_inventory_device_id=int(link.device_id),
                linked_tracked_device_id=int(link.tracked_device_id),
            )
        candidate_count = DeviceIdentityLinkCandidate.query.filter_by(device_id=int(device_id), status='pending').count()
        if candidate_count > 0:
            return IdentityStatus(status='pending_review', linked_inventory_device_id=int(device_id), candidate_count=candidate_count)
        return IdentityStatus(status='unlinked', linked_inventory_device_id=int(device_id), candidate_count=0)

    @staticmethod
    def backfill_exact_mac_links() -> dict:
        device_groups = defaultdict(list)
        tracked_groups = defaultdict(list)

        for device in Device.query.all():
            mac = normalize_mac(getattr(device, 'macaddress', None))
            if mac:
                device_groups[mac].append(device)
        for tracked in TrackedDevice.query.all():
            mac = normalize_mac(getattr(tracked, 'mac_address', None))
            if mac:
                tracked_groups[mac].append(tracked)

        created = 0
        skipped = 0
        ambiguous = 0
        for mac in sorted(set(device_groups.keys()) | set(tracked_groups.keys())):
            devices = device_groups.get(mac, [])
            tracked_devices = tracked_groups.get(mac, [])
            if len(devices) == 1 and len(tracked_devices) == 1:
                device = devices[0]
                tracked = tracked_devices[0]
                link = DeviceIdentityLink.query.filter_by(device_id=device.device_id, tracked_device_id=tracked.id).first()
                if link is None:
                    DeviceLinkService._deactivate_competing_links(device.device_id, tracked.id)
                    db.session.add(
                        DeviceIdentityLink(
                            device_id=device.device_id,
                            tracked_device_id=tracked.id,
                            normalized_mac=mac,
                            link_source='exact_mac',
                            confidence=100,
                            is_active=True,
                        )
                    )
                    created += 1
                else:
                    link.normalized_mac = mac
                    link.link_source = link.link_source or 'exact_mac'
                    link.confidence = max(int(link.confidence or 0), 100)
                    link.is_active = True
                    skipped += 1
            elif devices or tracked_devices:
                ambiguous += 1
        db.session.commit()
        return {'created': created, 'skipped': skipped, 'ambiguous_groups': ambiguous}

    @staticmethod
    def backfill_ambiguous_candidates() -> dict:
        device_groups = defaultdict(list)
        tracked_groups = defaultdict(list)

        for device in Device.query.all():
            mac = normalize_mac(getattr(device, 'macaddress', None))
            if mac:
                device_groups[mac].append(device)
        for tracked in TrackedDevice.query.all():
            mac = normalize_mac(getattr(tracked, 'mac_address', None))
            if mac:
                tracked_groups[mac].append(tracked)

        created = 0
        for mac in sorted(set(device_groups.keys()) & set(tracked_groups.keys())):
            devices = device_groups.get(mac, [])
            tracked_devices = tracked_groups.get(mac, [])
            if len(devices) <= 1 and len(tracked_devices) <= 1:
                continue
            group_key = f'mac:{mac}'
            for device in devices:
                for tracked in tracked_devices:
                    existing = DeviceIdentityLinkCandidate.query.filter_by(
                        device_id=int(device.device_id),
                        tracked_device_id=int(tracked.id),
                        ambiguity_group_key=group_key,
                    ).first()
                    if existing is not None:
                        continue
                    db.session.add(
                        DeviceIdentityLinkCandidate(
                            device_id=int(device.device_id),
                            tracked_device_id=int(tracked.id),
                            normalized_mac=mac,
                            ambiguity_group_key=group_key,
                            candidate_source='mac',
                            candidate_score=100,
                            status='pending',
                        )
                    )
                    created += 1
        db.session.commit()
        return {'created': created}

    @staticmethod
    def decide_candidate(candidate_id: int, action: str, actor: str, reason: str | None = None):
        candidate = DeviceIdentityLinkCandidate.query.get(int(candidate_id))
        if candidate is None:
            raise ValueError('candidate not found')

        normalized_action = str(action or '').strip().lower()
        if normalized_action not in {'confirm', 'reject'}:
            raise ValueError('action must be confirm or reject')

        actor_name = str(actor or 'system').strip() or 'system'
        reason_text = str(reason or '').strip() or None
        now_utc = datetime.utcnow()

        if normalized_action == 'reject':
            candidate.status = 'rejected'
            candidate.decided_at = now_utc
            candidate.decided_by = actor_name
            candidate.decision_reason = reason_text
            DeviceLinkService._audit_identity_decision('reject', candidate, actor_name, reason_text)
            db.session.commit()
            return candidate

        DeviceLinkService._deactivate_competing_links(candidate.device_id, candidate.tracked_device_id)
        link = DeviceIdentityLink.query.filter_by(
            device_id=int(candidate.device_id),
            tracked_device_id=int(candidate.tracked_device_id),
        ).first()
        if link is None:
            link = DeviceIdentityLink(
                device_id=int(candidate.device_id),
                tracked_device_id=int(candidate.tracked_device_id),
                normalized_mac=candidate.normalized_mac,
                link_source='manual',
                confidence=int(candidate.candidate_score or 0),
                is_active=True,
                resolved_by=actor_name,
                resolution_reason=reason_text,
            )
            db.session.add(link)
        else:
            link.normalized_mac = candidate.normalized_mac
            link.link_source = 'manual'
            link.confidence = int(candidate.candidate_score or 0)
            link.is_active = True
            link.resolved_by = actor_name
            link.resolution_reason = reason_text

        candidate.status = 'confirmed'
        candidate.decided_at = now_utc
        candidate.decided_by = actor_name
        candidate.decision_reason = reason_text

        competing_candidates = DeviceIdentityLinkCandidate.query.filter(
            DeviceIdentityLinkCandidate.status == 'pending',
            or_(
                DeviceIdentityLinkCandidate.device_id == int(candidate.device_id),
                DeviceIdentityLinkCandidate.tracked_device_id == int(candidate.tracked_device_id),
            ),
            DeviceIdentityLinkCandidate.id != int(candidate.id),
        ).all()
        for other in competing_candidates:
            other.status = 'rejected'
            other.decided_at = now_utc
            other.decided_by = actor_name
            other.decision_reason = f'Competing link confirmed via candidate {candidate.id}'

        DeviceLinkService._audit_identity_decision('confirm', candidate, actor_name, reason_text)
        db.session.commit()
        return link

    @staticmethod
    def _deactivate_competing_links(device_id: int, tracked_device_id: int) -> None:
        DeviceIdentityLink.query.filter(
            DeviceIdentityLink.is_active.is_(True),
            or_(
                DeviceIdentityLink.device_id == int(device_id),
                DeviceIdentityLink.tracked_device_id == int(tracked_device_id),
            ),
        ).update({'is_active': False, 'updated_at': datetime.utcnow()}, synchronize_session=False)

    @staticmethod
    def _audit_identity_decision(action: str, candidate: DeviceIdentityLinkCandidate, actor: str, reason: str | None) -> None:
        db.session.add(
            AuditLog(
                user_id=None,
                username=actor,
                user_role='admin',
                action='update',
                entity_type='device_identity_link',
                entity_id=int(candidate.id),
                entity_name=f'device:{candidate.device_id}->tracked:{candidate.tracked_device_id}',
                description=f'Identity link {action}ed for candidate {candidate.id}',
                changes={
                    'action': action,
                    'reason': reason,
                    'device_id': int(candidate.device_id),
                    'tracked_device_id': int(candidate.tracked_device_id),
                    'normalized_mac': candidate.normalized_mac,
                },
            )
        )


resolve_link_for_device = DeviceLinkService.resolve_link_for_device
resolve_link_for_tracked_device = DeviceLinkService.resolve_link_for_tracked_device
backfill_exact_mac_links = DeviceLinkService.backfill_exact_mac_links
backfill_ambiguous_candidates = DeviceLinkService.backfill_ambiguous_candidates
decide_candidate = DeviceLinkService.decide_candidate
