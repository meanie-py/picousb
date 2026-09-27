# Agent instructions

## Secrets are not conversation context

- Never open, read, search, print, summarize, or send the contents of `.env.flash`, other real `.env` files, generated configuration blocks, provisioned firmware, or flash dumps to an agent/model or tool output.
- `.gitignore` is not a read-access boundary. Explicitly exclude secret files from searches and inspection; do not use broad recursive content reads.
- Provisioning tools may consume `.env.flash` locally as opaque input, but must emit only safe status and sanitized validation errors. Never echo values, source lines, payloads, or exception text containing secrets. No shell tracing around provisioning.
- Use synthetic credentials for tests. Do not inspect real credentials to debug validation failures. The committed `.env.flash.example` contains placeholders and is safe to read.
- Ignore generated secret-bearing artifacts; stage explicit reviewed paths, never force-add secrets. Never upload provisioned images as CI artifacts.

## Project contract

- Read `docs/architecture.md` before implementation. Target Pico 2 W (`pico2_w`, RP2350); the original RP2040 handoff was superseded.
- Build a USB keyboard, mouse, and MSC appliance for developing a custom OS on a target Raspberry Pi. The host already captures the target's HDMI. Video is outside scope; UART is secondary.
- Keep FAT and directory synchronization on the host for the first version. Do not redesign this into a Pico-side file proxy or optimize before measuring.
- One authenticated control/storage session at a time; trusted network; US keyboard layout.
- Keep hardware adapters separate from testable protocol/state logic. Never block USB callbacks on network I/O.
- Distinguish automated test results from physical hardware acceptance. Never claim radio, USB, or recovery behavior was verified without exercising it.

## Product vision and development context

PicoUSB is an out-of-band development appliance for a human or AI agent building
our own operating system. It supplies real USB input and removable media even
when the target OS is unfinished, crashing, or unable to run a network agent.
The intended workflow is: build on the host, attach development media, exercise
the target, observe its HDMI/serial output, and iterate.

```text
Development host -- Wi-Fi / WebSocket --> Pico 2 W -- USB --> target Raspberry Pi
Development host <-- existing HDMI capture card ------------ target Raspberry Pi
                                      Pico GPIO <-- UART --> target GPIO (planned)
```

- The appliance board is Pico 2 W / RP2350. The target computer is a separate
  Raspberry Pi 4B; Raspberry Pi 5 is a future target. Do not confuse their roles.
- The target's USB driver is being developed in another lane, initially in QEMU.
  The physical Pi 4B is also in use there for UART crash debugging. Coordinate
  before using, resetting, or reflashing that target; it is not this lane's bench.
- Current physical acceptance uses the Pico connected to the Windows development
  PC, with a dedicated HID scratch window and disposable storage images.
- Bootable raw media is part of the vision. Serving an image's sectors does not
  establish that a Pi bootloader can boot it. Pi-compatible image contents,
  bootloader configuration, media readiness, and USB power/reset timing still
  need target-specific acceptance. A PC/FreeDOS image is not a Pi boot test.
- The host already captures HDMI. Do not add video transport to the Pico.

## GPIO serial console

The Pico's UART0 (GP0 TX -> target RXD, GP1 RX <- target TXD, common ground,
3.3 V) is bridged to TCP port 8082 (`docs/protocol.md`, "Serial console";
`pico-devbox serial`). Firmware diagnostics moved to UART1 on GP4/GP5 so they
never reach the target. The console releases its pins while the target's USB
power is absent, since the Pico can stay powered from VSYS. Physical acceptance
against the Pi 4B is recorded in `docs/hardware-acceptance.md`, not assumed.

## Current architecture and ownership

- Pico SDK C/C++ plus TinyUSB expose a composite keyboard, mouse, and MSC device.
  Portable core logic is separate from the Wi-Fi and USB adapters. USB must stay
  responsive when the host or Wi-Fi is unavailable.
- The binary, versioned WebSocket protocol carries control and sector traffic.
  Authentication is required when `DEVICE_TOKEN` is configured. One session owns
  control and storage; a new connection must not silently replace it.
- The final 4 KiB of board flash holds immutable provisioned configuration,
  validated by magic, version, length, and CRC. Linker/build checks protect it.
  There is no runtime secret-read or configuration-update API.
