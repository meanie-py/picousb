"""Experimental bounded selective-repeat transport for storage READ replies only."""

import asyncio
import struct
import zlib

from .protocol import MAX_PAYLOAD, DevBoxError

HEADER = struct.Struct("<4sII16sHHI")
CHUNK = 1024
WINDOW = 4


class UDPReads(asyncio.DatagramProtocol):
    def __init__(self, session: int, cookie: bytes) -> None:
        self.session, self.cookie = session, cookie
        self.transport: asyncio.DatagramTransport | None = None
        self.changed = asyncio.Event()
        self.closed = False
        self.active = False
        self.rid = self.size = self.crc = self.mask = self.full = 0
        self.packets = self.retries = self.batches = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.active or len(data) != HEADER.size:
            return
        magic, session, rid, cookie, size, mask, crc = HEADER.unpack(data)
        if (
            magic != b"PDA1"
            or session != self.session
            or rid != self.rid
            or cookie != self.cookie
            or size != self.size
            or crc != self.crc
            or mask & ~self.full
        ):
            return
        # ACKs can be reordered; progress is monotonic within one batch.
        self.mask |= mask
        self.changed.set()

    def error_received(self, exc: Exception) -> None:
        self.close()

    def connection_lost(self, exc: Exception | None) -> None:
        self.closed = True
        self.changed.set()

    def close(self) -> None:
        self.closed = True
        self.changed.set()
        if self.transport:
            self.transport.close()

    async def send(self, rid: int, data: bytes, *, timeout: float = 3.0) -> None:
        if self.active:
            raise DevBoxError(4, "UDP read already active")
        if (
            not 0x80000000 <= rid <= 0xFFFFFFFF
            or not data
            or len(data) > MAX_PAYLOAD
            or len(data) % 512
        ):
            raise DevBoxError(8, "invalid UDP read")
        if self.closed or self.transport is None:
            raise DevBoxError(5, "UDP transport closed")
        self.active = True
        self.rid, self.size, self.crc = rid, len(data), zlib.crc32(data)
        count = (len(data) + CHUNK - 1) // CHUNK
        self.mask, self.full = 0, (1 << count) - 1
        sent: dict[int, tuple[float, int]] = {}
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(timeout):
                while self.mask != self.full:
                    if self.closed:
                        raise DevBoxError(5, "UDP transport closed")
                    self.changed.clear()
                    now = loop.time()
                    outstanding = sum(not self.mask & (1 << i) for i in sent)
                    for i in range(count):
                        if self.mask & (1 << i):
                            continue
                        previous = sent.get(i)
                        if previous is None:
                            if outstanding >= WINDOW:
                                continue
                            outstanding += 1
                            attempts = 0
                        else:
                            when, attempts = previous
                            if now - when < min(0.04 * 2 ** min(attempts, 3), 0.2):
                                continue
                            self.retries += 1
                        packet = (
                            HEADER.pack(
                                b"PDR1", self.session, rid, self.cookie, len(data), i, self.crc
                            )
                            + data[i * CHUNK : (i + 1) * CHUNK]
                        )
                        self.transport.sendto(packet)
                        self.packets += 1
                        sent[i] = (now, attempts + 1)
                    try:
                        await asyncio.wait_for(self.changed.wait(), 0.04)
                    except TimeoutError:
                        pass
                self.batches += 1
        finally:
            self.active = False
