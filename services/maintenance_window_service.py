from datetime import datetime
from typing import Iterable

from sqlalchemy import func

from extensions import db
from models.device import Device
from models.maintenance_window import MaintenanceWindow


class MaintenanceWindowService:
    @staticmethod
    def _now(now=None):
        return now or datetime.utcnow()

    def deactivate_expired_windows(self, now=None, commit=True):
        now = self._now(now)
        expired_query = MaintenanceWindow.query.filter(
            MaintenanceWindow.is_active.is_(True),
            MaintenanceWindow.end_time < now
        )
        expired_count = expired_query.count()
        if expired_count > 0:
            expired_query.update(
                {MaintenanceWindow.is_active: False},
                synchronize_session=False
            )
            if commit:
                db.session.commit()
        return expired_count

    def _active_now_query(self, now=None):
        now = self._now(now)
        return MaintenanceWindow.query.filter(
            MaintenanceWindow.is_active.is_(True),
            MaintenanceWindow.start_time <= now,
            MaintenanceWindow.end_time >= now
        )

    def is_device_in_maintenance(self, device_id, now=None):
        if not device_id:
            return False
        return self._active_now_query(now).filter(
            MaintenanceWindow.device_id == device_id
        ).first() is not None

    def get_active_window_for_device(self, device_id, now=None):
        if not device_id:
            return None
        return self._active_now_query(now).filter(
            MaintenanceWindow.device_id == device_id
        ).order_by(
            MaintenanceWindow.end_time.asc()
        ).first()

    def get_active_window_map(self, device_ids: Iterable[int], now=None):
        ids = [int(did) for did in (device_ids or []) if did]
        if not ids:
            return {}

        windows = self._active_now_query(now).filter(
            MaintenanceWindow.device_id.in_(ids)
        ).order_by(
            MaintenanceWindow.device_id.asc(),
            MaintenanceWindow.end_time.asc()
        ).all()

        result = {}
        for window in windows:
            if window.device_id not in result:
                result[window.device_id] = window
        return result

    def count_active_devices(self, now=None):
        return (
            self._active_now_query(now)
            .with_entities(func.count(func.distinct(MaintenanceWindow.device_id)))
            .scalar()
            or 0
        )

    def schedule_window(self, *, device_id, start_time, end_time, reason=None, created_by=None):
        now = self._now()
        if start_time is None or end_time is None:
            raise ValueError('start_time and end_time are required')
        if end_time <= start_time:
            raise ValueError('end_time must be after start_time')
        if end_time <= now:
            raise ValueError('end_time must be in the future')

        device = Device.query.get(device_id)
        if not device:
            raise LookupError('Device not found')

        # Keep state clean before overlap checks.
        self.deactivate_expired_windows(now=now, commit=False)

        overlapping = MaintenanceWindow.query.filter(
            MaintenanceWindow.device_id == device_id,
            MaintenanceWindow.is_active.is_(True),
            MaintenanceWindow.end_time >= start_time,
            MaintenanceWindow.start_time <= end_time
        ).first()
        if overlapping:
            raise ValueError('An overlapping maintenance window already exists for this device')

        window = MaintenanceWindow(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
            reason=(reason or '').strip() or None,
            created_by=(created_by or '').strip() or None,
            is_active=True
        )
        db.session.add(window)
        db.session.commit()
        return window

    def cancel_window(self, window_id):
        window = MaintenanceWindow.query.get(window_id)
        if not window:
            raise LookupError('Maintenance window not found')
        if window.is_active:
            window.is_active = False
            db.session.commit()
        return window

    def list_windows(self, include_inactive=False, now=None):
        now = self._now(now)
        self.deactivate_expired_windows(now=now, commit=True)

        query = MaintenanceWindow.query
        if include_inactive:
            query = query.order_by(MaintenanceWindow.created_at.desc())
        else:
            query = query.filter(
                MaintenanceWindow.is_active.is_(True),
                MaintenanceWindow.end_time >= now
            ).order_by(
                MaintenanceWindow.start_time.asc(),
                MaintenanceWindow.id.desc()
            )
        return query.all()


maintenance_window_service = MaintenanceWindowService()
