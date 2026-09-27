# Flash configuration format v1

Pico 2 W has a configured 4 MiB flash. Reserve exactly 4096 bytes at physical
offset `0x003ff000`, XIP address `0x103ff000`, ending at `0x10400000` (exclusive).
The generated linker script limits normal FLASH to 4190208 bytes. A second build
check rejects public firmware binaries exceeding that extent. This address is
board-specific; tools reject other board configurations.

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 8 | `PICOUSB` followed by NUL |
| 8 | 2 | little-endian version, 1 |
| 10 | 2 | little-endian payload length, 1..4080 |
| 12 | 4 | little-endian IEEE CRC32 of payload |
| 16 | length | UTF-8 `KEY=VALUE` records, each terminated by NUL |
| remainder | to 4096 | `0xff` padding |

`.env.flash` is UTF-8 (optional BOM), one unquoted KEY=VALUE per line. Blank lines
and full-line `#` comments are ignored. Leading/trailing line whitespace is
trimmed. No shell interpolation, escapes, inline comments, or quote removal.
Values can contain `=`; they cannot contain NUL or newlines. Keys match
`[A-Z][A-Z0-9_]{0,63}`. Unknown keys are retained. Duplicate keys are rejected by
the provisioning parser. WIFI_SSID is 1..32 UTF-8 bytes, WIFI_PASSWORD is 8..63
bytes for WPA2-Personal, DEVICE_TOKEN is optional or 16..128 UTF-8 bytes.

Provisioning reads the private file internally and prints only success/failure.
It never prints values, offending lines, parsed dictionaries, or tracebacks.
Firmware validates magic, version, length, CRC, termination, and required keys
before attempting Wi-Fi. Invalid config leaves USB alive with no storage backend
and emits `config_invalid` on UART. Runtime APIs cannot read or modify the block.

Configuration UF2 uses the RP2350 ARM secure family ID `0xe48bff59`, sixteen
256-byte payload blocks addressed to the reserved sector, preceded by picotool's
ignored absolute-family RP2350-E10 workaround block. It is preserved separately
when combining firmware and config; ARM-family sequence counts exclude it. Generated `.bin` and
`.uf2` files are private ignored artifacts; do not upload or inspect them in an
agent session. Firmware-only UF2 updates preserve the reserved sector only once
`tools/firmware_uf2.py strip` has removed picotool's E10 block (`build.sh` does
this and `flash.sh` checks it). That block is addressed to 0x10ffff00. Without a
partition table the bootrom assumes 16 MiB of flash and writes it; on this 4 MiB
part it wraps onto 0x3fff00 and erases the reserved sector. A firmware UF2 dragged
on unstripped deprovisioned the board on 2026-09-27. The config UF2 survives its
own copy of the block, because its data blocks rewrite the sector afterwards. A
full flash erase does not preserve the sector either.
