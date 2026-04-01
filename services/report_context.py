"""
Context-aware rendering gates for enterprise PDF report sections.

ReportContext inspects the assembled report dict and answers boolean
questions that drive conditional section rendering.  All logic is
pure (no DB access, no Flask context).

Usage in generate_enterprise_pdf():
    ctx = ReportContext(report)
    if ctx.should_render_server_fleet():
        story += _build_server_fleet(report, styles)
"""
from __future__ import annotations

from services.report_rules import (
    MIN_DEVICES_FOR_DISTRIBUTION,
    should_render_exception_table,
)


class ReportContext:
    """Encapsulates data-state decisions for PDF section rendering."""

    def __init__(self, report_data: dict) -> None:
        self.data = report_data
        self.server_rows: list = report_data.get("server_rows") or []
        self.tracked_rows: list = report_data.get("tracked_rows") or []
        self.total_devices: int = len(self.server_rows) + len(self.tracked_rows)

    # ── Section guards ────────────────────────────────────────────────────────

    def should_render_server_fleet(self) -> bool:
        """Server fleet section is only useful when there are server rows."""
        return len(self.server_rows) > 0

    def should_render_tracked_fleet(self) -> bool:
        """Tracked fleet section is only useful when there are tracked rows."""
        return len(self.tracked_rows) > 0

    def should_render_violations(self) -> bool:
        """Violations section is skipped when violation count is zero."""
        v = self.data.get("violations") or {}
        total = v.get("total_site_violations", 0) + v.get("total_typed_text_alerts", 0)
        return total > 0

    # ── Density gates ─────────────────────────────────────────────────────────

    def should_render_sla_distribution(self) -> bool:
        """SLA distribution bars are meaningful only with >= 3 devices."""
        return self.total_devices >= MIN_DEVICES_FOR_DISTRIBUTION

    def should_render_exception_table(self, rows: list) -> bool:
        """Exception table adds value only when exceptional rows exist."""
        return should_render_exception_table(rows)

    # ── Fleet health state ────────────────────────────────────────────────────

    def fleet_is_healthy(self) -> bool:
        """True when every monitored device is within SLA thresholds.

        A healthy fleet suppresses the attention / exception table and
        allows the executive summary to show a single green banner instead.
        """
        all_rows = self.server_rows + self.tracked_rows
        if not all_rows:
            return False
        critical_count = sum(
            1 for r in all_rows
            if r.get("sla_tier") in ("Warning", "Critical")
        )
        return critical_count == 0
