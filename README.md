# picousb / Pico DevBox

Pico 2 W firmware and an async Python host client for exercising USB on a custom
OS running on a Raspberry Pi. The Pico exposes a keyboard, mouse, and removable
disk; the development host controls them over Wi-Fi. Storage can be a directory
backed by FAT16 or an existing raw disk image.

**Status:** Wi-Fi authentication, keyboard/mouse input, directory-backed USB,
and raw NTFS images have passed on a real Pico 2 W with Windows. A 512 MiB
write/read round trip matched all hashes; backend-interruption recovery and
read-only enforcement passed. Measured throughput was about 0.22 MiB/s writing
and 0.25 MiB/s reading. Pi boot and broader endurance/fault scenarios remain open in
[hardware acceptance](docs/hardware-acceptance.md). Use a dedicated test directory
and target during development.

```text
Development host / AI agent
    | Wi-Fi / WebSocket: control + sectors
    v
Pico 2 W (RP2350, Pico SDK + TinyUSB)
    | USB composite: keyboard + mouse + MSC
    v
Target Raspberry Pi / custom OS
    | HDMI
    v
Existing capture card on development host
```

Video stays in the existing capture workflow. UART streaming is deferred. GP0
(TX) and GP1 (RX) currently provide 115200-baud diagnostic UART and are reserved
for future serial support. Use 3.3 V UART levels and shared ground, not RS-232
voltages. USB stdio is disabled.

## Hardware and network

- Pico **2 W**, USB data cable, and target with USB host support.
- Development host with Python 3.11+ and access to the Pico's 2.4 GHz network.
- Implemented Wi-Fi mode: WPA2-Personal/AES. Guest isolation must allow the
  development host to initiate TCP connections to the Pico.
- Find DHCP hostname `picousb` in the router, use `picousb.local` where mDNS
  works, or read the assigned IP from UART. No internet is needed at runtime.
- One control/storage session. A running directory server owns that session;
  use the same Python `DevBox` instance to combine storage and input.

## Configuration privacy

Copy `.env.flash.example` to `.env.flash` and edit locally outside agent sessions.
Provide WIFI_SSID, WIFI_PASSWORD, and optionally a separate DEVICE_TOKEN of
16..128 bytes. Real values and private binary artifacts are ignored. Agents must
not inspect them: see [AGENTS.md](AGENTS.md).

Provisioning consumes the file internally and emits only safe status. The final
4096 bytes of flash hold immutable runtime configuration with magic, version,
length, and CRC32. Normal firmware updates preserve the region. Runtime APIs
cannot retrieve or update credentials. See [exact layout](docs/flash-config.md).

## Build

On Linux or WSL (Ubuntu/Debian):

```sh
sudo apt-get update
sudo apt-get install cmake ninja-build git g++ gcc-arm-none-eabi \
  libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib libusb-1.0-0-dev \
  python3-venv dosfstools mtools
bash tools/build.sh
```

Dependencies are pinned to Pico SDK 2.2.0 and TinyUSB 0.19.0. Bootstrap applies
a small auditable TinyUSB extension for asynchronous flush/eject and reset
cancellation. Output is `build/firmware/picousb.uf2`. Linker and post-build checks
protect the reserved sector. Building public firmware never reads `.env.flash`.

For faster WSL builds, put dependencies and intermediates on its Linux disk:
`PICOUSB_DEPS_ROOT=/tmp/picousb-deps PICOUSB_BUILD_DIR=/tmp/picousb-build bash tools/build.sh`.
Public firmware outputs still appear in `build/firmware/`.

## Flash

Hold BOOTSEL while plugging the board into Windows; the drive is labeled RP2350.
Substitute its drive letter:

```powershell
# Initial firmware and config, one transfer:
.\tools\flash.ps1 -Part Initial -Drive F:\
# Subsequent firmware-only updates preserve config:
.\tools\flash.ps1 -Part Firmware -Drive F:\
# Config-only update in BOOTSEL:
.\tools\flash.ps1 -Part Config -Drive F:\
```

