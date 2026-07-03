from __future__ import annotations

from datetime import datetime

from models.tracked_device import TrackedDevice
from models.tracking_sync_envelope import TrackingSyncEnvelope
from services.db_task_queue import claim_next_row, mark_row_retry, mark_row_succeeded
from services.tracking_sync_intake_service import current_sync_mode

CLAIM_TIMEOUT_SECONDS = 180


def run_once(now_utc: datetime | None = None):
    envelope = claim_next_row(
        TrackingSyncEnvelope,
        status_field='domain_status',
        retry_count_field='domain_retry_count',
        next_run_field='domain_next_run_at',
        started_at_field='domain_started_at',
        claim_token_field='domain_claim_token',
        claim_expires_field='domain_claim_expires_at',
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
                status_field='domain_status',
                claim_token_field='domain_claim_token',
                claim_expires_field='domain_claim_expires_at',
                finished_at_field='domain_finished_at',
                now_utc=now_utc,
            )
            return envelope

        mode = current_sync_mode()
        domain_history = payload.get('domain_history') or []

        if mode == 'shadow':
            shadow = dict(envelope.shadow_summary_json or {})
            shadow['domain_history_count_shadow'] = len(domain_history)
            envelope.shadow_summary_json = shadow
            envelope.shadow_status = 'completed'
        else:
            _apply_domain_history(device.id, domain_history, now_utc=now_utc)

        mark_row_succeeded(
            envelope,
            status_field='domain_status',
            claim_token_field='domain_claim_token',
            claim_expires_field='domain_claim_expires_at',
            finished_at_field='domain_finished_at',
            now_utc=now_utc,
        )
        return envelope
    except Exception as exc:
        mark_row_retry(
            envelope,
            status_field='domain_status',
            retry_count_field='domain_retry_count',
            next_run_field='domain_next_run_at',
            claim_token_field='domain_claim_token',
            claim_expires_field='domain_claim_expires_at',
            error_code_field='domain_error_code',
            error_message_field=None,
            error_code='TRACKING_SYNC_DOMAIN_FAILED',
            error_message=str(exc),
            delay_seconds=60,
            now_utc=now_utc,
        )
        raise


def _apply_domain_history(tracked_device_id: int, entries: list, now_utc: datetime | None = None) -> int:
    """Upsert domain visit records for a device. Returns the number of rows inserted/updated."""
    if not entries:
        return 0

    from extensions import db
    from sqlalchemy import text as _text

    upserted = 0
    for entry in entries:
        domain = (entry.get('domain') or '').strip().lower()
        if not domain:
            continue

        visit_count = int(entry.get('visit_count') or 1)
        first_seen = entry.get('first_seen_at')
        last_seen = entry.get('last_seen_at')
        category = entry.get('category')
        is_blocked = bool(entry.get('is_blocked', False))

        db.session.execute(
            _text("""
                INSERT INTO device_domain_logs
                    (tracked_device_id, domain, visit_count, first_seen_at, last_seen_at,
                     category, is_blocked, created_at)
                VALUES
                    (:device_id, :domain, :visit_count, :first_seen, :last_seen,
                     :category, :is_blocked, NOW())
                ON CONFLICT (tracked_device_id, domain)
                DO UPDATE SET
                    visit_count  = device_domain_logs.visit_count + EXCLUDED.visit_count,
                    last_seen_at = GREATEST(device_domain_logs.last_seen_at, EXCLUDED.last_seen_at),
                    first_seen_at = LEAST(
                        COALESCE(device_domain_logs.first_seen_at, EXCLUDED.first_seen_at),
                        COALESCE(EXCLUDED.first_seen_at, device_domain_logs.first_seen_at)
                    ),
                    category     = COALESCE(EXCLUDED.category, device_domain_logs.category),
                    is_blocked   = EXCLUDED.is_blocked
            """),
            {
                'device_id': tracked_device_id,
                'domain': domain,
                'visit_count': visit_count,
                'first_seen': first_seen,
                'last_seen': last_seen,
                'category': category,
                'is_blocked': is_blocked,
            },
        )
        upserted += 1

    db.session.commit()
    return upserted
