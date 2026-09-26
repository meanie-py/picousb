"""Opaque local provisioning. Errors must never include input values or lines."""

import argparse
import binascii
import re
import struct
import sys
from pathlib import Path

BLOCK_SIZE = 4096
HEADER = struct.Struct("<8sHHI")
MAGIC = b"PICOUSB\x00"
FLASH_SIZE = 4 * 1024 * 1024
ADDRESS = 0x10000000 + FLASH_SIZE - BLOCK_SIZE
FAMILY = 0xE48BFF59  # RP2350 ARM secure (same family as SDK default UF2)


class ConfigError(ValueError):
    pass


def parse_env(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key):
            raise ConfigError("invalid configuration syntax")
        if key in result or "\x00" in value:
            raise ConfigError("duplicate key or invalid character")
        result[key] = value
    validate(result)
    return result


def validate(values: dict[str, str]) -> None:
    for key, low, high in (("WIFI_SSID", 1, 32), ("WIFI_PASSWORD", 8, 63)):
        n = len(values.get(key, "").encode("utf-8"))
        if not low <= n <= high:
            raise ConfigError("missing or invalid Wi-Fi configuration")
    if "DEVICE_TOKEN" in values and not 16 <= len(values["DEVICE_TOKEN"].encode()) <= 128:
        raise ConfigError("invalid device token length")


def encode(values: dict[str, str]) -> bytes:
    validate(values)
    payload = b"".join((k + "=" + v).encode("utf-8") + b"\0" for k, v in values.items())
    if len(payload) > BLOCK_SIZE - HEADER.size:
        raise ConfigError("configuration exceeds reserved capacity")
    header = HEADER.pack(MAGIC, 1, len(payload), binascii.crc32(payload))
    return (header + payload).ljust(BLOCK_SIZE, b"\xff")


def decode(block: bytes) -> dict[str, str]:
    if len(block) != BLOCK_SIZE:
        raise ConfigError("invalid block size")
    magic, version, length, crc = HEADER.unpack_from(block)
    if magic != MAGIC or version != 1 or not 0 < length <= BLOCK_SIZE - HEADER.size:
        raise ConfigError("invalid config header")
    payload = block[HEADER.size : HEADER.size + length]
    if binascii.crc32(payload) != crc or not payload.endswith(b"\0"):
        raise ConfigError("invalid config checksum or terminator")
    try:
        result = parse_env(payload[:-1].decode("utf-8").replace("\0", "\n"))
    except (UnicodeError, ValueError):
        raise ConfigError("invalid config payload") from None
    return result


def workaround_block() -> bytes:
    # Official picotool RP2350-E10 workaround: absolute-family ignored block.
    header = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0xA000, 0x10FFFF00, 256, 0, 2, 0xE48BFF57)
    return (
        header
        + bytes([0xEF]) * 256
        + struct.pack("<I", 0x9957E304)
        + bytes(216)
        + struct.pack("<I", 0x0AB16F30)
    )


def uf2(block: bytes) -> bytes:
    if len(block) != BLOCK_SIZE:
        raise ConfigError("invalid block size")
    result = bytearray(workaround_block())
    for i in range(16):
        result += struct.pack(
            "<8I", 0x0A324655, 0x9E5D5157, 0x2000, ADDRESS + i * 256, 256, i, 16, FAMILY
        )
        result += block[i * 256 : (i + 1) * 256] + bytes(220)
        result += struct.pack("<I", 0x0AB16F30)
    return bytes(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build private Pico 2 W config (no values logged)")
    parser.add_argument("--input", type=Path, default=Path(".env.flash"))
    parser.add_argument("--output", type=Path, default=Path("build/config.uf2"))
    args = parser.parse_args()
    try:
        block = encode(parse_env(args.input.read_text(encoding="utf-8-sig")))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(uf2(block) if args.output.suffix == ".uf2" else block)
    except (OSError, ValueError, UnicodeError):
        print(
            "Provisioning failed: unreadable or invalid configuration; inspect locally.",
            file=sys.stderr,
        )
        return 1
    print("Private configuration artifact created. Contents suppressed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
