from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import inspect

from extensions import db
from models.report_export_job import ReportExportJob

_memory_jobs: dict[str, dict] = {}
_memory_jobs_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_naive() -> datetime:
    return _utcnow().replace(tzinfo=None)


def _ttl_seconds() -> int:
    return int(current_app.config.get("REPORT_ASYNC_JOB_TTL_SECONDS", 3600))


def _db_backend_available() -> bool:
    try:
        return "report_export_jobs" in inspect(db.engine).get_table_names()
    except Exception:
        return False


def get_backend() -> str:
    requested = str(current_app.config.get("REPORT_EXPORT_JOB_BACKEND", "db") or "db").strip().lower()
    if requested == "memory":
        return "memory"
    if requested == "db" and _db_backend_available():
        return "db"
    return "memory"


def _serialize_dt(value):
    return value.isoformat() if isinstance(value, datetime) else None


def _memory_job_to_dict(job: dict) -> dict:
    return {
        "job_id": job.get("job_id"),
        "owner_key": job.get("owner_key"),
        "scope_type": job.get("scope_type"),
        "scope_id": job.get("scope_id"),
        "report_type": job.get("report_type"),
        "format": job.get("format"),
        "status": job.get("status"),
        "params_json": job.get("params_json"),
        "payload_cache_key": job.get("payload_cache_key"),
        "row_count": job.get("row_count"),
        "filename": job.get("filename"),
        "file_path": job.get("file_path"),
        "error": job.get("error"),
        "created_at": _serialize_dt(job.get("created_at")),
        "updated_at": _serialize_dt(job.get("updated_at")),
        "started_at": _serialize_dt(job.get("started_at")),
        "finished_at": _serialize_dt(job.get("finished_at")),
        "expires_at": _serialize_dt(job.get("expires_at")),
    }


def _stale_job_timeout_seconds() -> int:
    return int(current_app.config.get("REPORT_EXPORT_JOB_TIMEOUT_SECONDS", 600))


def cleanup_export_jobs() -> None:
    now_utc = _utcnow_naive()
    timeout_seconds = _stale_job_timeout_seconds()
    cutoff = now_utc - timedelta(seconds=timeout_seconds)

    if get_backend() == "db":
        # 1. Remove expired completed/failed jobs
        stale_rows = (
            ReportExportJob.query.filter(
                ReportExportJob.expires_at.isnot(None),
                ReportExportJob.expires_at < now_utc,
            )
            .all()
        )
        for row in stale_rows:
            file_path = row.file_path
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            db.session.delete(row)

        # 2. Reap running/pending jobs stuck longer than timeout
        stuck_rows = (
            ReportExportJob.query.filter(
                ReportExportJob.status.in_(("pending", "running")),
                ReportExportJob.updated_at < cutoff,
            )
            .all()
        )
        for row in stuck_rows:
            row.status = "failed"
            row.error = f"Reaped: stuck in {row.status} for >{timeout_seconds}s"
            row.updated_at = now_utc
            row.expires_at = now_utc + timedelta(seconds=max(1, _ttl_seconds()))
            db.session.add(row)

        if stale_rows or stuck_rows:
            db.session.commit()
        return

    stale_ids: list[str] = []
    stuck_ids: list[str] = []
    with _memory_jobs_lock:
        for job_id, job in _memory_jobs.items():
            expires_at = job.get("expires_at")
            if expires_at and expires_at < now_utc:
                stale_ids.append(job_id)
            elif job.get("status") in ("pending", "running"):
                updated_at = job.get("updated_at")
                if updated_at and updated_at < cutoff:
                    stuck_ids.append(job_id)

        for job_id in stale_ids:
            job = _memory_jobs.pop(job_id, None)
            file_path = job.get("file_path") if job else None
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

        for job_id in stuck_ids:
            job = _memory_jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = f"Reaped: stuck in {job.get('status', 'unknown')} for >{timeout_seconds}s"
                job["updated_at"] = now_utc
                job["expires_at"] = now_utc + timedelta(seconds=max(1, _ttl_seconds()))


def create_export_job(
    *,
    owner_key: str,
    scope_type: str,
    scope_id: int | None,
    report_type: str,
    export_format: str,
    params: dict | None,
    payload_cache_key: str | None,
) -> str:
    job_id = uuid.uuid4().hex
    now_utc = _utcnow_naive()
    params_json = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    if get_backend() == "db":
        row = ReportExportJob(
            job_id=job_id,
            owner_key=owner_key,
            scope_type=str(scope_type or "global"),
            scope_id=scope_id,
            report_type=report_type,
            export_format=export_format,
            status="pending",
            params_json=params_json,
            payload_cache_key=payload_cache_key,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.session.add(row)
        db.session.commit()
        return job_id

    with _memory_jobs_lock:
        _memory_jobs[job_id] = {
            "job_id": job_id,
            "owner_key": owner_key,
            "scope_type": str(scope_type or "global"),
            "scope_id": scope_id,
            "report_type": report_type,
            "format": export_format,
            "status": "pending",
            "params_json": params_json,
            "payload_cache_key": payload_cache_key,
            "row_count": None,
            "filename": None,
            "file_path": None,
            "error": None,
            "created_at": now_utc,
            "updated_at": now_utc,
            "started_at": None,
            "finished_at": None,
            "expires_at": None,
        }
    return job_id


def update_export_job(job_id: str, **updates) -> None:
    now_utc = _utcnow_naive()
    if get_backend() == "db":
        row = db.session.get(ReportExportJob, job_id)
        if not row:
            return
        for key, value in updates.items():
            if key == "format":
                setattr(row, "export_format", value)
            elif hasattr(row, key):
                setattr(row, key, value)
        row.updated_at = now_utc
        if row.status in ("completed", "failed") and not row.expires_at:
            row.expires_at = now_utc + timedelta(seconds=max(1, _ttl_seconds()))
        db.session.add(row)
        db.session.commit()
        return

    with _memory_jobs_lock:
        job = _memory_jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = now_utc
        if job.get("status") in ("completed", "failed") and not job.get("expires_at"):
            job["expires_at"] = now_utc + timedelta(seconds=max(1, _ttl_seconds()))


def get_export_job(job_id: str, owner_key: str | None = None) -> dict | None:
    if get_backend() == "db":
        row = db.session.get(ReportExportJob, job_id)
        if row is None:
            return None
        if owner_key is not None and row.owner_key != owner_key:
            return None
        return row.to_dict()

    with _memory_jobs_lock:
        job = _memory_jobs.get(job_id)
        if job is None:
            return None
        if owner_key is not None and job.get("owner_key") != owner_key:
            return None
        return _memory_job_to_dict(job)


def count_running_export_jobs() -> int:
    if get_backend() == "db":
        return int(
            ReportExportJob.query.filter(
                ReportExportJob.status.in_(("pending", "running"))
            ).count()
        )

    with _memory_jobs_lock:
        return sum(1 for job in _memory_jobs.values() if job.get("status") in ("pending", "running"))
