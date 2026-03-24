"""
Report Intelligence Rules — auto-annotation engine.

Fires on every report generation. Each rule examines the report payload
and produces structured annotations. Rules are deterministic (no AI).

Rules implemented (from Master Report Specification):
1. Subnet-wide impact — >60% of /24 with latency/packet loss
2. Memory leak detection — monotonic increase over 3+ windows
3. Admin login anomaly — >3 logins in any 2-hour window
4. AI service violation — chatgpt.com, claude.ai, etc.
5. Long-term offline — >168h offline with no acknowledgment
6. Rogue/unknown device — new devices of type "unknown"
7. Zero-uptime flagging — 0.00% uptime = "Persistently Offline"
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.report_formatting import (
    AI_SERVICE_DOMAINS,
    SEVERITY_ORDER,
    format_duration,
    normalize_device_display,
)

logger = logging.getLogger(__name__)


class ReportIntelligenceRules:
    """Auto-annotation engine. Rule-based, no AI, no DB queries.
    Operates on report payloads already fetched by the report service."""

    def annotate(self, report_type: str, report_data: dict) -> List[dict]:
        """Run all rules against the report payload.
        Returns sorted list of annotations (critical first)."""
        annotations = []
        for rule_fn in self._rules:
            try:
                results = rule_fn(self, report_data)
                if results:
                    if isinstance(results, dict):
                        annotations.append(results)
                    elif isinstance(results, list):
                        annotations.extend(results)
            except Exception as exc:
                logger.warning("[IntelligenceRules] %s failed: %s", rule_fn.__name__, exc)
        annotations.sort(key=lambda a: SEVERITY_ORDER.get(a.get("severity", "info"), 99))
        return annotations

    # ── Rule 1: Subnet-wide impact ───────────────────────────────────────

    def _rule_subnet_impact(self, data: dict) -> List[dict]:
        """Flag subnets where >60% of devices show latency or packet loss."""
        results = []
        # Check server_rows from enterprise reports
        rows = data.get("server_rows", [])
        if not rows:
            # Also check top_problematic from executive reports
            rows = data.get("top_problematic", [])
        if not rows:
            return results

        subnet_devices: Dict[str, List[dict]] = defaultdict(list)
        for row in rows:
            ip = row.get("device_ip") or row.get("ip") or ""
            if not ip or ip.count(".") != 3:
                continue
            subnet = ".".join(ip.split(".")[:3]) + ".0/24"
            subnet_devices[subnet].append(row)

        for subnet, devices in subnet_devices.items():
            if len(devices) < 3:
                continue  # Skip tiny subnets
            impacted = 0
            for d in devices:
                latency = d.get("avg_latency_ms") or d.get("latency")
                pkt_loss = d.get("avg_packet_loss_pct") or d.get("packet_loss")
                if (latency is not None and float(latency) > 400) or \
                   (pkt_loss is not None and float(pkt_loss) > 50):
                    impacted += 1
            ratio = impacted / len(devices)
            if ratio > 0.60:
                results.append({
                    "rule": "subnet_impact",
                    "severity": "warning",
                    "text": f"Possible upstream switch/router issue on {subnet} \u2014 "
                            f"{impacted}/{len(devices)} devices ({ratio:.0%}) showing high latency or packet loss",
                    "devices": [d.get("device_name") or d.get("name") for d in devices],
                    "action": f"Investigate the gateway and uplink for {subnet}",
                })
        return results

    # ── Rule 2: Memory leak detection ────────────────────────────────────

    def _rule_memory_leak(self, data: dict) -> List[dict]:
        """Detect monotonic memory increase over 3+ consecutive windows."""
        results = []
        time_series = data.get("time_series", {})
        if not time_series:
            return results

        for device_id, device_data in time_series.items():
            points = device_data.get("points", [])
            if len(points) < 3:
                continue

            # Extract memory values
            mem_values = []
            for p in points:
                mem = p.get("mem")
                if mem is not None:
                    mem_values.append(float(mem))
                else:
                    mem_values.append(None)

            # Find longest monotonic increasing run
            max_run = 0
            current_run = 1
            run_start_val = None
            run_end_val = None
            for i in range(1, len(mem_values)):
                if mem_values[i] is not None and mem_values[i - 1] is not None:
                    if mem_values[i] > mem_values[i - 1]:
                        current_run += 1
                        if current_run > max_run:
                            max_run = current_run
                            run_end_val = mem_values[i]
                            run_start_val = mem_values[i - current_run + 1]
                    else:
                        current_run = 1
                else:
                    current_run = 1

            if max_run >= 3 and run_start_val is not None and run_end_val is not None:
                rate = (run_end_val - run_start_val) / max(1, max_run - 1)
                device_name = device_data.get("device_name", f"Device {device_id}")
                results.append({
                    "rule": "memory_leak",
                    "severity": "warning",
                    "text": f"Memory trending upward on {device_name} \u2014 "
                            f"possible memory leak. +{rate:.1f}pp per interval over {max_run} consecutive windows",
                    "devices": [device_name],
                    "action": f"Investigate memory-intensive processes on {device_name}",
                })
        return results

    # ── Rule 3: Admin login anomaly ──────────────────────────────────────

    def _rule_admin_login_anomaly(self, data: dict) -> List[dict]:
        """Flag >3 admin logins in any 2-hour sliding window."""
        results = []
        audit_log = data.get("recent_audit_log") or data.get("audit_log") or []
        if not audit_log:
            return results

        # Filter login events
        login_events = []
        for entry in audit_log:
            action = (entry.get("action") or "").lower()
            if "login" in action:
                ts_raw = entry.get("timestamp") or entry.get("created_at")
                if ts_raw:
                    try:
                        if isinstance(ts_raw, str):
                            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        else:
                            ts = ts_raw
                        login_events.append({
                            "timestamp": ts,
                            "user": entry.get("user") or entry.get("username") or "unknown",
                        })
                    except (ValueError, TypeError):
                        continue

        if len(login_events) < 4:
            return results

        # Sort by timestamp
        login_events.sort(key=lambda e: e["timestamp"])

        # Sliding window: check if any 2-hour window has >3 logins
        window = timedelta(hours=2)
        for i in range(len(login_events)):
            window_end = login_events[i]["timestamp"] + window
            count = sum(1 for e in login_events[i:] if e["timestamp"] <= window_end)
            if count > 3:
                users_in_window = list({e["user"] for e in login_events[i:] if e["timestamp"] <= window_end})
                results.append({
                    "rule": "admin_login_anomaly",
                    "severity": "warning",
                    "text": f"Unusual admin login frequency \u2014 {count} logins in 2-hour window "
                            f"starting {login_events[i]['timestamp'].strftime('%Y-%m-%d %H:%M UTC')}. "
                            f"Users: {', '.join(users_in_window)}",
                    "devices": [],
                    "action": "Verify legitimacy and check for credential compromise",
                })
                break  # Only report the worst window
        return results

    # ── Rule 4: AI service violation ─────────────────────────────────────

    def _rule_ai_service_violation(self, data: dict) -> List[dict]:
        """Flag access to AI service domains as HIGH risk."""
        results = []
        violations = data.get("restricted_site_violations", [])
        if not violations:
            # Also check violations section in enterprise report
            violations_section = data.get("violations", {})
            violations = violations_section.get("restricted_site_events", [])
        if not violations:
            return results

        ai_violations = []
        total_count = 0
        ai_count = 0
        for v in violations:
            domain = (v.get("domain") or "").lower().strip()
            count = int(v.get("count") or v.get("violation_count") or 1)
            total_count += count
            if domain in AI_SERVICE_DOMAINS or any(ai in domain for ai in AI_SERVICE_DOMAINS):
                ai_violations.append(v)
                ai_count += count

        if ai_violations:
            pct = (ai_count / total_count * 100) if total_count else 0
            devices = list({v.get("device_name") or "Unknown" for v in ai_violations})
            results.append({
                "rule": "ai_service_violation",
                "severity": "critical",
                "text": f"AI SERVICE ACCESS \u2014 potential data exfiltration risk. "
                        f"{ai_count} visits to AI services ({pct:.0f}% of {total_count} total violations). "
                        f"Devices: {', '.join(devices[:5])}",
                "devices": devices,
                "action": "Investigate session content, enforce DNS-level blocking, "
                          "and initiate a policy discussion with affected users",
            })
        return results

    # ── Rule 5: Long-term offline ────────────────────────────────────────

    def _rule_long_term_offline(self, data: dict) -> List[dict]:
        """Flag devices offline >168h (7 days) with no acknowledgment."""
        results = []
        rows = data.get("server_rows", []) + data.get("tracked_rows", [])
        if not rows:
            return results

        for row in rows:
            downtime_h = row.get("downtime_hours")
            if downtime_h is None:
                continue
            try:
                downtime_h = float(downtime_h)
            except (TypeError, ValueError):
                continue
            if downtime_h < 168:
                continue
            # Check if acknowledged (if field exists)
            if row.get("is_acknowledged"):
                continue
            device_name = normalize_device_display(
                row.get("device_name"), row.get("device_ip")
            )
            results.append({
                "rule": "long_term_offline",
                "severity": "critical",
                "text": f"ESCALATION REQUIRED \u2014 {device_name} offline for "
                        f"{format_duration(downtime_h)} with no acknowledgment. "
                        f"Assign to on-call engineer.",
                "devices": [device_name],
                "action": f"Verify physical connectivity for {device_name} or decommission if retired",
            })
        return results[:10]  # Cap at 10 to avoid flooding

    # ── Rule 6: Rogue/unknown device ─────────────────────────────────────

    def _rule_rogue_device(self, data: dict) -> List[dict]:
        """Flag new devices of type 'unknown'."""
        results = []
        new_devices = data.get("new_devices", [])
        if not new_devices:
            return results

        unknown_devices = []
        for d in new_devices:
            dtype = (d.get("device_type") or "").lower()
            if dtype in ("unknown", ""):
                name = d.get("device_name") or d.get("name") or "unnamed"
                ip = d.get("device_ip") or d.get("ip") or ""
                unknown_devices.append(normalize_device_display(name, ip))

        if unknown_devices:
            results.append({
                "rule": "rogue_device",
                "severity": "warning",
                "text": f"UNKNOWN DEVICE{'S' if len(unknown_devices) > 1 else ''} \u2014 "
                        f"{len(unknown_devices)} new device(s) of type 'unknown': "
                        f"{', '.join(unknown_devices[:5])}. "
                        f"Verify {'these are' if len(unknown_devices) > 1 else 'this is'} "
                        f"an authorized asset.",
                "devices": unknown_devices,
                "action": "Register in asset inventory or investigate as potential rogue device",
            })
        return results

    # ── Rule 7: Zero-uptime (Persistently Offline) ───────────────────────

    def _rule_zero_uptime(self, data: dict) -> List[dict]:
        """Flag devices with 0.00% uptime as 'Persistently Offline'."""
        results = []
        rows = data.get("server_rows", []) + data.get("tracked_rows", [])
        if not rows:
            return results

        zero_devices = []
        for row in rows:
            uptime = row.get("uptime_pct")
            if uptime is not None:
                try:
                    if float(uptime) == 0.0:
                        name = normalize_device_display(
                            row.get("device_name"), row.get("device_ip")
                        )
                        zero_devices.append(name)
                except (TypeError, ValueError):
                    continue

        if zero_devices:
            results.append({
                "rule": "zero_uptime",
                "severity": "critical",
                "text": f"Persistently Offline \u2014 {len(zero_devices)} device(s) with 0.00% uptime "
                        f"over the full reporting period: {', '.join(zero_devices[:10])}. "
                        f"These are distinct from degraded devices \u2014 they have been completely unreachable.",
                "devices": zero_devices,
                "action": "Investigate physical connectivity, verify power status, "
                          "or decommission if retired. Assign owner for each device.",
            })
        return results

    # ── Rule registry ────────────────────────────────────────────────────

    _rules = [
        _rule_zero_uptime,
        _rule_long_term_offline,
        _rule_ai_service_violation,
        _rule_subnet_impact,
        _rule_memory_leak,
        _rule_admin_login_anomaly,
        _rule_rogue_device,
    ]
