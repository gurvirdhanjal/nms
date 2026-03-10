from types import SimpleNamespace
import socket

import services.network_scanner as network_scanner_module


def _fake_socket_factory(primary_ip=None):
    class _FakeSocket:
        def settimeout(self, timeout):
            return None

        def connect(self, target):
            if primary_ip is None:
                raise OSError("no route")

        def getsockname(self):
            return (primary_ip, 0)

        def close(self):
            return None

    return _FakeSocket


def _addr(ip_address, netmask):
    return SimpleNamespace(
        family=socket.AF_INET,
        address=ip_address,
        netmask=netmask,
    )


def _stats(isup=True):
    return SimpleNamespace(isup=isup)


def _build_scanner(monkeypatch):
    monkeypatch.setattr(network_scanner_module, "MacLookup", lambda: object())
    return network_scanner_module.NetworkScanner()


def test_get_local_ip_range_prefers_physical_private_interface_over_virtual_primary(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    monkeypatch.setattr(network_scanner_module.socket, "socket", _fake_socket_factory("172.28.192.1"))
    monkeypatch.setattr(network_scanner_module.psutil, "net_if_addrs", lambda: {
        "vEthernet (Default Switch)": [_addr("172.28.192.1", "255.255.240.0")],
        "Ethernet": [_addr("192.168.1.44", "255.255.255.0")],
    })
    monkeypatch.setattr(network_scanner_module.psutil, "net_if_stats", lambda: {
        "vEthernet (Default Switch)": _stats(True),
        "Ethernet": _stats(True),
    })

    assert scanner.get_local_ip_range() == "192.168.1.0/24"


def test_get_local_ip_range_ignores_link_local_when_private_lan_exists(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    monkeypatch.setattr(network_scanner_module.socket, "socket", _fake_socket_factory(None))
    monkeypatch.setattr(network_scanner_module.psutil, "net_if_addrs", lambda: {
        "Ethernet 2": [_addr("169.254.44.5", "255.255.0.0")],
        "Wi-Fi": [_addr("10.10.8.25", "255.255.255.0")],
    })
    monkeypatch.setattr(network_scanner_module.psutil, "net_if_stats", lambda: {
        "Ethernet 2": _stats(True),
        "Wi-Fi": _stats(True),
    })

    assert scanner.get_local_ip_range() == "10.10.8.0/24"
