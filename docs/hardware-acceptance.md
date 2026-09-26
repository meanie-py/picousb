# Hardware acceptance checklist

Automated tests do not establish these results. Record observed outcomes and
board/target/host versions when running them. Never include credentials in evidence.

## Bring-up observations (2026-09-26)

- Official picotool successfully provisioned the attached RP2350/Pico 2 W.
- Wi-Fi DHCP and authenticated TCP/WebSocket connections work. Enabling guest
  intranet access changed the device's subnet; its old lease was no longer usable.
- Windows enumerates both HID interfaces and MSC. A boot-time stall was traced
  to Wi-Fi polling with a diagnostic watchdog and reproduced in native lwIP:
  mDNS exhausted the default timer pool. Reserving extra timers fixes the stall.
  A mutation check restoring the old setting fails the regression test with
  the same timer-pool assertion. The loop also now has a guarded 16 KiB stack.
- Wrong-token rejection, exclusive-session rejection, status, and authenticated
  reconnect passed on hardware.
- A 32 MiB FAT16 drive mounted in Windows. A USB-read file matched its host hash.
  File creation, overwrite, nested directories, deletion, volume flush and safe
  eject all published the expected ordinary files to the host backing directory.
  Reattachment preserved contents; a second cycle deleted an existing file.
- A disposable 64 KiB write plus file flush took 1.906 seconds. Its exported
  contents matched all 65,536 expected bytes. This is not sustained throughput.
- Windows initially refused exclusive volume locking; retry after flushing and
  allowing outstanding handles to close succeeded. Never force eject on a veto.
- The interactive Windows scratch test passed twice: ASCII case/punctuation,
  Enter, Ctrl/Alt/Shift state, release of held Shift on session disconnect,
  relative pointer movement, left/right/middle buttons, and wheel. GUI/Super
  and boot-protocol behavior remain software-tested only. Run it with
  `python tools/hid_check.py --url ws://DEVICE_IP:8080/ws` and click Run test;
  it stops sending input if its window loses focus. No custom-target-OS
  acceptance has been performed yet.
- Tested firmware includes optional watchdog diagnostics. Default builds disable
  this mode. With `-DPICOUSB_DIAGNOSTICS=ON`, a five-second main-loop stall reboots
  into USB-only mode with serial `PDBFAILnn`; 01 means USB task, 02 network polling.
  No credentials, frame payloads, or memory contents are exposed. Manual BOOTSEL
  is then needed to flash another build. This mode is for bench diagnosis.

## Acceptance gates

### Raw-image / NTFS acceptance (2026-09-26)

Pico 2 W running the normal (diagnostics-off) image-mode firmware from `e5b6f8c`;
Windows build 10.0.26200, Python 3.13.7, websockets 15.0.1. The physical target
Pi 4B remained in the other development lane; no Pi 4B/5 boot testing occurred.

- [x] A disposable 1 GiB NTFS superfloppy made with `mkntfs` mounted in Windows
  with the expected capacity and label. No physical host disk was formatted.
- [x] Four distinct 64 MiB files written through actual USB and Wi-Fi/WebSockets:
  **268,435,456 bytes in 1,149.0 seconds** (0.223 MiB/s), including file flushes.
- [x] After safe eject and reattachment, all four files read through actual USB:
  **268,435,456 bytes in 1,034.4 seconds** (0.247 MiB/s). Every SHA-256 matched.
  Backend byte counters confirmed the read reached the remote image, rather
  than only Windows' previous mount cache. Total file payload: **512 MiB**.
- [x] No backend errors during either sustained transfer. Ordinary metadata
  traffic was additional to the 512 MiB of tested file payload.
- [x] NTFS directory creation, long Unicode names, overwrite, rename, and deletion
  passed while the bulk write was active.
- [x] Independent Linux `ntfscat` reads of the backing image matched all four
  expected hashes, both before and after the later interruption test.
- [x] Terminating only the image-backend process during a separate write caused
  Windows I/O failure. Pico authenticated status remained responsive. Windows
  surfaced the application error after its own retry delay, not immediately;
  the firmware request deadline is not an application-level Windows deadline.
- [x] Explicitly reattaching the retained image succeeded without reflashing or
  manual lock-file cleanup. Windows CHKDSK reported no filesystem problems and
  zero bad sectors. All four completed files remained intact, and an independent
  check verified all five MiB acknowledged within the interrupted file itself.
- [x] Read-only attachment rejected native Windows file creation with
  **ERROR_WRITE_PROTECT (19)**. First/last samples from all four files matched;
  backend counters showed zero written bytes. Read-only safe eject succeeded.
- [x] Safe eject obtained an exclusive lock before dismount/eject. Transient
  Windows handle retention required retrying the lock; no refusal was bypassed.

The transfer buffer increase followed the first slow NTFS mounting attempt with
single-sector requests. Current transfers batch at most 32 sectors (16 KiB),
with one request outstanding and durable writes before acknowledgment. Static
firmware RAM is 200,380 bytes; image size does not change that allocation.

These observations establish one substantial transfer/recovery run on this
network, not unlimited endurance. NTFS owns its journals: CHKDSK consistency and
data retention passed, but exact journal replay was not instrumented. Whole-host
power loss, AP restart, repeated physical cable interruption, and Pi boot remain
unverified. Test image and manifests are retained only in ignored `build/image-test/`.

### Original appliance gates

- [x] Provision attached RP2350 board; radio initializes as Pico 2 W.
- [x] Guest Wi-Fi connection gets an address; host reaches TCP 8080 and authenticates.
- [x] Wrong token rejected; second concurrent session rejected.
- [x] Windows enumerates keyboard, mouse, and removable MSC without a backend.
- [x] Key down/up, modifiers, ASCII shift mapping, release, pointer/buttons/wheel work on Windows.
- [x] Dropped control connection releases held Shift on Windows.
- [x] Host-backed disk mounts on Windows; nested listings and file hashes match.
- [x] Windows creates, overwrites, and deletes 8.3 files; flush/eject exports correctly.
- [ ] No backend and failed host writes produce USB errors, never success.
- [ ] Wi-Fi loss during reads/writes fails within deadline without USB event-loop deadlock.
- [ ] BOT reset and unplug/replug cancel pending operations; no stale reply touches reused buffers.
- [x] Reconnection preserves acknowledged writes in recovery image (backend-process interruption).
- [ ] Wi-Fi recovers after AP restart; multicast discovery or documented IP works.
- [x] Sustained file transfers measured with hashes and elapsed time; characterize throughput.
- [ ] Repeat storage operations on a conventional OS and on the developing target OS.

Do not inject test keyboard input into an unattended host desktop. Use a known
target/editor and intentional test content. USB tests on this workstation can
enumerate interfaces and exercise storage without sending HID input.
