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

- [x] Provision attached RP2350 board; radio initializes as Pico 2 W.
- [x] Guest Wi-Fi connection gets an address; host reaches TCP 8080 and authenticates.
- [x] Wrong token rejected; second concurrent session rejected.
- [x] Windows enumerates keyboard, mouse, and removable MSC without a backend.
- [ ] Key down/up, modifiers, ASCII shift mapping, release, pointer/buttons/wheel work.
- [x] Dropped control connection releases held Shift on Windows.
- [x] Host-backed disk mounts on Windows; nested listings and file hashes match.
- [x] Windows creates, overwrites, and deletes 8.3 files; flush/eject exports correctly.
- [ ] No backend and failed host writes produce USB errors, never success.
- [ ] Wi-Fi loss during reads/writes fails within deadline without USB event-loop deadlock.
- [ ] BOT reset and unplug/replug cancel pending operations; no stale reply touches reused buffers.
- [ ] Reconnection preserves acknowledged writes in recovery image.
- [ ] Wi-Fi recovers after AP restart; multicast discovery or documented IP works.
- [ ] Sustained file transfers measured with hashes and latency; characterize throughput.
- [ ] Repeat storage operations on a conventional OS and on the developing target OS.

Do not inject test keyboard input into an unattended host desktop. Use a known
target/editor and intentional test content. USB tests on this workstation can
enumerate interfaces and exercise storage without sending HID input.
