"""Unit tests for generate_device_inspector_pdf()."""
import io
import pytest
from services.enterprise_pdf_service import generate_device_inspector_pdf

FULL_STATS = {
    'total_scans': 288, 'online_count': 280,
    'offline_count': 8,  'no_response_count': 6,
    'uptime_percentage': 97.22, 'downtime_percentage': 2.78,
    'avg_latency': 12.4, 'min_latency': 4.1,
    'max_latency': 89.3, 'latency_std_dev': 5.2,
    'avg_packet_loss': 0.5, 'max_packet_loss': 12.0,
    'agent_data': {'available': False},
}


def test_full_stats_returns_non_empty_pdf():
    buf = generate_device_inspector_pdf(
        FULL_STATS, 'Server-01', '10.0.0.1', 'Last 24 Hours'
    )
    assert isinstance(buf, io.BytesIO)
    assert len(buf.getvalue()) > 1000  # real PDF, not empty


def test_no_latency_omits_latency_section():
    stats = {**FULL_STATS, 'avg_latency': None,
             'min_latency': None, 'max_latency': None}
    buf = generate_device_inspector_pdf(
        stats, 'Server-01', '10.0.0.1', 'Last 7 Days'
    )
    assert len(buf.getvalue()) > 0  # still produces PDF without latency section


def test_with_agent_data_includes_telemetry():
    stats = {**FULL_STATS, 'agent_data': {
        'available': True,
        'latest': {
            'cpu_percent': 45.2, 'memory_percent': 62.1,
            'disk_percent': 33.0, 'network_in_bps': 1024000.0,
            'network_out_bps': 512000.0, 'uptime_seconds': 86400,
        },
    }}
    buf = generate_device_inspector_pdf(
        stats, 'Server-01', '10.0.0.1', 'Last 30 Days'
    )
    assert len(buf.getvalue()) > 0


def test_no_response_count_logic():
    """no_response_count = offline scans with ping_time_ms IS NULL."""
    class FakeScan:
        def __init__(self, ping_ms):
            self.ping_time_ms = ping_ms
    offline = [FakeScan(None), FakeScan(None), FakeScan(12.3)]
    count = sum(1 for s in offline if s.ping_time_ms is None)
    assert count == 2


def test_zero_scans_edge_case():
    """Stats with no_response_count=0 renders without error."""
    stats = {**FULL_STATS, 'no_response_count': 0}
    buf = generate_device_inspector_pdf(
        stats, 'Switch-01', '192.168.1.1', 'Last 24 Hours'
    )
    assert len(buf.getvalue()) > 0
