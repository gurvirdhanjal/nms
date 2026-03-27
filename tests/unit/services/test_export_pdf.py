"""Tests for services/export_service.py PDF export pipeline."""
import io
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


def _sample_report_data():
    """Minimal report data structure for testing."""
    return {
        "period": {"start": "2026-03-01", "end": "2026-03-15"},
        "meta": {
            "scope_type": "global",
            "scope_id": "",
            "freshness_state": "fresh",
        },
        "summary": [
            {"device_name": "server-01", "avg_cpu": 45.2, "max_cpu": 82.1},
        ],
    }


def test_dispatch_routes_to_builder():
    """export_to_pdf should route known report types to their builder."""
    from services.export_service import export_to_pdf, _PDF_BUILDERS

    for report_type in _PDF_BUILDERS:
        result = export_to_pdf(_sample_report_data(), report_type)
        assert isinstance(result, io.BytesIO), f"Builder for '{report_type}' did not return BytesIO"
        content = result.getvalue()
        assert content.startswith(b'%PDF'), f"Builder for '{report_type}' did not produce valid PDF"


def test_fallback_on_builder_exception():
    """If a builder raises, export_to_pdf should fall back gracefully."""
    from services.export_service import export_to_pdf

    with patch('services.export_service._PDF_BUILDERS', {'executive': MagicMock(side_effect=RuntimeError("boom"))}):
        result = export_to_pdf(_sample_report_data(), 'executive')
        assert isinstance(result, io.BytesIO)
        assert result.getvalue().startswith(b'%PDF')


def test_none_report_data_no_crash():
    """Passing None as report_data should not raise."""
    from services.export_service import export_to_pdf

    result = export_to_pdf(None, 'executive')
    assert isinstance(result, io.BytesIO)
    assert result.getvalue().startswith(b'%PDF')


def test_empty_rows_produces_pdf():
    """Empty report rows should produce a PDF with 'No data' message."""
    from services.export_service import export_to_pdf

    data = {"period": {"start": "2026-03-01", "end": "2026-03-15"}, "meta": {}}
    result = export_to_pdf(data, 'device-health')
    assert isinstance(result, io.BytesIO)
    assert result.getvalue().startswith(b'%PDF')


def test_export_buffer_routes_by_format():
    """export_report_buffer routes to the correct format based on the format arg."""
    from services.export_service import export_report_buffer

    # PDF (default and explicit)
    for fmt in (None, 'pdf', 'json'):
        result = export_report_buffer(_sample_report_data(), 'executive', fmt)
        assert isinstance(result, io.BytesIO)
        assert result.getvalue().startswith(b'%PDF'), f"format={fmt!r} did not produce PDF"

    # CSV — starts with column headers (plain text)
    csv_result = export_report_buffer(_sample_report_data(), 'executive', 'csv')
    assert isinstance(csv_result, io.BytesIO)
    csv_bytes = csv_result.getvalue()
    assert not csv_bytes.startswith(b'%PDF'), "format=csv should not produce PDF"
    # Content should be decodable UTF-8 text
    csv_bytes.decode('utf-8')

    # XLSX — ZIP-based format (starts with PK magic)
    xlsx_result = export_report_buffer(_sample_report_data(), 'executive', 'xlsx')
    assert isinstance(xlsx_result, io.BytesIO)
    assert xlsx_result.getvalue().startswith(b'PK'), "format=xlsx should produce XLSX (ZIP-based)"


def test_unknown_report_type_uses_generic():
    """Unknown report types should use the generic PDF path."""
    from services.export_service import export_to_pdf

    result = export_to_pdf(_sample_report_data(), 'unknown-report-type')
    assert isinstance(result, io.BytesIO)
    assert result.getvalue().startswith(b'%PDF')
