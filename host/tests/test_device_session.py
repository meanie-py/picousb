import argparse
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import device_session  # noqa: E402


async def test_opaque_authentication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    config = tmp_path / "synthetic-config"
    config.write_text(
        "WIFI_SSID=synthetic\nWIFI_PASSWORD=synthetic-password\nDEVICE_TOKEN=synthetic-token-1234\n"
    )
    box = AsyncMock()
    box.__aenter__.return_value = box

    def factory(url, token, *, udp_reads=False):
        assert token == "synthetic-token-1234"
        assert not udp_reads
        return box

    monkeypatch.setattr(device_session, "DevBox", factory)
    await device_session.run(argparse.Namespace(config=config, url="ws://fake/ws", serve=None))
    box.status.assert_awaited_once()
    assert "synthetic" not in capsys.readouterr().out


def test_errors_never_echo_secrets(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr("sys.argv", ["device_session"])
    monkeypatch.setattr(
        device_session, "run", AsyncMock(side_effect=ValueError("synthetic-secret"))
    )
    assert device_session.main() == 1
    assert "synthetic-secret" not in capsys.readouterr().out
