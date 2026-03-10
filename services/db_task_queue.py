from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import asc, or_

from extensions import db


def reclaim_expired_claims(
    model,
    *,
    status_field: str,
    retry_count_field: str | None,
    claim_token_field: str,
    claim_expires_field: str,
    next_run_field: str | None = None,
    running_status: str = 'running',
    pending_status: str = 'pending',
    backoff_seconds: int = 5,
    now_utc: datetime | None = None,
) -> int:
    now_utc = now_utc or datetime.utcnow()
    status_column = getattr(model, status_field)
    claim_expires_column = getattr(model, claim_expires_field)
    rows = model.query.filter(
        status_column == running_status,
        claim_expires_column.isnot(None),
        claim_expires_column < now_utc,
    ).all()
    reclaimed = 0
    for row in rows:
        setattr(row, status_field, pending_status)
        setattr(row, claim_token_field, None)
        setattr(row, claim_expires_field, None)
        if retry_count_field:
            setattr(row, retry_count_field, int(getattr(row, retry_count_field) or 0) + 1)
        if next_run_field:
            setattr(row, next_run_field, now_utc + timedelta(seconds=max(1, int(backoff_seconds))))
        reclaimed += 1
    if reclaimed:
        db.session.commit()
    return reclaimed


def claim_next_row(
    model,
    *,
    status_field: str,
    claim_token_field: str,
    claim_expires_field: str,
    claim_timeout_seconds: int,
    retry_count_field: str | None = None,
    next_run_field: str | None = 'next_run_at',
    started_at_field: str | None = 'started_at',
    pending_status: str = 'pending',
    running_status: str = 'running',
    extra_filters: list | None = None,
    order_fields: tuple[str, ...] = ('priority', 'next_run_at', 'id'),
    now_utc: datetime | None = None,
):
    now_utc = now_utc or datetime.utcnow()
    reclaim_expired_claims(
        model,
        status_field=status_field,
        retry_count_field=retry_count_field,
        claim_token_field=claim_token_field,
        claim_expires_field=claim_expires_field,
        next_run_field=next_run_field,
        running_status=running_status,
        pending_status=pending_status,
        now_utc=now_utc,
    )

    status_column = getattr(model, status_field)
    query = model.query.filter(status_column == pending_status)
    if next_run_field and hasattr(model, next_run_field):
        next_run_column = getattr(model, next_run_field)
        query = query.filter(or_(next_run_column.is_(None), next_run_column <= now_utc))
    for condition in extra_filters or []:
        query = query.filter(condition)

    order_by = []
    for field_name in order_fields:
        if not field_name:
            continue
        if field_name.startswith('-'):
            order_by.append(getattr(model, field_name[1:]).desc())
        elif hasattr(model, field_name):
            order_by.append(asc(getattr(model, field_name)))
    if order_by:
        query = query.order_by(*order_by)

    candidate_ids = [row[0] for row in query.with_entities(model.id).limit(25).all()]
    for row_id in candidate_ids:
        claim_token = uuid.uuid4().hex
        update_values = {
            status_field: running_status,
            claim_token_field: claim_token,
            claim_expires_field: now_utc + timedelta(seconds=max(1, int(claim_timeout_seconds))),
        }
        if started_at_field and hasattr(model, started_at_field):
            update_values[started_at_field] = now_utc
        update_query = model.query.filter(model.id == row_id, status_column == pending_status)
        if next_run_field and hasattr(model, next_run_field):
            next_run_column = getattr(model, next_run_field)
            update_query = update_query.filter(or_(next_run_column.is_(None), next_run_column <= now_utc))
        updated = update_query.update(update_values, synchronize_session=False)
        if updated == 1:
            db.session.commit()
            return model.query.get(row_id)
        db.session.rollback()
    return None


def mark_row_succeeded(
    row,
    *,
    status_field: str = 'status',
    claim_token_field: str = 'claim_token',
    claim_expires_field: str = 'claim_expires_at',
    finished_at_field: str | None = 'finished_at',
    success_status: str = 'completed',
    now_utc: datetime | None = None,
) -> None:
    now_utc = now_utc or datetime.utcnow()
    setattr(row, status_field, success_status)
    setattr(row, claim_token_field, None)
    setattr(row, claim_expires_field, None)
    if finished_at_field and hasattr(row, finished_at_field):
        setattr(row, finished_at_field, now_utc)
    db.session.commit()


def mark_row_retry(
    row,
    *,
    status_field: str = 'status',
    retry_count_field: str | None = 'retry_count',
    next_run_field: str | None = 'next_run_at',
    claim_token_field: str = 'claim_token',
    claim_expires_field: str = 'claim_expires_at',
    error_code_field: str | None = 'error_code',
    error_message_field: str | None = 'error_message',
    error_code: str | None = None,
    error_message: str | None = None,
    pending_status: str = 'pending',
    delay_seconds: int = 30,
    now_utc: datetime | None = None,
) -> None:
    now_utc = now_utc or datetime.utcnow()
    setattr(row, status_field, pending_status)
    setattr(row, claim_token_field, None)
    setattr(row, claim_expires_field, None)
    if retry_count_field and hasattr(row, retry_count_field):
        setattr(row, retry_count_field, int(getattr(row, retry_count_field) or 0) + 1)
    if next_run_field and hasattr(row, next_run_field):
        setattr(row, next_run_field, now_utc + timedelta(seconds=max(1, int(delay_seconds))))
    if error_code_field and hasattr(row, error_code_field):
        setattr(row, error_code_field, error_code)
    if error_message_field and hasattr(row, error_message_field):
        setattr(row, error_message_field, (str(error_message or '')[:1000] or None))
    db.session.commit()
