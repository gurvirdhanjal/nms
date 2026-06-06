"""Unit tests for classify_connection_type_from_interfaces (no DB required)."""
import pytest
from services.snmp_service import classify_connection_type_from_interfaces


def test_wifi_by_iana_type():
    assert classify_connection_type_from_interfaces([{"if_type": 71, "name": "eth0"}]) == "wifi"


def test_lan_by_iana_type():
    assert classify_connection_type_from_interfaces([{"if_type": 6, "name": "iface0"}]) == "lan"


def test_wifi_by_name_heuristic():
    assert classify_connection_type_from_interfaces([{"if_type": 1, "name": "wlan0"}]) == "wifi"


def test_wifi_by_name_heuristic_wireless():
    assert classify_connection_type_from_interfaces(
        [{"if_type": None, "name": "Wireless Network Adapter"}]
    ) == "wifi"


def test_lan_by_name_heuristic():
    assert classify_connection_type_from_interfaces([{"if_type": 1, "name": "eth0"}]) == "lan"


def test_lan_by_name_heuristic_gig():
    assert classify_connection_type_from_interfaces(
        [{"if_type": None, "name": "GigabitEthernet0/1"}]
    ) == "lan"


def test_wifi_wins_over_lan_mixed():
    ifaces = [{"if_type": 6, "name": "eth0"}, {"if_type": 71, "name": "wlan0"}]
    assert classify_connection_type_from_interfaces(ifaces) == "wifi"


def test_empty_list():
    assert classify_connection_type_from_interfaces([]) == "unknown"


def test_no_match_loopback():
    assert classify_connection_type_from_interfaces([{"if_type": 1, "name": "lo"}]) == "unknown"


def test_missing_keys_does_not_raise():
    result = classify_connection_type_from_interfaces([{}])
    assert result in ("wifi", "lan", "unknown")
