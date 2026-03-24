"""Alert history and security/compliance report mixins."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import case, desc, func

from extensions import db
from models.audit_log import AuditLog
from models.dashboard import DashboardEvent
from models.device import Device
from models.restricted_site_policy import RestrictedSiteEvent
from models.server_metric_threshold_state import ServerMetricThresholdState
from models.tracked_device import TrackedDevice, TrackingHistoryIntegrityAudit
from .base import _utcnow_naive, _safe_round


class AlertReportMixin:
    def get_alert_history_report(self, start_date=None, end_date=None, severity=None, device_ids=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=7))
        base_query = self._scoped_dashboard_event_query(device_ids).filter(
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date,
        )
        if severity:
            base_query = base_query.filter(DashboardEvent.severity == str(severity).upper())

        # PR 18: Total count before cap (for truncation metadata)
        alerts_total_count = base_query.count()
        # Cap at 20 rows (Master Spec: full data via export only)
        alerts = base_query.order_by(DashboardEvent.timestamp.desc()).limit(20).all()
        device_name_map = {
            row.device_id: row.device_name
            for row in self._inventory_devices_query().filter(
                Device.device_id.in_([alert.device_id for alert in alerts if alert.device_id is not None])
            )
        }
        alert_list = []
        for alert in alerts:
            row = alert.to_dict()
            row["device_name"] = device_name_map.get(alert.device_id) or alert.device_ip
            row["resolved_at"] = alert.resolved_at.isoformat() if alert.resolved_at else None
            alert_list.append(row)

        daily_rows = (
            base_query.with_entities(
                func.date(DashboardEvent.timestamp).label("day"),
                DashboardEvent.severity,
                func.count(DashboardEvent.event_id).label("count"),
            )
            .group_by(func.date(DashboardEvent.timestamp), DashboardEvent.severity)
            .order_by(func.date(DashboardEvent.timestamp))
            .all()
        )
        daily_trend = {}
        for row in daily_rows:
            daily_trend.setdefault(str(row.day), {})
            daily_trend[str(row.day)][row.severity] = int(row.count or 0)

        tta = (
            base_query.with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.acknowledged_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_tta")
            )
            .filter(DashboardEvent.is_acknowledged.is_(True))
            .first()
        )
        ttr = (
            base_query.with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.resolved_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_ttr")
            )
            .filter(DashboardEvent.resolved.is_(True), DashboardEvent.resolved_at.isnot(None))
            .first()
        )
        tta_seconds = round(tta.avg_tta) if tta and tta.avg_tta else None
        ttr_seconds = round(ttr.avg_ttr) if ttr and ttr.avg_ttr else None

        top_devices = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name or "Unknown",
                "device_ip": row.device_ip or "",
                "alert_count": int(row.alert_count or 0),
            }
            for row in (
                base_query.with_entities(
                    DashboardEvent.device_id,
                    Device.device_name,
                    Device.device_ip,
                    func.count(DashboardEvent.event_id).label("alert_count"),
                )
                .outerjoin(Device, Device.device_id == DashboardEvent.device_id)
                .group_by(DashboardEvent.device_id, Device.device_name, Device.device_ip)
                .order_by(desc("alert_count"))
                .limit(10)
                .all()
            )
        ]
        severity_breakdown = {
            row.severity: int(row.count or 0)
            for row in (
                base_query.with_entities(
                    DashboardEvent.severity,
                    func.count(DashboardEvent.event_id).label("count"),
                )
                .group_by(DashboardEvent.severity)
                .all()
            )
        }

        # PR 18: Alert type breakdown (Master Spec Template B, Section 3)
        alert_type_breakdown = _build_alert_type_breakdown(base_query)

        # PR 18: Unresolved aging buckets (0-24h, 1-7d, 7-30d, 30d+)
        unresolved_aging = _build_unresolved_aging(base_query, end_date)

        # PR 18: Subnet analysis (Master Spec Template B, Section 5)
        subnet_analysis = _build_subnet_analysis(alert_list, top_devices)

        # PR 18: Risk summary + recommended actions (deterministic)
        risk_summary, recommended_actions = _build_alert_risk_summary(
            severity_breakdown, top_devices, tta_seconds, ttr_seconds, unresolved_aging
        )

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "alerts": alert_list,
            "alerts_total_count": alerts_total_count,
            "alerts_truncated": alerts_total_count > 20,
            "alerts_export_note": "Full alert list available via CSV/XLSX export" if alerts_total_count > 20 else None,
            "daily_trend": daily_trend,
            "tta": {"seconds": tta_seconds, "human": str(timedelta(seconds=tta_seconds)) if tta_seconds is not None else None},
            "ttr": {"seconds": ttr_seconds, "human": str(timedelta(seconds=ttr_seconds)) if ttr_seconds is not None else None},
            "top_alerted_devices": top_devices,
            "severity_breakdown": severity_breakdown,
            "alert_type_breakdown": alert_type_breakdown,
            "unresolved_aging": unresolved_aging,
            "subnet_analysis": subnet_analysis,
            "risk_summary": risk_summary,
            "recommended_actions": recommended_actions,
        }

    def get_security_compliance_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        tracked_ids = self._tracked_device_ids_subquery()

        alerts_query = self._scoped_dashboard_event_query().filter(
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date,
        )
        # PR 18: Total counts + cap at 20 rows
        _sec_alert_total = alerts_query.count()
        recent_alerts = alerts_query.order_by(DashboardEvent.timestamp.desc()).limit(20).all()
        device_name_map = {row.device_id: row.device_name for row in self._inventory_devices_query().all()}
        recent_audit_log = (
            self._scoped_audit_log_query()
            .filter(AuditLog.timestamp >= start_date, AuditLog.timestamp <= end_date)
            .order_by(AuditLog.timestamp.desc())
            .limit(20)
            .all()
        )
        restricted_site_violations = [
            {
                "device_name": row.device_name,
                "domain": row.domain,
                "count": int(row.hit_count or 0),
                "observed_at_utc": row.latest_seen.isoformat() if row.latest_seen else None,
            }
            for row in (
                db.session.query(
                    TrackedDevice.device_name,
                    RestrictedSiteEvent.domain,
                    func.count(RestrictedSiteEvent.id).label("hit_count"),
                    func.max(RestrictedSiteEvent.observed_at_utc).label("latest_seen"),
                )
                .join(TrackedDevice, TrackedDevice.id == RestrictedSiteEvent.device_id)
                .filter(
                    RestrictedSiteEvent.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    RestrictedSiteEvent.observed_at_utc >= start_date,
                    RestrictedSiteEvent.observed_at_utc <= end_date,
                )
                .group_by(TrackedDevice.device_name, RestrictedSiteEvent.domain)
                .order_by(desc("hit_count"))
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
        threshold_breaches = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "metric_key": row.metric_key,
                "breach_streak": int(row.breach_streak or 0),
                "last_state": row.last_state,
            }
            for row in (
                db.session.query(
                    ServerMetricThresholdState.device_id,
                    Device.device_name,
                    ServerMetricThresholdState.metric_key,
                    ServerMetricThresholdState.breach_streak,
                    ServerMetricThresholdState.last_state,
                )
                .join(Device, Device.device_id == ServerMetricThresholdState.device_id)
                .filter(
                    ServerMetricThresholdState.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    ServerMetricThresholdState.breach_streak > 0,
                )
                .order_by(ServerMetricThresholdState.breach_streak.desc())
                .limit(20)
                .all()
            )
        ]
        summary = {
            "total_alerts": int(alerts_query.count()),
            "critical_alerts": int(alerts_query.filter(DashboardEvent.severity == "CRITICAL").count()),
            "acknowledged_alerts": int(alerts_query.filter(DashboardEvent.is_acknowledged.is_(True)).count()),
            "unresolved_alerts": int(alerts_query.filter(DashboardEvent.resolved.is_(False)).count()),
            "audit_rows": len(recent_audit_log),
            "restricted_site_violations": len(restricted_site_violations),
            "integrity_findings": int(sum(integrity_breakdown.values())),
            "threshold_breaches": len(threshold_breaches),
        }

        # PR 18: Enrich violations with risk context
        from services.report_formatting import classify_violation_risk, violation_risk_note
        for v in restricted_site_violations:
            risk = classify_violation_risk(v.get("domain", ""))
            v["risk"] = risk
            v["risk_note"] = violation_risk_note(risk)

        # PR 18: Top risks = first 5 threshold breaches
        top_risks = threshold_breaches[:5]

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": summary,
            "recent_alerts": [
                {**row.to_dict(), "device_name": device_name_map.get(row.device_id) or row.device_ip}
                for row in recent_alerts
            ],
            "recent_alerts_total_count": _sec_alert_total,
            "recent_alerts_truncated": _sec_alert_total > 20,
            "recent_audit_log": [row.to_dict() for row in recent_audit_log],
            "restricted_site_violations": restricted_site_violations,
            "integrity_breakdown": integrity_breakdown,
            "threshold_breaches": threshold_breaches,
            "top_risks": top_risks,
        }


# ── PR 18: Alert synthesis helper functions ──────────────────────────────────

def _build_alert_type_breakdown(base_query):
    """Map alerts into Master Spec categories: OFFLINE, Latency, Packet Loss, Server Health, Other."""
    rows = (
        base_query.with_entities(
            DashboardEvent.event_type,
            DashboardEvent.message,
            func.count(DashboardEvent.event_id).label("count"),
        )
        .group_by(DashboardEvent.event_type, DashboardEvent.message)
        .all()
    )
    categories = defaultdict(lambda: {"count": 0, "devices": set()})
    for row in rows:
        msg = (row.message or "").lower()
        event_type = (row.event_type or "").lower()
        count = int(row.count or 0)
        if "offline" in msg or event_type == "status_change":
            categories["Device OFFLINE"]["count"] += count
        elif "latency" in msg or "ping" in msg:
            categories["High Latency (PING)"]["count"] += count
        elif "packet" in msg or "loss" in msg:
            categories["Packet Loss (PING)"]["count"] += count
        elif "cpu" in msg or "memory" in msg or "disk" in msg or event_type == "threshold":
            categories["Server Health"]["count"] += count
        else:
            categories["Other"]["count"] += count

    total = sum(c["count"] for c in categories.values())
    return [
        {"type": cat, "count": data["count"],
         "pct_of_total": round(data["count"] / total * 100, 1) if total else 0}
        for cat, data in sorted(categories.items(), key=lambda x: -x[1]["count"])
        if data["count"] > 0
    ]


def _build_unresolved_aging(base_query, end_date):
    """Bucket unresolved alerts by age: 0-24h, 1-7d, 7-30d, 30d+."""
    from .base import _utcnow_naive
    now = end_date or _utcnow_naive()
    unresolved = (
        base_query.with_entities(DashboardEvent.timestamp)
        .filter(DashboardEvent.resolved.is_(False))
        .all()
    )
    buckets = {"0-24h": 0, "1-7d": 0, "7-30d": 0, "30d+": 0}
    for row in unresolved:
        if row.timestamp is None:
            continue
        age = now - row.timestamp
        hours = age.total_seconds() / 3600
        if hours <= 24:
            buckets["0-24h"] += 1
        elif hours <= 168:
            buckets["1-7d"] += 1
        elif hours <= 720:
            buckets["7-30d"] += 1
        else:
            buckets["30d+"] += 1
    return buckets


def _build_subnet_analysis(alert_list, top_devices):
    """Group alerts by /24 subnet. Flag if one subnet >30% of total."""
    subnet_groups = defaultdict(lambda: {"total": 0, "offline": 0, "latency": 0, "pkt_loss": 0, "devices": set()})
    for alert in alert_list:
        ip = alert.get("device_ip") or ""
        if not ip or ip.count(".") != 3:
            continue
        subnet = ".".join(ip.split(".")[:3]) + ".0/24"
        sg = subnet_groups[subnet]
        sg["total"] += 1
        sg["devices"].add(alert.get("device_name") or ip)
        msg = (alert.get("message") or "").lower()
        if "offline" in msg:
            sg["offline"] += 1
        elif "latency" in msg or "ping" in msg:
            sg["latency"] += 1
        elif "packet" in msg or "loss" in msg:
            sg["pkt_loss"] += 1

    total_alerts = sum(sg["total"] for sg in subnet_groups.values())
    result = []
    for subnet, sg in sorted(subnet_groups.items(), key=lambda x: x[1]["total"], reverse=True):
        entry = {
            "subnet": subnet, "total": sg["total"],
            "offline": sg["offline"], "latency": sg["latency"], "pkt_loss": sg["pkt_loss"],
            "device_count": len(sg["devices"]),
        }
        if total_alerts and sg["total"] / total_alerts > 0.30:
            entry["flag"] = "Potential upstream infrastructure issue — >30% of all alerts from this subnet"
        result.append(entry)
    return result


def _build_alert_risk_summary(severity_breakdown, top_devices, tta_seconds, ttr_seconds, unresolved_aging):
    """Build deterministic risk summary text and recommended actions."""
    parts = []
    actions = []

    crit = severity_breakdown.get("CRITICAL", 0)
    unresolved_total = sum(unresolved_aging.values())
    old_unresolved = unresolved_aging.get("7-30d", 0) + unresolved_aging.get("30d+", 0)

    if old_unresolved > 0:
        parts.append(f"{old_unresolved} unresolved alert(s) older than 7 days.")
        actions.append(f"Investigate {old_unresolved} aging unresolved alerts")

    if crit > 0:
        parts.append(f"{crit} critical alerts in this period.")
        actions.append(f"Investigate {crit} critical alerts")

    if top_devices:
        top = top_devices[0]
        count = top.get("alert_count", 0)
        if count > 20:
            parts.append(f"Top affected: {top.get('device_name', 'Unknown')} ({count} alerts).")
            actions.append(f"Review alerting configuration for {top.get('device_name', 'Unknown')}")

    if tta_seconds and tta_seconds > 3600:
        parts.append("Alert acknowledgment SLA exceeded.")
        actions.append("Configure notification channels to reduce TTA below 1 hour")

    risk_summary = " ".join(parts) if parts else "No significant alert risks identified."
    if not actions:
        actions.append("Continue monitoring — alert levels are acceptable")

    return risk_summary, actions
