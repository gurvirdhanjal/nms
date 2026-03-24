"""Operational report mixin (heatmap + audit log)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func

from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.server_health import ServerHealthLog
from models.server_health_rollups import ServerHealthDailyRollup, ServerHealthHourlyRollup
from services.timescaledb_service import TimescaleDBService
from .base import _utcnow_naive


class OperationalReportMixin:
    def get_operational_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        span = end_date - start_date

        if span <= timedelta(hours=24):
            heatmap = self._operational_heatmap_from_raw(start_date, end_date)
            granularity = "raw"
        elif span <= timedelta(days=30):
            heatmap = self._operational_heatmap_from_hourly(start_date, end_date)
            granularity = "hourly"
            if not heatmap:
                heatmap = self._operational_heatmap_from_raw(start_date, end_date)
                granularity = "raw"
        else:
            heatmap = self._operational_heatmap_from_daily(start_date, end_date)
            granularity = "daily"
            if not heatmap:
                heatmap = self._operational_heatmap_from_hourly(start_date, end_date)
                granularity = "hourly"
            if not heatmap:
                heatmap = self._operational_heatmap_from_raw(start_date, end_date)
                granularity = "raw"

        audit_logs = (
            self._scoped_dashboard_event_query()
            .filter(
                DashboardEvent.event_type.in_(["SYSTEM", "CONFIG", "SECURITY"]),
                DashboardEvent.timestamp >= start_date,
                DashboardEvent.timestamp <= end_date,
            )
            .order_by(DashboardEvent.timestamp.desc())
            .limit(50)
            .all()
        )
        new_devices = (
            self._inventory_devices_query()
            .filter(Device.created_at >= start_date, Device.created_at <= end_date)
            .order_by(Device.created_at.desc())
            .limit(20)
            .all()
        )
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "heatmap": heatmap,
            "heatmap_granularity": granularity,
            "audit_log": [row.to_dict() for row in audit_logs],
            "new_devices": [row.to_dict() for row in new_devices],
        }

    def _operational_heatmap_from_raw(self, start_date, end_date):
        inventory_ids = self._inventory_device_ids_subquery()
        rows = (
            db.session.query(ServerHealthLog.timestamp)
            .filter(
                ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthLog.timestamp >= start_date,
                ServerHealthLog.timestamp <= end_date,
            )
            .all()
        )
        buckets = {}
        for row in rows:
            if row.timestamp is None:
                continue
            key = (self._heatmap_day_index(row.timestamp), row.timestamp.hour)
            buckets[key] = buckets.get(key, 0) + 1
        return [[day, hour, count] for (day, hour), count in sorted(buckets.items())]

    def _operational_heatmap_from_hourly(self, start_date, end_date):
        if TimescaleDBService.is_timescaledb_enabled():
            device_ids = self._inventory_device_id_list()
            rows = self._timescaledb_rows(
                """
                SELECT
                    bucket_hour,
                    SUM(COALESCE(sample_count, 0)) AS activity_count
                FROM server_health_hourly_cagg
                WHERE device_id IN :device_ids
                  AND bucket_hour >= :start_date
                  AND bucket_hour <= :end_date
                GROUP BY bucket_hour
                ORDER BY bucket_hour ASC
                """,
                device_ids,
                start_date=start_date,
                end_date=end_date,
            )
            return [
                [self._heatmap_day_index(row["bucket_hour"]), row["bucket_hour"].hour, int(row["activity_count"] or 0)]
                for row in rows
                if row.get("bucket_hour") is not None
            ]

        inventory_ids = self._inventory_device_ids_subquery()
        rows = (
            db.session.query(
                ServerHealthHourlyRollup.bucket_hour.label("bucket_hour"),
                func.sum(ServerHealthHourlyRollup.sample_count).label("activity_count"),
            )
            .filter(
                ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthHourlyRollup.bucket_hour >= start_date,
                ServerHealthHourlyRollup.bucket_hour <= end_date,
            )
            .group_by(ServerHealthHourlyRollup.bucket_hour)
            .order_by(ServerHealthHourlyRollup.bucket_hour.asc())
            .all()
        )
        return [
            [self._heatmap_day_index(row.bucket_hour), row.bucket_hour.hour, int(row.activity_count or 0)]
            for row in rows
            if row.bucket_hour is not None
        ]

    def _operational_heatmap_from_daily(self, start_date, end_date):
        if TimescaleDBService.is_timescaledb_enabled():
            device_ids = self._inventory_device_id_list()
            rows = self._timescaledb_rows(
                """
                SELECT
                    bucket_day,
                    SUM(COALESCE(sample_count, 0)) AS activity_count
                FROM server_health_daily_cagg
                WHERE device_id IN :device_ids
                  AND bucket_day >= :start_date
                  AND bucket_day <= :end_date
                GROUP BY bucket_day
                ORDER BY bucket_day ASC
                """,
                device_ids,
                start_date=start_date,
                end_date=end_date,
            )
            return [
                [self._heatmap_day_index(row["bucket_day"]), 12, int(row["activity_count"] or 0)]
                for row in rows
                if row.get("bucket_day") is not None
            ]

        inventory_ids = self._inventory_device_ids_subquery()
        rows = (
            db.session.query(
                ServerHealthDailyRollup.bucket_day.label("bucket_day"),
                func.sum(ServerHealthDailyRollup.sample_count).label("activity_count"),
            )
            .filter(
                ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthDailyRollup.bucket_day >= start_date.date(),
                ServerHealthDailyRollup.bucket_day <= end_date.date(),
            )
            .group_by(ServerHealthDailyRollup.bucket_day)
            .order_by(ServerHealthDailyRollup.bucket_day.asc())
            .all()
        )
        return [
            [self._heatmap_day_index(datetime.combine(row.bucket_day, datetime.min.time())), 12, int(row.activity_count or 0)]
            for row in rows
            if row.bucket_day is not None
        ]
