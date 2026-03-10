from datetime import datetime
import urllib.parse

import pytest

from extensions import db
from models.restricted_site_policy import RestrictedSitePolicy, normalize_domain


pytestmark = pytest.mark.unit


def test_normalize_domain_rejects_invalid_shapes(monkeypatch):
    assert normalize_domain(None) is None
    assert normalize_domain('') is None
    assert normalize_domain('   ') is None
    assert normalize_domain('https:///') is None
    assert normalize_domain('exa*mple.com') is None
    assert normalize_domain('192.168.1.10') is None
    assert normalize_domain(f"{'a' * 64}.com") is None
    assert normalize_domain('-bad.com') is None
    assert normalize_domain('bad-.com') is None
    assert normalize_domain('exa_mple.com') is None

    def _raise(_value):
        raise ValueError('boom')

    monkeypatch.setattr(urllib.parse, 'urlparse', _raise)
    assert normalize_domain('https://example.com') is None


def test_normalize_domain_accepts_common_inputs():
    assert normalize_domain('Example.com:443/path') == 'example.com'
    assert normalize_domain('*.www.Example.com') == 'example.com'


def test_restricted_site_policy_handles_bad_json_and_to_dict():
    policy = RestrictedSitePolicy(
        id=1,
        enabled=True,
        blocked_domains_json='not-json',
        cooldown_seconds=900,
        dns_poll_seconds=60,
        window_poll_seconds=10,
        dns_seen_ttl_seconds=1800,
        policy_version='v1',
        updated_by='tester',
        updated_at=datetime.utcnow(),
    )
    db.session.add(policy)
    db.session.commit()

    assert policy.blocked_domains == []
    payload = policy.to_dict()
    assert payload['enabled'] is True
    assert payload['policy_version'] == 'v1'
    assert payload['updated_by'] == 'tester'
    assert payload['updated_at'] is not None


def test_restricted_site_policy_singleton_creation_and_recompute():
    policy = RestrictedSitePolicy.get_singleton()
    assert policy.id == 1
    assert policy.policy_version

    policy.policy_version = ''
    db.session.commit()

    refreshed = RestrictedSitePolicy.get_singleton()
    assert refreshed.id == 1
    assert refreshed.policy_version
