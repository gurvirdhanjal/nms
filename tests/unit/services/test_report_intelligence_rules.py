"""Tests for PR 16: report_intelligence_rules.py — auto-annotation engine.

Covers all 7 rules: subnet impact, memory leak, admin login anomaly,
AI service violation, long-term offline, rogue device, zero uptime.
Also covers annotate() dispatch and severity sorting.
"""
import pytest
from datetime import datetime, timedelta, timezone

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────────

def _engine():
    from services.report_intelligence_rules import ReportIntelligenceRules
    return ReportIntelligenceRules()


# ── Rule 1: Subnet impact ────────────────────────────────────────────────────

class TestRuleSubnetImpact:

    def test_fires_when_over_60pct_high_latency(self):
        """4/5 devices on same /24 with high latency → fires."""
        rows = [
            {"device_ip": "10.0.1.10", "avg_latency_ms": 500, "device_name": "Dev1"},
            {"device_ip": "10.0.1.11", "avg_latency_ms": 600, "device_name": "Dev2"},
            {"device_ip": "10.0.1.12", "avg_latency_ms": 450, "device_name": "Dev3"},
            {"device_ip": "10.0.1.13", "avg_latency_ms": 410, "device_name": "Dev4"},
            {"device_ip": "10.0.1.14", "avg_latency_ms": 20, "device_name": "Dev5"},
        ]
        data = {"server_rows": rows}
        results = _engine()._rule_subnet_impact(data)
        assert len(results) == 1
        assert results[0]["rule"] == "subnet_impact"
        assert results[0]["severity"] == "warning"
        assert "10.0.1.0/24" in results[0]["text"]

    def test_does_not_fire_below_60pct(self):
        """1/5 devices with high latency → does not fire."""
        rows = [
            {"device_ip": "10.0.1.10", "avg_latency_ms": 500, "device_name": "Dev1"},
            {"device_ip": "10.0.1.11", "avg_latency_ms": 20, "device_name": "Dev2"},
            {"device_ip": "10.0.1.12", "avg_latency_ms": 30, "device_name": "Dev3"},
            {"device_ip": "10.0.1.13", "avg_latency_ms": 40, "device_name": "Dev4"},
            {"device_ip": "10.0.1.14", "avg_latency_ms": 10, "device_name": "Dev5"},
        ]
        data = {"server_rows": rows}
        results = _engine()._rule_subnet_impact(data)
        assert results == []

    def test_skipped_when_fewer_than_3_devices(self):
        """Only 2 devices on a /24 → skipped even if all have high latency."""
        rows = [
            {"device_ip": "10.0.1.10", "avg_latency_ms": 999, "device_name": "Dev1"},
            {"device_ip": "10.0.1.11", "avg_latency_ms": 999, "device_name": "Dev2"},
        ]
        data = {"server_rows": rows}
        results = _engine()._rule_subnet_impact(data)
        assert results == []

    def test_packet_loss_also_triggers(self):
        """High packet loss (>50%) should also count as impacted."""
        rows = [
            {"device_ip": "10.0.2.10", "avg_packet_loss_pct": 60, "device_name": "D1"},
            {"device_ip": "10.0.2.11", "avg_packet_loss_pct": 70, "device_name": "D2"},
            {"device_ip": "10.0.2.12", "avg_packet_loss_pct": 55, "device_name": "D3"},
        ]
        data = {"server_rows": rows}
        results = _engine()._rule_subnet_impact(data)
        assert len(results) == 1

    def test_empty_data_returns_empty(self):
        results = _engine()._rule_subnet_impact({})
        assert results == []

    def test_uses_top_problematic_fallback(self):
        """Falls back to top_problematic when server_rows is absent."""
        rows = [
            {"ip": "10.0.3.10", "latency": 500, "name": "D1"},
            {"ip": "10.0.3.11", "latency": 600, "name": "D2"},
            {"ip": "10.0.3.12", "latency": 450, "name": "D3"},
        ]
        data = {"top_problematic": rows}
        results = _engine()._rule_subnet_impact(data)
        assert len(results) == 1


# ── Rule 2: Memory leak detection ────────────────────────────────────────────

