"""
ReportSectionBuilder — fluent builder for enterprise PDF section openers.

Every fleet/report section follows the same 5-element pipeline:
  1. PageBreak
  2. Section heading (with optional right-aligned confidence metadata)
  3. Description paragraph (muted grey, 8pt)
  4. Spacer
  5. Narrative block (0 or more flowables from _build_narrative_section)

Using this builder instead of repeating those 5 lines in every section function
ensures a consistent visual structure and makes the pattern enforceable.

Usage:
    from services.pdf_section_builder import ReportSectionBuilder

    elems = (
        ReportSectionBuilder("Server Fleet (12 devices)", styles)
        .description("SNMP/ICMP managed devices — uptime from daily rollups.")
        .confidence_meta(confidence_dict, "server_fleet", period, device_count=12)
        .narrative(narratives.get("server_fleet"))
        .build()
    )
    # → list[Flowable]: PageBreak, heading Table, Paragraph, Spacer, ...narrative
"""
from __future__ import annotations

from typing import Any

from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, PageBreak, Spacer


class ReportSectionBuilder:
    """Fluent builder for one PDF section opener.

    Collect configuration, then call `.build()` to get a list of flowables
    to extend into the document story.
    """

    def __init__(self, title: str, styles):
        self._title = title
        self._styles = styles
        self._description: str = ""
        self._meta: str = ""
        self._narrative: dict | None = None
        self._page_break: bool = True

    # ── Configuration setters ─────────────────────────────────────────────────

    def description(self, text: str) -> "ReportSectionBuilder":
        """Short muted description placed immediately below the section heading."""
        self._description = text
        return self

    def meta(self, meta_text: str) -> "ReportSectionBuilder":
        """Right-aligned confidence metadata string (pre-built)."""
        self._meta = meta_text or ""
        return self

    def confidence_meta(
        self,
        confidence: dict,
        key: str,
        period: dict | None = None,
        device_count: int | None = None,
    ) -> "ReportSectionBuilder":
        """Build and set confidence metadata from a _confidence dict.

        Calls `_confidence_meta_text()` from enterprise_pdf_service.
        This is the preferred way to set metadata — avoids pre-computing
        the string outside the builder.
        """
        from services.enterprise_pdf_service import _confidence_meta_text
        self._meta = _confidence_meta_text(confidence, key, period, device_count)
        return self

    def narrative(self, narrative: dict | None) -> "ReportSectionBuilder":
        """Narrative dict produced by ReportNarrativeService.generate_narrative().

        If None or empty, no narrative flowables are added.
        """
        self._narrative = narrative
        return self

    def no_page_break(self) -> "ReportSectionBuilder":
        """Suppress the leading PageBreak (for first section on a page)."""
        self._page_break = False
        return self

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self) -> list[Any]:
        """Return ordered list of flowables for this section opener."""
        from services.enterprise_pdf_service import (
            normal_paragraph,
            section_heading_with_meta,
            _build_narrative_section,
            TEXT_MID,
        )

        elems: list[Any] = []

        if self._page_break:
            elems.append(PageBreak())

        elems.append(section_heading_with_meta(self._title, self._styles, self._meta))

        if self._description:
            elems.append(normal_paragraph(self._description, self._styles, color=TEXT_MID))

        elems.append(Spacer(1, 4))

        if self._narrative:
            elems.extend(_build_narrative_section(self._narrative, self._styles))

        return elems
