"""Make a public firmware UF2 safe to drag onto the BOOTSEL drive.

picotool prepends an RP2350-E10 workaround block addressed to 0x10ffff00. Without
a partition table the bootrom assumes 16 MiB of flash and writes that block; on the
Pico 2 W's 4 MiB part the address wraps to 0x3fff00, so the bootrom erases the
reserved configuration sector and a firmware-only update silently deprovisions
the board (observed 2026-09-27: `config_invalid` after a drag-and-drop update).
picotool load skips such blocks, which is why that path never showed it.

`strip` removes every block whose target, after that wraparound, falls in the
reserved sector and refuses if one of them carries firmware data. `check` fails
if any block would still land there. Only public firmware UF2s are handled;
never pass config or provisioned images.
"""

import struct
import sys
from pathlib import Path

BLOCK = 512
MAGIC = (0x0A324655, 0x9E5D5157)
MAGIC_END = 0x0AB16F30
FLASH_BASE = 0x10000000
FLASH_SIZE = 4 * 1024 * 1024
RESERVED = (FLASH_SIZE - 4096, FLASH_SIZE)
ABSOLUTE_FAMILY = 0xE48BFF57


def blocks(data: bytes) -> list[bytes]:
    if len(data) % BLOCK:
        raise ValueError("not a UF2 file")
    out = [data[i : i + BLOCK] for i in range(0, len(data), BLOCK)]
    for b in out:
        m0, m1 = struct.unpack_from("<2I", b)
        if (m0, m1) != MAGIC or struct.unpack_from("<I", b, BLOCK - 4)[0] != MAGIC_END:
            raise ValueError("not a UF2 file")
    return out


def hits_reserved(block: bytes) -> bool:
    address, size = struct.unpack_from("<2I", block, 12)
    if not FLASH_BASE <= address < FLASH_BASE + 0x1000000:
        return False
    start = (address - FLASH_BASE) % FLASH_SIZE
    return start < RESERVED[1] and start + size > RESERVED[0]


def strip(data: bytes) -> tuple[bytes, int]:
    kept, removed = [], 0
    for b in blocks(data):
        if hits_reserved(b):
            if struct.unpack_from("<I", b, 28)[0] != ABSOLUTE_FAMILY:
                raise ValueError("firmware data overlaps the reserved configuration sector")
            removed += 1
            continue
        kept.append(b)
    return b"".join(kept), removed


def check(data: bytes) -> None:
    if any(hits_reserved(b) for b in blocks(data)):
        raise ValueError("UF2 would write the reserved configuration sector")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("strip", "check"):
        print("usage: firmware_uf2.py strip|check <public firmware .uf2>", file=sys.stderr)
        return 2
    path = Path(sys.argv[2])
    try:
        if sys.argv[1] == "strip":
            data, removed = strip(path.read_bytes())
            path.write_bytes(data)
            print(f"UF2: removed {removed} block(s) aliasing the configuration sector.")
        else:
            check(path.read_bytes())
            print("UF2 leaves the reserved configuration sector alone.")
    except (OSError, ValueError) as error:
        print(f"firmware_uf2: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
