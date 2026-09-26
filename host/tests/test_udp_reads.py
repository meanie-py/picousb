import asyncio
import zlib
from pathlib import Path

import pytest
from pico_devbox import DevBox, DevBoxError
from pico_devbox.protocol import RESPONSE, Message, Op
from pico_devbox.udp_reads import HEADER, UDPReads
from websockets.asyncio.server import ServerConnection, serve

COOKIE = b"synthetic-cookie"


class Receiver(asyncio.DatagramProtocol):
    def __init__(self, *, loss: bool = False) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.loss = loss
        self.seen: dict[int, int] = {}
        self.data: dict[int, bytes] = {}
        self.mask = 0
        self.dropped_ack = False
        self.done = asyncio.Event()
        self.cookie = COOKIE

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        magic, session, rid, cookie, size, index, crc = HEADER.unpack_from(data)
        assert magic == b"PDR1" and cookie == self.cookie
        assert len(data) <= 1060
        self.seen[index] = self.seen.get(index, 0) + 1
        if self.loss and index == 1 and self.seen[index] == 1:
            return
        self.data[index] = data[HEADER.size :]
        self.mask |= 1 << index
        full = (1 << ((size + 1023) // 1024)) - 1
        if self.mask == full:
            assert zlib.crc32(b"".join(self.data[i] for i in sorted(self.data))) == crc
            self.done.set()
            if self.loss and not self.dropped_ack:
                self.dropped_ack = True
                return
        assert self.transport
        self.transport.sendto(
            HEADER.pack(b"PDA1", session, rid, cookie, size, self.mask, crc), addr
        )


@pytest.mark.parametrize("size", [512, 1536, 16384])
@pytest.mark.parametrize("loss", [False, True])
async def test_transfer(size: int, loss: bool) -> None:
    loop = asyncio.get_running_loop()
    receiver = Receiver(loss=loss)
    server, _ = await loop.create_datagram_endpoint(lambda: receiver, local_addr=("127.0.0.1", 0))
    sender = UDPReads(42, COOKIE)
    client, _ = await loop.create_datagram_endpoint(
        lambda: sender, remote_addr=server.get_extra_info("sockname")
    )
    try:
        data = bytes(i % 251 for i in range(size))
        await sender.send(0x80000001, data)
        assert b"".join(receiver.data[i] for i in sorted(receiver.data)) == data
        assert sender.batches == 1
        if loss:
            assert sender.retries >= 1
    finally:
        client.close()
        server.close()


def test_invalid_and_reordered_acks() -> None:
    sender = UDPReads(42, COOKIE)
    sender.active = True
    sender.rid, sender.size, sender.crc, sender.full = 0x80000001, 2048, 123, 3
    fields = [b"PDA1", 42, 0x80000001, COOKIE, 2048, 1, 123]
    sender.datagram_received(b"junk", ("127.0.0.1", 1))
    for index, wrong in enumerate([b"BAD!", 43, 0x80000002, b"x" * 16, 512, 4, 999]):
        invalid = fields.copy()
        invalid[index] = wrong
        sender.datagram_received(HEADER.pack(*invalid), ("127.0.0.1", 1))
        assert sender.mask == 0
    sender.datagram_received(HEADER.pack(*fields), ("127.0.0.1", 1))
    fields[5] = 2
    sender.datagram_received(HEADER.pack(*fields), ("127.0.0.1", 1))
    fields[5] = 1
    sender.datagram_received(HEADER.pack(*fields), ("127.0.0.1", 1))
    assert sender.mask == 3


@pytest.mark.parametrize("finish", ["timeout", "close", "socket", "cancel"])
async def test_failed_transfer(finish: str) -> None:
    loop = asyncio.get_running_loop()
    server, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    sender = UDPReads(42, COOKIE)
    client, _ = await loop.create_datagram_endpoint(
        lambda: sender, remote_addr=server.get_extra_info("sockname")
    )
    try:
        task = asyncio.create_task(sender.send(0x80000001, bytes(16384), timeout=0.1))
        await asyncio.sleep(0.01)
        with pytest.raises(DevBoxError):
            await sender.send(0x80000002, bytes(512))
        if finish == "close":
            sender.close()
        elif finish == "socket":
            sender.error_received(OSError())
        elif finish == "cancel":
            task.cancel()
        expected = (
            TimeoutError
            if finish == "timeout"
            else asyncio.CancelledError
            if finish == "cancel"
            else DevBoxError
        )
        with pytest.raises(expected):
            await task
        assert not sender.active and sender.batches == 0
        assert sender.packets <= 8  # Bounded window, no receiver means no new chunks.
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("size", [0, 1, 511, 16385])
async def test_invalid_size(size: int) -> None:
    with pytest.raises(DevBoxError):
        await UDPReads(42, COOKIE).send(0x80000001, bytes(size))


async def test_websocket_storage_integration(tmp_path: Path) -> None:
    image = tmp_path / "test.img"
    data = bytes(i % 251 for i in range(16384))
    image.write_bytes(data)
    receiver = Receiver(loss=True)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: receiver, local_addr=("127.0.0.1", 8081)
    )
    completed = asyncio.Event()

    async def handle(ws: ServerConnection) -> None:
        auth = Message.decode(await ws.recv())
        await ws.send(Message(Op.AUTH | RESPONSE, auth.id, 42).encode())
        negotiation = Message.decode(await ws.recv())
        assert negotiation.op == Op.UDP_READS and negotiation.a == 1
        receiver.cookie = negotiation.payload
        await ws.send(Message(Op.UDP_READS | RESPONSE, negotiation.id, 42, 1, 8081).encode())
        attach = Message.decode(await ws.recv())
        await ws.send(Message(Op.ATTACH | RESPONSE, attach.id, 42).encode())
        await ws.send(Message(Op.READ, 0x80000001, 42, 0, 32).encode())
        await asyncio.wait_for(receiver.done.wait(), 2)
        # A queued write must stay on WS and be persisted before its reply.
        await ws.send(Message(Op.WRITE, 0x80000002, 42, 0, 1, b"z" * 512).encode())
        reply = Message.decode(await ws.recv())
        assert reply.op == Op.WRITE | RESPONSE and not reply.status and not reply.payload
        # Windows enforces the image's exclusive lock even for this test reader.
        assert box.storage.disk is not None
        assert box.storage.disk.read(0, 32) == b"z" * 512 + data[512:]
        await ws.send(Message(Op.EJECT, 0x80000003, 42).encode())
        reply = Message.decode(await ws.recv())
        assert reply.op == Op.EJECT | RESPONSE and not reply.status
        completed.set()
        await ws.wait_closed()

    try:
        async with serve(handle, "127.0.0.1", 0) as server:
            async with DevBox(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", udp_reads=True
            ) as box:
                await box.storage.serve_image(image, read_only=False)
                await asyncio.wait_for(completed.wait(), 3)
                assert box._udp and box._udp.batches == 1 and box._udp.retries
                assert b"".join(receiver.data[i] for i in sorted(receiver.data)) == data
            assert box._udp is None
            assert image.read_bytes() == b"z" * 512 + data[512:]
    finally:
        transport.close()


async def test_unsupported_negotiation() -> None:
    async def handle(ws: ServerConnection) -> None:
        async for raw in ws:
            m = Message.decode(raw)
            await ws.send(
                Message(m.op | RESPONSE, m.id, 42, status=8 if m.op == Op.UDP_READS else 0).encode()
            )

    async with serve(handle, "127.0.0.1", 0) as server:
        box = DevBox(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", udp_reads=True)
        with pytest.raises(DevBoxError):
            await box.connect()
        assert box.ws is None and box._udp is None


async def test_disconnect_and_reconnect_revoke_udp(tmp_path: Path) -> None:
    image = tmp_path / "test.img"
    image.write_bytes(bytes(16384))
    cookies: list[bytes] = []

    async def handle(ws: ServerConnection) -> None:
        auth = Message.decode(await ws.recv())
        await ws.send(Message(Op.AUTH | RESPONSE, auth.id, 42).encode())
        negotiation = Message.decode(await ws.recv())
        cookies.append(negotiation.payload)
        await ws.send(Message(Op.UDP_READS | RESPONSE, negotiation.id, 42, 1, 8081).encode())
        attach = Message.decode(await ws.recv())
        await ws.send(Message(Op.ATTACH | RESPONSE, attach.id, 42).encode())
        await ws.send(Message(Op.READ, 0x80000001, 42, 0, 32).encode())
        await asyncio.sleep(0.02)
        await ws.close()

    # Consume datagrams but never acknowledge, then lose the control session.
    sink, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 8081)
    )
    try:
        async with serve(handle, "127.0.0.1", 0) as server:
            box = DevBox(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", udp_reads=True)
            await box.connect()
            for run in range(2):
                udp = box._udp
                await box.storage.serve_image(image)
                assert box._receiver
                await asyncio.wait_for(box._receiver, 0.5)
                assert udp and udp.closed and udp.batches == 0
                if run == 0:
                    await asyncio.wait_for(box.reconnect(), 0.5)
                else:
                    await asyncio.wait_for(box.close(), 0.5)
            assert cookies[0] != cookies[1] and box.storage.disk is None
    finally:
        sink.close()
