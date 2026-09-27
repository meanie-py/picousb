import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from pico_devbox import DevBoxError, cli
from pico_devbox.serial import SerialConsole, console_host

TOKEN = "synthetic-test-token"


@asynccontextmanager
async def fake_console(
    token: str = TOKEN, output: bytes = b"", received: list[bytes] | None = None
) -> AsyncIterator[int]:
    """Minimal device side of the console stream: hello line, OK, then relay."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        hello = await reader.readline()
        expected = b"PICOSERIAL 1" + (b" " + token.encode() if token else b"") + b"\n"
        if hello != expected:
            writer.close()
            return
        writer.write(b"OK\n" + output)
        await writer.drain()
        data = await reader.read(100)
        if received is not None:
            received.append(data)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def test_console_host() -> None:
    assert console_host("ws://picousb:8080/ws") == "picousb"
    with pytest.raises(ValueError):
        console_host("not a url")


async def test_console_round_trip() -> None:
    received: list[bytes] = []
    async with fake_console(output=b"UEFI> ", received=received) as port:
        async with SerialConsole("127.0.0.1", TOKEN, port=port) as console:
            assert await console.read() == b"UEFI> "
            await console.write(b"ls\r")
            with pytest.raises(ConnectionError):
                while True:
                    await console.read()
    assert received == [b"ls\r"]


async def test_console_without_token() -> None:
    async with fake_console(token="", output=b"x") as port:
        async with SerialConsole("127.0.0.1", "", port=port) as console:
            assert await console.read() == b"x"


async def test_console_rejects_wrong_token() -> None:
    async with fake_console() as port:
        with pytest.raises(DevBoxError) as error:
            await SerialConsole("127.0.0.1", "wrong-synthetic-token", port=port).connect()
    assert error.value.code == 2


async def test_console_not_connected() -> None:
    console = SerialConsole("127.0.0.1")
    with pytest.raises(ConnectionError):
        await console.read()
    with pytest.raises(ConnectionError):
        await console.write(b"x")
    await console.close()


async def test_cli_serial_sends_and_prints(
    monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    received: list[bytes] = []
    monkeypatch.setenv("PICO_DEVICE_TOKEN", TOKEN)
    monkeypatch.delenv("PICO_DEVICE_TOKEN_FILE", raising=False)
    async with fake_console(output=b"boot log", received=received) as port:
        args = argparse.Namespace(
            url="ws://127.0.0.1:8080/ws",
            command="serial",
            port=port,
            send="ifconfig -l",
            raw=False,
            seconds=0.5,
        )
        with pytest.raises(ConnectionError):
            await cli.run(args)
    assert received == [b"ifconfig -l\r"]
    assert capsysbinary.readouterr().out == b"boot log"


async def test_cli_serial_stops_after_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PICO_DEVICE_TOKEN", TOKEN)
    monkeypatch.delenv("PICO_DEVICE_TOKEN_FILE", raising=False)

    async def quiet(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        writer.write(b"OK\n")
        await writer.drain()
        await asyncio.sleep(5)
        writer.close()

    server = await asyncio.start_server(quiet, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        args = argparse.Namespace(
            url="ws://127.0.0.1:8080/ws",
            command="serial",
            port=port,
            send="\x1b",
            raw=True,
            seconds=0.2,
        )
        await asyncio.wait_for(cli.run(args), 3)
    finally:
        server.close()


async def test_cli_bootsel(monkeypatch: pytest.MonkeyPatch) -> None:
    box = AsyncMock()
    box.__aenter__.return_value = box
    monkeypatch.setenv("PICO_DEVICE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "DevBox", lambda *a, **kw: box)
    await cli.run(argparse.Namespace(url="ws://synthetic", command="bootsel"))
    box.reboot_to_bootsel.assert_awaited_once()
