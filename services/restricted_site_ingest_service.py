from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from extensions import db
from models.dashboard import DashboardEvent
from models.restricted_site_policy import (
    RestrictedSiteAlertState,
    RestrictedSiteDomainMeta,
    RestrictedSiteEvent,
    RestrictedSitePolicy,
    normalize_domain,
)

RESTRICTED_SOURCE_WINDOW = 'window_title'
RESTRICTED_SOURCE_DNS = 'dns_cache'
RESTRICTED_CONFIDENCE_HIGH = 'HIGH'
RESTRICTED_CONFIDENCE_MEDIUM = 'MEDIUM'
RESTRICTED_CONFIDENCE_LOW = 'LOW'
RESTRICTED_CORROBORATION_WINDOW_SECONDS = 120


@dataclass(frozen=True)
class RestrictedSiteEventPlanItem:
    observed_domain: str
    matched_rule: str
    source: str
    confidence: str
    process_name: str | None
    raw_evidence: str | None
    observed_at: datetime


@dataclass(frozen=True)
class RestrictedSiteIngestPlan:
    device_id: int
    binding_key_id: str | None
    policy_version: str
    blocked_domains: list[str]
    cooldown_seconds: int
    now_utc: datetime
    fanout_channels: tuple[str, ...] = ('sse', 'email')
    items: tuple[RestrictedSiteEventPlanItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RestrictedSiteIngestResult:
    ingested_events: int
    alert_updates: int
    emails_sent: int
    policy_version: str
    queued_fanout_tasks: int = 0

    def to_dict(self) -> dict:
        return {
            'ingested_events': int(self.ingested_events),
            'alert_updates': int(self.alert_updates),
            'emails_sent': int(self.emails_sent),
            'policy_version': self.policy_version,
            'queued_fanout_tasks': int(self.queued_fanout_tasks),
        }


def coerce_restricted_events(raw_value) -> list[dict]:
    if isinstance(raw_value, list):
        return [item for item in raw_value if isinstance(item, dict)]
    if isinstance(raw_value, dict):
        nested = raw_value.get('events')
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def parse_observed_datetime(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    text = str(value or '').strip()
    if not text:
        return datetime.utcnow()
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return datetime.utcnow()


def match_restricted_domain(observed_domain, blocked_domains: Iterable[str]) -> str | None:
    observed = normalize_domain(observed_domain)
    if not observed:
        return None
    for rule in blocked_domains or []:
        normalized_rule = normalize_domain(rule)
        if not normalized_rule:
            continue
        if observed == normalized_rule or observed.endswith(f'.{normalized_rule}'):
            return normalized_rule
    return None


def build_restricted_alert_message(domain: str, source: str, confidence: str, hit_count: int) -> str:
    source_label = 'Foreground window title' if source == RESTRICTED_SOURCE_WINDOW else 'DNS cache lookup'
    return (
        f'Restricted domain detected: {domain} | source={source_label} | confidence={confidence} | '
        f'hit_count={int(hit_count or 1)}'
    )


def maybe_uplift_confidence(device_id: int, domain: str, source: str, observed_at: datetime) -> str:
    opposite_source = RESTRICTED_SOURCE_DNS if source == RESTRICTED_SOURCE_WINDOW else RESTRICTED_SOURCE_WINDOW
    lookback = observed_at - timedelta(seconds=RESTRICTED_CORROBORATION_WINDOW_SECONDS)
    corroborated = RestrictedSiteEvent.query.filter(
        RestrictedSiteEvent.device_id == int(device_id),
        RestrictedSiteEvent.domain == str(domain),
        RestrictedSiteEvent.source == opposite_source,
        RestrictedSiteEvent.observed_at_utc >= lookback,
    ).order_by(RestrictedSiteEvent.observed_at_utc.desc()).first()
    if not corroborated:
        return RESTRICTED_CONFIDENCE_HIGH if source == RESTRICTED_SOURCE_WINDOW else RESTRICTED_CONFIDENCE_LOW
    if source == RESTRICTED_SOURCE_WINDOW:
        return RESTRICTED_CONFIDENCE_HIGH
    return RESTRICTED_CONFIDENCE_MEDIUM


def build_alert_delivery_key(dashboard_event_id: str, channel: str, event_status: str, updated_at: datetime) -> str:
    updated_iso = updated_at.replace(microsecond=0).isoformat() if isinstance(updated_at, datetime) else ''
    payload = f'{dashboard_event_id}:{channel}:{event_status}:{updated_iso}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def plan_restricted_site_ingest(device, events, binding_key_id=None, policy=None, now_utc=None) -> RestrictedSiteIngestPlan:
    policy = policy or RestrictedSitePolicy.get_singleton()
    now_utc = now_utc or datetime.utcnow()
    device_domains = [
        row.domain
        for row in RestrictedSiteDomainMeta.query.filter_by(device_id=int(device.id)).all()
    ]
    blocked_domains = sorted(set((policy.blocked_domains or []) + device_domains))
    planned_items: list[RestrictedSiteEventPlanItem] = []

    for raw_event in coerce_restricted_events(events):
        observed_domain = normalize_domain(raw_event.get('domain'))
        if not observed_domain:
            continue

        matched_rule = match_restricted_domain(observed_domain, blocked_domains)
        if not matched_rule:
            continue

        source = str(raw_event.get('source') or RESTRICTED_SOURCE_DNS).strip().lower()
        if source not in (RESTRICTED_SOURCE_DNS, RESTRICTED_SOURCE_WINDOW):
            source = RESTRICTED_SOURCE_DNS

        observed_at = parse_observed_datetime(raw_event.get('observed_at_utc') or raw_event.get('observed_at'))
        planned_items.append(
            RestrictedSiteEventPlanItem(
                observed_domain=observed_domain,
                matched_rule=matched_rule,
                source=source,
                confidence=maybe_uplift_confidence(int(device.id), observed_domain, source, observed_at),
                process_name=str(raw_event.get('process_name') or '').strip() or None,
                raw_evidence=str(raw_event.get('raw_evidence') or raw_event.get('evidence_title') or '').strip()[:500] or None,
                observed_at=observed_at,
            )
        )

    return RestrictedSiteIngestPlan(
        device_id=int(device.id),
        binding_key_id=binding_key_id,
        policy_version=str(policy.policy_version or ''),
        blocked_domains=blocked_domains,
        cooldown_seconds=max(60, int(policy.cooldown_seconds or 900)),
        now_utc=now_utc,
        items=tuple(planned_items),
    )


def apply_restricted_site_ingest(plan: RestrictedSiteIngestPlan, fanout_mode: str = 'inline') -> RestrictedSiteIngestResult:
    fanout_mode = str(fanout_mode or 'inline').strip().lower()
    if fanout_mode not in {'inline', 'queued', 'none'}:
        raise ValueError('fanout_mode must be inline, queued, or none')

    from models.alert_fanout_task import AlertFanoutTask
    from models.tracked_device import TrackedDevice
    from services.notification_service import NotificationService
    from services.sse_broadcaster import broadcast_event

    try:
        from services.device_link_service import DeviceLinkService
    except Exception:
        DeviceLinkService = None

    device = TrackedDevice.query.get(int(plan.device_id))
    if device is None:
        raise ValueError('tracked device not found for restricted-site ingest plan')

    linked_inventory_id = None
    if DeviceLinkService is not None:
        linked_inventory = DeviceLinkService.resolve_inventory_device_for_tracked_device(int(device.id))
        linked_inventory_id = getattr(linked_inventory, 'device_id', None) if linked_inventory is not None else None

    ingested = 0
    alert_updates = 0
    emails_sent = 0
    queued_fanout_tasks = 0

    for item in plan.items:
        event_row = RestrictedSiteEvent(
            device_id=int(device.id),
            domain=item.observed_domain,
            matched_rule=item.matched_rule,
            source=item.source,
            confidence=item.confidence,
            policy_version=plan.policy_version,
            raw_evidence=item.raw_evidence,
            process_name=item.process_name,
            observed_at_utc=item.observed_at,
            received_at_utc=plan.now_utc,
            agent_key_id=plan.binding_key_id,
        )
        db.session.add(event_row)
        ingested += 1

        state = RestrictedSiteAlertState.query.filter_by(device_id=int(device.id), domain=item.observed_domain).first()
        if state is None:
            state = RestrictedSiteAlertState(
                device_id=int(device.id),
                domain=item.observed_domain,
                hit_count=0,
                first_seen_at=item.observed_at,
            )
            db.session.add(state)
            db.session.flush()

        state.hit_count = int(state.hit_count or 0) + 1
        if not state.first_seen_at:
            state.first_seen_at = item.observed_at
        state.last_seen_at = item.observed_at

        metric_name = f'restricted_site:tracked:{device.id}:{item.observed_domain}'
        existing_alert = DashboardEvent.query.filter_by(
            metric_name=metric_name,
            resolved=False,
        ).order_by(DashboardEvent.timestamp.desc()).first()

        message = build_restricted_alert_message(
            domain=item.observed_domain,
            source=item.source,
            confidence=item.confidence,
            hit_count=state.hit_count,
        )
        should_emit_alert = (
            not state.last_alerted_at
            or ((plan.now_utc - state.last_alerted_at).total_seconds() >= plan.cooldown_seconds)
        )

        if bool(getattr(device, 'maintenance_mode', False)):
            continue

        dashboard_event = existing_alert
        if should_emit_alert:
            if dashboard_event is None:
                dashboard_event = DashboardEvent(
                    event_id=str(uuid.uuid4()),
                    device_id=linked_inventory_id,
                    device_ip=device.ip_address,
                    event_type='restricted_site',
                    severity='WARNING',
                    metric_name=metric_name,
                    message=message,
                    value=float(state.hit_count),
                    timestamp=plan.now_utc,
                    resolved=False,
                )
                db.session.add(dashboard_event)
            else:
                dashboard_event.device_id = linked_inventory_id
                dashboard_event.timestamp = plan.now_utc
                dashboard_event.severity = 'WARNING'
                dashboard_event.message = message
                dashboard_event.value = float(state.hit_count)

            state.active_dashboard_event_id = dashboard_event.event_id
            state.last_alerted_at = plan.now_utc
            alert_updates += 1
        elif dashboard_event is not None:
            dashboard_event.timestamp = plan.now_utc
            dashboard_event.message = message
            dashboard_event.value = float(state.hit_count)
            state.active_dashboard_event_id = dashboard_event.event_id

        should_email = (
            not state.last_emailed_at
            or ((plan.now_utc - state.last_emailed_at).total_seconds() >= plan.cooldown_seconds)
        )

        if should_emit_alert and dashboard_event is not None:
            payload = {
                'event_id': dashboard_event.event_id,
                'delivery_key': None,
                'device_id': int(device.id),
                'linked_inventory_device_id': linked_inventory_id,
                'device_ip': device.ip_address,
                'metric_name': metric_name,
                'severity': 'WARNING',
                'message': message,
                'domain': item.observed_domain,
                'event_status': 'active',
                'event_updated_at': plan.now_utc.isoformat(),
            }
            if fanout_mode == 'inline':
                broadcast_event('alert_created', payload)
                if should_email:
                    NotificationService.send_warning_alert(
                        device,
                        metric=metric_name,
                        value=state.hit_count,
                        message=message,
                    )
                    state.last_emailed_at = plan.now_utc
                    emails_sent += 1
            elif fanout_mode == 'queued':
                for channel in ('sse', 'email'):
                    if channel == 'email' and not should_email:
                        continue
                    delivery_key = build_alert_delivery_key(dashboard_event.event_id, channel, 'active', plan.now_utc)
                    payload_with_key = dict(payload)
                    payload_with_key['delivery_key'] = delivery_key
                    existing_task = AlertFanoutTask.query.filter_by(delivery_key=delivery_key).first()
                    if existing_task is None:
                        db.session.add(
                            AlertFanoutTask(
                                dashboard_event_id=dashboard_event.event_id,
                                tracked_device_id=int(device.id),
                                channel=channel,
                                delivery_key=delivery_key,
                                payload_json=payload_with_key,
                                status='pending',
                                priority=100 if channel == 'sse' else 200,
                                next_run_at=plan.now_utc,
                            )
                        )
                        queued_fanout_tasks += 1
                    if channel == 'email':
                        state.last_emailed_at = plan.now_utc
            else:
                if should_email:
                    state.last_emailed_at = plan.now_utc

    return RestrictedSiteIngestResult(
        ingested_events=ingested,
        alert_updates=alert_updates,
        emails_sent=emails_sent,
        policy_version=plan.policy_version,
        queued_fanout_tasks=queued_fanout_tasks,
    )
