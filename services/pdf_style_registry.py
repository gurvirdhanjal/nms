"""
PDF Style Registry — single source of truth for all ParagraphStyle definitions.

Usage:
    from services.pdf_style_registry import PDFStyleRegistry
    reg = PDFStyleRegistry(base_styles)   # base_styles from getSampleStyleSheet()
    title_para = Paragraph("Hello", reg.cover_title)
    # or via dict access:
    title_para = Paragraph("Hello", reg["cover_title"])

All styles are created once at registry instantiation. Re-creating ParagraphStyle
objects on every render call was causing subtle drift when styles shared names
across multiple function calls in the same document.
"""
from __future__ import annotations

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor

# ── Colour palette (mirrors enterprise_pdf_service.py) ───────────────────────
_NAVY     = "#1B2A4A"
_TEAL     = "#0EA5E9"
_BG_ALT   = "#EEF2F7"
_BG_LIGHT = "#F8FAFC"
_WHITE    = "#FFFFFF"
_TEXT_DARK  = "#1A202C"
_TEXT_MID   = "#4A5568"
_TEXT_LIGHT = "#718096"
_BORDER   = "#CBD5E0"


def _hex(color: str) -> HexColor:
    return HexColor(color)


class PDFStyleRegistry:
    """Lazy-built registry of named ParagraphStyle objects.

    Pass in base_styles from getSampleStyleSheet() so that parent styles
    ('Normal', 'BodyText', etc.) are already resolved.
    """

    def __init__(self, base_styles=None):
        self._base = base_styles or getSampleStyleSheet()
        self._styles: dict[str, ParagraphStyle] = {}
        self._build()

    def _build(self):
        N = self._base["Normal"]

        # ── Cover page ────────────────────────────────────────────────────────
        self._add("cover_title", ParagraphStyle(
            "CoverTitle",
            fontName="Helvetica-Bold",
            fontSize=26,
            textColor=_hex(_WHITE),
            leading=32,
            spaceAfter=6,
        ))
        self._add("cover_subtitle", ParagraphStyle(
            "CoverSubtitle",
            fontName="Helvetica",
            fontSize=13,
            textColor=_hex(_TEAL),
            spaceAfter=4,
        ))
        self._add("cover_meta", ParagraphStyle(
            "CoverMeta",
            fontName="Helvetica",
            fontSize=9,
            textColor=_hex(_BG_ALT),
            spaceAfter=3,
        ))
        self._add("cover_confidential", ParagraphStyle(
            "CoverConfidential",
            fontName="Helvetica-Oblique",
            fontSize=8,
            textColor=_hex(_TEXT_LIGHT),
        ))

        # ── Section headings ──────────────────────────────────────────────────
        self._add("section_heading", ParagraphStyle(
            "SectionHeading",
            parent=N,
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=_hex(_NAVY),
            spaceBefore=14,
            spaceAfter=4,
        ))
        self._add("section_title", ParagraphStyle(
            "_sec_title",
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=_hex(_NAVY),
            leading=14,
        ))
        self._add("subheading", ParagraphStyle(
            "SubHeading",
            parent=N,
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=_hex(_TEXT_MID),
            spaceBefore=8,
            spaceAfter=2,
        ))
        self._add("table_label", ParagraphStyle(
            "TableLabel",
            parent=N,
            fontName="Helvetica-Bold",
            fontSize=8.5,
            textColor=_hex(_NAVY),
            spaceBefore=10,
            spaceAfter=3,
        ))

        # ── Body text ─────────────────────────────────────────────────────────
        self._add("body", ParagraphStyle(
            "BodyText_PDF",
            parent=N,
            fontSize=8,
            leading=11,
            spaceAfter=4,
            textColor=_hex(_TEXT_DARK),
        ))
        self._add("body_muted", ParagraphStyle(
            "BodyMuted",
            parent=N,
            fontSize=8,
            leading=11,
            spaceAfter=4,
            textColor=_hex(_TEXT_MID),
        ))
        self._add("caption", ParagraphStyle(
            "Caption_PDF",
            parent=N,
            fontSize=6.5,
            textColor=_hex(_TEXT_LIGHT),
            spaceBefore=2,
        ))

        # ── Narrative block ───────────────────────────────────────────────────
        self._add("narrative_action_required", ParagraphStyle(
            "NarrActionRequired",
            parent=N,
            fontSize=8,
            spaceBefore=4,
            spaceAfter=6,
            leading=12,
            borderWidth=1,
            borderColor=_hex("#ef4444"),
            borderPadding=6,
            backColor=_hex("#FEF2F2"),
        ))
        self._add("narrative_no_action", ParagraphStyle(
            "NarrNoAction",
            parent=N,
            fontSize=8,
            spaceBefore=2,
            spaceAfter=4,
            textColor=_hex("#22c55e"),
        ))
        self._add("narrative_risk_summary", ParagraphStyle(
            "NarrRiskSummary",
            parent=N,
            fontSize=8,
            spaceBefore=2,
            spaceAfter=6,
            leading=12,
            borderWidth=1,
            borderColor=_hex("#ef4444"),
            borderPadding=5,
            leftIndent=3,
            backColor=_hex("#FEF2F2"),
        ))
        self._add("narrative_section_intro", ParagraphStyle(
            "NarrSectionIntro",
            parent=N,
            fontSize=9,
            spaceBefore=2,
            spaceAfter=4,
            leading=13,
        ))
        self._add("narrative_findings", ParagraphStyle(
            "NarrFindings",
            parent=N,
            fontSize=8,
            spaceBefore=2,
            spaceAfter=4,
            leading=11,
        ))
        self._add("narrative_interpretation", ParagraphStyle(
            "NarrInterpretation",
            parent=N,
            fontSize=8,
            spaceBefore=2,
            spaceAfter=4,
            leading=11,
            backColor=_hex("#F0F9FF"),
            borderPadding=4,
        ))
        self._add("narrative_rec_actions", ParagraphStyle(
            "NarrRecActions",
            parent=N,
            fontSize=8,
            spaceBefore=2,
            spaceAfter=6,
            leading=11,
        ))

        # ── Intelligence / insights block ─────────────────────────────────────
        self._add("insights_block", ParagraphStyle(
            "InsightsBlock",
            parent=N,
            fontSize=8,
            spaceBefore=4,
            spaceAfter=8,
            leading=12,
            borderWidth=1,
            borderColor=_hex(_TEAL),
            borderPadding=6,
            backColor=_hex("#F0F9FF"),
        ))
        self._add("insights_source", ParagraphStyle(
            "InsightsSource",
            parent=N,
            fontSize=6,
            spaceAfter=8,
        ))

        # ── Confidence ────────────────────────────────────────────────────────
        self._add("confidence_legend", ParagraphStyle(
            "ConfLegend",
            parent=N,
            fontSize=7,
            spaceBefore=4,
        ))
        self._add("confidence_item", ParagraphStyle(
            "ConfItem",
            parent=N,
            fontSize=6,
            spaceBefore=1,
        ))

        # ── Table cells ───────────────────────────────────────────────────────
        self._add("table_cell", ParagraphStyle(
            "TableCell",
            fontName="Helvetica",
            fontSize=7.5,
            leading=9,
            wordWrap="CJK",
        ))
        self._add("kpi_strip_cell", ParagraphStyle(
            "KpiStripCell",
            fontName="Helvetica",
            fontSize=7,
            alignment=1,
            leading=10,
            textColor=_hex("#64748B"),
        ))

        # ── Section heading meta (right-aligned confidence badge) ─────────────
        self._add("section_meta_right", ParagraphStyle(
            "SectionMetaRight",
            parent=N,
            fontName="Helvetica",
            fontSize=7,
            textColor=_hex(_TEXT_LIGHT),
            spaceBefore=0,
            spaceAfter=0,
            alignment=2,  # RIGHT
        ))

    def _add(self, key: str, style: ParagraphStyle) -> None:
        self._styles[key] = style

    def __getitem__(self, key: str) -> ParagraphStyle:
        return self._styles[key]

    def __getattr__(self, key: str) -> ParagraphStyle:
        try:
            return self._styles[key]
        except KeyError:
            raise AttributeError(f"PDFStyleRegistry has no style '{key}'")

    def get(self, key: str, default=None):
        return self._styles.get(key, default)

    def keys(self):
        return self._styles.keys()
