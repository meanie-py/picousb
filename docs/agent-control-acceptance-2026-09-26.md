# Agent-control hardware acceptance — 2026-09-26

Device: Pico 2 W, connected to the Windows development PC. The target Pi 4B
remained with the other development lane throughout. Firmware-only flashing
preserved the existing configuration sector.

## Physical results

Windows enumerated all four interfaces successfully: keyboard, boot mouse,
MSC, and the new absolute pointer. The dedicated scratch window passed all
checks in two completed runs:

- Connection and focus, ASCII typing, Enter/Ctrl/Alt/Shift.
- Release of held Shift after control disconnect.
- Relative movement, all three mouse buttons, and wheel.
- Absolute positioning and click inside the designated scratch field.
- Three timed F1 presses and a raw keyboard release report.
- Completion fences for all three HID interfaces.

After a separate unplug, approximately five seconds without power, and normal
reconnection, device boot-clock milestones were:

| Milestone | Cold power-on | Reboot after flashing |
| --- | ---: | ---: |
| First USB configuration | 1,101 ms | 1,143 ms |
| First network-ready indication | 13,540 ms | 17,444 ms |

These are device-reported milestones, not stopwatch measurements of the first
authenticated host request. The later authenticated readiness check succeeded;
USB was mounted and not suspended. It reported two bus resets, one
SET_CONFIGURATION, and one SET_IDLE on each HID interface. Windows sent no
SET_PROTOCOL in this observed boot; firmware reported its default report mode.
Keyboard and relative mouse neutral reports had completed. The absolute
pointer correctly had no report until used, avoiding a startup pointer warp.

## Limits and follow-up

The measured network startup exceeds the stated roughly five-second UEFI F1
window when the Pico starts with the target. Separate-power design evaluation
is in [agent-control.md](agent-control.md); independent VSYS power and VBUS-aware
USB attachment still require wiring, firmware support, and bench acceptance.

No Pi 4B/5 USB-driver or UEFI acceptance is claimed. No physical external-power
test, Linux desktop pointer test, or actual Linux BOOTSEL flash was performed.
The Linux flash paths are covered by synthetic mount/picotool tests. Existing
MSC transport was retained; this acceptance run did not repeat bulk-storage
throughput or filesystem durability testing.