class TestRuleMemoryLeak:

    def test_fires_on_monotonic_increase(self):
        """3+ consecutive increasing memory readings → fires."""
        data = {
            "time_series": {
                "srv1": {
                    "device_name": "Server-01",
                    "points": [
                        {"mem": 50}, {"mem": 55}, {"mem": 60}, {"mem": 65},
                    ],
                }
            }
        }
        results = _engine()._rule_memory_leak(data)
        assert len(results) == 1
        assert results[0]["rule"] == "memory_leak"
        assert results[0]["severity"] == "warning"
        assert "Server-01" in results[0]["text"]
        assert "memory leak" in results[0]["text"].lower()

    def test_does_not_fire_on_fluctuating_data(self):
        """Non-monotonic data → does not fire."""
        data = {
            "time_series": {
                "srv1": {
                    "device_name": "Server-01",
                    "points": [
                        {"mem": 50}, {"mem": 40}, {"mem": 55}, {"mem": 45},
                    ],
                }
            }
        }
        results = _engine()._rule_memory_leak(data)
        assert results == []

    def test_skipped_with_fewer_than_3_points(self):
        """<3 data points → skipped."""
        data = {
            "time_series": {
                "srv1": {
                    "device_name": "Server-01",
                    "points": [{"mem": 50}, {"mem": 55}],
                }
            }
        }
        results = _engine()._rule_memory_leak(data)
        assert results == []

    def test_empty_time_series(self):
        results = _engine()._rule_memory_leak({})
        assert results == []

    def test_none_mem_values_handled(self):
        """None values in mem should not cause errors."""
        data = {
            "time_series": {
                "srv1": {
                    "device_name": "Server-01",
                    "points": [
                        {"mem": None}, {"mem": 50}, {"mem": None}, {"mem": 60},
                    ],
                }
            }
        }
        results = _engine()._rule_memory_leak(data)
        # Should not raise, result depends on run detection
        assert isinstance(results, list)


# ── Rule 3: Admin login anomaly ──────────────────────────────────────────────

class TestRuleAdminLoginAnomaly:

    def _make_login_events(self, count, start_time=None, interval_minutes=10):
        """Create `count` login events at regular intervals."""
        start = start_time or datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        return [
            {
                "action": "admin_login",
                "timestamp": (start + timedelta(minutes=i * interval_minutes)).isoformat(),
                "user": f"admin{i % 2}",
            }
            for i in range(count)
        ]

    def test_fires_with_4_logins_in_2h(self):
        """>3 logins in 2-hour window → fires."""
        events = self._make_login_events(5, interval_minutes=20)
        data = {"recent_audit_log": events}
        results = _engine()._rule_admin_login_anomaly(data)
        assert len(results) == 1
        assert results[0]["rule"] == "admin_login_anomaly"
        assert results[0]["severity"] == "warning"
        assert "login" in results[0]["text"].lower()

    def test_does_not_fire_with_3_or_fewer(self):
        """3 logins in 2h → does not fire (need >3)."""
        events = self._make_login_events(3, interval_minutes=30)
        data = {"recent_audit_log": events}
        results = _engine()._rule_admin_login_anomaly(data)
        assert results == []

    def test_does_not_fire_when_spread_over_time(self):
        """4 logins spread over 10 hours (way outside 2h window) → does not fire."""
        events = self._make_login_events(4, interval_minutes=180)
        data = {"recent_audit_log": events}
        results = _engine()._rule_admin_login_anomaly(data)
        assert results == []

    def test_empty_audit_log(self):
        results = _engine()._rule_admin_login_anomaly({})
        assert results == []

    def test_uses_audit_log_key_fallback(self):
        """Also tries 'audit_log' key."""
        events = self._make_login_events(5, interval_minutes=10)
        data = {"audit_log": events}
        results = _engine()._rule_admin_login_anomaly(data)
        assert len(results) == 1


# ── Rule 4: AI service violation ──────────────────────────────────────────────

