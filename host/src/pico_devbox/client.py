"""Async host client. A dedicated receiver services storage during control calls."""

import asyncio
import secrets
from contextlib import suppress
from pathlib import Path

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .image import ImageDisk
from .protocol import HEADER, MAX_PAYLOAD, RESPONSE, SECTORS, DevBoxError, Message, Op
from .storage import DirectoryDisk
from .udp_reads import UDPReads

KEYS = {
    "ENTER": 40,
    "ESC": 41,
    "BACKSPACE": 42,
    "TAB": 43,
    "SPACE": 44,
    "DELETE": 76,
    "RIGHT": 79,
    "LEFT": 80,
    "DOWN": 81,
    "UP": 82,
    "HOME": 74,
    "END": 77,
    "PAGEUP": 75,
    "PAGEDOWN": 78,
    "CTRL": 224,
    "SHIFT": 225,
    "ALT": 226,
    "GUI": 227,
    "SUPER": 227,
}
KEYS.update({chr(i + 65): i + 4 for i in range(26)})
KEYS.update({str(i): 29 + i for i in range(1, 10)})
KEYS["0"] = 39
KEYS.update({f"F{i}": 57 + i for i in range(1, 13)})


def key_code(key: str | int) -> int:
    if isinstance(key, int) and (4 <= key <= 115 or 224 <= key <= 231):
        return key
    if isinstance(key, str) and key.upper() in KEYS:
        return KEYS[key.upper()]
    raise ValueError("unknown HID key")


class Keyboard:
    def __init__(self, box: "DevBox") -> None:
        self.box = box

    async def key_down(self, key: str | int) -> None:
        await self.box.request(Op.KEY_DOWN, key_code(key))

    async def key_up(self, key: str | int) -> None:
        await self.box.request(Op.KEY_UP, key_code(key))

    async def press(self, key: str | int, *modifiers: str) -> None:
        mask = 0
        for mod in modifiers:
            code = key_code(mod)
            if not 224 <= code <= 231:
                raise ValueError("invalid modifier")
            mask |= 1 << (code - 224)
        await self.box.request(Op.KEY_PRESS, key_code(key), mask)

    async def release_all(self) -> None:
        await self.box.request(Op.KEY_RELEASE)

    async def type(self, text: str) -> None:
        data = text.encode("ascii")
        if any(c < 32 and c not in (8, 9, 10, 13) or c == 127 for c in data):
            raise ValueError("unsupported ASCII control character")
        for offset in range(0, len(data), 64):
            await self.box.request(Op.KEY_TEXT, payload=data[offset : offset + 64])
            await self.box.drain()


class Mouse:
    def __init__(self, box: "DevBox") -> None:
        self.box = box

    async def move(self, x: int, y: int) -> None:
        if abs(x) > 32767 or abs(y) > 32767:
            raise ValueError("movement exceeds limit")
        while x or y:
            dx, dy = max(-127, min(127, x)), max(-127, min(127, y))
            await self.box.request(Op.MOUSE_MOVE, dx, dy)
            x, y = x - dx, y - dy

    async def _button(self, op: Op, button: str) -> None:
        if button not in ("left", "right", "middle"):
            raise ValueError("unknown mouse button")
        await self.box.request(op, {"left": 1, "right": 2, "middle": 4}[button])

    async def button_down(self, button: str = "left") -> None:
        await self._button(Op.MOUSE_DOWN, button)

    async def button_up(self, button: str = "left") -> None:
        await self._button(Op.MOUSE_UP, button)

    async def click(self, button: str = "left") -> None:
        await self._button(Op.MOUSE_CLICK, button)

    async def scroll(self, amount: int) -> None:
        if not -127 <= amount <= 127:
            raise ValueError("wheel delta exceeds limit")
        await self.box.request(Op.MOUSE_SCROLL, amount)

    async def release_all(self) -> None:
        await self.box.request(Op.MOUSE_RELEASE)