If Windows rejects the BOOTSEL drive copy, install the official Raspberry Pi
`picotool` Windows release and pass `-Picotool C:\path\to\picotool.exe`.
The script also detects picotool on PATH. It loads through the bootloader USB
API, then explicitly reboots the application. Ensure the BOOTSEL button is
released; a tight case can hold it down and force every reboot back to BOOTSEL.

Manual/Linux provisioning:

```sh
python3 tools/config_block.py   # private build/config.uf2
python3 tools/provision.py      # private build/provisioned.uf2
# Copy build/provisioned.uf2 to the mounted RP2350 BOOTSEL drive.
```

The board reboots after transfer. LED on means Wi-Fi link up. Invalid config
leaves USB alive with no backend and a sanitized UART error. Full flash erase
destroys config; normal public firmware UF2 updates preserve it. Never commit,
upload, or inspect generated private config/combined UF2 files in an agent session.

## Python library

Create and activate a virtual environment, then `python -m pip install -e ./host`.

```python
import asyncio
import getpass
from pico_devbox import DevBox

async def main():
    token = getpass.getpass("Device token: ")
    async with DevBox("ws://picousb.local:8080/ws", token=token) as box:
        await box.storage.serve_directory("./virtual-drive")
        await box.keyboard.type("hello world")
        await box.keyboard.press("ENTER")
        await box.mouse.move(100, 0)
        await box.mouse.click("left")
        await box.drain()
        await box.storage.ejected.wait()  # Keep serving until target safe eject.

asyncio.run(main())
```

Create the backing directory first. Use 8.3 names such as `KERNEL.BIN`, `TEST.TXT`,
and `BOOT/CONFIG.TXT`. Target-created long names export under their DOS 8.3 aliases;
their long spelling is not preserved in v1. Existing host filename
case is preserved. Held input uses `key_down`/`key_up`, `button_down`/`button_up`,
and `release_all`; text assumes US keyboard layout. `reconnect()` authenticates
again but requires explicit storage reattachment.

## CLI

Each command prompts privately for the token, or reads PICO_DEVICE_TOKEN from
the environment. Use `--url` or PICO_URL if mDNS is unavailable.

```sh
pico-devbox --url ws://192.0.2.10:8080/ws status
pico-devbox type "hello world"
pico-devbox key CTRL ALT DELETE
pico-devbox mouse move 50 -20
pico-devbox mouse click left
pico-devbox serve ./virtual-drive
pico-devbox serve-image ./os.img                 # read-only by default
pico-devbox serve-image ./disposable.img --writable
```

Do not launch a second CLI while `serve` owns the session; use the library for
combined storage/control. HID commands affect the connected USB host, so use an
intentional test target. Disconnect releases held keys and buttons.

## FAT implementation and recovery

The host manages a **32 MiB FAT16 superfloppy image** under `.picousb/` in the
backing directory. Users configure a directory, not an image. Host-side FAT keeps
Pico RAM bounded and makes recovery testable. The Pico proxies one bounded block
operation at a time without blocking USB callbacks or holding an entire disk.

Import occurs once; the target then owns the volume exclusively. Do not edit the
host directory concurrently. Successful USB writes mean sectors are persisted
and flushed on the host. Ordinary files and deletions are published after a
validated target flush/eject. Safely eject before stopping the host process.
Missing backends, failed writes, and deadlines produce USB errors, never pretend
success. An idle period alone does not cause export.

After an unclean disconnect, re-serving uses the retained image. Do not delete
`.picousb` to fix an error; it may contain the only copy of acknowledged writes.
After a process crash, remove `.picousb/lock` only after confirming no server is
running. An `export-pending.json` marker blocks reattachment after interrupted
publication; the image, staging files, and backups are retained for manual repair.