class TestRuleAiServiceViolation:

    def test_fires_for_chatgpt(self):
        """chatgpt.com → fires as critical."""
        data = {
            "restricted_site_violations": [
                {"domain": "chatgpt.com", "count": 10, "device_name": "PC-001"},
            ]
        }
        results = _engine()._rule_ai_service_violation(data)
        assert len(results) == 1
        assert results[0]["rule"] == "ai_service_violation"
        assert results[0]["severity"] == "critical"
        assert "data exfiltration" in results[0]["text"].lower()
        assert "PC-001" in results[0]["text"]

    def test_does_not_fire_for_youtube(self):
        """youtube.com is not an AI service → does not fire."""
        data = {
            "restricted_site_violations": [
                {"domain": "youtube.com", "count": 20, "device_name": "PC-002"},
            ]
        }
        results = _engine()._rule_ai_service_violation(data)
        assert results == []

    def test_empty_violations(self):
        results = _engine()._rule_ai_service_violation({})
        assert results == []

    def test_uses_violations_section_fallback(self):
        """Also checks data['violations']['restricted_site_events']."""
        data = {
            "violations": {
                "restricted_site_events": [
                    {"domain": "claude.ai", "count": 5, "device_name": "PC-003"},
                ]
            }
        }
        results = _engine()._rule_ai_service_violation(data)
        assert len(results) == 1
        assert results[0]["severity"] == "critical"

    def test_percentage_calculation(self):
        """AI violations show percentage of total."""
        data = {
            "restricted_site_violations": [
                {"domain": "chatgpt.com", "count": 20, "device_name": "PC-001"},
                {"domain": "youtube.com", "count": 80, "device_name": "PC-002"},
            ]
        }
        results = _engine()._rule_ai_service_violation(data)
        assert len(results) == 1
        # 20 of 100 = 20%
        assert "20%" in results[0]["text"]


# ── Rule 5: Long-term offline ────────────────────────────────────────────────

