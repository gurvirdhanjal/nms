"""Reporting service package — split from monolithic reporting_service.py."""
from .base import ReportingServiceBase, APP_CATEGORIES, _utcnow_naive, _classify_app, _safe_round, _row_value
from .executive import ExecutiveReportMixin
from .operational import OperationalReportMixin
from .health import HealthReportMixin
from .alert import AlertReportMixin
from .other import OtherReportMixin


class ReportingService(
    ReportingServiceBase,
    ExecutiveReportMixin,
    OperationalReportMixin,
    HealthReportMixin,
    AlertReportMixin,
    OtherReportMixin,
):
    """Composed reporting service with all report generators."""
    pass
