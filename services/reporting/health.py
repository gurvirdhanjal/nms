"""Device health report mixin."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func

from extensions import db
from models.device import Device
from models.server_health import ServerHealthLog
from models.server_health_rollups import ServerHealthDailyRollup, ServerHealthHourlyRollup
from services.timescaledb_service import TimescaleDBService
from .base import _utcnow_naive, _safe_round, _row_value


class HealthReportMixin:
    def get_device_health_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        span = end_date - start_date
        if span <= timedelta(hours=24):
            time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
            granularity = "raw"
        elif span <= timedelta(days=30):
            # Fallback order for <=30d is intentional:
            # 1. hourly rollups (preferred — pre-aggregated, fast)
            # 2. raw scan history (fallback when rollups missing)
            # 3. daily rollups (final fallback post-startup-backfill)
            time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
            granularity = "hourly"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
                granularity = "raw"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_daily(device_ids, start_date, end_date)
                granularity = "daily"
        else:
            time_series, summary = self._health_from_daily(device_ids, start_date, end_date)
            granularity = "daily"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
                granularity = "hourly"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
                granularity = "raw"

        total_samples = sum(len(v) for v in time_series.values()) if time_series else 0
        # Window-aware sparse threshold: < 20% of expected hourly sample count = sparse
        expected_samples = max(1, int((end_date - start_date).total_seconds() / 3600))
        data_note = None
        if total_samples == 0:
            data_note = "no_data"
        elif total_samples < expected_samples * 0.2:
            data_note = "sparse"

        # PR 19: Peaks, breaches, and capacity runway
        peaks_and_breaches = _extract_peaks_and_breaches(time_series, granularity)

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "granularity": granularity,
            "time_series": time_series,
            "summary": summary,
            "total_samples": total_samples,
            "data_note": data_note,
            "peaks_and_breaches": peaks_and_breaches,
        }

    def _health_from_raw(self, device_ids, start_dt, end_dt):
        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        rows = (
            db.session.query(
                ServerHealthLog.device_id,
                Device.device_name,
                ServerHealthLog.timestamp,
                ServerHealthLog.cpu_usage,
                ServerHealthLog.memory_usage,
                ServerHealthLog.disk_usage,
                ServerHealthLog.network_in_bps,
                ServerHealthLog.network_out_bps,
            )
            .join(Device, Device.device_id == ServerHealthLog.device_id)
            .filter(
                ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthLog.timestamp >= start_dt,
                ServerHealthLog.timestamp <= end_dt,
            )
            .order_by(ServerHealthLog.timestamp.asc())
            .all()
        )
        summary_rows = (
            db.session.query(
                ServerHealthLog.device_id,
                Device.device_name,
                func.avg(ServerHealthLog.cpu_usage).label("avg_cpu"),
                func.max(ServerHealthLog.cpu_usage).label("max_cpu"),
                func.avg(ServerHealthLog.memory_usage).label("avg_mem"),
                func.max(ServerHealthLog.memory_usage).label("max_mem"),
                func.avg(ServerHealthLog.disk_usage).label("avg_disk"),
                func.count(ServerHealthLog.id).label("samples"),
            )
            .join(Device, Device.device_id == ServerHealthLog.device_id)
            .filter(
                ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthLog.timestamp >= start_dt,
                ServerHealthLog.timestamp <= end_dt,
            )
            .group_by(ServerHealthLog.device_id, Device.device_name)
            .all()
        )
        return self._build_time_series(rows), self._build_health_summary(summary_rows)

    def _health_from_hourly(self, device_ids, start_dt, end_dt):
        if TimescaleDBService.is_timescaledb_enabled():
            scoped_ids = self._inventory_device_id_list(device_ids)
            rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    c.bucket_hour AS timestamp,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS cpu_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS memory_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS disk_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_in_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_in_bps,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_out_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_out_bps
                FROM server_health_hourly_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_hour >= :start_dt
                  AND c.bucket_hour <= :end_dt
                GROUP BY c.device_id, d.device_name, c.bucket_hour
                ORDER BY timestamp ASC, d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            summary_rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_cpu,
                    MAX(c.max_cpu_usage) AS max_cpu,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_mem,
                    MAX(c.max_memory_usage) AS max_mem,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_disk,
                    SUM(COALESCE(c.sample_count, 0)) AS samples
                FROM server_health_hourly_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_hour >= :start_dt
                  AND c.bucket_hour <= :end_dt
                GROUP BY c.device_id, d.device_name
                ORDER BY d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return self._build_time_series(rows), self._build_health_summary(summary_rows)

        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        rows = (
            db.session.query(
                ServerHealthHourlyRollup.device_id,
                Device.device_name,
                ServerHealthHourlyRollup.bucket_hour.label("timestamp"),
                ServerHealthHourlyRollup.avg_cpu_usage.label("cpu_usage"),
                ServerHealthHourlyRollup.avg_memory_usage.label("memory_usage"),
                ServerHealthHourlyRollup.avg_disk_usage.label("disk_usage"),
                ServerHealthHourlyRollup.avg_network_in_bps.label("network_in_bps"),
                ServerHealthHourlyRollup.avg_network_out_bps.label("network_out_bps"),
            )
            .join(Device, Device.device_id == ServerHealthHourlyRollup.device_id)
            .filter(
                ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthHourlyRollup.bucket_hour >= start_dt,
                ServerHealthHourlyRollup.bucket_hour <= end_dt,
            )
            .order_by(ServerHealthHourlyRollup.bucket_hour.asc())
            .all()
        )
        summary_rows = (
            db.session.query(
                ServerHealthHourlyRollup.device_id,
                Device.device_name,
                func.avg(ServerHealthHourlyRollup.avg_cpu_usage).label("avg_cpu"),
                func.max(ServerHealthHourlyRollup.max_cpu_usage).label("max_cpu"),
                func.avg(ServerHealthHourlyRollup.avg_memory_usage).label("avg_mem"),
                func.max(ServerHealthHourlyRollup.max_memory_usage).label("max_mem"),
                func.avg(ServerHealthHourlyRollup.avg_disk_usage).label("avg_disk"),
                func.sum(ServerHealthHourlyRollup.sample_count).label("samples"),
            )
            .join(Device, Device.device_id == ServerHealthHourlyRollup.device_id)
            .filter(
                ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthHourlyRollup.bucket_hour >= start_dt,
                ServerHealthHourlyRollup.bucket_hour <= end_dt,
            )
            .group_by(ServerHealthHourlyRollup.device_id, Device.device_name)
            .all()
        )
        return self._build_time_series(rows), self._build_health_summary(summary_rows)

    def _health_from_daily(self, device_ids, start_dt, end_dt):
        if TimescaleDBService.is_timescaledb_enabled():
            scoped_ids = self._inventory_device_id_list(device_ids)
            rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    c.bucket_day AS timestamp,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS cpu_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS memory_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS disk_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_in_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_in_bps,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_out_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_out_bps
                FROM server_health_daily_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_day >= :start_dt
                  AND c.bucket_day <= :end_dt
                GROUP BY c.device_id, d.device_name, c.bucket_day
                ORDER BY timestamp ASC, d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            summary_rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_cpu,
                    MAX(c.max_cpu_usage) AS max_cpu,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_mem,
                    MAX(c.max_memory_usage) AS max_mem,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_disk,
                    SUM(COALESCE(c.sample_count, 0)) AS samples
                FROM server_health_daily_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_day >= :start_dt
                  AND c.bucket_day <= :end_dt
                GROUP BY c.device_id, d.device_name
                ORDER BY d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return self._build_time_series(rows), self._build_health_summary(summary_rows)

        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        rows = (
            db.session.query(
                ServerHealthDailyRollup.device_id,
                Device.device_name,
                ServerHealthDailyRollup.bucket_day.label("timestamp"),
                ServerHealthDailyRollup.avg_cpu_usage.label("cpu_usage"),
                ServerHealthDailyRollup.avg_memory_usage.label("memory_usage"),
                ServerHealthDailyRollup.avg_disk_usage.label("disk_usage"),
                ServerHealthDailyRollup.avg_network_in_bps.label("network_in_bps"),
                ServerHealthDailyRollup.avg_network_out_bps.label("network_out_bps"),
            )
            .join(Device, Device.device_id == ServerHealthDailyRollup.device_id)
            .filter(
                ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthDailyRollup.bucket_day >= start_dt.date(),
                ServerHealthDailyRollup.bucket_day <= end_dt.date(),
            )
            .order_by(ServerHealthDailyRollup.bucket_day.asc())
            .all()
        )
        summary_rows = (
            db.session.query(
                ServerHealthDailyRollup.device_id,
                Device.device_name,
                func.avg(ServerHealthDailyRollup.avg_cpu_usage).label("avg_cpu"),
                func.max(ServerHealthDailyRollup.max_cpu_usage).label("max_cpu"),
                func.avg(ServerHealthDailyRollup.avg_memory_usage).label("avg_mem"),
                func.max(ServerHealthDailyRollup.max_memory_usage).label("max_mem"),
                func.avg(ServerHealthDailyRollup.avg_disk_usage).label("avg_disk"),
                func.sum(ServerHealthDailyRollup.sample_count).label("samples"),
            )
            .join(Device, Device.device_id == ServerHealthDailyRollup.device_id)
            .filter(
                ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthDailyRollup.bucket_day >= start_dt.date(),
                ServerHealthDailyRollup.bucket_day <= end_dt.date(),
            )
            .group_by(ServerHealthDailyRollup.device_id, Device.device_name)
            .all()
        )
        return self._build_time_series(rows), self._build_health_summary(summary_rows)

    @staticmethod
    def _build_health_summary(rows):
        return [
            {
                "device_id": _row_value(row, "device_id"),
                "device_name": _row_value(row, "device_name"),
                "avg_cpu": _safe_round(_row_value(row, "avg_cpu")),
                "max_cpu": _safe_round(_row_value(row, "max_cpu")),
                "avg_mem": _safe_round(_row_value(row, "avg_mem")),
                "max_mem": _safe_round(_row_value(row, "max_mem")),
                "avg_disk": _safe_round(_row_value(row, "avg_disk")),
                "samples": _row_value(row, "samples"),
            }
            for row in rows
        ]

    @staticmethod
    def _build_time_series(rows):
        by_device = {}
        for row in rows:
            device_id = _row_value(row, "device_id")
            device_name = _row_value(row, "device_name")
            timestamp = _row_value(row, "timestamp")
            by_device.setdefault(device_id, {"device_name": device_name, "points": []})
            by_device[device_id]["points"].append(
                {
                    "ts": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                    "cpu": _safe_round(_row_value(row, "cpu_usage")),
                    "mem": _safe_round(_row_value(row, "memory_usage")),
                    "disk": _safe_round(_row_value(row, "disk_usage")),
                    "net_in": _safe_round(_row_value(row, "network_in_bps")),
                    "net_out": _safe_round(_row_value(row, "network_out_bps")),
                }
            )
        return by_device


# ── PR 19: Peaks, breaches, and capacity runway ─────────────────────────────

_GRANULARITY_HOURS = {"raw": 5 / 60, "hourly": 1.0, "daily": 24.0}

_METRIC_MAP = [
    ("cpu", "CPU", "cpu_usage_pct"),
    ("mem", "Memory", "memory_usage_pct"),
    ("disk", "Disk", "disk_usage_pct"),
]


def _extract_peaks_and_breaches(time_series: dict, granularity: str = "hourly") -> dict:
    """For each device, extract peak values, threshold breaches, trend descriptions,
    and capacity runway estimates from the time-series data.

    Uses get_report_thresholds() as canonical threshold source.
    No DB queries — operates on already-fetched time_series dict."""
    if not time_series:
        return {}

    try:
        from services.report_insight_engine import get_report_thresholds
        thresholds = get_report_thresholds()
    except Exception:
        thresholds = {
            "cpu_usage_pct": {"warning": 80, "critical": 90},
            "memory_usage_pct": {"warning": 75, "critical": 95},
            "disk_usage_pct": {"warning": 90, "critical": 95},
        }

    gran_hours = _GRANULARITY_HOURS.get(granularity, 1.0)
    result = {}

    for device_id, device_data in time_series.items():
        points = device_data.get("points", [])
        if not points:
            continue

        device_name = device_data.get("device_name", f"Device {device_id}")
        peaks = []
        breaches = []
        trend_parts = []
        capacity_runway = []

        for ts_key, label, threshold_key in _METRIC_MAP:
            values = []
            timestamps = []
            for p in points:
                v = p.get(ts_key)
                if v is not None:
                    try:
                        values.append(float(v))
                        timestamps.append(p.get("ts", ""))
                    except (TypeError, ValueError):
                        pass

            if not values:
                continue

            avg_val = sum(values) / len(values)
            max_val = max(values)
            max_idx = values.index(max_val)
            min_val = min(values)

            # Peak
            peaks.append({
                "metric": label,
                "peak_value": round(max_val, 1),
                "peak_at": timestamps[max_idx] if max_idx < len(timestamps) else None,
                "avg_value": round(avg_val, 1),
            })

            # Breach detection
            t = thresholds.get(threshold_key, {})
            for level in ("critical", "warning"):
                thresh = t.get(level)
                if thresh is None:
                    continue
                breach_points = [i for i, v in enumerate(values) if v > thresh]
                if not breach_points:
                    continue

                # Sustained breach: longest consecutive run
                max_consecutive = 0
                current_run = 1
                for i in range(1, len(breach_points)):
                    if breach_points[i] == breach_points[i - 1] + 1:
                        current_run += 1
                        max_consecutive = max(max_consecutive, current_run)
                    else:
                        current_run = 1
                if len(breach_points) == 1:
                    max_consecutive = 1
                elif max_consecutive == 0:
                    max_consecutive = 1

                breaches.append({
                    "metric": label,
                    "threshold": thresh,
                    "level": level,
                    "breach_count": len(breach_points),
                    "max_consecutive": max_consecutive,
                    "sustained_hours": round(max_consecutive * gran_hours, 1) if max_consecutive >= 2 else 0,
                    "first_breach_at": timestamps[breach_points[0]] if breach_points else None,
                    "last_breach_at": timestamps[breach_points[-1]] if breach_points else None,
                })
                break  # Only report the worst level per metric

            # Trend description
            spread = max_val - min_val
            if spread < 10:
                trend_parts.append(f"{label}: stable around {avg_val:.0f}%")
            elif max_val > avg_val * 2:
                peak_ts = timestamps[max_idx] if max_idx < len(timestamps) else "unknown"
                trend_parts.append(f"{label}: stable {min_val:.0f}-{avg_val:.0f}% with spike to {max_val:.0f}% at {peak_ts}")
            else:
                trend_parts.append(f"{label}: ranging {min_val:.0f}-{max_val:.0f}% (avg {avg_val:.0f}%)")

            # Capacity runway (linear regression)
            if len(values) >= 7:
                runway = _compute_capacity_runway(values, label, threshold_key, thresholds)
                if runway:
                    capacity_runway.append(runway)

        result[device_id] = {
            "device_name": device_name,
            "peaks": peaks,
            "breaches": breaches,
            "trend_description": ". ".join(trend_parts) + "." if trend_parts else "",
            "capacity_runway": capacity_runway,
        }

    return result


def _compute_capacity_runway(values, label, threshold_key, thresholds):
    """Simple linear regression for capacity runway estimate.
    Only shown when growth rate > 0.1%/day, R² > 0.3, and ≥7 data points."""
    n = len(values)
    if n < 7:
        return None

    # Simple linear regression: y = mx + b
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return None

    slope = numerator / denominator  # units per data point interval

    # R² calculation
    ss_res = sum((v - (slope * i + (y_mean - slope * x_mean))) ** 2 for i, v in enumerate(values))
    ss_tot = sum((v - y_mean) ** 2 for v in values)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    if r_squared < 0.3:
        return None  # Too noisy
    if slope <= 0.1:
        return None  # Not growing meaningfully

    # Estimate days to breach
    t = thresholds.get(threshold_key, {})
    warn_thresh = t.get("warning")
    if warn_thresh is None:
        return None

    current_avg = values[-1]  # Use latest value
    if current_avg >= warn_thresh:
        return None  # Already breached

    days_to_breach = (warn_thresh - current_avg) / slope

    return {
        "metric": label,
        "current_avg": round(current_avg, 1),
        "daily_growth_rate": round(slope, 2),
        "threshold": warn_thresh,
        "estimated_days_to_breach": round(days_to_breach),
        "r_squared": round(r_squared, 2),
        "confidence": "high" if r_squared > 0.7 else "medium",
    }
