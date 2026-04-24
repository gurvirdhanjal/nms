import asyncio

import services.network_scanner as network_scanner_module


def _build_scanner(monkeypatch):
    monkeypatch.setattr(network_scanner_module, "MacLookup", lambda: object())
    return network_scanner_module.NetworkScanner()


def test_parse_windows_ping_success(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    parsed = scanner._parse_system_ping_result(
        stdout=(
            "Pinging 172.16.2.79 with 32 bytes of data:\n"
            "Reply from 172.16.2.79: bytes=32 time=124ms TTL=64\n\n"
            "Ping statistics for 172.16.2.79:\n"
            "    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),\n"
        ),
        stderr="",
        returncode=0,
        is_windows=True,
    )

    assert parsed == (0.124, "Reply received")


def test_parse_windows_ping_timeout(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    parsed = scanner._parse_system_ping_result(
        stdout=(
            "Pinging 172.16.2.79 with 32 bytes of data:\n"
            "Request timed out.\n\n"
            "Ping statistics for 172.16.2.79:\n"
            "    Packets: Sent = 1, Received = 0, Lost = 1 (100% loss),\n"
        ),
        stderr="",
        returncode=1,
        is_windows=True,
    )

    assert parsed == (None, "Request timed out")


def test_parse_linux_ping_no_reply(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    parsed = scanner._parse_system_ping_result(
        stdout=(
            "--- 172.16.2.79 ping statistics ---\n"
            "1 packets transmitted, 0 received, 100% packet loss, time 0ms\n"
        ),
        stderr="",
        returncode=1,
        is_windows=False,
    )

    assert parsed == (None, "No reply")


def test_ping_device_uses_parsed_system_ping_latency(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    async def raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    async def fake_system_ping(ip, timeout, is_windows):
        assert ip == "172.16.2.79"
        assert timeout == 2
        assert is_windows is True
        return 0.12471, "Reply received"

    monkeypatch.setattr(network_scanner_module.aioping, "ping", raise_timeout)
    monkeypatch.setattr(scanner, "_ping_system", fake_system_ping)
    monkeypatch.setattr(scanner, "_ping_for_ttl", lambda ip: 64)
    monkeypatch.setattr(network_scanner_module.platform, "system", lambda: "Windows")

    status, latency, packet_loss, jitter, ttl, detail, *_ = asyncio.run(
        scanner.ping_device(
            "172.16.2.79",
            timeout=2,
            count=1,
        )
    )

    assert status == "Online"
    assert latency == 124.71
    assert packet_loss == 0.0
    assert jitter == 0.0
    assert ttl == 64
    assert detail == "Reply received"


def test_ping_device_returns_timeout_detail_when_host_does_not_reply(monkeypatch):
    scanner = _build_scanner(monkeypatch)

    async def raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    async def fake_system_ping(ip, timeout, is_windows):
        return None, "Request timed out"

    monkeypatch.setattr(network_scanner_module.aioping, "ping", raise_timeout)
    monkeypatch.setattr(scanner, "_ping_system", fake_system_ping)
    monkeypatch.setattr(network_scanner_module.platform, "system", lambda: "Windows")

    status, latency, packet_loss, jitter, ttl, detail, *_ = asyncio.run(
        scanner.ping_device(
            "172.16.2.79",
            timeout=2,
            count=1,
        )
    )

    assert status == "Offline"
    assert latency is None
    assert packet_loss == 100.0
    assert jitter is None
    assert ttl is None
    assert detail == "Request timed out"
