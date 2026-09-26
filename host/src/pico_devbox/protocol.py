"""Bounded protocol shared with firmware/include/devbox.h."""

import struct
from dataclasses import dataclass
from enum import IntEnum

HEADER = struct.Struct("<4sBBHIIIII")
MAGIC = b"PDB1"
MAX_PAYLOAD = 16384
RESPONSE = 0x80
SECTORS = 65536
SECTOR_SIZE = 512


class Op(IntEnum):
    AUTH = 1
    STATUS = 2
    METRICS = 3
    UDP_PROBE = 4
    UDP_READS = 5
    USB_STATUS = 6
    RAW_REPORT = 7
    POINTER = 8
    KEY_DOWN = 10
    KEY_UP = 11
    KEY_PRESS = 12
    KEY_RELEASE = 13
    KEY_TEXT = 14
    MOUSE_MOVE = 20
    MOUSE_DOWN = 21
    MOUSE_UP = 22
    MOUSE_SCROLL = 23
    MOUSE_CLICK = 24
    MOUSE_RELEASE = 25
    ATTACH = 30
    DETACH = 31
    READ = 40
    WRITE = 41
    FLUSH = 42
    EJECT = 43


class DevBoxError(Exception):
    """Safe structured device/protocol error; never includes received payloads."""

    def __init__(self, code: int, message: str = "device operation failed") -> None:
        self.code = code
        super().__init__(f"{message} (code {code})")


@dataclass(frozen=True)
class Message:
    op: int
    id: int
    session: int = 0
    a: int = 0
    b: int = 0
    payload: bytes = b""
    status: int = 0

    def encode(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise DevBoxError(3, "payload too large")
        return (
            HEADER.pack(
                MAGIC,
                self.op,
                self.status,
                0,
                self.id,
                self.session,
                self.a & 0xFFFFFFFF,
                self.b & 0xFFFFFFFF,
                len(self.payload),
            )
            + self.payload
        )

    @classmethod
    def decode(cls, data: bytes | str) -> "Message":
        if not isinstance(data, bytes) or len(data) < HEADER.size:
            raise DevBoxError(1, "invalid binary envelope")
        magic, op, status, reserved, rid, session, a, b, length = HEADER.unpack_from(data)
        if magic != MAGIC:
            raise DevBoxError(1, "protocol version mismatch")
        if reserved or not rid or length > MAX_PAYLOAD or len(data) != HEADER.size + length:
            raise DevBoxError(1, "invalid envelope fields")
        return cls(op, rid, session, a, b, data[HEADER.size :], status)
