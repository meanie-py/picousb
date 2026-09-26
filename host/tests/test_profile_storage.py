import argparse
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pico_devbox.protocol import DevBoxError, Op

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import profile_storage  # noqa: E402


@pytest.mark.parametrize("response_code", [8, 6])
async def test_network_probe_has_no_media_or_hid_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, response_code: int
) -> None:
    config = tmp_path / "synthetic-config"
    config.write_text(
        "WIFI_SSID=synthetic\nWIFI_PASSWORD=synthetic\nDEVICE_TOKEN=synthetic-token-1234\n"
    )
    box = AsyncMock()
    box.__aenter__.return_value = box
    box.request.side_effect = DevBoxError(response_code)
    monkeypatch.setattr(profile_storage, "DevBox", lambda *args, **kwargs: box)
    args = argparse.Namespace(config=config, url="ws://fake/ws", mode="network")
    if response_code == 8:
        await profile_storage.device(args)
        assert box.request.await_count == 300
        assert {len(call.kwargs["payload"]) for call in box.request.await_args_list} == {
            512,
            4096,
            16384,
        }
    else:
        with pytest.raises(DevBoxError):
            await profile_storage.device(args)
        assert box.request.await_count == 1
    assert all(call.args[0] == Op.STATUS for call in box.request.await_args_list)
    box.storage.serve_image.assert_not_awaited()
    box.keyboard.key_down.assert_not_awaited()
    assert "synthetic" not in capsys.readouterr().out


def test_profile_failure_does_not_log_exception(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(sys, "argv", ["profile_storage", "local"])
    monkeypatch.setattr(profile_storage, "local", AsyncMock(side_effect=ValueError("secret-value")))
    assert profile_storage.main() == 1
    assert "secret-value" not in capsys.readouterr().out
