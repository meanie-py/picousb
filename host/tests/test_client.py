import asyncio
import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pico_devbox import DevBox, DevBoxError
from pico_devbox.protocol import RESPONSE, Message, Op
from websockets.asyncio.server import ServerConnection, serve

NATIVE = Path(__file__).resolve().parents[2] / "build/native/fake_pico"


@pytest.fixture
async def fake() -> AsyncIterator[tuple[str, list[Message]]]:
    messages: list[Message] = []

    async def handle(ws: ServerConnection) -> None:
        async for raw in ws:
            m = Message.decode(raw)
            messages.append(m)
            status = 0 if m.op != Op.AUTH or m.payload == b"synthetic-test-token" else 2
            payload = b""
            if Op.KEY_DOWN <= m.op <= Op.KEY_TEXT or Op.MOUSE_MOVE <= m.op <= Op.MOUSE_RELEASE:
                payload = bytes(16)
            if m.op == Op.USB_STATUS:
                payload = struct.pack(
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
            await ws.send(
                Message(m.op | RESPONSE, m.id, 42, status=status, payload=payload).encode()
            )

    async with serve(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}/ws", messages


async def test_hid_lifecycle(fake: tuple[str, list[Message]]) -> None:
    uri, messages = fake
    async with DevBox(uri, "synthetic-test-token") as box:
        await box.keyboard.press("DELETE", "CTRL", "ALT")
        await box.keyboard.type("Hello!\n")
        await box.keyboard.key_down("SHIFT")
        await box.keyboard.key_up("SHIFT")
        await box.keyboard.release_all()
        await box.mouse.move(300, -128)
        await box.mouse.click("middle")
        await box.mouse.scroll(-1)
        await box.mouse.release_all()
        assert (await box.status())["network"] == "connected"
        await box.reconnect()
        assert box.session == 42
    press = next(m for m in messages if m.op == Op.KEY_PRESS)
    assert (press.a, press.b) == (76, 5)
    moves = [m for m in messages if m.op == Op.MOUSE_MOVE]
    assert len(moves) == 3
    assert sum(m.a for m in moves) == 300


async def test_bad_auth(fake: tuple[str, list[Message]]) -> None:
    box = DevBox(fake[0], "wrong")
    with pytest.raises(DevBoxError) as exc:
        await box.connect()
    assert exc.value.code == 2
    assert box.ws is None


async def test_concurrent_requests(fake: tuple[str, list[Message]]) -> None:
    async with DevBox(fake[0], "synthetic-test-token") as box:
        await asyncio.gather(*(box.status() for _ in range(20)))
    ids = [m.id for m in fake[1]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "invalid", [b"garbage", "text", Message(0x82, 2, 99).encode(), b"PDB0" + bytes(24)]
)
async def test_malformed_server(invalid: bytes | str) -> None:
    async def handle(ws: ServerConnection) -> None:
        m = Message.decode(await ws.recv())
        await ws.send(Message(0x81, m.id, 42).encode())
        await ws.recv()
        await ws.send(invalid)

    async with serve(handle, "127.0.0.1", 0) as server:
        async with DevBox(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}") as box:
            with pytest.raises(DevBoxError):
                await box.status()


async def test_timeout() -> None:
    async def handle(ws: ServerConnection) -> None:
        await ws.recv()
        await ws.wait_closed()

    async with serve(handle, "127.0.0.1", 0) as server:
        box = DevBox(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", timeout=0.05)
        with pytest.raises(TimeoutError):
            await box.connect()
        assert box.ws is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_uncertain_command_closes_session(cancel: bool) -> None:
    command_received = asyncio.Event()
    disconnected = asyncio.Event()

    async def handle(ws: ServerConnection) -> None:
        auth = Message.decode(await ws.recv())
        await ws.send(Message(Op.AUTH | RESPONSE, auth.id, 42).encode())
        await ws.recv()  # Applied command, but its acknowledgment never arrives.
        command_received.set()
        await ws.wait_closed()
        disconnected.set()

    async with serve(handle, "127.0.0.1", 0) as server:
        async with DevBox(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", timeout=0.1
        ) as box:
            command = asyncio.create_task(box.keyboard.key_down("SHIFT"))
            await asyncio.wait_for(command_received.wait(), 2)
            if cancel:
                command.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await command
            await asyncio.wait_for(disconnected.wait(), 0.5)
            assert not box._pending


async def test_request_deadline_includes_send_lock(fake: tuple[str, list[Message]]) -> None:
    async with DevBox(fake[0], "synthetic-test-token", timeout=0.05) as box:
        async with box._send_lock:
            request = asyncio.create_task(box.status())
            try:
                done, _ = await asyncio.wait({request}, timeout=0.5)
                assert request in done, "request deadline did not cover the send phase"
                with pytest.raises(TimeoutError):
                    await request
                assert not box._pending
            finally:
                if not request.done():
                    request.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await request


async def test_storage_end_to_end(tmp_path: Path) -> None:
    finished = asyncio.Event()
    responses: list[Message] = []

    async def handle(ws: ServerConnection) -> None:
        m = Message.decode(await ws.recv())
        await ws.send(Message(0x81, m.id, 42).encode())
        m = Message.decode(await ws.recv())
        assert m.op == Op.ATTACH
        await ws.send(Message(0x9E, m.id, 42).encode())
        # Concurrent requests exercise the bounded serialization worker.
        for i in range(3):
            await ws.send(Message(Op.READ, 0x80000001 + i, 42, i, 1).encode())
        for _ in range(3):
            responses.append(Message.decode(await ws.recv()))
        await ws.send(Message(Op.WRITE, 0x80000004, 42, 65536, 1, bytes(512)).encode())
        responses.append(Message.decode(await ws.recv()))
        finished.set()
        await ws.wait_closed()

    async with serve(handle, "127.0.0.1", 0) as server:
        async with DevBox(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}") as box:
            await box.storage.serve_directory(tmp_path)
            await asyncio.wait_for(finished.wait(), 3)
    assert responses[0].payload[510:] == b"\x55\xaa"
    assert all(len(r.payload) == 512 and not r.status for r in responses[:3])
    assert responses[-1].status == 6


@pytest.mark.skipif(not NATIVE.exists(), reason="build native fake_pico first")
async def test_real_firmware_core_through_websocket() -> None:
    async def handle(ws: ServerConnection) -> None:
        proc = await asyncio.create_subprocess_exec(
            str(NATIVE), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
        )
        assert proc.stdin and proc.stdout
        try:
            async for raw in ws:
                assert isinstance(raw, bytes)
                proc.stdin.write(struct.pack("<H", len(raw)) + raw)
                await proc.stdin.drain()
                header = await proc.stdout.readexactly(2)
                payload = await proc.stdout.readexactly(struct.unpack("<H", header)[0])
                await ws.send(payload)
        finally:
            proc.stdin.close()
            await proc.wait()

    async with serve(handle, "127.0.0.1", 0) as server:
        async with DevBox(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", "synthetic-test-token"
        ) as box:
            await box.keyboard.type("Ab!\n")
            await box.keyboard.press("DELETE", "CTRL", "ALT")
            await box.mouse.move(100, -20)
            await box.mouse.click()
            assert (await box.status())["media"] is False
