import hashlib
import ipaddress
import json
from datetime import datetime

from extensions import db


def normalize_domain(value):
    if value is None:
        return None

    text = str(value).strip().lower()
    if not text:
        return None

    if '://' in text:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(text)
            text = parsed.hostname or ''
        except Exception:
            return None
    else:
        text = text.split('/', 1)[0].split('?', 1)[0]
        if ':' in text:
            text = text.split(':', 1)[0]

    text = text.strip().strip('.')
    if text.startswith('*.'):
        text = text[2:]
    if text.startswith('www.'):
        text = text[4:]
    text = text.strip().strip('.')
    if not text:
        return None

    if '*' in text:
        return None

    try:
        ipaddress.ip_address(text)
        return None
    except Exception:
        pass

    labels = [label for label in text.split('.') if label]
    if len(labels) < 2:
        return None
    if any(len(label) > 63 for label in labels):
        return None

    allowed = set('abcdefghijklmnopqrstuvwxyz0123456789-')
    for label in labels:
        if label.startswith('-') or label.endswith('-'):
            return None
        if any(ch not in allowed for ch in label):
            return None

    return '.'.join(labels)


def build_policy_version(enabled, blocked_domains, cooldown_seconds, dns_poll_seconds, window_poll_seconds, dns_seen_ttl_seconds):
    normalized_domains = sorted(
        {
            domain
            for domain in (normalize_domain(item) for item in (blocked_domains or []))
            if domain
        }
    )
    canonical = json.dumps(
        {
            'enabled': bool(enabled),
            'blocked_domains': normalized_domains,
            'cooldown_seconds': int(cooldown_seconds or 900),
            'dns_poll_seconds': int(dns_poll_seconds or 60),
            'window_poll_seconds': int(window_poll_seconds or 10),
            'dns_seen_ttl_seconds': int(dns_seen_ttl_seconds or 1800),
        },
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest(), normalized_domains


class RestrictedSitePolicy(db.Model):
    __tablename__ = 'restricted_site_policy'

    id = db.Column(db.Integer, primary_key=True, default=1)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    blocked_domains_json = db.Column(db.Text, nullable=False, default='[]')
    cooldown_seconds = db.Column(db.Integer, nullable=False, default=900)
    dns_poll_seconds = db.Column(db.Integer, nullable=False, default=60)
    window_poll_seconds = db.Column(db.Integer, nullable=False, default=10)
    dns_seen_ttl_seconds = db.Column(db.Integer, nullable=False, default=1800)
    policy_version = db.Column(db.String(64), nullable=False, default='', index=True)
    updated_by = db.Column(db.String(100), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def blocked_domains(self):
        try:
            raw = json.loads(self.blocked_domains_json or '[]')
        except Exception:
            raw = []
        return [domain for domain in (normalize_domain(item) for item in raw) if domain]

    def apply_domains(self, domains):
        normalized = sorted({domain for domain in (normalize_domain(item) for item in (domains or [])) if domain})
        self.blocked_domains_json = json.dumps(normalized, ensure_ascii=True)
        return normalized

    def recompute_version(self):
        version, normalized = build_policy_version(
            enabled=self.enabled,
            blocked_domains=self.blocked_domains,
            cooldown_seconds=self.cooldown_seconds,
            dns_poll_seconds=self.dns_poll_seconds,
            window_poll_seconds=self.window_poll_seconds,
            dns_seen_ttl_seconds=self.dns_seen_ttl_seconds,
        )
        self.blocked_domains_json = json.dumps(normalized, ensure_ascii=True)
        self.policy_version = version
        return version

    def to_dict(self):
        return {
            'enabled': bool(self.enabled),
            'blocked_domains': self.blocked_domains,
            'cooldown_seconds': int(self.cooldown_seconds or 900),
            'dns_poll_seconds': int(self.dns_poll_seconds or 60),
            'window_poll_seconds': int(self.window_poll_seconds or 10),
            'dns_seen_ttl_seconds': int(self.dns_seen_ttl_seconds or 1800),
            'policy_version': self.policy_version or '',
            'updated_by': self.updated_by,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def get_singleton(cls):
        policy = cls.query.get(1)
        if policy is None:
            policy = cls(id=1)
            policy.apply_domains([])
            policy.recompute_version()
            db.session.add(policy)
            db.session.commit()
        elif not policy.policy_version:
            policy.recompute_version()
            db.session.commit()
        return policy


class TrackingAgentKeyBinding(db.Model):
    __tablename__ = 'tracking_agent_key_bindings'

    id = db.Column(db.Integer, primary_key=True)
    key_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    key_hash = db.Column(db.String(128), nullable=False)
    tracked_device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    rotated_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    last_used_ip = db.Column(db.String(64), nullable=True)

    __table_args__ = (
        db.Index('ix_tracking_agent_key_bindings_device_active', 'tracked_device_id', 'is_active'),
    )


class RestrictedSiteEvent(db.Model):
    __tablename__ = 'restricted_site_events'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=False, index=True)
    matched_rule = db.Column(db.String(255), nullable=False)
    source = db.Column(db.String(32), nullable=False, index=True)
    confidence = db.Column(db.String(16), nullable=False, index=True)
    policy_version = db.Column(db.String(64), nullable=False)
    raw_evidence = db.Column(db.String(500), nullable=True)
    process_name = db.Column(db.String(120), nullable=True)
    observed_at_utc = db.Column(db.DateTime, nullable=False, index=True)
    received_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    agent_key_id = db.Column(db.String(64), nullable=True, index=True)

    __table_args__ = (
        db.Index('ix_restricted_site_events_device_domain_observed', 'device_id', 'domain', 'observed_at_utc'),
    )


class RestrictedSiteAlertState(db.Model):
    __tablename__ = 'restricted_site_alert_state'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('tracked_devices.id', ondelete='CASCADE'), nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=False)
    hit_count = db.Column(db.Integer, nullable=False, default=0)
    first_seen_at = db.Column(db.DateTime, nullable=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    last_alerted_at = db.Column(db.DateTime, nullable=True)
    last_emailed_at = db.Column(db.DateTime, nullable=True)
    active_dashboard_event_id = db.Column(db.String(36), nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint('device_id', 'domain', name='uq_restricted_site_alert_state_device_domain'),
        db.Index('ix_restricted_site_alert_state_domain_last_seen', 'domain', 'last_seen_at'),
    )


class RestrictedSiteDomainMeta(db.Model):
    __tablename__ = 'restricted_site_domain_meta'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer,
        db.ForeignKey('tracked_devices.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    domain = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(64), nullable=False, default='Custom')
    reason = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.String(100), nullable=True)
    updated_by = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('device_id', 'domain', name='uq_restricted_site_domain_meta_device_domain'),
        db.Index('ix_restricted_site_domain_meta_device_updated', 'device_id', 'updated_at'),
    )

    def to_dict(self):
        return {
            'device_id': int(self.device_id),
            'domain': self.domain,
            'category': self.category or 'Custom',
            'reason': self.reason or '',
            'created_by': self.created_by,
            'updated_by': self.updated_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
