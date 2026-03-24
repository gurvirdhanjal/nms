"""Tests for services/enterprise_report_service.py.

Covers: sla_tier boundary values, downtime_hours math, _mttr_mtbf edge cases,
fleet validation, and empty DB report structure.
"""
import pytest
from datetime import datetime, timedelta

pytestmark = pytest.mark.unit


# ── sla_tier() boundary tests ───────────────────────────────────────────────

class TestSlaTier:

    def test_gold_at_exact_threshold(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(99.9) == "Gold"

    def test_gold_above_threshold(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(100.0) == "Gold"

    def test_silver_at_exact_threshold(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(99.5) == "Silver"

    def test_silver_just_below_gold(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(99.89) == "Silver"

    def test_bronze_at_exact_threshold(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(99.0) == "Bronze"

    def test_warning_at_exact_threshold(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(95.0) == "Warning"

    def test_critical_below_warning(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(94.9) == "Critical"

    def test_critical_at_zero(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(0.0) == "Critical"

    def test_unknown_for_none(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(None) == "Unknown"


# ── downtime_hours() math tests ─────────────────────────────────────────────

class TestDowntimeHours:

    def test_100_percent_uptime_zero_downtime(self):
        from services.enterprise_report_service import downtime_hours
        assert downtime_hours(100.0, 720.0) == 0.0

    def test_99_percent_uptime_in_30_days(self):
        from services.enterprise_report_service import downtime_hours
        result = downtime_hours(99.0, 720.0)
        assert result == 7.2  # 1% of 720h

    def test_none_uptime_returns_none(self):
        from services.enterprise_report_service import downtime_hours
        assert downtime_hours(None, 720.0) is None

    def test_zero_uptime_equals_full_period(self):
        from services.enterprise_report_service import downtime_hours
        assert downtime_hours(0.0, 24.0) == 24.0


# ── _mttr_mtbf() edge cases ────────────────────────────────────────────────

class TestMttrMtbf:

    def test_no_incidents_returns_none_none(self):
        from services.enterprise_report_service import _mttr_mtbf
        mttr, mtbf = _mttr_mtbf([])
        assert mttr is None
        assert mtbf is None

    def test_single_incident_no_mtbf(self):
        from services.enterprise_report_service import _mttr_mtbf
        incidents = [{"start": "2026-03-01T00:00:00", "end": "2026-03-01T00:30:00", "duration_min": 30.0}]
        mttr, mtbf = _mttr_mtbf(incidents)
        assert mttr == 30.0
        assert mtbf is None

    def test_two_incidents_compute_mtbf(self):
        from services.enterprise_report_service import _mttr_mtbf
        incidents = [
            {"start": "2026-03-01T00:00:00", "end": "2026-03-01T00:30:00", "duration_min": 30.0},
            {"start": "2026-03-01T06:00:00", "end": "2026-03-01T06:15:00", "duration_min": 15.0},
        ]
        mttr, mtbf = _mttr_mtbf(incidents)
        assert mttr == 22.5  # (30+15)/2
        assert mtbf == 6.0   # 6h gap between starts


# ── build_enterprise_uptime_report() ────────────────────────────────────────

class TestBuildEnterpriseUptimeReport:

    def test_invalid_fleet_raises_valueerror(self):
        from services.enterprise_report_service import build_enterprise_uptime_report
        with pytest.raises(ValueError, match="Invalid fleet"):
            build_enterprise_uptime_report(fleet="invalid")

    def test_empty_db_returns_valid_structure(self):
        from services.enterprise_report_service import build_enterprise_uptime_report
        report = build_enterprise_uptime_report()
        assert "period" in report
        assert "summary" in report
        assert "server_rows" in report
        assert "tracked_rows" in report
        assert "generated_at" in report
        assert report["summary"]["total_devices"] == 0
        assert report["server_rows"] == []
        assert report["tracked_rows"] == []

    def test_server_fleet_filter_returns_no_tracked(self):
        from services.enterprise_report_service import build_enterprise_uptime_report
        report = build_enterprise_uptime_report(fleet="server")
        assert report["tracked_rows"] == []

    def test_workstation_fleet_filter_returns_no_servers(self):
        from services.enterprise_report_service import build_enterprise_uptime_report
        report = build_enterprise_uptime_report(fleet="workstation")
        assert report["server_rows"] == []


# ── _fleet_violation_summary() tests ──────────────────────────────────────

class TestFleetViolationSummary:

    def test_empty_device_ids_returns_empty_list(self):
        from services.enterprise_report_service import _fleet_violation_summary
        now = datetime.utcnow()
        result = _fleet_violation_summary([], now - timedelta(days=30), now)
        assert result == []

    def test_nonexistent_device_ids_returns_empty_list(self):
        from services.enterprise_report_service import _fleet_violation_summary
        now = datetime.utcnow()
        result = _fleet_violation_summary([999999], now - timedelta(days=30), now)
        assert result == []

    def test_return_dict_has_expected_keys(self):
        """If there are results, each dict must have the canonical keys."""
        from services.enterprise_report_service import _fleet_violation_summary
        now = datetime.utcnow()
        result = _fleet_violation_summary([999999], now - timedelta(days=30), now)
        # Empty is valid — but if we had data, check the shape
        expected_keys = {"device_id", "device_name", "employee_name", "domain",
                         "violation_count", "last_violation"}
        for row in result:
            assert set(row.keys()) == expected_keys


class TestBuildReportIncludesViolationDetails:

    def test_workstation_fleet_has_website_violation_details_key(self):
        from services.enterprise_report_service import build_enterprise_uptime_report
        report = build_enterprise_uptime_report(fleet="workstation")
        assert "website_violation_details" in report
        assert isinstance(report["website_violation_details"], list)

    def test_server_fleet_has_empty_violation_details(self):
        from services.enterprise_report_service import build_enterprise_uptime_report
        report = build_enterprise_uptime_report(fleet="server")
        assert report["website_violation_details"] == []


# ── _compute_focus_score() edge cases ───────────────────────────────────────

class TestComputeFocusScore:

    def test_no_data_returns_none(self):
        from services.enterprise_report_service import _compute_focus_score
        now = datetime.utcnow()
        result = _compute_focus_score(device_id=99999, start=now - timedelta(days=1), end=now)
        assert result is None


# ── ReportingService null guards (MTTA / TTA / TTR / MTTR) ──────────────────
#
# These tests patch build_scope_context + scoped_query so that
# ReportingService can be instantiated and called without a Flask request
# context.  The patches give all service calls admin-scope (Device.query
# unfiltered).

from unittest.mock import patch
from extensions import db
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.device import Device
from models.scan_history import DeviceScanHistory

_ADMIN_SCOPE = {
    'role': 'admin', 'scope_type': 'global', 'scope_key': 'global',
    'scope_label': 'Global', 'site_id': None, 'department_id': None,
}


def _svc():
    """Build ReportingService with admin scope (no request context needed)."""
    from services.reporting_service import ReportingService
    with patch('services.reporting.base.build_scope_context', return_value=_ADMIN_SCOPE):
        return ReportingService()


def _exec(svc, start, end):
    with patch('services.reporting.base.scoped_query', side_effect=lambda m: m.query):
        return svc.get_executive_fleet_health(start_date=start, end_date=end)


def _alert(svc, start, end):
    with patch('services.reporting.base.scoped_query', side_effect=lambda m: m.query):
        return svc.get_alert_history_report(start_date=start, end_date=end)


def _network(svc, start, end):
    with patch('services.reporting.base.scoped_query', side_effect=lambda m: m.query):
        return svc.get_network_performance_report(start_date=start, end_date=end)


class TestReportingServiceNullGuards:
    """MTTA / TTA / TTR / MTTR return None when no matching events exist."""

    def test_executive_mtta_none_when_no_acknowledged_alerts(self):
        svc = _svc()
        end = datetime(2026, 3, 17, 12, 0, 0)
        result = _exec(svc, end - timedelta(days=30), end)
        assert result['sla_metrics']['mtta_seconds'] is None
        assert result['sla_metrics']['mtta_human'] is None

    def test_alert_tta_ttr_none_when_no_resolved_alerts(self):
        svc = _svc()
        end = datetime(2026, 3, 17, 12, 0, 0)
        result = _alert(svc, end - timedelta(days=7), end)
        assert result['tta']['seconds'] is None
        assert result['ttr']['seconds'] is None

    def test_network_mttr_none_when_no_resolved_incidents(self):
        svc = _svc()
        end = datetime(2026, 3, 17, 12, 0, 0)
        result = _network(svc, end - timedelta(days=7), end)
        assert result['mttr']['seconds'] is None
        assert result['mttr']['human'] is None


class TestReportingServicePrevPeriod:
    """prev_uptime_score is populated from DailyDeviceStats or None when absent."""

    def _device(self, ip='10.99.1.1'):
        d = Device(device_name='Prev-Test', device_type='Server', device_ip=ip)
        db.session.add(d)
        db.session.flush()
        return d

    def test_executive_prev_uptime_score_returned(self):
        end = datetime(2026, 3, 17, 0, 0, 0)
        start = end - timedelta(days=30)
        prev_start = start - timedelta(days=30)

        device = self._device()

        # Current period: 95 % uptime (10 rows)
        for i in range(10):
            db.session.add(DailyDeviceStats(
                device_id=device.device_id,
                date=start.date() + timedelta(days=i),
                uptime_percent=95.0,
            ))

        # Previous period: 80 % uptime (10 rows — clearly different)
        for i in range(10):
            db.session.add(DailyDeviceStats(
                device_id=device.device_id,
                date=prev_start.date() + timedelta(days=i),
                uptime_percent=80.0,
            ))

        db.session.commit()

        svc = _svc()
        result = _exec(svc, start, end)

        assert result['prev_uptime_score'] is not None
        assert result['uptime_score'] != result['prev_uptime_score']

    def test_executive_prev_uptime_score_none_when_no_history(self):
        svc = _svc()
        end = datetime(2026, 3, 17, 0, 0, 0)
        result = _exec(svc, end - timedelta(days=30), end)
        assert result['prev_uptime_score'] is None


class _MockAvailabilityEvent:
    """Minimal stand-in for TrackedDeviceAvailabilityEvent in pure-logic tests."""
    def __init__(self, status: str, observed_at: datetime):
        self.status = status
        self.observed_at = observed_at


class TestComputeUptimeFromEvents:
    """Unit tests for the pure _compute_uptime_from_events state machine."""

    def test_no_events_returns_none(self):
        from services.enterprise_report_service import _compute_uptime_from_events
        start = datetime(2026, 3, 1)
        end = datetime(2026, 3, 2)
        uptime, incidents, cov = _compute_uptime_from_events([], start, end)
        assert uptime is None
        assert incidents == []

    def test_single_online_event_full_uptime(self):
        from services.enterprise_report_service import _compute_uptime_from_events
        start = datetime(2026, 3, 1, 0, 0, 0)
        end = datetime(2026, 3, 1, 1, 0, 0)
        events = [_MockAvailabilityEvent("online", start)]
        uptime, incidents, cov = _compute_uptime_from_events(events, start, end)
        # No offline event — no downtime recorded
        assert uptime == 100.0
        assert incidents == []
        assert isinstance(cov, dict)

    def test_offline_then_online_counts_incident(self):
        from services.enterprise_report_service import _compute_uptime_from_events
        start = datetime(2026, 3, 1, 0, 0, 0)
        mid = datetime(2026, 3, 1, 0, 30, 0)
        end = datetime(2026, 3, 1, 1, 0, 0)
        events = [
            _MockAvailabilityEvent("offline", start),
            _MockAvailabilityEvent("online", mid),
        ]
        uptime, incidents, cov = _compute_uptime_from_events(events, start, end)
        assert len(incidents) == 1
        assert incidents[0]["duration_min"] == 30.0
        # 30 min down out of 60 min window → ~50 % uptime
        assert uptime == 50.0
        assert isinstance(cov, dict)

    def test_open_incident_at_window_end(self):
        from services.enterprise_report_service import _compute_uptime_from_events
        start = datetime(2026, 3, 1, 0, 0, 0)
        offline_at = datetime(2026, 3, 1, 0, 45, 0)
        end = datetime(2026, 3, 1, 1, 0, 0)
        events = [_MockAvailabilityEvent("offline", offline_at)]
        uptime, incidents, cov = _compute_uptime_from_events(events, start, end)
        assert len(incidents) == 1
        assert incidents[0].get("open") is True
        assert incidents[0]["duration_min"] == 15.0
        # Observed window = (end - first_event) = 15 min.
        # Device offline for all 15 observed minutes → 0% uptime.
        assert uptime == 0.0
        assert isinstance(cov, dict)

    def test_device_always_offline(self):
        from services.enterprise_report_service import _compute_uptime_from_events
        start = datetime(2026, 3, 1, 0, 0, 0)
        end = datetime(2026, 3, 1, 1, 0, 0)
        events = [
            _MockAvailabilityEvent("offline", start),
            _MockAvailabilityEvent("degraded", datetime(2026, 3, 1, 0, 20, 0)),
        ]
        uptime, incidents, cov = _compute_uptime_from_events(events, start, end)
        # Only the first offline event opens the incident; degraded while already
        # offline is ignored (offline_since already set).  Incident runs to end.
        assert len(incidents) == 1
        assert uptime == 0.0
        assert isinstance(cov, dict)


class TestReportingServiceDataHealth:
    """data_health dict reflects actual scan history and daily stats coverage."""

    def _device(self, ip='10.99.2.1'):
        d = Device(device_name='Health-Test', device_type='Server', device_ip=ip)
        db.session.add(d)
        db.session.flush()
        return d

    def test_executive_data_health_fields_returned(self):
        end = datetime(2026, 3, 17, 0, 0, 0)
        start = end - timedelta(days=30)
        device = self._device()

        # 32 scans spanning from 2 days before start → inside window
        # Oldest scan is 32 days before end → scan_history_days >= 30
        for i in range(32):
            db.session.add(DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status='Online',
                scan_timestamp=start - timedelta(days=2) + timedelta(days=i),
            ))

        # 15 daily stats within the current window
        for i in range(15):
            db.session.add(DailyDeviceStats(
                device_id=device.device_id,
                date=start.date() + timedelta(days=i),
                uptime_percent=99.0,
            ))

        db.session.commit()

        svc = _svc()
        result = _exec(svc, start, end)

        dh = result['data_health']
        assert dh['scan_history_days'] >= 30
        assert dh['daily_stats_coverage'] == 15
        assert dh['trend_window_days'] == 30

    def test_executive_data_health_fields_zero_when_empty(self):
        svc = _svc()
        end = datetime(2026, 3, 17, 0, 0, 0)
        result = _exec(svc, end - timedelta(days=30), end)
        dh = result['data_health']
        assert dh['scan_history_days'] == 0
        assert dh['daily_stats_coverage'] == 0
        assert dh['trend_window_days'] == 30
