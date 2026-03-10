from __future__ import annotations

from datetime import datetime

from models.tracked_device import TrackedDevice
from models.tracking_sync_envelope import TrackingSyncEnvelope
from services.db_task_queue import claim_next_row, mark_row_retry, mark_row_succeeded
from services.restricted_site_ingest_service import apply_restricted_site_ingest, plan_restricted_site_ingest
from services.tracking_sync_intake_service import current_sync_mode

CLAIM_TIMEOUT_SECONDS = 180


def run_once(now_utc: datetime | None = None):
    envelope = claim_next_row(
        TrackingSyncEnvelope,
        status_field='violation_status',
        retry_count_field='violation_retry_count',
        next_run_field='violation_next_run_at',
        started_at_field='violation_started_at',
        claim_token_field='violation_claim_token',
        claim_expires_field='violation_claim_expires_at',
        claim_timeout_seconds=CLAIM_TIMEOUT_SECONDS,
        now_utc=now_utc,
    )
    if envelope is None:
        return None

    try:
        payload = dict(envelope.payload_json or {})
        device = TrackedDevice.query.get(envelope.tracked_device_id) if envelope.tracked_device_id else None
        if device is None:
            mark_row_succeeded(
                envelope,
                status_field='violation_status',
                claim_token_field='violation_claim_token',
                claim_expires_field='violation_claim_expires_at',
                finished_at_field='violation_finished_at',
                now_utc=now_utc,
            )
            return envelope

        restricted_events = payload.get('restricted_site_events')
        plan = plan_restricted_site_ingest(device, restricted_events, policy=None, now_utc=now_utc)
        mode = current_sync_mode()
        if mode == 'shadow':
            shadow = dict(envelope.shadow_summary_json or {})
            shadow['restricted_event_count_shadow'] = len(plan.items)
            envelope.shadow_summary_json = shadow
            envelope.shadow_status = 'completed'
        else:
            apply_restricted_site_ingest(plan, fanout_mode='queued')
        mark_row_succeeded(
            envelope,
            status_field='violation_status',
            claim_token_field='violation_claim_token',
            claim_expires_field='violation_claim_expires_at',
            finished_at_field='violation_finished_at',
            now_utc=now_utc,
        )
        return envelope
    except Exception as exc:
        mark_row_retry(
            envelope,
            status_field='violation_status',
            retry_count_field='violation_retry_count',
            next_run_field='violation_next_run_at',
            claim_token_field='violation_claim_token',
            claim_expires_field='violation_claim_expires_at',
            error_code_field='violation_error_code',
            error_message_field=None,
            error_code='TRACKING_SYNC_VIOLATION_FAILED',
            error_message=str(exc),
            delay_seconds=60,
            now_utc=now_utc,
        )
        raise
