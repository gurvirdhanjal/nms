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
    """Normalise a datetime to naive UTC. Prevents TypeError on aware/naive subtraction."""
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

        self.period_hours: float   = period_s / 3600.0
        self.interval_seconds: int = int(interval_seconds) if interval_seconds else 0

        if self.interval_seconds > 0:
            self.expected_scans: Optional[int]  = int(period_s / self.interval_seconds)
            self.ping_interval_label: str       = f"{self.interval_seconds // 60} min"
        else:
            self.expected_scans      = None
            self.ping_interval_label = "—"

    def enrich(self, rows: List[dict]) -> List[dict]:
        """Return a new list of enriched row dicts. Input rows are never mutated."""
        return [self._enrich_row(row) for row in rows]

    def _enrich_row(self, row: dict) -> dict:
        """Return a new dict: shallow copy of row + 9 enriched fields."""
        raise NotImplementedError  # implemented in Task 3
