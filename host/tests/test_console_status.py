import struct
from collections.abc import AsyncIterator

import pytest
from pico_devbox import DevBox, DevBoxError
from pico_devbox.protocol import RESPONSE, Message, Op
from pico_devbox.usb_status import ConsoleStatus
from websockets.asyncio.server import ServerConnection, serve

TOKEN = "synthetic-test-token"
USB = struct.pack(
    "<39I",
    1,
    1,
    0,
    0,
    1,
    0,
    100,
    1,
    1,
    *([0] * 7 + [0x81, 0, 0]),
    *([0] * 7 + [0x82, 0, 0]),
    *([0] * 7 + [0x84, 0, 0]),
)
CONSOLE = struct.pack("<8I", 1, 1, 1, 1, 4235, 1, 7, 3)


def test_decode() -> None:
    c = ConsoleStatus.decode(CONSOLE)
    assert c == ConsoleStatus(True, True, 1, 4235, 1, 7, 3)
    for bad in (CONSOLE[:-1], struct.pack("<8I", 2, 0, 0, 0, 0, 0, 0, 0)):
        with pytest.raises(DevBoxError):
            ConsoleStatus.decode(bad)
    with pytest.raises(DevBoxError):
        ConsoleStatus.decode(struct.pack("<8I", 1, 2, 0, 0, 0, 0, 0, 0))


async def device(console_status: int) -> AsyncIterator[str]:
    async def handle(ws: ServerConnection) -> None:
        async for raw in ws:
            m = Message.decode(raw)
            status, payload = 0, b""
            if m.op == Op.USB_STATUS:
                payload = USB
            elif m.op == Op.CONSOLE_STATUS:
                status = console_status
                payload = CONSOLE if not status else b""
            await ws.send(
                Message(m.op | RESPONSE, m.id, 42, status=status, payload=payload).encode()
            )

    async with serve(handle, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws"


async def test_status_includes_console() -> None:
    async for uri in device(0):
        async with DevBox(uri, TOKEN) as box:
            status = await box.status()
            assert status["console"] == {
                "target_powered": True,
                "client_attached": True,
                "rx_level": 1,
                "rx_bytes": 4235,
                "rx_errors": 1,
                "dropped": 7,
                "tx_queued": 3,
            }


async def test_status_tolerates_firmware_without_console() -> None:
    async for uri in device(1):  # older firmware: protocol error for op 9
        async with DevBox(uri, TOKEN) as box:
            assert (await box.status())["console"] is None
