import argparse
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pico_devbox import DevBox, DevBoxError, cli
from pico_devbox.protocol import RESPONSE, Message, Op
from websockets.asyncio.server import ServerConnection, serve


@pytest.mark.parametrize(
    "args",
    [
        {"command": "connect"},
        {"command": "status"},
        {"command": "type", "text": "hi"},
        {"command": "key", "keys": ["CTRL", "ALT", "DELETE"]},
        {"command": "mouse", "action": "move", "x": 50, "y": -20},
        {"command": "mouse", "action": "click", "button": "left"},
        {"command": "mouse", "action": "scroll", "amount": 1},
    ],
)
async def test_cli_uses_library(args: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    box = AsyncMock()
    box.__aenter__.return_value = box
    box.status.return_value = {"network": "connected"}
    monkeypatch.setenv("PICO_DEVICE_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(cli, "DevBox", lambda *a, **kw: box)
    await cli.run(argparse.Namespace(url="ws://synthetic", **args))
    box.__aenter__.assert_awaited_once()
    box.__aexit__.assert_awaited_once()


def test_cli_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["pico-devbox", "status"])
    run = AsyncMock()
    monkeypatch.setattr(cli, "run", run)
    cli.main()
    run.assert_awaited_once()


def test_cli_error_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["pico-devbox", "status"])
    monkeypatch.setattr(cli, "run", AsyncMock(side_effect=RuntimeError("SYNTHETIC-SECRET")))
    with pytest.raises(SystemExit):
        cli.main()
    assert "SYNTHETIC-SECRET" not in capsys.readouterr().err


async def test_validation_and_disconnected() -> None:
    box = DevBox("ws://unused")
    with pytest.raises(DevBoxError):
        await box.status()
    for call in (
        box.keyboard.press("NO_SUCH_KEY"),
        box.keyboard.press("A", "ENTER"),
        box.keyboard.type("\x01"),
        box.mouse.move(100000, 0),
        box.mouse.click("other"),
        box.mouse.scroll(128),
    ):
        with pytest.raises(ValueError):
            await call
    await box.close()


async def test_busy_attach_detach_and_storage_flush(tmp_path: Path) -> None:
    finished = asyncio.Event()
    replies: list[Message] = []

    async def server(ws: ServerConnection) -> None:
        async for raw in ws:
            m = Message.decode(raw)
            if m.op & RESPONSE:
                replies.append(m)
                if len(replies) == 3:
                    finished.set()
                continue
            await ws.send(Message(m.op | RESPONSE, m.id, 9).encode())
            if m.op == Op.ATTACH:
                for i, op in enumerate((Op.WRITE, Op.FLUSH, Op.EJECT)):
                    await ws.send(
                        Message(
                            op, 0x80000001 + i, 9, payload=b"bad" if op == Op.WRITE else b""
                        ).encode()
                    )

    async with serve(server, "127.0.0.1", 0) as s:
        async with DevBox(f"ws://127.0.0.1:{s.sockets[0].getsockname()[1]}") as box:
            await box.storage.serve_directory(tmp_path)
            with pytest.raises(DevBoxError):
                await box.storage.serve_directory(tmp_path)
            await asyncio.wait_for(finished.wait(), 3)
            assert box.storage.ejected.is_set()
            with pytest.raises(DevBoxError):
                await box.connect()
            await box.storage.detach()
            assert box.storage.disk is None
    assert [r.status for r in replies] == [6, 0, 0]


async def test_server_disconnect_propagates() -> None:
    async def server(ws: ServerConnection) -> None:
        m = Message.decode(await ws.recv())
        await ws.send(Message(0x81, m.id, 1).encode())
        await ws.recv()
        await ws.close()

    async with serve(server, "127.0.0.1", 0) as s:
        async with DevBox(f"ws://127.0.0.1:{s.sockets[0].getsockname()[1]}") as box:
            with pytest.raises(DevBoxError):
                await box.keyboard.key_down("A")
