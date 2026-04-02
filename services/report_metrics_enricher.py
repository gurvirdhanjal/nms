"""
report_metrics_enricher.py — post-processing enricher for PDF report rows.

Takes canonical rows (18-field contract from core_metrics_service) and adds
9 new fields required for the 3-table PDF layout. No DB calls. No mutations.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


def _to_naive_utc(dt: datetime) -> datetime:
    """Normalise a datetime to naive UTC. Prevents TypeError on aware/naive subtraction.

    Raises ValueError on None input — callers must supply valid datetimes.
    """
    if dt is None:
        raise ValueError("_to_naive_utc: datetime must not be None")
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class ReportMetricsEnricher:
    """Enrich canonical device rows with computed reporting fields.

    Usage:
        enricher = ReportMetricsEnricher(interval_seconds, start_date, end_date)
        enriched_rows = enricher.enrich(report["server_rows"])
    """

    def __init__(self, interval_seconds: int, start_date: datetime, end_date: datetime) -> None:
        start = _to_naive_utc(start_date)
        end   = _to_naive_utc(end_date)
        period_s = (end - start).total_seconds()

        self.period_hours: float   = max(0.0, period_s / 3600.0)
        self.interval_seconds: int = int(interval_seconds) if interval_seconds else 0

        if self.interval_seconds > 0 and period_s > 0:
            self.expected_scans: Optional[int]  = int(period_s / self.interval_seconds)
            self.ping_interval_label: str       = f"{self.interval_seconds // 60} min"
        elif self.interval_seconds > 0:
            # interval set but period is zero or negative — degrade gracefully
            self.expected_scans      = None
            self.ping_interval_label = f"{self.interval_seconds // 60} min"
        else:
            self.expected_scans      = None
            self.ping_interval_label = "—"

    def enrich(self, rows: List[dict]) -> List[dict]:
        """Return a new list of enriched row dicts. Input rows are never mutated."""
        return [self._enrich_row(row) for row in rows]

    def _enrich_row(self, row: dict) -> dict:
        """Return a new dict: shallow copy of row + 9 enriched fields."""
        enriched = dict(row)   # shallow copy — never mutates input

        expected = self.expected_scans

        # ── actual_scans (derived from monitoring_coverage_pct) ──────────────
        cov_pct = row.get("monitoring_coverage_pct")
        if cov_pct is not None and expected:
            actual_scans: Optional[int] = round(cov_pct / 100.0 * expected)
        else:
            actual_scans = None

        # ── timeout_pct ───────────────────────────────────────────────────────
        tc = row.get("timeout_count")
        if tc is not None and expected and expected > 0:
            timeout_pct: Optional[float] = round(float(tc) / expected * 100.0, 2)
        else:
            timeout_pct = None

        # ── data_confidence ───────────────────────────────────────────────────
        if expected is None or expected == 0 or cov_pct is None:
            data_confidence = "NO_DATA"
        elif cov_pct >= 90.0:
            data_confidence = "HIGH"
        elif cov_pct >= 70.0:
            data_confidence = "MEDIUM"
        else:
            data_confidence = "LOW"

        # ── downtime_pct ──────────────────────────────────────────────────────
        up = row.get("uptime_pct")
        downtime_pct: Optional[float] = round(100.0 - up, 2) if up is not None else None

        # ── uptime_hours ──────────────────────────────────────────────────────
        uptime_hours: Optional[float] = (
            round((up / 100.0) * self.period_hours, 1) if up is not None else None
        )

        # ── agent_status ──────────────────────────────────────────────────────
        fleet = row.get("fleet", "")
        if fleet == "workstation":
            agent_status = "Installed" if up is not None else "Offline"
        else:
            agent_status = "Installed" if row.get("avg_cpu") is not None else "N/A"

        enriched.update({
            "actual_scans":        actual_scans,
            "expected_scans":      expected,
            "timeout_pct":         timeout_pct,
            "data_confidence":     data_confidence,
            "downtime_pct":        downtime_pct,
            "uptime_hours":        uptime_hours,
            "ping_interval_label": self.ping_interval_label,
            "agent_status":        agent_status,
            "min_latency_ms":      row.get("min_latency_ms"),
        })
        return enriched
