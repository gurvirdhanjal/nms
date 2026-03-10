from datetime import datetime

import pytest

from extensions import db
from models.policy_rebuild_task import PolicyRebuildTask
from models.restricted_site_policy import RestrictedSitePolicy
from models.tracked_device import TrackedDevice
from workers import policy_rebuild_worker


pytestmark = pytest.mark.unit


def test_policy_rebuild_worker_completes_task():
    tracked = TrackedDevice(mac_address='AA:BB:CC:DD:EE:82', device_name='Policy Rebuild Device')
    db.session.add(tracked)
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['rebuild.example'])
    db.session.flush()
    task = PolicyRebuildTask(tracked_device_id=tracked.id, status='pending', next_run_at=datetime.utcnow())
    db.session.add(task)
    db.session.commit()

    processed = policy_rebuild_worker.run_once()

    db.session.refresh(task)
    assert processed.id == task.id
    assert task.status == 'completed'


def test_policy_rebuild_worker_retries_on_failure(monkeypatch):
    tracked = TrackedDevice(mac_address='AA:BB:CC:DD:EE:83', device_name='Policy Rebuild Retry Device')
    db.session.add(tracked)
    db.session.flush()
    task = PolicyRebuildTask(tracked_device_id=tracked.id, status='pending', next_run_at=datetime.utcnow())
    db.session.add(task)
    db.session.commit()

    monkeypatch.setattr(policy_rebuild_worker, 'rebuild_effective_policy_cache', lambda tracked_device_id: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(RuntimeError):
        policy_rebuild_worker.run_once()

    db.session.refresh(task)
    assert task.status == 'pending'
    assert task.retry_count == 1
    assert task.error_code == 'POLICY_REBUILD_FAILED'


def test_policy_rebuild_worker_returns_none_when_no_task():
    assert policy_rebuild_worker.run_once() is None
