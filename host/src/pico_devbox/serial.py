"""Target serial console relayed by the Pico's GPIO UART (docs/protocol.md, "Serial console").

A raw TCP stream on its own port, separate from the control session, so a capture can
stay attached while other tools connect and disconnect. The first line authenticates;
after ``OK`` every byte is console data in both directions.
"""

import asyncio
from urllib.parse import urlparse

from .protocol import DevBoxError

HELLO = b"PICOSERIAL 1"
DEFAULT_PORT = 8082


def console_host(url: str) -> str:
    """The device host named by a control URL such as ``ws://picousb:8080/ws``."""
    host = urlparse(url).hostname
    if not host:
        raise ValueError("control URL has no host")
    return host


class SerialConsole:
    def __init__(
        self, host: str, token: str = "", port: int = DEFAULT_PORT, timeout: float = 5
    ) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._token = token
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout
        )
        self._reader, self._writer = reader, writer
        line = HELLO + (b" " + self._token.encode() if self._token else b"") + b"\n"
        writer.write(line)
        await writer.drain()
        try:
            reply = await asyncio.wait_for(reader.readline(), self.timeout)
        except (ConnectionError, asyncio.IncompleteReadError):
            reply = b""
        if reply != b"OK\n":
            await self.close()
            raise DevBoxError(2, "console authentication failed")

    async def read(self, size: int = 4096) -> bytes:
        """Next console bytes; raises ConnectionError when the device closes the stream."""
        if self._reader is None:
            raise ConnectionError("console not connected")
        data = await self._reader.read(size)
        if not data:
            raise ConnectionError("console stream closed")
        return data

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            raise ConnectionError("console not connected")
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def __aenter__(self) -> "SerialConsole":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
