"""
Report Narrative Service — per-report-type narrative generators.

Produces progressive disclosure output per Master Report Specification:
  Executive Banner → Action Required → Details → Interpretation → Actions

All logic is deterministic (rule-based). No Gemini, no DB queries.
Operates on report payloads already fetched by the report service.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from services.report_formatting import (
    SEVERITY_ORDER,
    fmt,
    fmt_ms,
    fmt_pct,
    format_duration,
    format_timestamp_utc,
    normalize_device_display,
    classify_violation_risk,
)

logger = logging.getLogger(__name__)


class ReportNarrativeService:
    """Deterministic per-section narrative generator."""

    def __init__(self):
        self._thresholds = None

    def _load_thresholds(self):
        if self._thresholds is None:
            try:
                engine = _get_insight_engine()
                self._thresholds = engine._all_thresholds()
            except Exception:
                self._thresholds = {
                    "uptime_pct": {"warning": 95, "critical": 90, "inverted": True},
                    "latency_ms": {"warning": 100, "critical": 200},
                    "packet_loss_pct": {"warning": 5, "critical": 15},
                    "cpu_usage_pct": {"warning": 80, "critical": 90},
                    "memory_usage_pct": {"warning": 75, "critical": 95},
                    "disk_usage_pct": {"warning": 90, "critical": 95},
                }
        return self._thresholds

    _handlers: Dict[str, str] = {
        "executive": "_narrate_executive",
        "server-fleet": "_narrate_server_fleet",
        "tracked-fleet": "_narrate_tracked_fleet",
        "alerts": "_narrate_alerts",
        "security-compliance": "_narrate_security",
        "operational": "_narrate_operational",
        "device-health": "_narrate_device_health",
        "device-inspector": "_narrate_device_inspector",
    }

    def generate_narrative(self, report_type: str, report_data: dict) -> Optional[dict]:
        """Dispatch to per-type generator. Returns narrative dict or None.
        Never raises — returns None on any failure."""
        handler_name = self._handlers.get(report_type)
        if not handler_name:
            return None
        handler = getattr(self, handler_name, None)
        if not handler:
            return None
        start = time.monotonic()
        try:
            result = handler(report_data)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("[Narrative] type=%s generated=%s took_ms=%d",
                        report_type, bool(result), elapsed_ms)
            return result
        except Exception as exc:
            logger.warning("[Narrative] %s failed: %s", report_type, exc)
            return None

    # ── Executive ────────────────────────────────────────────────────────

    def _narrate_executive(self, data: dict) -> dict:
        """Master Spec Template E: Fleet Health Overview."""
        uptime = data.get("uptime_score")
        latency = data.get("avg_latency")
        total = data.get("total_devices", 0)
        health = data.get("health_distribution", {})
        healthy = health.get("Healthy", 0)
        critical = health.get("Critical", 0)
        prev_uptime = data.get("prev_uptime_score")
        mtta = (data.get("sla_metrics") or {}).get("mtta_seconds")

        if not total:
            return _zero_data_narrative()

        # Banner KPIs
        kpis = [
            _kpi("Fleet Availability", fmt_pct(uptime), _status_for_uptime(uptime), _delta(uptime, prev_uptime)),
            _kpi("Total Devices", str(total), "ok", None),
            _kpi("Healthy", f"{healthy} ({healthy * 100 // max(total, 1)}%)",
                 "ok" if healthy > critical else "warning", None),
            _kpi("Critical", f"{critical} ({critical * 100 // max(total, 1)}%)",
                 "critical" if critical > 0 else "ok", None),
            _kpi("Avg Latency", fmt_ms(latency),
                 "critical" if latency and latency > 200 else "warning" if latency and latency > 100 else "ok", None),
        ]
        if mtta is not None:
            kpis.append(_kpi("MTTA", format_duration(mtta / 3600),
                             "warning" if mtta > 3600 else "ok", None))

        # Action required
        action_required = []
        for device in data.get("top_problematic", []):
            device_uptime = device.get("uptime")
            if device_uptime is not None and float(device_uptime) == 0.0:
                action_required.append({
                    "severity": "critical",
                    "device": normalize_device_display(device.get("name"), device.get("ip")),
                    "ip": device.get("ip", ""),
                    "text": f"Persistently Offline \u2014 0% uptime over full period",
                    "since": "full period",
                })

        # Intro
        intro = (
            f"Fleet of {total} devices at {fmt_pct(uptime)} availability. "
            f"{critical} critical ({critical * 100 // max(total, 1)}%)."
        )
        if prev_uptime is not None and uptime is not None:
            delta = uptime - prev_uptime
            direction = "\u2191" if delta > 0 else "\u2193" if delta < 0 else "\u2192"
            intro += f" Trend: {direction} {abs(delta):.1f}pp vs previous period."

        # Top findings
        findings = []
        if uptime is not None and uptime < 95:
            findings.append(_finding(
                "critical" if uptime < 90 else "warning",
                f"Fleet availability {fmt_pct(uptime)} is below 95% threshold",
                f"{critical} of {total} devices in critical state",
            ))
        if latency is not None and latency > 200:
            findings.append(_finding(
                "critical",
                f"Average latency {fmt_ms(latency)} exceeds 200ms threshold",
                "Remote desktop and interactive sessions will be sluggish",
            ))
        elif latency is not None and latency > 100:
            findings.append(_finding(
                "warning",
                f"Average latency {fmt_ms(latency)} exceeds 100ms warning threshold",
            ))
        if mtta is not None and mtta > 3600:
            findings.append(_finding(
                "warning",
                f"Mean Time to Acknowledge is {format_duration(mtta / 3600)} \u2014 exceeds 1h SLA",
                "Critical alerts sit unattended for too long",
            ))
        if not findings:
            findings.append(_finding("ok", "All monitored metrics within acceptable thresholds"))

        # Risk summary (plain English for management)
        risk_parts = []
        if uptime is not None and uptime < 95:
            risk_parts.append(
                f"The network fleet is operating at {fmt_pct(uptime)} availability, "
                f"well below the 95% acceptable threshold."
            )
        zero_uptime_count = sum(
            1 for d in data.get("top_problematic", [])
            if d.get("uptime") is not None and float(d.get("uptime")) == 0.0
        )
        if zero_uptime_count:
            risk_parts.append(
                f"{zero_uptime_count} device(s) have been offline for the full reporting period "
                f"and should be investigated or decommissioned."
            )
        risk_summary = " ".join(risk_parts) if risk_parts else None

        # Action items
        action_items = []
        if zero_uptime_count:
            action_items.append(f"Investigate {zero_uptime_count} persistently offline device(s) \u2014 decommission review or physical inspection")
        if critical > healthy:
            action_items.append("Critical devices outnumber healthy \u2014 prioritize triage")
        if mtta and mtta > 3600:
            action_items.append("Configure notification channels to reduce alert acknowledgment time")
        if not action_items:
            action_items.append("Continue monitoring \u2014 fleet health is within acceptable parameters")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": action_required,
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": _interpret_executive(uptime, latency, critical, total),
            "action_items": action_items,
            "risk_summary": risk_summary,
        }

    # ── Server Fleet ─────────────────────────────────────────────────────

    def _narrate_server_fleet(self, data: dict) -> dict:
        """Master Spec Template D: Server/infrastructure fleet."""
        rows = data.get("server_rows", [])
        summary = data.get("summary", {})
        sla_dist = summary.get("sla_distribution", {})

        if not rows:
            return _zero_data_narrative()

        n = len(rows)
        gold_count = sla_dist.get("Gold", 0)
        critical_count = sla_dist.get("Critical", 0)
        unknown_count = sla_dist.get("Unknown", 0)
        avg_uptime = summary.get("fleet_avg_uptime")

        kpis = [
            _kpi("Infrastructure Devices", str(n), "ok", None),
            _kpi("Fleet Avg Uptime", fmt_pct(avg_uptime), _status_for_uptime(avg_uptime), None),
            _kpi("Gold SLA", str(gold_count), "ok" if gold_count else "warning", None),
            _kpi("Critical SLA", str(critical_count), "critical" if critical_count else "ok", None),
            _kpi("Unknown/No Data", str(unknown_count), "warning" if unknown_count else "ok", None),
        ]

        # Action required: 0% uptime devices
        action_required = []
        for r in rows:
            if r.get("uptime_pct") is not None and float(r.get("uptime_pct")) == 0.0:
                action_required.append({
                    "severity": "critical",
                    "device": normalize_device_display(r.get("device_name"), r.get("device_ip")),
                    "ip": r.get("device_ip", ""),
                    "text": "Persistently Offline \u2014 decommission review or physical inspection",
                    "since": "full period",
                })

        # Intro
        gold_pct = gold_count * 100 // max(n, 1)
        critical_pct = critical_count * 100 // max(n, 1)
        intro = f"{n} infrastructure devices. {gold_pct}% Gold SLA. {critical_pct}% Critical."

        # Findings
        findings = []
        worst = sorted([r for r in rows if r.get("uptime_pct") is not None],
                        key=lambda r: float(r["uptime_pct"]))[:3]
        for w in worst:
            if float(w["uptime_pct"]) < 95:
                findings.append(_finding(
                    "critical" if float(w["uptime_pct"]) < 90 else "warning",
                    f"{normalize_device_display(w.get('device_name'), w.get('device_ip'))} "
                    f"at {fmt_pct(w['uptime_pct'])} uptime",
                ))

        thresholds = self._load_thresholds()
        cpu_warn = thresholds.get("cpu_usage_pct", {}).get("warning", 80)
        for r in rows:
            cpu = r.get("avg_cpu")
            if cpu is not None and float(cpu) > cpu_warn:
                findings.append(_finding(
                    "warning",
                    f"{normalize_device_display(r.get('device_name'), r.get('device_ip'))} "
                    f"avg CPU {fmt_pct(cpu)} exceeds {cpu_warn}% warning threshold",
                ))
                break  # Only worst
        findings = findings[:5]

        if not findings:
            findings.append(_finding("ok", "All infrastructure devices within SLA thresholds"))

        # Interpretation
        interpretation_parts = []
        if avg_uptime is not None and avg_uptime < 95:
            interpretation_parts.append(
                f"Fleet average uptime {fmt_pct(avg_uptime)} is below the 95% acceptable threshold."
            )
        if critical_count > n * 0.5:
            interpretation_parts.append(
                "Majority of fleet is in Critical SLA tier \u2014 indicates systemic issues."
            )
        avg_disk = summary.get("fleet_avg_disk")
        if avg_disk is not None and float(avg_disk) > 80:
            interpretation_parts.append(
                f"Fleet average disk usage {fmt_pct(avg_disk)} \u2014 headroom is shrinking."
            )
        interpretation = " ".join(interpretation_parts) if interpretation_parts else "Fleet metrics within expected ranges."

        action_items = []
        if action_required:
            action_items.append(f"Investigate {len(action_required)} persistently offline device(s)")
        if critical_count:
            action_items.append(f"Triage {critical_count} devices in Critical SLA tier")
        if unknown_count:
            action_items.append(f"Review {unknown_count} devices with no monitoring data")
        if not action_items:
            action_items.append("Continue monitoring \u2014 fleet health is acceptable")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": action_required,
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }

    # ── Tracked/Workstation Fleet ────────────────────────────────────────

    def _narrate_tracked_fleet(self, data: dict) -> dict:
        """Workstation/employee fleet narrative."""
        rows = data.get("tracked_rows", [])
        if not rows:
            return _zero_data_narrative()

        n = len(rows)
        uptimes = [float(r["uptime_pct"]) for r in rows if r.get("uptime_pct") is not None]
        avg_uptime = sum(uptimes) / len(uptimes) if uptimes else None
        mttrs = [float(r["mttr_min"]) for r in rows if r.get("mttr_min") is not None]
        avg_mttr = sum(mttrs) / len(mttrs) if mttrs else None
        total_incidents = sum(int(r.get("incident_count") or 0) for r in rows)

        kpis = [
            _kpi("Employee Devices", str(n), "ok", None),
            _kpi("Avg Uptime", fmt_pct(avg_uptime), _status_for_uptime(avg_uptime), None),
            _kpi("Avg MTTR", f"{fmt(avg_mttr, '.0f')} min" if avg_mttr else "N/A",
                 "warning" if avg_mttr and avg_mttr > 60 else "ok", None),
            _kpi("Total Incidents", str(total_incidents), "warning" if total_incidents > 50 else "ok", None),
        ]

        action_required = []
        for r in rows:
            score = r.get("flapping_score")
            if score is not None and float(score) > 0.5:
                action_required.append({
                    "severity": "warning",
                    "device": normalize_device_display(r.get("device_name"), r.get("device_ip")),
                    "ip": r.get("device_ip", ""),
                    "text": f"High flapping ({float(score):.0%} of incidents are noise)",
                    "since": "",
                })

        intro = (
            f"{n} employee devices tracked. "
            f"Average uptime {fmt_pct(avg_uptime)}, MTTR {fmt(avg_mttr, '.0f', 'N/A')} min."
        )

        findings = []
        worst_mttr = sorted([r for r in rows if r.get("mttr_min") is not None],
                             key=lambda r: -float(r["mttr_min"]))[:2]
        for w in worst_mttr:
            if float(w["mttr_min"]) > 60:
                findings.append(_finding(
                    "warning",
                    f"{normalize_device_display(w.get('device_name'), w.get('device_ip'))} "
                    f"MTTR {fmt(w['mttr_min'], '.0f')} min \u2014 slow recovery",
                ))

        most_incidents = sorted(rows, key=lambda r: -int(r.get("incident_count") or 0))[:2]
        for m in most_incidents:
            count = int(m.get("incident_count") or 0)
            if count > 10:
                findings.append(_finding(
                    "warning",
                    f"{normalize_device_display(m.get('device_name'), m.get('device_ip'))} "
                    f"had {count} incidents",
                ))
        findings = findings[:5]
        if not findings:
            findings.append(_finding("ok", "Employee devices operating within normal parameters"))

        interpretation_parts = []
        if avg_mttr and avg_mttr > 60:
            interpretation_parts.append("High MTTR suggests devices may be unattended or out of office.")
        if total_incidents > n * 5:
            interpretation_parts.append("Frequent connectivity drops suggest unstable Wi-Fi or VPN.")
        interpretation = " ".join(interpretation_parts) if interpretation_parts else "Employee device fleet is stable."

        action_items = []
        if avg_mttr and avg_mttr > 60:
            action_items.append("Review network stability for devices with high MTTR")
        if total_incidents > n * 5:
            action_items.append("Investigate Wi-Fi/VPN reliability for high-incident devices")
        if not action_items:
            action_items.append("Continue monitoring \u2014 fleet is within normal parameters")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": action_required,
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }

    # ── Alerts ───────────────────────────────────────────────────────────

    def _narrate_alerts(self, data: dict) -> dict:
        """Master Spec Template B: Alert History."""
        sev = data.get("severity_breakdown", {})
        total = sum(sev.values()) if sev else 0
        crit = sev.get("CRITICAL", 0)
        warn = sev.get("WARNING", 0)
        tta_data = data.get("tta", {})
        tta_sec = tta_data.get("seconds")
        top_devices = data.get("top_alerted_devices", [])

        if total == 0:
            return _zero_alert_narrative()

        kpis = [
            _kpi("Total Alerts", str(total), "warning" if total > 100 else "ok", None),
            _kpi("Critical", str(crit), "critical" if crit > 0 else "ok", None),
            _kpi("Warning", str(warn), "warning" if warn > 0 else "ok", None),
            _kpi("Avg TTA", format_duration(tta_sec / 3600) if tta_sec else "N/A",
                 "warning" if tta_sec and tta_sec > 3600 else "ok", None),
        ]

        action_required = []
        # Unresolved criticals from alerts list
        for alert in data.get("alerts", [])[:20]:
            if (alert.get("severity") or "").upper() == "CRITICAL" and not alert.get("resolved"):
                action_required.append({
                    "severity": "critical",
                    "device": alert.get("device_name") or alert.get("device_ip", ""),
                    "ip": alert.get("device_ip", ""),
                    "text": alert.get("message", "Unresolved critical alert"),
                    "since": format_timestamp_utc(alert.get("timestamp")),
                })
        action_required = action_required[:5]

        intro = (
            f"{total} alerts in this period. {crit} critical, {warn} warning. "
            f"Avg acknowledgment: {format_duration(tta_sec / 3600) if tta_sec else 'N/A'}."
        )

        findings = []
        if crit > 0:
            findings.append(_finding("critical", f"{crit} critical alerts recorded"))
        if top_devices:
            top = top_devices[0]
            findings.append(_finding(
                "warning",
                f"Most impacted device: {top.get('device_name', 'Unknown')} "
                f"with {top.get('alert_count', 0)} alerts",
            ))
        if tta_sec and tta_sec > 3600:
            findings.append(_finding(
                "warning",
                f"Alert acknowledgment SLA exceeded \u2014 avg TTA is {format_duration(tta_sec / 3600)}",
            ))
        if not findings:
            findings.append(_finding("ok", "Alert volume within normal parameters"))

        # Trend interpretation
        trend = data.get("daily_trend", {})
        if trend:
            daily_totals = [sum(day_sev.values()) for day_sev in trend.values()]
            if len(daily_totals) >= 3:
                first_half = sum(daily_totals[:len(daily_totals)//2])
                second_half = sum(daily_totals[len(daily_totals)//2:])
                if second_half > first_half * 1.3:
                    trend_text = "Alert volume is increasing \u2014 investigate root causes."
                elif first_half > second_half * 1.3:
                    trend_text = "Alert volume is decreasing \u2014 remediation efforts are working."
                else:
                    trend_text = "Alert volume is stable over the period."
            else:
                trend_text = "Insufficient trend data for analysis."
        else:
            trend_text = ""

        interpretation = trend_text
        if tta_sec and tta_sec > 3600:
            interpretation += (
                f" Average {format_duration(tta_sec / 3600)} to acknowledge means "
                f"critical alerts sit unattended for too long."
            )

        action_items = []
        if crit > 0:
            action_items.append(f"Investigate {crit} critical alerts")
        if top_devices and int(top_devices[0].get("alert_count", 0)) > 20:
            action_items.append(
                f"Review alerting configuration for {top_devices[0].get('device_name', 'top device')} "
                f"({top_devices[0].get('alert_count', 0)} alerts)"
            )
        if tta_sec and tta_sec > 3600:
            action_items.append("Configure notification channels to reduce TTA below 1 hour")
        if not action_items:
            action_items.append("Continue monitoring \u2014 alert levels are acceptable")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": action_required,
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }

    # ── Security/Compliance ──────────────────────────────────────────────

    def _narrate_security(self, data: dict) -> dict:
        """Master Spec Template A: Security Compliance."""
        summary = data.get("summary", {})
        total = summary.get("total_alerts", 0)
        crit = summary.get("critical_alerts", 0)
        unresolved = summary.get("unresolved_alerts", 0)
        acked = summary.get("acknowledged_alerts", 0)
        violations = summary.get("restricted_site_violations", 0)
        integrity = summary.get("integrity_findings", 0)
        breaches = summary.get("threshold_breaches", 0)

        if total == 0 and violations == 0:
            return _zero_data_narrative()

        kpis = [
            _kpi("Total Alerts", str(total), "warning" if total > 50 else "ok", None),
            _kpi("Critical", str(crit), "critical" if crit > 0 else "ok", None),
            _kpi("Unresolved", str(unresolved), "critical" if unresolved > 0 else "ok", None),
            _kpi("Acknowledged", str(acked), "warning" if acked == 0 and unresolved > 0 else "ok", None),
            _kpi("Policy Violations", str(violations), "critical" if violations > 0 else "ok", None),
            _kpi("Integrity Findings", str(integrity), "critical" if integrity > 0 else "ok", None),
            _kpi("Threshold Breaches", str(breaches), "warning" if breaches > 0 else "ok", None),
        ]

        action_required = []
        for alert in data.get("recent_alerts", [])[:10]:
            if (alert.get("severity") or "").upper() == "CRITICAL" and not alert.get("resolved"):
                action_required.append({
                    "severity": "critical",
                    "device": alert.get("device_name") or alert.get("device_ip", ""),
                    "ip": alert.get("device_ip", ""),
                    "text": alert.get("message", "Unresolved critical security event"),
                    "since": format_timestamp_utc(alert.get("timestamp")),
                })
        action_required = action_required[:5]

        intro = (
            f"{total} security events in this period. "
            f"{crit} critical, {unresolved} unresolved. "
            f"{violations} policy violations."
        )

        findings = []
        if unresolved > 0:
            findings.append(_finding("critical", f"{unresolved} unresolved security alerts"))
        if violations > 0:
            findings.append(_finding("warning", f"{violations} restricted site policy violations detected"))

        # AI violation analysis
        site_violations = data.get("restricted_site_violations", [])
        ai_count = 0
        total_violation_count = 0
        for v in site_violations:
            count = int(v.get("count") or 0)
            total_violation_count += count
            domain = (v.get("domain") or "").lower()
            if classify_violation_risk(domain) == "HIGH":
                ai_count += count
        if ai_count > 0 and total_violation_count > 0:
            pct = ai_count * 100 // total_violation_count
            findings.append(_finding(
                "critical",
                f"AI service access represents {pct}% of {total_violation_count} violations \u2014 "
                f"potential data exfiltration risk",
            ))

        if breaches > 0:
            findings.append(_finding("warning", f"{breaches} active threshold breaches"))
        findings = findings[:5]
        if not findings:
            findings.append(_finding("ok", "No security issues detected"))

        interpretation = ""
        if ai_count > 0:
            interpretation = (
                f"AI service access ({ai_count} visits) represents a HIGH risk finding. "
                f"Sensitive company data may have been shared with external AI services. "
            )
        if unresolved > 0:
            interpretation += f"{unresolved} alerts remain unresolved and require attention."

        action_items = []
        if ai_count > 0:
            action_items.append("Investigate AI service session content, enforce DNS-level blocking, initiate policy discussion")
        if unresolved > 0:
            action_items.append(f"Acknowledge and resolve {unresolved} open alerts")
        if breaches > 0:
            action_items.append(f"Investigate {breaches} active threshold breaches")
        if not action_items:
            action_items.append("Continue monitoring \u2014 security posture is acceptable")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": action_required,
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }

    # ── Operational ──────────────────────────────────────────────────────

    def _narrate_operational(self, data: dict) -> dict:
        new_devices = data.get("new_devices", [])
        audit_log = data.get("audit_log", [])
        heatmap = data.get("heatmap", [])

        new_count = len(new_devices)
        audit_count = len(audit_log)

        kpis = [
            _kpi("New Devices", str(new_count), "warning" if new_count > 5 else "ok", None),
            _kpi("System Events", str(audit_count), "ok", None),
        ]

        action_required = []
        unknown_new = [d for d in new_devices if (d.get("device_type") or "").lower() in ("unknown", "")]
        if unknown_new:
            for d in unknown_new[:3]:
                action_required.append({
                    "severity": "warning",
                    "device": normalize_device_display(d.get("device_name"), d.get("device_ip")),
                    "ip": d.get("device_ip", ""),
                    "text": "UNKNOWN DEVICE \u2014 verify authorized asset",
                    "since": format_timestamp_utc(d.get("created_at")),
                })

        intro = f"{new_count} new devices discovered. {audit_count} system events logged."

        findings = []
        if unknown_new:
            findings.append(_finding(
                "warning",
                f"{len(unknown_new)} new device(s) of type 'unknown' \u2014 potential rogue devices",
            ))

        # Peak activity from heatmap
        if heatmap:
            peak = max(heatmap, key=lambda h: h[2] if len(h) >= 3 else 0)
            if len(peak) >= 3:
                findings.append(_finding(
                    "info",
                    f"Peak activity: day {peak[0]} hour {peak[1]:02d}:00 with {peak[2]} events",
                ))
        findings = findings[:5]
        if not findings:
            findings.append(_finding("ok", "No significant operational events"))

        interpretation = ""
        if heatmap:
            interpretation = "Activity patterns can indicate scheduled jobs or work-hour peaks."

        action_items = []
        if unknown_new:
            action_items.append(f"Register {len(unknown_new)} unknown device(s) in asset inventory or investigate")
        if not action_items:
            action_items.append("No operational action required")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": action_required,
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }

    # ── Device Health ────────────────────────────────────────────────────

    def _narrate_device_health(self, data: dict) -> dict:
        """Master Spec Template C: Device Health."""
        summary_list = data.get("summary", [])
        granularity = data.get("granularity", "unknown")
        data_note = data.get("data_note")
        total_samples = data.get("total_samples", 0)

        if not summary_list or data_note == "no_data":
            return _zero_data_narrative()

        n = len(summary_list) if isinstance(summary_list, list) else 0

        kpis = [
            _kpi("Devices with Telemetry", str(n), "ok", None),
            _kpi("Data Source", granularity, "ok", None),
            _kpi("Total Samples", str(total_samples), "warning" if data_note == "sparse" else "ok", None),
        ]

        intro = f"{n} devices with health telemetry. Data source: {granularity} rollups."
        if data_note == "sparse":
            intro += " Warning: data is sparse \u2014 trends may be approximate."

        findings = []
        thresholds = self._load_thresholds()
        cpu_crit = thresholds.get("cpu_usage_pct", {}).get("critical", 90)
        mem_crit = thresholds.get("memory_usage_pct", {}).get("critical", 95)

        if isinstance(summary_list, list):
            for s in summary_list:
                max_cpu = s.get("max_cpu")
                if max_cpu is not None and float(max_cpu) > 50:
                    findings.append(_finding(
                        "critical" if float(max_cpu) > cpu_crit else "warning",
                        f"{s.get('device_name', 'Unknown')} \u2014 CPU spiked to {fmt_pct(max_cpu)}",
                        "Investigate for batch jobs, backup processes, or intrusion",
                    ))
                max_mem = s.get("max_mem")
                if max_mem is not None and float(max_mem) > mem_crit:
                    findings.append(_finding(
                        "critical",
                        f"{s.get('device_name', 'Unknown')} \u2014 memory peaked at {fmt_pct(max_mem)}",
                        "Consider upgrading RAM or identifying memory-intensive processes",
                    ))
        findings = findings[:5]
        if not findings:
            findings.append(_finding("ok", "All resource metrics within thresholds"))

        interpretation = ""
        if data_note == "sparse":
            interpretation = "Sparse telemetry data \u2014 trends are approximate. Consider increasing agent reporting frequency."

        action_items = []
        for f in findings:
            if f["severity"] in ("critical", "warning") and f.get("detail"):
                action_items.append(f["detail"])
        if not action_items:
            action_items.append("Continue monitoring \u2014 resource usage is within limits")

        return {
            "executive_banner": {"kpis": kpis},
            "action_required": [],
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }

    # ── Device Inspector ─────────────────────────────────────────────────

    def _narrate_device_inspector(self, data: dict) -> dict:
        """Per-device deep inspection narrative."""
        device_name = data.get("device_name") or data.get("name") or "Unknown"
        device_type = data.get("device_type") or "unknown"
        device_ip = data.get("device_ip") or data.get("ip") or ""
        status = data.get("status") or data.get("availability_status") or "unknown"

        display_name = normalize_device_display(device_name, device_ip)
        intro = f"{display_name} ({device_type}) at {device_ip}. Status: {status}."

        findings = []
        thresholds = self._load_thresholds()

        # Uptime
        uptime = data.get("uptime_pct") or data.get("availability_pct")
        if uptime is not None:
            uptime_f = float(uptime)
            if uptime_f < 90:
                findings.append(_finding("critical", f"Availability {fmt_pct(uptime)} \u2014 critically low"))
            elif uptime_f < 95:
                findings.append(_finding("warning", f"Availability {fmt_pct(uptime)} \u2014 below SLA threshold"))

        # Latency
        latency = data.get("avg_latency_ms") or data.get("latency")
        if latency is not None:
            lat_f = float(latency)
            if lat_f > 200:
                findings.append(_finding(
                    "critical",
                    f"Latency {fmt_ms(latency)} \u2014 remote desktop and VoIP will be severely impacted",
                ))
            elif lat_f > 100:
                findings.append(_finding("warning", f"Latency {fmt_ms(latency)} \u2014 interactive sessions may be sluggish"))

        # Packet loss
        pkt_loss = data.get("avg_packet_loss_pct") or data.get("packet_loss")
        if pkt_loss is not None:
            pkt_f = float(pkt_loss)
            if pkt_f > 50:
                findings.append(_finding(
                    "critical",
                    f"Packet loss {fmt_pct(pkt_loss)} \u2014 roughly half of all requests fail. "
                    f"Device is effectively unusable for interactive work. VoIP calls will be unintelligible. "
                    f"File transfers will be extremely slow.",
                ))
            elif pkt_f > 15:
                findings.append(_finding("critical", f"Packet loss {fmt_pct(pkt_loss)} \u2014 significant data loss"))
            elif pkt_f > 5:
                findings.append(_finding("warning", f"Packet loss {fmt_pct(pkt_loss)} \u2014 above acceptable threshold"))

        findings = findings[:5]
        if not findings:
            findings.append(_finding("ok", f"{display_name} is operating within normal parameters"))

        interpretation = ""
        if pkt_loss is not None and float(pkt_loss) > 50:
            interpretation = (
                f"{fmt_pct(pkt_loss)} packet loss means approximately half of all network requests fail. "
                f"This makes the device unusable for interactive work, VoIP, or file transfers. "
                f"Check NIC driver/firmware, cable integrity, and switch port."
            )
        elif latency is not None and float(latency) > 200:
            interpretation = (
                f"{fmt_ms(latency)} latency means noticeable delay on every interaction. "
                f"Remote desktop sessions will lag. Check network path and congestion."
            )

        action_items = []
        if pkt_loss and float(pkt_loss) > 15:
            action_items.append("Check NIC driver/firmware, cable integrity, and switch port")
        if latency and float(latency) > 200:
            action_items.append("Check network path and congestion for this device")
        if uptime is not None and float(uptime) < 90:
            action_items.append("Investigate cause of frequent outages")
        if not action_items:
            action_items.append("No immediate action required")

        return {
            "executive_banner": {"kpis": []},
            "action_required": [],
            "section_intro": intro,
            "top_findings": findings,
            "interpretation": interpretation,
            "action_items": action_items,
            "risk_summary": None,
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_insight_engine():
    """Lazy import to avoid circular dependency."""
    from services.report_insight_engine import ReportInsightEngine
    return ReportInsightEngine()


def _zero_data_narrative() -> dict:
    return {
        "executive_banner": {"kpis": []},
        "action_required": [],
        "section_intro": "No monitoring data available for this period.",
        "top_findings": [],
        "interpretation": None,
        "action_items": [],
        "risk_summary": None,
    }


def _zero_alert_narrative() -> dict:
    return {
        "executive_banner": {"kpis": []},
        "action_required": [],
        "section_intro": "No alerts recorded in this period.",
        "top_findings": [_finding("ok", "No alerts \u2014 all systems operating normally")],
        "interpretation": "Zero alert volume indicates either healthy systems or incomplete monitoring coverage.",
        "action_items": ["Verify alert pipeline is functioning correctly"],
        "risk_summary": None,
    }


def _kpi(label: str, value: str, status: str, delta: Optional[str]) -> dict:
    return {"label": label, "value": value, "status": status, "delta": delta}


def _finding(severity: str, text: str, detail: Optional[str] = None) -> dict:
    return {"severity": severity, "text": text, "detail": detail}


def _delta(current, previous) -> Optional[str]:
    """Compute period-over-period delta text."""
    if current is None or previous is None:
        return None
    try:
        d = float(current) - float(previous)
        direction = "\u2191" if d > 0 else "\u2193" if d < 0 else "\u2192"
        return f"{direction} {abs(d):.1f}pp"
    except (TypeError, ValueError):
        return None


def _status_for_uptime(uptime) -> str:
    if uptime is None:
        return "warning"
    try:
        u = float(uptime)
        if u < 90:
            return "critical"
        if u < 95:
            return "warning"
        return "ok"
    except (TypeError, ValueError):
        return "warning"


def _interpret_executive(uptime, latency, critical_count, total) -> str:
    parts = []
    if uptime is not None:
        u = float(uptime)
        if u >= 99:
            parts.append("Fleet availability is excellent.")
        elif u >= 95:
            parts.append("Fleet availability is acceptable but could be improved.")
        else:
            parts.append("Fleet availability is below the 95% minimum threshold and requires attention.")
    if latency is not None:
        l = float(latency)
        if l > 200:
            parts.append(f"Average latency of {fmt_ms(latency)} means remote desktop sessions will lag.")
        elif l > 100:
            parts.append(f"Average latency of {fmt_ms(latency)} is elevated but acceptable for most workloads.")
    if total and critical_count > total * 0.3:
        parts.append(f"{critical_count} of {total} devices ({critical_count * 100 // total}%) are in critical state.")
    return " ".join(parts) if parts else "Fleet metrics are within normal parameters."
