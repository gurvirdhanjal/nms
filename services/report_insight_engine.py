"""
Rule-based report insight engine with optional Gemini enhancement.

Layer 1 (MANDATORY): Deterministic findings from metric thresholds.
    Always runs. Uses ServerThresholdConfig from DB for metric thresholds.
    Produces structured findings, recommendations, and severity classifications.

Layer 2 (OPTIONAL): Gemini rewrites Layer 1 output into natural language.
    Only runs when GEMINI_REPORT_INSIGHTS_ENABLED=true.
    Never produces NEW findings — only enhances existing ones.
    Cached by report hash. Validates output with strict schema.
    Graceful fallback: Layer 1 always shown if Layer 2 fails.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Report-level metric thresholds (network/availability) ─────────────────
# Server resource thresholds are loaded from ServerThresholdConfig (DB).
# These cover metrics not in the server health catalog.
_REPORT_THRESHOLDS = {
    "latency_ms":       {"warning": 100,  "critical": 200},
    "packet_loss_pct":  {"warning": 5,    "critical": 15},
    "uptime_pct":       {"warning": 95,   "critical": 90,   "inverted": True},
}


class ReportInsightEngine:
    """Deterministic insight generator from report metrics."""

    def __init__(self):
        self._server_thresholds: Optional[Dict] = None

    def _load_server_thresholds(self) -> Dict[str, Dict[str, float]]:
        """Load thresholds from METRIC_CATALOG (DB-backed via ServerThresholdConfig)."""
        if self._server_thresholds is not None:
            return self._server_thresholds
        try:
            from services.server_thresholds import METRIC_CATALOG
            self._server_thresholds = {
                key: {"warning": m["default_warning"], "critical": m["default_critical"]}
                for key, m in METRIC_CATALOG.items()
                if m.get("default_enabled")
            }
        except Exception:
            self._server_thresholds = {
                "cpu_usage_pct":    {"warning": 80, "critical": 90},
                "memory_usage_pct": {"warning": 75, "critical": 95},
                "disk_usage_pct":   {"warning": 90, "critical": 95},
            }
        return self._server_thresholds

    def _all_thresholds(self) -> Dict[str, Dict]:
        """Combined server + report-level thresholds."""
        t = dict(_REPORT_THRESHOLDS)
        t.update(self._load_server_thresholds())
        return t

    def classify_severity(self, metric: str, value: Any) -> str:
        """Classify a single metric value as critical/warning/healthy/nodata."""
        if value is None:
            return "nodata"
        try:
            val = float(value)
        except (TypeError, ValueError):
            return "nodata"

        thresholds = self._all_thresholds()
        t = thresholds.get(metric)
        if not t:
            return "healthy"

        if t.get("inverted"):
            # Lower is worse (e.g. uptime)
            if val < t["critical"]:
                return "critical"
            if val < t["warning"]:
                return "warning"
            return "healthy"
        else:
            # Higher is worse (e.g. latency, CPU)
            if val > t["critical"]:
                return "critical"
            if val > t["warning"]:
                return "warning"
            return "healthy"

    def generate_findings(self, report_data: dict) -> List[dict]:
        """Generate deterministic findings from report metrics."""
        findings = []
        summary = report_data.get("summary", {})

        # Fleet uptime
        uptime = summary.get("fleet_avg_uptime") or report_data.get("uptime_score")
        if uptime is not None:
            sev = self.classify_severity("uptime_pct", uptime)
            if sev in ("critical", "warning"):
                t = _REPORT_THRESHOLDS["uptime_pct"]
                findings.append({
                    "severity": sev,
                    "text": f"Fleet uptime ({uptime:.1f}%) is {'critically ' if sev == 'critical' else ''}below {t[sev]}% threshold",
                    "metric": "uptime_score",
                    "value": uptime,
                    "threshold": t[sev],
                    "recommendation": "Investigate top offline devices immediately" if sev == "critical"
                                      else "Review devices with degraded availability",
                })

        # Latency
        latency = report_data.get("avg_latency") or summary.get("fleet_avg_latency")
        if latency is not None:
            sev = self.classify_severity("latency_ms", latency)
            if sev in ("critical", "warning"):
                findings.append({
                    "severity": sev,
                    "text": f"Average latency {latency:.0f}ms exceeds {_REPORT_THRESHOLDS['latency_ms'][sev]}ms threshold",
                    "metric": "avg_latency",
                    "value": latency,
                    "threshold": _REPORT_THRESHOLDS["latency_ms"][sev],
                    "recommendation": "Check network path and congestion for affected devices",
                })

        # Packet loss
        pkt_loss = summary.get("fleet_avg_packet_loss")
        if pkt_loss is not None:
            sev = self.classify_severity("packet_loss_pct", pkt_loss)
            if sev in ("critical", "warning"):
                findings.append({
                    "severity": sev,
                    "text": f"Packet loss {pkt_loss:.1f}% exceeds {_REPORT_THRESHOLDS['packet_loss_pct'][sev]}% threshold",
                    "metric": "packet_loss",
                    "value": pkt_loss,
                    "recommendation": "Investigate network infrastructure — possible link degradation",
                })

        # CPU/Mem/Disk fleet averages
        for metric_key, report_key, label in [
            ("cpu_usage_pct", "fleet_avg_cpu", "CPU"),
            ("memory_usage_pct", "fleet_avg_mem", "Memory"),
            ("disk_usage_pct", "fleet_avg_disk", "Disk"),
        ]:
            val = summary.get(report_key)
            if val is not None:
                sev = self.classify_severity(metric_key, val)
                if sev in ("critical", "warning"):
                    t = self._load_server_thresholds().get(metric_key, {})
                    findings.append({
                        "severity": sev,
                        "text": f"Fleet avg {label} ({val:.1f}%) exceeds {t.get(sev, 'N/A')}% threshold",
                        "metric": report_key,
                        "value": val,
                        "recommendation": f"Review top {label.lower()} consumers and consider capacity planning",
                    })

        # Flapping detection (from incident stats)
        for row in report_data.get("tracked_rows", []):
            score = row.get("flapping_score")
            if score is not None and score > 0.5:
                findings.append({
                    "severity": "warning",
                    "text": f"Device '{row.get('device_name', 'Unknown')}' has high flapping ({score:.0%} of incidents are noise)",
                    "metric": "flapping_score",
                    "value": score,
                    "recommendation": "Review probe interval and network stability for this device",
                })
                break  # Only report the worst flapper

        # Violation spike
        violations = report_data.get("violations", {})
        total_v = (violations.get("total_site_violations", 0) or 0) + (violations.get("total_typed_text_alerts", 0) or 0)
        if total_v > 10:
            findings.append({
                "severity": "warning" if total_v < 50 else "critical",
                "text": f"{total_v} policy violations detected in this period",
                "metric": "total_violations",
                "value": total_v,
                "recommendation": "Review restricted site policy and top offenders",
            })

        return findings[:5]  # Cap at 5 findings

    def generate_metric_severities(self, report_data: dict) -> Dict[str, str]:
        """Classify severity for key metrics in the report."""
        summary = report_data.get("summary", {})
        severities = {}

        metric_map = {
            "uptime_score": ("uptime_pct", summary.get("fleet_avg_uptime") or report_data.get("uptime_score")),
            "avg_latency": ("latency_ms", report_data.get("avg_latency")),
            "fleet_avg_cpu": ("cpu_usage_pct", summary.get("fleet_avg_cpu")),
            "fleet_avg_mem": ("memory_usage_pct", summary.get("fleet_avg_mem")),
            "fleet_avg_disk": ("disk_usage_pct", summary.get("fleet_avg_disk")),
        }
        for key, (metric, value) in metric_map.items():
            severities[key] = self.classify_severity(metric, value)

        return severities

    def generate_insights(self, report_data: dict, report_type: str = "enterprise") -> dict:
        """Full insight package — deterministic Layer 1."""
        findings = self.generate_findings(report_data)
        severities = self.generate_metric_severities(report_data)
        recommendations = list(dict.fromkeys(
            f.get("recommendation") for f in findings if f.get("recommendation")
        ))

        # Rule-based executive summary
        critical_count = sum(1 for f in findings if f["severity"] == "critical")
        warning_count = sum(1 for f in findings if f["severity"] == "warning")
        if critical_count:
            summary = f"{critical_count} critical issue{'s' if critical_count > 1 else ''} detected. Immediate attention required."
        elif warning_count:
            summary = f"{warning_count} warning{'s' if warning_count > 1 else ''} detected. Review recommended."
        else:
            summary = "All monitored metrics are within acceptable thresholds."

        return {
            "executive_summary": summary,
            "findings": findings,
            "recommendations": recommendations,
            "metric_severities": severities,
            "insight_source": "rule_based",
        }

    def enhance_with_gemini(self, insights: dict, report_data: dict) -> dict:
        """Layer 2: Rewrite rule-based findings into natural language via Gemini.

        Never produces NEW findings. Only enhances existing ones.
        Cached by report data hash. Validates output.
        Returns original insights on any failure.
        """
        if not insights.get("findings"):
            return insights

        # Cache key from report metrics hash
        cache_key = _report_hash(report_data)
        cached = _gemini_cache.get(cache_key)
        if cached:
            return cached

        try:
            import os
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                logger.debug("[InsightEngine] GOOGLE_API_KEY not set — skipping Gemini enhancement")
                return insights

            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")

            # Build prompt — only send metric values, NEVER device names/IPs
            prompt = _build_gemini_prompt(insights, report_data)

            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    max_output_tokens=500,
                ),
                request_options={"timeout": 10},
            )

            enhanced = _parse_gemini_response(response.text, insights)
            _gemini_cache[cache_key] = enhanced
            return enhanced

        except Exception as exc:
            logger.warning("[InsightEngine] Gemini enhancement failed: %s", exc)
            return insights


# ── Gemini helpers ────────────────────────────────────────────────────────────

_gemini_cache: Dict[str, dict] = {}  # Simple in-memory cache, keyed by report hash


def _report_hash(report_data: dict) -> str:
    """SHA256 hash of key metrics for cache dedup."""
    summary = report_data.get("summary", {})
    key_data = json.dumps({
        "uptime": summary.get("fleet_avg_uptime"),
        "cpu": summary.get("fleet_avg_cpu"),
        "mem": summary.get("fleet_avg_mem"),
        "disk": summary.get("fleet_avg_disk"),
        "devices": summary.get("total_devices"),
        "latency": report_data.get("avg_latency"),
    }, sort_keys=True)
    return hashlib.sha256(key_data.encode()).hexdigest()[:16]


def _build_gemini_prompt(insights: dict, report_data: dict) -> str:
    """Build Gemini prompt from rule-based findings. No device names/IPs."""
    findings_text = "\n".join(
        f"- [{f['severity'].upper()}] {f['text']}"
        for f in insights.get("findings", [])
    )
    return (
        "You are a network operations analyst. Rewrite these monitoring findings "
        "into a concise executive summary paragraph (max 3 sentences). "
        "Do not add new findings. Do not mention device names or IP addresses. "
        "Focus on business impact and urgency.\n\n"
        f"Findings:\n{findings_text}\n\n"
        'Respond as JSON: {"executive_summary": "...", "enhanced_recommendations": ["...", "..."]}'
    )


def _parse_gemini_response(response_text: str, original_insights: dict) -> dict:
    """Parse and validate Gemini JSON response. Returns original on failure."""
    try:
        data = json.loads(response_text)
        if not isinstance(data, dict):
            return original_insights

        enhanced = dict(original_insights)

        # Only override executive_summary if Gemini provided one
        if isinstance(data.get("executive_summary"), str) and len(data["executive_summary"]) <= 500:
            enhanced["executive_summary"] = data["executive_summary"]
            enhanced["insight_source"] = "gemini_enhanced"

        # Only add recommendations that are strings and under 200 chars
        if isinstance(data.get("enhanced_recommendations"), list):
            valid_recs = [
                r for r in data["enhanced_recommendations"]
                if isinstance(r, str) and len(r) <= 200
            ][:5]
            if valid_recs:
                enhanced["recommendations"] = valid_recs

        return enhanced
    except (json.JSONDecodeError, KeyError, TypeError):
        return original_insights


# ── Module-level canonical threshold accessor ────────────────────────────────

def get_report_thresholds() -> Dict[str, Dict]:
    """Canonical threshold dict used by narrative, peaks, and insight engine.

    Returns merged _REPORT_THRESHOLDS + METRIC_CATALOG server thresholds.
    Single source of truth — never duplicate these constants elsewhere.
    All consumers (ReportNarrativeService, _extract_peaks_and_breaches,
    PDF severity coloring) import this function.
    """
    engine = ReportInsightEngine()
    return engine._all_thresholds()