class TestRuleLongTermOffline:

    def test_fires_over_168h(self):
        """>168h offline → fires."""
        data = {
            "server_rows": [
                {"device_name": "Switch-01", "device_ip": "10.0.0.1", "downtime_hours": 200},
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_long_term_offline(data)
        assert len(results) == 1
        assert results[0]["rule"] == "long_term_offline"
        assert results[0]["severity"] == "critical"
        assert "ESCALATION" in results[0]["text"]

    def test_does_not_fire_under_168h(self):
        """<168h → does not fire."""
        data = {
            "server_rows": [
                {"device_name": "Switch-01", "device_ip": "10.0.0.1", "downtime_hours": 100},
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_long_term_offline(data)
        assert results == []

    def test_acknowledged_device_skipped(self):
        """Acknowledged device (>168h) → does not fire."""
        data = {
            "server_rows": [
                {
                    "device_name": "Switch-01", "device_ip": "10.0.0.1",
                    "downtime_hours": 300, "is_acknowledged": True,
                },
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_long_term_offline(data)
        assert results == []

    def test_tracked_rows_also_checked(self):
        """Rule checks both server_rows and tracked_rows."""
        data = {
            "server_rows": [],
            "tracked_rows": [
                {"device_name": "Laptop-01", "device_ip": "10.0.0.5", "downtime_hours": 200},
            ],
        }
        results = _engine()._rule_long_term_offline(data)
        assert len(results) == 1

    def test_empty_data(self):
        results = _engine()._rule_long_term_offline({})
        assert results == []

    def test_caps_at_10_results(self):
        """Should cap annotations at 10 to avoid flooding."""
        rows = [
            {"device_name": f"Dev-{i}", "device_ip": f"10.0.0.{i}", "downtime_hours": 200}
            for i in range(15)
        ]
        data = {"server_rows": rows, "tracked_rows": []}
        results = _engine()._rule_long_term_offline(data)
        assert len(results) <= 10


# ── Rule 6: Rogue/unknown device ─────────────────────────────────────────────

class TestRuleRogueDevice:

    def test_fires_for_unknown_type(self):
        """Device type 'unknown' → fires."""
        data = {
            "new_devices": [
                {"device_name": "NewDevice", "device_ip": "10.0.0.99", "device_type": "unknown"},
            ]
        }
        results = _engine()._rule_rogue_device(data)
        assert len(results) == 1
        assert results[0]["rule"] == "rogue_device"
        assert results[0]["severity"] == "warning"
        assert "UNKNOWN DEVICE" in results[0]["text"]

    def test_fires_for_empty_type(self):
        """Empty device_type string also counts as unknown."""
        data = {
            "new_devices": [
                {"device_name": "NewDevice", "device_ip": "10.0.0.99", "device_type": ""},
            ]
        }
        results = _engine()._rule_rogue_device(data)
        assert len(results) == 1

    def test_does_not_fire_for_known_type(self):
        """Known device type → does not fire."""
        data = {
            "new_devices": [
                {"device_name": "NewSwitch", "device_ip": "10.0.0.100", "device_type": "switch"},
            ]
        }
        results = _engine()._rule_rogue_device(data)
        assert results == []

    def test_empty_new_devices(self):
        results = _engine()._rule_rogue_device({})
        assert results == []

    def test_pluralization(self):
        """Multiple unknown devices → pluralized text."""
        data = {
            "new_devices": [
                {"device_name": "Dev1", "device_ip": "10.0.0.1", "device_type": "unknown"},
                {"device_name": "Dev2", "device_ip": "10.0.0.2", "device_type": "unknown"},
            ]
        }
        results = _engine()._rule_rogue_device(data)
        assert len(results) == 1
        assert "UNKNOWN DEVICES" in results[0]["text"]
        assert "2 new device(s)" in results[0]["text"]


# ── Rule 7: Zero uptime ──────────────────────────────────────────────────────

class TestRuleZeroUptime:

    def test_fires_for_zero_uptime(self):
        """0.00% uptime → fires."""
        data = {
            "server_rows": [
                {"device_name": "Switch-Off", "device_ip": "10.0.0.1", "uptime_pct": 0.0},
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_zero_uptime(data)
        assert len(results) == 1
        assert results[0]["rule"] == "zero_uptime"
        assert results[0]["severity"] == "critical"
        assert "Persistently Offline" in results[0]["text"]

    def test_does_not_fire_for_5_pct(self):
        """5% uptime → does not fire (only zero triggers)."""
        data = {
            "server_rows": [
                {"device_name": "Switch-Low", "device_ip": "10.0.0.1", "uptime_pct": 5.0},
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_zero_uptime(data)
        assert results == []

    def test_does_not_fire_for_none_uptime(self):
        """None uptime → does not fire."""
        data = {
            "server_rows": [
                {"device_name": "Switch-None", "device_ip": "10.0.0.1", "uptime_pct": None},
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_zero_uptime(data)
        assert results == []

    def test_multiple_zero_devices_combined(self):
        """Multiple zero-uptime devices → single annotation listing all."""
        data = {
            "server_rows": [
                {"device_name": "Dev1", "device_ip": "10.0.0.1", "uptime_pct": 0.0},
                {"device_name": "Dev2", "device_ip": "10.0.0.2", "uptime_pct": 0.0},
            ],
            "tracked_rows": [],
        }
        results = _engine()._rule_zero_uptime(data)
        assert len(results) == 1
        assert "2 device(s)" in results[0]["text"]

    def test_empty_data(self):
        results = _engine()._rule_zero_uptime({})
        assert results == []


# ── annotate() dispatch ──────────────────────────────────────────────────────

class TestAnnotate:

    def test_results_sorted_by_severity_critical_first(self):
        """Critical annotations must come before warning annotations."""
        data = {
            "server_rows": [
                {"device_name": "Dev1", "device_ip": "10.0.0.1", "uptime_pct": 0.0},
            ],
            "tracked_rows": [],
            "new_devices": [
                {"device_name": "Rogue", "device_ip": "10.0.0.99", "device_type": "unknown"},
            ],
        }
        results = _engine().annotate("executive", data)
        # Find the severities present
        severities = [r["severity"] for r in results]
        # All criticals should come before all warnings
        if "critical" in severities and "warning" in severities:
            last_critical = max(i for i, s in enumerate(severities) if s == "critical")
            first_warning = min(i for i, s in enumerate(severities) if s == "warning")
            assert last_critical < first_warning

    def test_empty_data_returns_empty(self):
        results = _engine().annotate("executive", {})
        assert results == []

    def test_all_rules_execute_without_error(self):
        """annotate() should never raise even with random data shapes."""
        data = {
            "server_rows": [{"device_ip": "10.0.0.1"}],
            "tracked_rows": [],
        }
        results = _engine().annotate("executive", data)
        assert isinstance(results, list)