class Storage:
    def __init__(self, box: "DevBox") -> None:
        self.box = box
        self.disk: DirectoryDisk | ImageDisk | None = None
        self.lock = asyncio.Lock()
        self.ejected = asyncio.Event()
        self.read_bytes = 0
        self.written_bytes = 0
        self.requests = 0
        self.errors = 0

    async def serve_directory(self, path: str | Path) -> None:
        await self._attach(path, image=False, read_only=False)

    async def serve_image(self, path: str | Path, *, read_only: bool = True) -> None:
        """Expose an existing raw disk image; writable access must be explicit."""
        await self._attach(path, image=True, read_only=read_only)

    async def _attach(self, path: str | Path, *, image: bool, read_only: bool) -> None:
        async with self.lock:
            await self._open_attach(path, image=image, read_only=read_only)

    async def _open_attach(self, path: str | Path, *, image: bool, read_only: bool) -> None:
        if self.disk is not None:
            raise DevBoxError(4, "storage already attached")

        async def open_disk() -> DirectoryDisk | ImageDisk:
            if image:
                return await asyncio.to_thread(ImageDisk, path, read_only=read_only)
            return await asyncio.to_thread(DirectoryDisk, path)

        opening = asyncio.create_task(open_disk())
        try:
            self.disk = await asyncio.shield(opening)
        except asyncio.CancelledError:
            disk = await opening
            disk.close()
            raise
        self.ejected.clear()
        try:
            sectors = self.disk.sectors if isinstance(self.disk, ImageDisk) else SECTORS
            await self.box.request(Op.ATTACH, sectors, 512, bytes([read_only]) if image else b"")
        except BaseException:
            self.disk.close()
            self.disk = None
            raise

    async def detach(self) -> None:
        await self.box.request(Op.DETACH)
        await self.close()

    async def close(self) -> None:
        async with self.lock:
            if self.disk:
                self.disk.close()
                self.disk = None

    async def handle(self, m: Message) -> None:
        # Bounded worker queue in DevBox ensures peer floods cannot grow task count.
        status, data = 0, b""
        async with self.lock:
            try:
                if self.disk is None:
                    raise DevBoxError(5, "storage unavailable")
                if m.op == Op.READ and 1 <= m.b <= MAX_PAYLOAD // 512 and not m.payload:
                    data = await asyncio.to_thread(self.disk.read, m.a, m.b)
                    self.read_bytes += len(data)
                elif (
                    m.op == Op.WRITE
                    and 1 <= m.b <= MAX_PAYLOAD // 512
                    and len(m.payload) == m.b * 512
                ):
                    await asyncio.to_thread(self.disk.write, m.a, m.payload)
                    self.written_bytes += len(m.payload)
                elif m.op in (Op.FLUSH, Op.EJECT) and not m.payload and not m.a and not m.b:
                    await asyncio.to_thread(self.disk.export)
                else:
                    raise DevBoxError(8, "invalid storage request")
            except Exception:
                # Never transmit filenames, local paths, contents, or exception strings.
                status = 6
                self.errors += 1
            self.requests += 1
        if m.op == Op.READ and not status and self.box._udp is not None:
            await self.box._udp.send(m.id, data)
        else:
            await self.box._send(
                Message(m.op | RESPONSE, m.id, m.session, payload=data, status=status)
            )
        if m.op == Op.EJECT and not status:
            self.ejected.set()


