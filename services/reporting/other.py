"""Remaining report mixins: productivity, network, maintenance, inventory, tracking, printer."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, case, cast, desc, func, or_, text

from extensions import db
from middleware.rbac import scoped_query
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.department import Department
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.interfaces import DeviceInterface, InterfaceTrafficHistory
from models.maintenance_window import MaintenanceWindow
from models.printer import PrintJobAudit, PrinterMetrics
from models.scan_history import DeviceScanHistory
from models.site import Site
from models.subnet import Subnet
from models.tracked_device import (
    DeviceActivityLog, DeviceApplicationLog, TrackedDevice,
    TrackedDeviceAvailabilityEvent, TrackingHistoryIntegrityAudit, TrackingSample,
)
from services.timescaledb_service import TimescaleDBService
from services.tracking_freshness import build_productivity_freshness_summary
from .base import _utcnow_naive, _classify_app, _safe_round, _row_value, APP_CATEGORIES


class OtherReportMixin:
    def get_productivity_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        tracked_ids = self._tracked_device_ids_subquery(device_ids)

        app_rows = (
            db.session.query(
                DeviceApplicationLog.device_id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                DeviceApplicationLog.application_name,
                func.sum(DeviceApplicationLog.duration).label("total_seconds"),
                func.count(DeviceApplicationLog.id).label("session_count"),
            )
            .join(TrackedDevice, TrackedDevice.id == DeviceApplicationLog.device_id)
            .filter(
                DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                DeviceApplicationLog.timestamp >= start_date,
                DeviceApplicationLog.timestamp <= end_date,
            )
            .group_by(
                DeviceApplicationLog.device_id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                DeviceApplicationLog.application_name,
            )
            .all()
        )

        app_breakdown = {}
        category_totals = {}
        for row in app_rows:
            category = _classify_app(row.application_name)
            category_totals[category] = category_totals.get(category, 0) + int(row.total_seconds or 0)
            app_breakdown.setdefault(
                row.device_id,
                {"device_name": row.device_name, "employee_name": row.employee_name, "apps": []},
            )
            app_breakdown[row.device_id]["apps"].append(
                {
                    "name": row.application_name,
                    "category": category,
                    "total_seconds": int(row.total_seconds or 0),
                    "sessions": int(row.session_count or 0),
                }
            )

        activity_rows = (
            db.session.query(
                DeviceActivityLog.device_id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                DeviceActivityLog.activity_type,
                func.sum(DeviceActivityLog.event_count).label("total_events"),
            )
            .join(TrackedDevice, TrackedDevice.id == DeviceActivityLog.device_id)
            .filter(
                DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                DeviceActivityLog.timestamp >= start_date,
                DeviceActivityLog.timestamp <= end_date,
            )
            .group_by(
                DeviceActivityLog.device_id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                DeviceActivityLog.activity_type,
            )
            .all()
        )
        activity_summary = {}
        for row in activity_rows:
            activity_summary.setdefault(
                row.device_id,
                {
                    "device_name": row.device_name,
                    "employee_name": row.employee_name,
                    "active": 0,
                    "idle": 0,
                    "keyboard": 0,
                    "mouse": 0,
                },
            )
            activity_type = str(row.activity_type or "").lower()
            if activity_type in activity_summary[row.device_id]:
                activity_summary[row.device_id][activity_type] = int(row.total_events or 0)
            elif activity_type == "scroll":
                activity_summary[row.device_id]["active"] += int(row.total_events or 0)

        for device_id, info in app_breakdown.items():
            info["apps"].sort(key=lambda item: (-int(item.get("total_seconds") or 0), str(item.get("name") or "")))
            activity_summary.setdefault(
                device_id,
                {
                    "device_name": info.get("device_name"),
                    "employee_name": info.get("employee_name"),
                    "active": 0,
                    "idle": 0,
                    "keyboard": 0,
                    "mouse": 0,
                },
            )

        category_totals = dict(
            sorted(
                category_totals.items(),
                key=lambda item: (-int(item[1] or 0), str(item[0] or "")),
            )
        )

        freshness_device_ids = [
            int(row[0])
            for row in (
                db.session.query(TrackingSample.device_id)
                .filter(
                    TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingSample.received_at >= start_date,
                    TrackingSample.received_at <= end_date,
                )
                .distinct()
                .all()
            )
            if row and row[0]
        ]
        freshness_device_ids = sorted(set(freshness_device_ids) | set(app_breakdown.keys()) | set(activity_summary.keys()))
        freshness_summary = build_productivity_freshness_summary(freshness_device_ids, start_date, end_date)

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "app_breakdown": app_breakdown,
            "category_totals": category_totals,
            "activity_summary": activity_summary,
            "freshness_summary": freshness_summary,
        }

    def get_network_performance_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        inventory_ids = self._inventory_device_ids_subquery(device_ids)

        bandwidth_rows = (
            db.session.query(
                DeviceInterface.device_id,
                Device.device_name,
                DeviceInterface.name.label("interface_name"),
                InterfaceTrafficHistory.timestamp,
                InterfaceTrafficHistory.rx_bps,
                InterfaceTrafficHistory.tx_bps,
                InterfaceTrafficHistory.rx_utilization_pct,
                InterfaceTrafficHistory.tx_utilization_pct,
            )
            .join(DeviceInterface, DeviceInterface.interface_id == InterfaceTrafficHistory.interface_id)
            .join(Device, Device.device_id == DeviceInterface.device_id)
            .filter(
                DeviceInterface.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                InterfaceTrafficHistory.timestamp >= start_date,
                InterfaceTrafficHistory.timestamp <= end_date,
            )
            .order_by(InterfaceTrafficHistory.timestamp.asc())
            .all()
        )
        bandwidth = {}
        for row in bandwidth_rows:
            key = f"{row.device_id}_{row.interface_name}"
            bandwidth.setdefault(
                key,
                {
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "interface": row.interface_name,
                    "points": [],
                },
            )
            bandwidth[key]["points"].append(
                {
                    "ts": row.timestamp.isoformat(),
                    "rx_bps": _safe_round(row.rx_bps),
                    "tx_bps": _safe_round(row.tx_bps),
                    "rx_util": _safe_round(row.rx_utilization_pct),
                    "tx_util": _safe_round(row.tx_utilization_pct),
                }
            )

        uptime_summary = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "avg_uptime": _safe_round(row.avg_uptime),
                "avg_latency_ms": _safe_round(row.avg_latency),
                "avg_packet_loss": _safe_round(row.avg_packet_loss),
            }
            for row in (
                db.session.query(
                    DailyDeviceStats.device_id,
                    Device.device_name,
                    func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                        DailyDeviceStats.uptime_percent.isnot(None),
                        DailyDeviceStats.uptime_percent >= 0,
                        DailyDeviceStats.uptime_percent <= 200,
                    ).label("avg_uptime"),
                    func.avg(cast(DailyDeviceStats.avg_latency_ms, db.Float)).filter(
                        DailyDeviceStats.avg_latency_ms.isnot(None),
                        DailyDeviceStats.avg_latency_ms >= 0,
                        DailyDeviceStats.avg_latency_ms < 1e15,
                    ).label("avg_latency"),
                    func.avg(cast(DailyDeviceStats.avg_packet_loss_pct, db.Float)).filter(
                        DailyDeviceStats.avg_packet_loss_pct.isnot(None),
                        DailyDeviceStats.avg_packet_loss_pct >= 0,
                        DailyDeviceStats.avg_packet_loss_pct <= 100,
                    ).label("avg_packet_loss"),
                )
                .join(Device, Device.device_id == DailyDeviceStats.device_id)
                .filter(
                    DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    DailyDeviceStats.date >= start_date.date(),
                    DailyDeviceStats.date <= end_date.date(),
                )
                .group_by(DailyDeviceStats.device_id, Device.device_name)
                .all()
            )
        ]
        uptime_basis = "daily_device_stats"
        if not uptime_summary:
            uptime_basis = "device_scan_history"
            _scan_rows = (
                self._cagg_scan_uptime_rows(device_ids=device_ids, start_date=start_date, end_date=end_date)
                or self._raw_scan_uptime_rows(device_ids=device_ids, start_date=start_date, end_date=end_date)
            )
            uptime_summary = [
                {
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "avg_uptime": self._availability_pct(row.online_scans, row.total_scans),
                    "avg_latency_ms": _safe_round(row.avg_latency),
                    "avg_packet_loss": _safe_round(row.avg_packet_loss),
                }
                for row in _scan_rows
            ]

        # Enrich uptime_summary with p95 latency, jitter, and timeout rate
        if uptime_summary and db.engine.dialect.name != "sqlite":
            try:
                dev_ids = [d["device_id"] for d in uptime_summary]
                scan_stats = self._fetch_scan_stats_batch(dev_ids, start_date, end_date)
                for dev in uptime_summary:
                    ss = scan_stats.get(dev["device_id"], {})
                    ta = ss.get("timeout_analysis") or {}
                    dev["p95_latency_ms"] = ss.get("p95_latency_ms")
                    dev["jitter_avg_ms"] = ss.get("jitter_avg_ms")
                    dev["timeout_rate_pct"] = ta.get("timeout_rate_pct")
            except Exception:
                pass

        mttr = (
            self._scoped_dashboard_event_query(device_ids)
            .with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.resolved_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_resolve_seconds"),
                func.count(DashboardEvent.event_id).label("total_incidents"),
            )
            .filter(
                DashboardEvent.resolved.is_(True),
                DashboardEvent.resolved_at.isnot(None),
                DashboardEvent.timestamp >= start_date,
                DashboardEvent.timestamp <= end_date,
            )
            .first()
        )
        mttr_seconds = round(mttr.avg_resolve_seconds) if mttr and mttr.avg_resolve_seconds else None

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "uptime_basis": uptime_basis,
            "bandwidth": bandwidth,
            "uptime_summary": uptime_summary,
            "mttr": {
                "seconds": mttr_seconds,
                "human": str(timedelta(seconds=mttr_seconds)) if mttr_seconds is not None else None,
                "total_incidents": int(mttr.total_incidents or 0) if mttr else 0,
            },
        }

    def get_maintenance_availability_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        inventory_ips = self._inventory_device_ips_subquery()
        tracked_ids = self._tracked_device_ids_subquery()

        scheduled_windows = [
            row.to_dict()
            for row in (
                MaintenanceWindow.query.join(Device, Device.device_id == MaintenanceWindow.device_id)
                .filter(
                    MaintenanceWindow.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    MaintenanceWindow.start_time <= end_date,
                    MaintenanceWindow.end_time >= start_date,
                )
                .order_by(MaintenanceWindow.start_time.desc())
                .limit(50)
                .all()
            )
        ]
        maintenance_devices = [
            row.to_dict()
            for row in self._inventory_devices_query().filter(Device.maintenance_mode.is_(True)).limit(50).all()
        ]

        downtime_rows = (
            db.session.query(
                DailyDeviceStats.device_id,
                Device.device_name,
                Device.device_ip,
                func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                    DailyDeviceStats.uptime_percent.isnot(None),
                    DailyDeviceStats.uptime_percent >= 0,
                    DailyDeviceStats.uptime_percent <= 200,
                ).label("avg_uptime"),
            )
            .join(Device, Device.device_id == DailyDeviceStats.device_id)
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .group_by(DailyDeviceStats.device_id, Device.device_name, Device.device_ip)
            .order_by(func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                DailyDeviceStats.uptime_percent.isnot(None),
                DailyDeviceStats.uptime_percent >= 0,
                DailyDeviceStats.uptime_percent <= 200,
            ).asc().nullslast())
            .limit(20)
            .all()
        )
        downtime_leaders = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "device_ip": row.device_ip,
                "availability_pct": _safe_round(row.avg_uptime),
            }
            for row in downtime_rows
        ]
        if not downtime_leaders:
            device_map = {
                row.device_ip: (row.device_id, row.device_name)
                for row in self._inventory_devices_query().all()
            }
            raw_rows = (
                db.session.query(
                    DeviceScanHistory.device_ip,
                    func.count(DeviceScanHistory.scan_id).label("total_scans"),
                    func.sum(case((func.lower(DeviceScanHistory.status) == "online", 1), else_=0)).label("online_scans"),
                )
                .filter(
                    DeviceScanHistory.device_ip.in_(db.session.query(inventory_ips.c.device_ip)),
                    DeviceScanHistory.scan_timestamp >= start_date,
                    DeviceScanHistory.scan_timestamp <= end_date,
                )
                .group_by(DeviceScanHistory.device_ip)
                .order_by(desc("total_scans"))
                .limit(20)
                .all()
            )
            for row in raw_rows:
                total_scans = int(row.total_scans or 0)
                online_scans = int(row.online_scans or 0)
                availability_pct = round((online_scans / total_scans) * 100.0, 2) if total_scans else None
                device_id, device_name = device_map.get(row.device_ip, (None, row.device_ip))
                downtime_leaders.append(
                    {
                        "device_id": device_id,
                        "device_name": device_name,
                        "device_ip": row.device_ip,
                        "availability_pct": availability_pct,
                    }
                )

        tracked_instability = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "offline_events": int(row.offline_events or 0),
                "degraded_events": int(row.degraded_events or 0),
                "event_count": int(row.event_count or 0),
            }
            for row in (
                db.session.query(
                    TrackedDevice.id.label("device_id"),
                    TrackedDevice.device_name,
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "offline", 1), else_=0)).label("offline_events"),
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "degraded", 1), else_=0)).label("degraded_events"),
                    func.count(TrackedDeviceAvailabilityEvent.id).label("event_count"),
                )
                .join(TrackedDeviceAvailabilityEvent, TrackedDeviceAvailabilityEvent.device_id == TrackedDevice.id)
                .filter(
                    TrackedDevice.id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackedDeviceAvailabilityEvent.observed_at >= start_date,
                    TrackedDeviceAvailabilityEvent.observed_at <= end_date,
                )
                .group_by(TrackedDevice.id, TrackedDevice.device_name)
                .order_by(desc("offline_events"), desc("degraded_events"))
                .limit(20)
                .all()
            )
        ]

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "scheduled_windows": scheduled_windows,
            "maintenance_devices": maintenance_devices,
            "downtime_leaders": downtime_leaders,
            "tracked_instability": tracked_instability,
            "summary": {
                "scheduled_windows": len(scheduled_windows),
                "maintenance_devices": len(maintenance_devices),
                "downtime_leaders": len(downtime_leaders),
                "tracked_instability": len(tracked_instability),
            },
        }

    def get_inventory_assets_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        tracked_ids = self._tracked_device_ids_subquery()

        inventory_devices = [row.to_dict() for row in self._inventory_devices_query().order_by(Device.created_at.desc()).limit(50).all()]
        tracked_devices = [row.to_dict() for row in self._tracked_devices_query().order_by(TrackedDevice.created_at.desc()).limit(50).all()]

        active_links = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "tracked_device_id": row.tracked_device_id,
                "tracked_device_name": row.tracked_device_name,
                "confidence": int(row.confidence or 0),
                "link_source": row.link_source,
            }
            for row in (
                db.session.query(
                    DeviceIdentityLink.device_id,
                    Device.device_name,
                    DeviceIdentityLink.tracked_device_id,
                    TrackedDevice.device_name.label("tracked_device_name"),
                    DeviceIdentityLink.confidence,
                    DeviceIdentityLink.link_source,
                )
                .join(Device, Device.device_id == DeviceIdentityLink.device_id)
                .join(TrackedDevice, TrackedDevice.id == DeviceIdentityLink.tracked_device_id)
                .filter(
                    DeviceIdentityLink.is_active.is_(True),
                    or_(
                        DeviceIdentityLink.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                        DeviceIdentityLink.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    ),
                )
                .order_by(DeviceIdentityLink.updated_at.desc())
                .limit(50)
                .all()
            )
        ]
        pending_candidates = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "tracked_device_id": row.tracked_device_id,
                "tracked_device_name": row.tracked_device_name,
                "candidate_score": int(row.candidate_score or 0),
                "status": row.status,
            }
            for row in (
                db.session.query(
                    DeviceIdentityLinkCandidate.device_id,
                    Device.device_name,
                    DeviceIdentityLinkCandidate.tracked_device_id,
                    TrackedDevice.device_name.label("tracked_device_name"),
                    DeviceIdentityLinkCandidate.candidate_score,
                    DeviceIdentityLinkCandidate.status,
                )
                .join(Device, Device.device_id == DeviceIdentityLinkCandidate.device_id)
                .join(TrackedDevice, TrackedDevice.id == DeviceIdentityLinkCandidate.tracked_device_id)
                .filter(
                    DeviceIdentityLinkCandidate.status == "pending",
                    or_(
                        DeviceIdentityLinkCandidate.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                        DeviceIdentityLinkCandidate.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    ),
                )
                .order_by(DeviceIdentityLinkCandidate.detected_at.desc())
                .limit(50)
                .all()
            )
        ]

        linked_inventory_count = int(
            db.session.query(func.count(func.distinct(DeviceIdentityLink.device_id)))
            .filter(
                DeviceIdentityLink.is_active.is_(True),
                DeviceIdentityLink.device_id.in_(db.session.query(inventory_ids.c.device_id)),
            )
            .scalar()
            or 0
        )
        linked_tracked_count = int(
            db.session.query(func.count(func.distinct(DeviceIdentityLink.tracked_device_id)))
            .filter(
                DeviceIdentityLink.is_active.is_(True),
                DeviceIdentityLink.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
            )
            .scalar()
            or 0
        )
        summary = {
            "inventory_devices": int(self._inventory_devices_query().count()),
            "tracked_devices": int(self._tracked_devices_query().count()),
            "sites": int(scoped_query(Site).count()),
            "departments": int(scoped_query(Department).count()),
            "subnets": int(scoped_query(Subnet).count()),
            "active_links": len(active_links),
            "pending_candidates": len(pending_candidates),
            "unlinked_inventory_devices": max(int(self._inventory_devices_query().count()) - linked_inventory_count, 0),
            "unlinked_tracked_devices": max(int(self._tracked_devices_query().count()) - linked_tracked_count, 0),
        }

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": summary,
            "inventory_devices": inventory_devices,
            "tracked_devices": tracked_devices,
            "active_links": active_links,
            "pending_candidates": pending_candidates,
        }

    def get_tracking_operations_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=7))
        tracked_ids = self._tracked_device_ids_subquery()
        scoped_tracked_devices = self._tracked_devices_query().all()
        freshness_device_ids = [row.id for row in scoped_tracked_devices]
        freshness_summary = build_productivity_freshness_summary(freshness_device_ids, start_date, end_date)
        freshness_devices = freshness_summary.get("devices") or {}
        device_name_map = {row.id: row.device_name for row in scoped_tracked_devices}

        top_applications = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "application_name": row.application_name,
                "category": _classify_app(row.application_name),
                "total_seconds": int(row.total_seconds or 0),
            }
            for row in (
                db.session.query(
                    DeviceApplicationLog.device_id,
                    TrackedDevice.device_name,
                    DeviceApplicationLog.application_name,
                    func.sum(DeviceApplicationLog.duration).label("total_seconds"),
                )
                .join(TrackedDevice, TrackedDevice.id == DeviceApplicationLog.device_id)
                .filter(
                    DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceApplicationLog.timestamp >= start_date,
                    DeviceApplicationLog.timestamp <= end_date,
                )
                .group_by(DeviceApplicationLog.device_id, TrackedDevice.device_name, DeviceApplicationLog.application_name)
                .order_by(desc("total_seconds"))
                .limit(20)
                .all()
            )
        ]
        activity_totals = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "activity_type": row.activity_type,
                "total_events": int(row.total_events or 0),
            }
            for row in (
                db.session.query(
                    DeviceActivityLog.device_id,
                    TrackedDevice.device_name,
                    DeviceActivityLog.activity_type,
                    func.sum(DeviceActivityLog.event_count).label("total_events"),
                )
                .join(TrackedDevice, TrackedDevice.id == DeviceActivityLog.device_id)
                .filter(
                    DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceActivityLog.timestamp >= start_date,
                    DeviceActivityLog.timestamp <= end_date,
                )
                .group_by(DeviceActivityLog.device_id, TrackedDevice.device_name, DeviceActivityLog.activity_type)
                .order_by(desc("total_events"))
                .limit(30)
                .all()
            )
        ]
        availability_breakdown = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "offline_events": int(row.offline_events or 0),
                "degraded_events": int(row.degraded_events or 0),
                "online_events": int(row.online_events or 0),
            }
            for row in (
                db.session.query(
                    TrackedDevice.id.label("device_id"),
                    TrackedDevice.device_name,
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "offline", 1), else_=0)).label("offline_events"),
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "degraded", 1), else_=0)).label("degraded_events"),
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "online", 1), else_=0)).label("online_events"),
                )
                .join(TrackedDeviceAvailabilityEvent, TrackedDeviceAvailabilityEvent.device_id == TrackedDevice.id)
                .filter(
                    TrackedDevice.id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackedDeviceAvailabilityEvent.observed_at >= start_date,
                    TrackedDeviceAvailabilityEvent.observed_at <= end_date,
                )
                .group_by(TrackedDevice.id, TrackedDevice.device_name)
                .order_by(desc("offline_events"), desc("degraded_events"))
                .limit(20)
                .all()
            )
        ]
        integrity_breakdown = {
            row.severity: int(row.count or 0)
            for row in (
                TrackingHistoryIntegrityAudit.query.with_entities(
                    TrackingHistoryIntegrityAudit.severity,
                    func.count(TrackingHistoryIntegrityAudit.id).label("count"),
                )
                .filter(
                    TrackingHistoryIntegrityAudit.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingHistoryIntegrityAudit.created_at >= start_date,
                    TrackingHistoryIntegrityAudit.created_at <= end_date,
                )
                .group_by(TrackingHistoryIntegrityAudit.severity)
                .all()
            )
        }
        if not integrity_breakdown:
            integrity_breakdown = {
                row.integrity_status: int(row.count or 0)
                for row in (
                    TrackingSample.query.with_entities(
                        TrackingSample.integrity_status,
                        func.count(TrackingSample.id).label("count"),
                    )
                    .filter(
                        TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                        TrackingSample.received_at >= start_date,
                        TrackingSample.received_at <= end_date,
                    )
                    .group_by(TrackingSample.integrity_status)
                    .all()
                )
            }
        device_freshness = []
        for device_id, device_name in device_name_map.items():
            freshness = freshness_devices.get(str(device_id)) or freshness_devices.get(device_id) or {}
            device_freshness.append(
                {
                    "device_id": device_id,
                    "device_name": device_name,
                    "freshness_state": freshness.get("freshness_state", "empty"),
                    "coverage_pct": freshness.get("coverage_pct", 0.0),
                    "last_sample_at": freshness.get("last_sample_at"),
                    "sample_count": freshness.get("sample_count", 0),
                    "report_eligible": bool(freshness.get("report_eligible")),
                }
            )
        device_freshness.sort(key=lambda item: (item["freshness_state"], -(item["coverage_pct"] or 0)))
        summary = {
            "tracked_devices": len(scoped_tracked_devices),
            "sample_count": int(
                TrackingSample.query.filter(
                    TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingSample.received_at >= start_date,
                    TrackingSample.received_at <= end_date,
                ).count()
            ),
            "application_logs": int(
                DeviceApplicationLog.query.filter(
                    DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceApplicationLog.timestamp >= start_date,
                    DeviceApplicationLog.timestamp <= end_date,
                ).count()
            ),
            "activity_logs": int(
                DeviceActivityLog.query.filter(
                    DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceActivityLog.timestamp >= start_date,
                    DeviceActivityLog.timestamp <= end_date,
                ).count()
            ),
            "availability_events": int(
                TrackedDeviceAvailabilityEvent.query.filter(
                    TrackedDeviceAvailabilityEvent.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackedDeviceAvailabilityEvent.observed_at >= start_date,
                    TrackedDeviceAvailabilityEvent.observed_at <= end_date,
                ).count()
            ),
        }
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": summary,
            "freshness_summary": freshness_summary,
            "device_freshness": device_freshness[:20],
            "top_applications": top_applications,
            "activity_totals": activity_totals,
            "availability_breakdown": availability_breakdown,
            "integrity_breakdown": integrity_breakdown,
        }

    def get_printer_operations_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        printer_filter = func.lower(Device.device_type).like("%printer%")
        printer_device_count = int(self._inventory_devices_query().filter(printer_filter).count())

        printer_status = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "status": row.status,
                "page_count_total": row.page_count_total,
            }
            for row in (
                db.session.query(
                    PrinterMetrics.device_id,
                    Device.device_name,
                    PrinterMetrics.timestamp,
                    PrinterMetrics.status,
                    PrinterMetrics.page_count_total,
                )
                .join(Device, Device.device_id == PrinterMetrics.device_id)
                .filter(
                    PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    PrinterMetrics.timestamp >= start_date,
                    PrinterMetrics.timestamp <= end_date,
                )
                .order_by(PrinterMetrics.timestamp.desc())
                .limit(20)
                .all()
            )
        ]
        print_volume = [
            {
                "printer_name": row.printer_name,
                "user_account": row.user_account,
                "job_count": int(row.job_count or 0),
                "total_pages": int(row.total_pages or 0),
            }
            for row in (
                db.session.query(
                    PrintJobAudit.printer_name,
                    PrintJobAudit.user_account,
                    func.count(PrintJobAudit.id).label("job_count"),
                    func.sum(PrintJobAudit.page_count).label("total_pages"),
                )
                .filter(
                    PrintJobAudit.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    PrintJobAudit.submission_time >= start_date,
                    PrintJobAudit.submission_time <= end_date,
                )
                .group_by(PrintJobAudit.printer_name, PrintJobAudit.user_account)
                .order_by(desc("job_count"))
                .limit(20)
                .all()
            )
        ]
        trigger_start = end_date - timedelta(days=30)
        printer_metrics_count_30d = int(
            PrinterMetrics.query.filter(
                PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                PrinterMetrics.timestamp >= trigger_start,
                PrinterMetrics.timestamp <= end_date,
            ).count()
        )
        print_job_count_30d = int(
            PrintJobAudit.query.filter(
                PrintJobAudit.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                PrintJobAudit.submission_time >= trigger_start,
                PrintJobAudit.submission_time <= end_date,
            ).count()
        )
        consecutive_metric_devices = int(
            db.session.query(func.count())
            .select_from(
                db.session.query(
                    PrinterMetrics.device_id.label("device_id"),
                    func.count(func.distinct(func.date(PrinterMetrics.timestamp))).label("days_with_data"),
                )
                .join(Device, Device.device_id == PrinterMetrics.device_id)
                .filter(
                    PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    printer_filter,
                    PrinterMetrics.timestamp >= end_date - timedelta(days=14),
                    PrinterMetrics.timestamp <= end_date,
                )
                .group_by(PrinterMetrics.device_id)
                .having(func.count(func.distinct(func.date(PrinterMetrics.timestamp))) >= 14)
                .subquery()
            )
            .scalar()
            or 0
        )
        promotion_ready = (
            printer_metrics_count_30d >= 10000
            or print_job_count_30d >= 5000
            or consecutive_metric_devices >= 10
        )
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": {
                "printer_devices": printer_device_count,
                "printer_status_rows": len(printer_status),
                "print_volume_rows": len(print_volume),
                "promotion_ready": promotion_ready,
            },
            "promotion_triggers": {
                "printer_metrics_30d": printer_metrics_count_30d,
                "print_job_audit_30d": print_job_count_30d,
                "managed_printers_with_14d_ingestion": consecutive_metric_devices,
                "promotion_ready": promotion_ready,
            },
            "printer_status": printer_status,
            "print_volume": print_volume,
        }