Known limits: fixed FAT16 geometry, ASCII 8.3 names, up to 16 directory levels,
8192 entries, one session, and throughput limited by network/host flush latency.
No automatic merge of host edits or interrupted-export repair. Full power-loss
durability depends on host filesystem guarantees. USB-powered availability
depends on target USB power. UART streaming is deferred.

## Raw images and boot media

Use `await box.storage.serve_image("os.img")` to expose an existing raw disk
image as the USB disk itself. Unlike placing an `.img` file inside a served
directory, this preserves partition tables, boot sectors, long filenames, and
non-FAT filesystems such as NTFS. Capacity comes from the file (whole 512-byte
sectors, below 1 TiB). Neither the disk nor large files are held in Pico RAM.
Transfers are bounded to 16 KiB per request; larger host I/O is streamed.
One additional 16 KiB read-ahead buffer overlaps sequential network reads with
USB transfers. Random reads fall back to requested blocks; writes, flush/eject,
disconnects, and media changes invalidate predictions. Writes are still persisted
on the host before acknowledgment; this is not a write-back cache or a journal.
Update host and firmware together for this larger-transfer protocol extension.

Images are read-only by default. `read_only=False` or CLI `--writable` modifies
the original image directly, with each write fsynced before acknowledgment.
Use disposable copies for development. There is no copy-on-write overlay or
additional filesystem journal: Windows owns NTFS journaling and recovery.
Safe eject remains necessary. Host process exit releases the image lock;
reconnect and reattach explicitly. Do not mount or edit the image concurrently.

An appropriately prepared image could provide boot media for the target Pi 4B
or future Pi 5, but **actual Pi boot acceptance is pending**. A QEMU artifact is
not necessarily a Pi boot image. USB bootloader compatibility and readiness
before its timeout must be checked, particularly when target reboot cuts Pico
USB power. The existing UART/debugging target is not needed for Windows tests.

See [image testing](docs/image-testing.md) for a disposable NTFS workflow,
large-transfer checks, and the distinction between transport persistence and
filesystem crash recovery.

## Security and testing

The token gates operations and is never returned. `ws://` is plaintext beyond
Wi-Fi link encryption: use a trusted network, not internet exposure. Directory
traversal, unsafe FAT names, symlinks, and Windows junctions are rejected. Use a
dedicated backing directory; concurrent local tampering is outside its exclusive
ownership contract.

```sh
python3 -m venv build/venv
build/venv/bin/pip install -e './host[dev]'
PYTHON="$PWD/build/venv/bin/python" bash tools/test.sh
bash tools/build.sh
```

Checks include ASan/UBSan native firmware tests, config/CRC corruption, HID state,
session/deadline/disconnect handling, Python lifecycle and CLI, path containment,
FAT recovery, independent filesystem tools, and WebSocket integration against
both Python fakes and the actual native firmware core. Ruff and mypy run in the
same workflow, including the actual lwIP DHCP/mDNS timer regression. Gitea Actions
is configured in `.gitea/workflows/ci.yml` and enabled for the repository on
Sunspot's Linux runner. CI never provisions or uploads private images.

See [protocol](docs/protocol.md), [architecture](docs/architecture.md), and
[hardware acceptance](docs/hardware-acceptance.md) for exact contracts and checks.

For local hardware checks without copying tokens into commands or conversation,
`python tools/device_session.py --url ws://DEVICE_IP:8080/ws` consumes
`.env.flash` internally and prints only a sanitized success/failure result.
Add `--serve ./virtual-drive` to keep a directory backend attached until eject.
Never inspect that file or enable verbose network logging during provisioning.

On Windows, `python tools/hid_check.py --url ws://DEVICE_IP:8080/ws` opens an
interactive scratch window for physical keyboard/mouse acceptance. Click Run
test and keep that window focused; input stops if focus changes. This test
consumes the local token without displaying it and does not send GUI shortcuts.
