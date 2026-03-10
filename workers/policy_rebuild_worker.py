from __future__ import annotations

from datetime import datetime

from services.db_task_queue import claim_next_row, mark_row_retry, mark_row_succeeded
from services.effective_policy_service import rebuild_effective_policy_cache
from models.policy_rebuild_task import PolicyRebuildTask

CLAIM_TIMEOUT_SECONDS = 120


def run_once(now_utc: datetime | None = None):
    task = claim_next_row(
        PolicyRebuildTask,
        status_field='status',
        retry_count_field='retry_count',
        next_run_field='next_run_at',
        claim_token_field='claim_token',
        claim_expires_field='claim_expires_at',
        claim_timeout_seconds=CLAIM_TIMEOUT_SECONDS,
        now_utc=now_utc,
    )
    if task is None:
        return None

    try:
        rebuild_effective_policy_cache(int(task.tracked_device_id))
        mark_row_succeeded(task, now_utc=now_utc)
        return task
    except Exception as exc:
        mark_row_retry(task, error_code='POLICY_REBUILD_FAILED', error_message=str(exc), delay_seconds=30, now_utc=now_utc)
        raise
