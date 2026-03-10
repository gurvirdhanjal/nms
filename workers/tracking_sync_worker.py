from __future__ import annotations

from datetime import datetime

from models.tracking_sync_envelope import TrackingSyncEnvelope
from services.db_task_queue import claim_next_row, mark_row_retry, mark_row_succeeded
from services.tracking_sync_core_service import apply_sync_core_plan, plan_sync_core_mutations
from services.tracking_sync_intake_service import current_sync_mode

CLAIM_TIMEOUT_SECONDS = 300


def _compute_mismatches(inline_summary: dict | None, shadow_summary: dict | None) -> dict:
    inline = inline_summary or {}
    shadow = shadow_summary or {}
    mismatches = {}
    for key in sorted(set(inline.keys()) | set(shadow.keys())):
        if inline.get(key) != shadow.get(key):
            mismatches[key] = {'inline': inline.get(key), 'shadow': shadow.get(key)}
    return mismatches


def run_once(now_utc: datetime | None = None):
    envelope = claim_next_row(
        TrackingSyncEnvelope,
        status_field='core_status',
        retry_count_field='core_retry_count',
        next_run_field='core_next_run_at',
        started_at_field='core_started_at',
        claim_token_field='core_claim_token',
        claim_expires_field='core_claim_expires_at',
        claim_timeout_seconds=CLAIM_TIMEOUT_SECONDS,
        now_utc=now_utc,
    )
    if envelope is None:
        return None

    mode = current_sync_mode()
    try:
        payload = dict(envelope.payload_json or {})
        plan = plan_sync_core_mutations(payload, envelope.normalized_mac, envelope.unique_client_id, now_utc=now_utc)
        shadow_summary = plan.to_summary()

        if mode == 'shadow':
            envelope.shadow_summary_json = shadow_summary
            envelope.shadow_mismatches_json = _compute_mismatches(envelope.inline_summary_json or {}, shadow_summary)
            envelope.shadow_status = 'completed'
            mark_row_succeeded(
                envelope,
                status_field='core_status',
                claim_token_field='core_claim_token',
                claim_expires_field='core_claim_expires_at',
                finished_at_field='core_finished_at',
                now_utc=now_utc,
            )
            return envelope

        result = apply_sync_core_plan(plan)
        envelope.shadow_summary_json = result.summary
        envelope.shadow_status = 'completed'
        mark_row_succeeded(
            envelope,
            status_field='core_status',
            claim_token_field='core_claim_token',
            claim_expires_field='core_claim_expires_at',
            finished_at_field='core_finished_at',
            now_utc=now_utc,
        )
        return envelope
    except Exception as exc:
        mark_row_retry(
            envelope,
            status_field='core_status',
            retry_count_field='core_retry_count',
            next_run_field='core_next_run_at',
            claim_token_field='core_claim_token',
            claim_expires_field='core_claim_expires_at',
            error_code_field='core_error_code',
            error_message_field=None,
            error_code='TRACKING_SYNC_CORE_FAILED',
            error_message=str(exc),
            delay_seconds=60,
            now_utc=now_utc,
        )
        raise