- `serve_directory(path)` imports a directory into a host-owned 32 MiB FAT16
  image. Directory mode uses DOS 8.3 names. The target owns the mounted filesystem;
  concurrent host-directory edits are unsupported. Validated flush/eject exports
  coherent state back to ordinary files. Preserve interrupted recovery state.
- `serve_image(path, read_only=True)` serves an existing raw image, including its
  partition table and filesystem bytes. Read-only is the default; writable mode
  changes that image directly. There is no copy-on-write overlay. NTFS and other
  filesystem journals belong to the target filesystem, not the Pico.
- MSC requests blocks, never filenames. The Pico holds bounded buffers, not an
  entire image. Current transfers allow one outstanding batch of up to 32 sectors
  (16 KiB) over the network. One additional 16 KiB read-ahead buffer overlaps the
  next sequential fetch with the current USB transfer; writes remain durable
  before acknowledgment. See `storage_pipeline.c` for ownership/invalidation.
- The host persists writes before acknowledging them. Offline/failed writes must
  fail. Disconnect/reset/timeout must cancel ownership safely and reject stale
  replies; reconnect must not silently discard or replace unsynchronized media.
- `ws://` is an accepted trusted-network compromise, not end-to-end encryption.
  WPA protects the wireless link only. Keep host storage confined to its chosen
  root, including traversal, symlink/junction, and malformed-filesystem cases.

## Evidence and review priorities

Snapshot from 2026-09-26; consult `docs/hardware-acceptance.md` for exact evidence
and unchecked gates rather than treating this summary as a new test run.

- Physical Windows tests passed Wi-Fi/authentication, composite enumeration,
  focused keyboard/mouse input, and directory-backed FAT reads/writes/export.
- A disposable 1 GiB NTFS image mounted through the Pico. Four 64 MiB files were
  written and then read after eject/reattach: 512 MiB total payload, matching
  hashes, and no backend errors. Metadata operations and read-only protection
  also passed. Backend-process termination produced I/O failure; reattachment
  retained completed data and CHKDSK reported no filesystem problems.
- That is one substantial run, not proof of unlimited endurance or journal replay.
  Pi 4B/5 boot, custom-OS acceptance, AP restart, whole-host power loss, and repeated
  cable/BOT-reset fault testing remain open. The optional diagnostic watchdog
  is a bench tool, not a production recovery guarantee.
- Measured write/read rates were 0.223/0.247 **MiB/s**, not Mbps. The serialized
  request pipeline, Wi-Fi power management, host persistence, and USB scheduling
  are profiling candidates, not established causes. Measure before optimizing;
  compare network-only, host I/O, and USB paths before changing durability rules.
- Review asynchronous completion/cancellation, session/media identity, write and
  flush semantics, recovery, bounds checking, path containment, and test blind
  spots. Keep confirmed defects distinct from hypotheses and accepted tradeoffs.
  The immediate next step is a review pass before further implementation.
- Never inject HID into an unattended desktop. Use the intentional scratch test.
  Storage experiments must identify the Pico media and use disposable artifacts;
  never format a physical host disk or bypass an OS safe-eject refusal.

## Repository guide

- `docs/architecture.md`: accepted decisions; its initial bring-up wording is
  historical. `docs/hardware-acceptance.md` records subsequent physical results.
- `docs/protocol.md`, `docs/flash-config.md`: wire contract and exact flash layout.
- `docs/image-testing.md`: disposable-image transfer and recovery workflow.
- `firmware/src/core.c`, `firmware/include/devbox.h`: portable state/protocol logic.
- `firmware/src/network.c`, `websocket.c`, `usb.c`, `main.c`: hardware/transports.
- `host/src/pico_devbox/`: async client, directory/FAT backend, raw-image backend,
  and thin CLI. `firmware/tests/` and `host/tests/` contain software-only coverage.
- `tools/patch_tinyusb.py`: pinned TinyUSB asynchronous MSC extension; changes
  here need particular scrutiny of flush/eject/reset and buffer ownership.
- `tools/test.sh` runs native and Python checks; `tools/build.sh` cross-compiles.
  Follow README prerequisites. Hardware acceptance remains a separate activity.
- Private repository: https://git.999999999.loan/lambda/picousb on Sunspot Gitea.
  CI is `.gitea/workflows/ci.yml`: native sanitizer tests, pytest, Ruff, type checks,
  and actual Pico 2 W compilation. Do not assume GitHub-hosted infrastructure.
