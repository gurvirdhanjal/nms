from datetime import datetime, timedelta

import pytest

from extensions import db
from models.policy_rebuild_task import PolicyRebuildTask
from models.tracked_device import TrackedDevice
from services import db_task_queue


pytestmark = pytest.mark.unit


def _task(status='pending', next_run_at=None, claim_expires_at=None):
    tracked = TrackedDevice(
        mac_address=f'AA:BB:CC:DD:EE:{TrackedDevice.query.count() + 1:02d}',
        device_name='Queue Device',
        availability_status='online',
    )
    db.session.add(tracked)
    db.session.flush()
    row = PolicyRebuildTask(
        tracked_device_id=tracked.id,
        status=status,
        priority=100,
        retry_count=0,
        next_run_at=next_run_at or datetime.utcnow(),
        claim_expires_at=claim_expires_at,
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_reclaim_expired_claims_requeues_rows():
    expired = _task(status='running', claim_expires_at=datetime.utcnow() - timedelta(seconds=1))
    db.session.commit()

    reclaimed = db_task_queue.reclaim_expired_claims(
        PolicyRebuildTask,
        status_field='status',
        retry_count_field='retry_count',
        claim_token_field='claim_token',
        claim_expires_field='claim_expires_at',
        next_run_field='next_run_at',
    )

    db.session.refresh(expired)
    assert reclaimed == 1
    assert expired.status == 'pending'
    assert expired.retry_count == 1
    assert expired.claim_token is None


def test_claim_next_row_claims_once_and_returns_none_on_second_attempt():
    row = _task()
    db.session.commit()

    claimed = db_task_queue.claim_next_row(
        PolicyRebuildTask,
        status_field='status',
        retry_count_field='retry_count',
        next_run_field='next_run_at',
        claim_token_field='claim_token',
        claim_expires_field='claim_expires_at',
        claim_timeout_seconds=30,
    )
    second = db_task_queue.claim_next_row(
        PolicyRebuildTask,
        status_field='status',
        retry_count_field='retry_count',
        next_run_field='next_run_at',
        claim_token_field='claim_token',
        claim_expires_field='claim_expires_at',
        claim_timeout_seconds=30,
    )

    assert claimed.id == row.id
    assert claimed.status == 'running'
    assert claimed.claim_token
    assert second is None


def test_mark_row_succeeded_and_retry_update_state():
    row = _task()
    db.session.commit()

    claimed = db_task_queue.claim_next_row(
        PolicyRebuildTask,
        status_field='status',
        retry_count_field='retry_count',
        next_run_field='next_run_at',
        claim_token_field='claim_token',
        claim_expires_field='claim_expires_at',
        claim_timeout_seconds=30,
    )
    db_task_queue.mark_row_retry(claimed, error_code='ERR', error_message='boom', delay_seconds=10)
    db.session.refresh(claimed)
    assert claimed.status == 'pending'
    assert claimed.retry_count == 1
    assert claimed.error_code == 'ERR'

    claimed = db_task_queue.claim_next_row(
        PolicyRebuildTask,
        status_field='status',
        retry_count_field='retry_count',
        next_run_field='next_run_at',
        claim_token_field='claim_token',
        claim_expires_field='claim_expires_at',
        claim_timeout_seconds=30,
        now_utc=claimed.next_run_at,
    )
    db_task_queue.mark_row_succeeded(claimed)
    db.session.refresh(claimed)
    assert claimed.status == 'completed'
    assert claimed.finished_at is not None
