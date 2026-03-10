import pytest
from datetime import datetime
from extensions import db
from models.restricted_site_policy import RestrictedSitePolicy

pytestmark = pytest.mark.integration

def test_get_admin_restricted_sites_policy(admin_client):
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['test.com', 'example.org'])
    db.session.commit()

    response = admin_client.get('/api/admin/restricted-sites-policy')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['success'] is True
    assert payload['domains'] == ['example.org', 'test.com']
    assert payload['mode'] == 'blocking'

def test_add_admin_restricted_sites_policy_domain(admin_client):
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains([])
    db.session.commit()

    response = admin_client.post(
        '/api/admin/restricted-sites-policy/domains',
        json={'domain': 'youtube.com', 'category': 'Productivity', 'reason': 'focus time'},
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['success'] is True
    assert 'youtube.com' in payload['domains']
    
    # Check DB
    db.session.refresh(policy)
    assert 'youtube.com' in policy.blocked_domains

def test_add_invalid_domain_admin_policy(admin_client):
    response = admin_client.post(
        '/api/admin/restricted-sites-policy/domains',
        json={'domain': ''},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False

def test_remove_admin_restricted_sites_policy_domains(admin_client):
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['keep.com', 'remove.com'])
    db.session.commit()

    response = admin_client.delete(
        '/api/admin/restricted-sites-policy/domains',
        json={'domains': ['remove.com']},
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['success'] is True
    assert 'remove.com' not in payload['domains']
    assert 'keep.com' in payload['domains']

    db.session.refresh(policy)
    assert 'remove.com' not in policy.blocked_domains

def test_set_admin_restricted_sites_policy_mode(admin_client):
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = False
    db.session.commit()

    response = admin_client.post(
        '/api/admin/restricted-sites-policy/mode',
        json={'mode': 'blocking'},
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['success'] is True
    assert payload['mode'] == 'blocking'

    db.session.refresh(policy)
    assert policy.enabled is True
