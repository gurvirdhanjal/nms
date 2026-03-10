from __future__ import annotations

from datetime import datetime

from models.alert_fanout_task import AlertFanoutTask
from models.tracked_device import TrackedDevice
from services.db_task_queue import claim_next_row, mark_row_retry, mark_row_succeeded

CLAIM_TIMEOUT_SECONDS = 60


def build_email_message_id(task: AlertFanoutTask) -> str:
    return f"<{task.delivery_key}@device-monitoring-tactical.local>"


def _default_sse_sender(payload: dict) -> None:
    from services.sse_broadcaster import broadcast_event

    broadcast_event('alert_created', payload)


def _default_email_sender(task: AlertFanoutTask, payload: dict) -> None:
    from services.notification_service import NotificationService

    device = TrackedDevice.query.get(int(task.tracked_device_id))
    if device is None:
        raise ValueError('tracked device not found for email fanout')
    NotificationService.send_warning_alert(
        device,
        metric=payload.get('metric_name'),
        value=payload.get('value', 1),
        message=payload.get('message'),
    )


def deliver_alert_fanout_task(task: AlertFanoutTask, sse_sender=None, email_sender=None) -> None:
    payload = dict(task.payload_json or {})
    payload.setdefault('delivery_key', task.delivery_key)
    if task.channel == 'sse':
        (sse_sender or _default_sse_sender)(payload)
        return
    if task.channel == 'email':
        payload.setdefault('message_id', build_email_message_id(task))
        (email_sender or _default_email_sender)(task, payload)
        task.provider_message_id = payload.get('message_id')
        return
    raise ValueError(f'unsupported fanout channel: {task.channel}')


def run_once(sse_sender=None, email_sender=None, now_utc: datetime | None = None):
    task = claim_next_row(
        AlertFanoutTask,
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
        deliver_alert_fanout_task(task, sse_sender=sse_sender, email_sender=email_sender)
        mark_row_succeeded(task, now_utc=now_utc)
        return task
    except Exception as exc:
        mark_row_retry(task, error_code='ALERT_FANOUT_FAILED', error_message=str(exc), delay_seconds=30, now_utc=now_utc)
        raise
