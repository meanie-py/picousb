import argparse
import asyncio
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import probe_udp  # noqa: E402
from pico_devbox.protocol import DevBoxError, Message, Op  # noqa: E402


@pytest.mark.parametrize("tcp_bytes", [0, 16384])
async def test_paced_run_synthetic_transport(tmp_path, monkeypatch, capsys, tcp_bytes):
    config = tmp_path / "synthetic-config"
    config.write_text(
        "WIFI_SSID=synthetic\nWIFI_PASSWORD=synthetic\nDEVICE_TOKEN=synthetic-token-1234\n"
    )
    box = AsyncMock()
    box.__aenter__.return_value = box
    box.session = 42
    box.ws = SimpleNamespace(remote_address=("127.0.0.1", 8080))
    calls = []

    async def request(op, *args, **kwargs):
        calls.append(op)
        if op == Op.STATUS:
            assert kwargs["payload"] == b"P" * tcp_bytes
            raise DevBoxError(8)
        assert op == Op.UDP_PROBE
        return Message(op | 128, 1, a=1, b=8081, payload=struct.pack("<5I", 0, 0, 0, 0, 0))

    box.request.side_effect = request
    monkeypatch.setattr(probe_udp, "DevBox", lambda *args, **kwargs: box)
    loop = asyncio.get_running_loop()

    async def endpoint(factory, **kwargs):
        probe = factory()
        transport = SimpleNamespace(
            sendto=lambda data: loop.call_soon(probe.datagram_received, data, None),
            close=lambda: None,
        )
        return transport, probe

    monkeypatch.setattr(loop, "create_datagram_endpoint", endpoint)
    real_sleep = asyncio.sleep

    async def short_grace(delay):
        await real_sleep(0.01 if delay == 2 else delay)

    monkeypatch.setattr(probe_udp.asyncio, "sleep", short_grace)
    await probe_udp.run(
        argparse.Namespace(
            config=config, url="ws://fake/ws", seconds=0.03, pps=100, tcp_bytes=tcp_bytes
        )
    )
    output = capsys.readouterr().out
    assert "synthetic" not in output
    result = json.loads(output)
    assert result["sent"] > 0 and result["received"] == result["sent"]
    assert result["missing_after_grace"] == 0
    assert all(op in (Op.STATUS, Op.UDP_PROBE) for op in calls)


def test_loss_duplicates_reordering_and_validation(monkeypatch):
    p = probe_udp.Probe(42, b"s" * 16)
    p.sent = {1: 0.0, 2: 0.0, 3: 0.0}
    monkeypatch.setattr(probe_udp.time, "perf_counter", lambda: 0.1)
    p.datagram_received(p.packet(2), None)
    p.datagram_received(p.packet(2), None)
    p.datagram_received(p.packet(1), None)
    p.datagram_received(p.packet(4), None)
    p.datagram_received(b"short", None)
    bad = bytearray(p.packet(3))
    bad[8] ^= 1
    p.datagram_received(bytes(bad), None)
    assert p.received == {1, 2} and p.duplicates == 1 and p.reordered == 1
    assert p.invalid == 3 and p.rtt == [0.1, 0.1]


def test_errors_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["probe_udp"])
    monkeypatch.setattr(probe_udp, "run", AsyncMock(side_effect=ValueError("synthetic-secret")))
    assert probe_udp.main() == 1
    assert "synthetic-secret" not in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--seconds", "46"], ["--pps", "401"], ["--pps", "0"]])
def test_bounded_probe(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["probe_udp", *args])
    with pytest.raises(SystemExit):
        probe_udp.main()