class DevBox:
    def __init__(
        self, uri: str, token: str = "", *, timeout: float = 8.0, udp_reads: bool = False
    ) -> None:
        self.uri, self._token, self.timeout = uri, token, timeout
        self.ws: ClientConnection | None = None
        self.session = 0
        self.udp_reads = udp_reads
        self._udp: UDPReads | None = None
        self._next_id = 0
        self._pending: dict[int, tuple[int, asyncio.Future[Message]]] = {}
        self._receiver: asyncio.Task[None] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[Message | None] = asyncio.Queue(maxsize=4)
        self._send_lock = asyncio.Lock()
        self.keyboard, self.mouse, self.storage = Keyboard(self), Mouse(self), Storage(self)

    async def __aenter__(self) -> "DevBox":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self.ws is not None:
            raise DevBoxError(4, "already connected")
        self.session, self._next_id = 0, 0
        self._queue = asyncio.Queue(maxsize=4)
        self.ws = await connect(
            self.uri,
            max_size=HEADER.size + MAX_PAYLOAD,
            max_queue=4,
            compression=None,
            open_timeout=self.timeout,
            ping_interval=20,
            ping_timeout=10,
        )
        self._receiver = asyncio.create_task(self._receive())
        self._worker = asyncio.create_task(self._storage_worker())
        try:
            reply = await self.request(Op.AUTH, payload=self._token.encode())
            if not reply.session:
                raise DevBoxError(1, "invalid session")
            self.session = reply.session
            if self.udp_reads:
                cookie = secrets.token_bytes(16)
                reply = await self.request(Op.UDP_READS, a=1, payload=cookie)
                if reply.a != 1 or reply.b != 8081 or reply.payload:
                    raise DevBoxError(1, "invalid UDP negotiation")
                udp = UDPReads(self.session, cookie)
                self._udp = udp
                await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: udp, remote_addr=(self.ws.remote_address[0], reply.b)
                )
        except BaseException:
            await self.close()
            raise

    async def reconnect(self) -> None:
        # Reattach is explicit: do not unexpectedly re-present a mounted disk.
        await self.close()
        await self.connect()

    async def _send(self, message: Message) -> None:
        async with self._send_lock:
            if self.ws is None:
                raise DevBoxError(5, "disconnected")
            try:
                await self.ws.send(message.encode())
            except ConnectionClosed:
                raise DevBoxError(5, "connection closed") from None

    async def request(self, op: Op, a: int = 0, b: int = 0, payload: bytes = b"") -> Message:
        if len(self._pending) >= 32 or self._next_id >= 0x7FFFFFFE:
            raise DevBoxError(3, "request capacity exceeded")
        self._next_id += 1
        rid = self._next_id
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._pending[rid] = (op, future)
        try:
            async with asyncio.timeout(self.timeout):
                await self._send(Message(op, rid, self.session, a, b, payload))
                reply = await future
            if reply.status:
                raise DevBoxError(reply.status)
            return reply
        except (TimeoutError, asyncio.CancelledError):
            # The command may already have changed HID or media state. End the
            # session so the device releases ownership instead of leaving an
            # unacknowledged operation active. Reconnection remains explicit.
            future.cancel()
            if self.ws is not None:
                await self.ws.close()
            raise
        finally:
            self._pending.pop(rid, None)

    async def status(self) -> dict[str, int | bool | str]:
        reply = await self.request(Op.STATUS)
        return {
            "network": "connected",
            "media": bool(reply.a),
            "queued_reports": reply.b,
            "session": self.session,
        }

    async def drain(self) -> None:
        async with asyncio.timeout(self.timeout):
            while (await self.request(Op.STATUS)).b:
                await asyncio.sleep(0.02)

    async def _receive(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                m = Message.decode(raw)
                if m.op & RESPONSE:
                    pending = self._pending.get(m.id)
                    if pending is None or m.op != pending[0] | RESPONSE:
                        raise DevBoxError(1, "unexpected response")
                    if pending[0] != Op.AUTH and m.session != self.session:
                        raise DevBoxError(1, "stale session")
                    if pending[1].done():
                        raise DevBoxError(1, "duplicate response")
                    pending[1].set_result(m)
                else:
                    if (
                        m.status
                        or not self.session
                        or m.session != self.session
                        or m.id < 0x80000000
                    ):
                        raise DevBoxError(1, "invalid storage session")
                    self._queue.put_nowait(m)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            if self._udp:
                self._udp.close()
            for _, future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        DevBoxError(5, "connection lost or invalid server message")
                    )
            if self.ws:
                await self.ws.close()

    async def _storage_worker(self) -> None:
        while True:
            m = await self._queue.get()
            try:
                if m is None:
                    return
                await self.storage.handle(m)
            except Exception:
                if self.ws:
                    await self.ws.close()
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        if self._udp:
            self._udp.close()
        if self.ws:
            await self.ws.close()
        if self._receiver:
            with suppress(asyncio.CancelledError):
                await self._receiver
            self._receiver = None
        # Finish any in-flight durable write before releasing the image handle.
        if self._worker:
            await self._queue.put(None)
            await self._worker
            self._worker = None
        await self.storage.close()
        self.ws = None
        self.session = 0
        self._udp = None
