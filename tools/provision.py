"""Opaque initial provisioning: combine public firmware and private config."""

import argparse
import struct
import sys
from pathlib import Path

from config_block import ADDRESS, FAMILY, ConfigError, encode, parse_env, uf2, workaround_block


def combine(firmware: bytes, config: bytes) -> bytes:
    if not firmware or len(firmware) % 512:
        raise ConfigError("invalid firmware UF2")
    pieces = []
    seen: set[int] = set()
    for offset in range(0, len(firmware), 512):
        block = bytearray(firmware[offset : offset + 512])
        if offset == 0 and block == workaround_block():
            continue
        m1, m2, flags, address, size, _, _, family = struct.unpack_from("<8I", block)
        if (
            m1 != 0x0A324655
            or m2 != 0x9E5D5157
            or flags != 0x2000
            or size != 256
            or family != FAMILY
            or address < 0x10000000
            or address + size > ADDRESS
            or address % 256
            or address in seen
            or struct.unpack_from("<I", block, 508)[0] != 0x0AB16F30
        ):
            raise ConfigError("incompatible firmware UF2 or reserved-region overlap")
        pieces.append(block)
        seen.add(address)
    if not pieces or min(seen) != 0x10000000:
        raise ConfigError("missing firmware start")
    private = uf2(config)
    pieces.extend(bytearray(private[i : i + 512]) for i in range(512, len(private), 512))
    for i, block in enumerate(pieces):
        struct.pack_into("<II", block, 20, i, len(pieces))
    return workaround_block() + b"".join(pieces)


def main() -> int:
    parser = argparse.ArgumentParser(description="Opaque initial Pico 2 W provisioning")
    parser.add_argument("--firmware", type=Path, default=Path("build/firmware/picousb.uf2"))
    parser.add_argument("--config", type=Path, default=Path(".env.flash"))
    parser.add_argument("--output", type=Path, default=Path("build/provisioned.uf2"))
    args = parser.parse_args()
    try:
        block = encode(parse_env(args.config.read_text(encoding="utf-8-sig")))
        image = combine(args.firmware.read_bytes(), block)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image)
    except (OSError, ValueError, UnicodeError, struct.error):
        print(
            "Provisioning failed. Check configuration and public firmware locally; values suppressed.",
            file=sys.stderr,
        )
        return 1
    print("Private combined UF2 created. Contents suppressed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
