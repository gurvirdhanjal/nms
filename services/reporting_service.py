"""Backward-compat shim — real implementation is in services/reporting/.

Re-exports symbols that existing code patches in tests:
  - ReportingService (the composed class)
  - build_scope_context, scoped_query (patched by test_enterprise_report_service.py)
  - Module-level helpers (APP_CATEGORIES, _utcnow_naive, etc.)
"""
from services.reporting import ReportingService  # noqa: F401
from services.reporting.base import (  # noqa: F401
    APP_CATEGORIES,
    _classify_app,
    _row_value,
    _safe_round,
    _utcnow_naive,
)
from middleware.rbac import build_scope_context, scoped_query  # noqa: F401
