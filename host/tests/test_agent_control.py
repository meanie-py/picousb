import asyncio
import json
import struct
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pico_devbox import DevBox, DevBoxError, cli
from pico_devbox.auth import cli_token, read_token_file
from pico_devbox.protocol import Message, Op
from pico_devbox.usb_status import USBStatus
from websockets.asyncio.server import ServerConnection, serve


def telemetry(*, mounted=1, epoch=1, completed=0, accepted=1):
    return struct.pack(
        "<39I",
        1,
        mounted,
        0,
        2,
        1,
        epoch,
        1000,
        200,
        900,
        accepted,
        accepted,
        completed,
        0 if completed else 0xFFFFFFFF,
        1,
        1,
        1,
        0x81,
        completed,
        0 if completed else 0xFFFFFFFF,
        *([0] * 7 + [0x82, 0, 0]),
        *([0] * 7 + [0x84, 0, 0]),
    )


async def test_drain_waits_for_completion():
    box = DevBox("ws://unused", timeout=0.2)
    box._hid_fence = (1, 1, 0, 0)
    box.usb_status = AsyncMock(
        side_effect=[USBStatus.decode(telemetry(completed=n)) for n in (0, 0, 1)]
    )
    await box.drain()
    assert box.usb_status.await_count == 3


async def test_drain_rejects_new_connection_with_same_epoch():
    box = DevBox("ws://unused")
    box._hid_fence = (1, 1, 0, 0)

    async def replaced_connection():
        box.ws = object()
        return USBStatus.decode(telemetry(completed=1))

    box.usb_status = replaced_connection
    with pytest.raises(DevBoxError) as error:
        await box.drain()
    assert error.value.code == 11


async def test_chunked_mouse_motion_disallows_mid_sequence_reconnect():
    box = DevBox("ws://unused", auto_reconnect=True)
    box.wait_ready = AsyncMock()
    box.request = AsyncMock(side_effect=[None, DevBoxError(5)])
    with pytest.raises(DevBoxError):
        await box.mouse.move(400, 0)
    assert box.wait_ready.await_count == 1
    assert box.request.await_count == 2
    assert not box._input_active


@pytest.mark.parametrize(
    "payload,code", [(telemetry(mounted=0), 9), (telemetry(epoch=2), 11), (telemetry(), 10)]
)
async def test_drain_diagnoses_failures(payload, code):
    box = DevBox("ws://unused", timeout=0.01)
    box._hid_fence = (1, 1, 0, 0)
    box.usb_status = AsyncMock(return_value=USBStatus.decode(payload))
    with pytest.raises(DevBoxError) as error:
        await box.drain()
    assert error.value.code == code


def test_status_validation():
    value = USBStatus.decode(telemetry())
    assert value.bus_resets == 2 and value.hid[0].last_completion_age_ms is None
    assert value.first_network_ready_ms == 900
    for invalid in (b"", telemetry()[:-1], b"\2" + telemetry()[1:]):
        with pytest.raises(DevBoxError):
            USBStatus.decode(invalid)


async def test_timed_keys_typing_pointer_raw():
    box = DevBox("ws://unused")
    box.request = AsyncMock()
    box.drain = AsyncMock()
    await box.keyboard.press("F1", hold_ms=1, repeat=2, interval_ms=1)
    assert [c.args[0] for c in box.request.await_args_list] == [Op.KEY_DOWN, Op.KEY_RELEASE] * 2
    box.request.reset_mock()
    await box.keyboard.type("ab", delay_ms=1)
    assert [c.kwargs["payload"] for c in box.request.await_args_list] == [b"a", b"b"]
    await box.pointer.click(32767, 123)
    assert box.request.await_args_list[-2].args == (Op.POINTER, 32767, 123, b"\1")
    assert box.request.await_args_list[-1].args == (Op.POINTER, 32767, 123, b"\0")
    await box.raw_report(0, bytes(8))
    with pytest.raises(ValueError):
        await box.raw_report(0, bytes(9))
    with pytest.raises(ValueError):
        await box.pointer.move(32768, 0)
    with pytest.raises(ValueError):
        await box.keyboard.press("F1", repeat=0)


async def test_cancel_held_key_releases():
    box = DevBox("ws://unused")
    box.request = AsyncMock()
    box.drain = AsyncMock()
    task = asyncio.create_task(box.keyboard.press("F1", hold_ms=1000))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert box.request.await_args_list[-1].args == (Op.KEY_RELEASE,)


def test_auth_file_and_no_prompt(tmp_path: Path, monkeypatch):
    token = tmp_path / "synthetic-token"
    token.write_text("synthetic-token-1234\n")
    monkeypatch.setenv("PICO_DEVICE_TOKEN_FILE", str(token))
    monkeypatch.setenv("PICO_DEVICE_TOKEN", "ignored")
    assert cli_token() == "synthetic-token-1234"
    assert DevBox("ws://unused", token_file=token)._token == "synthetic-token-1234"
    token.write_bytes(b"x" * 132)
    with pytest.raises(DevBoxError):
        read_token_file(token)
    monkeypatch.delenv("PICO_DEVICE_TOKEN_FILE")
    monkeypatch.delenv("PICO_DEVICE_TOKEN")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(DevBoxError):
        cli_token()


@pytest.mark.parametrize(
    "error,code,category",
    [
        (OSError("secret"), 3, "unreachable"),
        (DevBoxError(2), 4, "auth_failed"),
        (DevBoxError(9), 5, "usb_not_mounted"),
        (DevBoxError(10), 6, "host_not_polling"),
        (DevBoxError(11), 7, "usb_reset"),
        (ValueError("secret"), 1, "other"),
    ],
)
def test_json_errors(error, code, category, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["pico-devbox", "status"])
    monkeypatch.setattr(cli, "run", AsyncMock(side_effect=error))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == code
    assert json.loads(capsys.readouterr().err) == {
        "ok": False,
        "error": category,
        "exit_code": code,
    }


async def test_reconnect_does_not_replay_input():
    inputs = 0
    sessions = 0

    async def handler(ws: ServerConnection):
        nonlocal inputs, sessions
        sessions += 1
        async for raw in ws:
            m = Message.decode(raw)
            if m.op == Op.KEY_DOWN:
                inputs += 1
                await ws.close()
                return
            await ws.send(
                Message(
                    m.op | 128,
                    m.id,
                    sessions,
                    payload=telemetry(completed=1) if m.op == Op.USB_STATUS else b"",
                ).encode()
            )

    async with serve(handler, "127.0.0.1", 0) as server:
        box = DevBox(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", auto_reconnect=True)
        async with box:
            with pytest.raises(DevBoxError):
                await box.keyboard.key_down("F1")
            assert box.ws
            await box.ws.wait_closed()
            assert (await box.status())["network"] == "connected"
            assert inputs == 1 and sessions == 2
            assert box.storage.disk is None


async def test_wait_ready_timeout_and_auth():
    box = DevBox("ws://127.0.0.1:1")
    with pytest.raises(DevBoxError) as error:
        await box.wait_ready(timeout=0.05)
    assert error.value.code == 5
    box.connect = AsyncMock(side_effect=DevBoxError(2))
    with pytest.raises(DevBoxError) as error:
        await box.wait_ready(timeout=1)
    assert error.value.code == 2 and box.connect.await_count == 1
