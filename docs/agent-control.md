# VibeOS bench control

See the [Windows hardware acceptance record](agent-control-acceptance-2026-09-26.md)
for exercised input behavior and measured cold-boot milestones.

The device now exposes four USB interfaces: boot keyboard (0, IN 0x81), boot
mouse (1, IN 0x82), MSC (2, OUT 0x03 / IN 0x83), and a tablet-style absolute
pointer (3, IN 0x84). HID instance numbers in the API are 0, 1, 2 respectively.
The new pointer uses Generic Desktop Mouse/Pointer usages with absolute X/Y,
not a multitouch touchscreen or pen-pressure descriptor. VibeOS needs support
for that descriptor; its boot mouse path remains available unchanged.

## Non-interactive access

Set `PICO_DEVICE_TOKEN_FILE` to a local file containing only the device token,
optionally followed by a newline. The CLI reads it internally without printing
it. This is not `.env.flash`: never point the token loader at the Wi-Fi config.
Create the file yourself in an appropriate private location; agents must not
inspect it. The library also accepts `DevBox(url, token_file=path)`.
The file takes precedence over `PICO_DEVICE_TOKEN`. If neither is configured,
interactive terminals prompt; non-interactive execution fails immediately.
An explicitly empty `PICO_DEVICE_TOKEN` supports a device with authentication
disabled. No secrets belong in command-line arguments.

```sh
export PICO_DEVICE_TOKEN_FILE=/private/path/picousb-token
pico-devbox --url ws://picousb:8080/ws status
pico-devbox --url ws://picousb:8080/ws wait-ready --timeout 30
pico-devbox --url ws://picousb:8080/ws key F1 --hold 40 --repeat 20 --interval 100
pico-devbox --url ws://picousb:8080/ws type 'hello' --delay 30
pico-devbox --url ws://picousb:8080/ws pointer click 16384 16384
pico-devbox --url ws://picousb:8080/ws raw-report keyboard 0000000000000000
```

Use the router's DNS name or numeric IP in WSL when mDNS is unavailable. Name
resolution depends on the host/router configuration; `.local` is not required.

## Observability and drain

Status includes USB configured/mounted and suspended state, lifetime bus resets,
SET_CONFIGURATION requests seen, first USB configuration and network-ready
times, and per-HID SET_IDLE/SET_PROTOCOL counts. These counters distinguish
requests observed from successful current configuration; SET_CONFIGURATION is
device-wide. SET_PROTOCOL is counted after its control status stage, SET_IDLE
at the setup callback. The current protocol value is meaningful after its
corresponding request, or can be inferred from TinyUSB's report-mode default.

For each HID endpoint, accepted/submitted/completed counts belong to an input
epoch. Separate lifetime completion counts/ages survive control reconnects.
Completion ages are null until observed. The Pico uptime clock and age arithmetic
are uint32 milliseconds (wrap around after about 49.7 days).

Every accepted HID command returns an epoch and accepted-report fence.
`drain()` waits for USB completion through that fence, not just an empty queue.
A bus reset, unmount, or control-session replacement invalidates the epoch.
USB completion proves a successful transfer to the host controller, not that
VibeOS interpreted the input or that an application acted on it. An old
completion age alone does not prove absent polling when no report is pending.

Runtime errors are one sanitized JSON line on stderr:

| Exit | JSON `error` | Meaning |
| --- | --- | --- |
| 1 | `other` | Other operation/protocol/argument failure |
| 2 | argparse usage | Invalid CLI syntax (ordinary argparse output) |
| 3 | `unreachable` | Connection unavailable/lost or network deadline |
| 4 | `auth_failed` | Authentication rejected or token configuration unavailable |
| 5 | `usb_not_mounted` | USB not configured, or USB-ready deadline expired |
| 6 | `host_not_polling` | Valid telemetry continues but HID completion deadline expires |
| 7 | `usb_reset` | Input epoch changed before drain completed |

## Timing and reconnection

Key hold duration starts after the press report completes. `--interval` is the
gap after release completion before the next press. Repetition is bounded to
10,000, and hold/gap/type delays to 60,000 ms. These are host-scheduled minimum
delays, not real-time firmware scheduling. Network stalls extend them. Cancellation
attempts release; session loss also clears held state on the Pico.

`wait-ready` retries network connections until its total deadline and by default
also requires USB configured and not suspended. `--network-only` checks only
authenticated control availability. It does not inject a polling probe or press
F1 on its own. `--auto-reconnect` / `DevBox(..., auto_reconnect=True)` reconnects
before a new operation after a known disconnect. An uncertain in-flight command
fails and is never replayed. Storage reattachment is always explicit. Wait for
readiness, then begin the F1 sequence; a power cycle during that sequence fails
it rather than unexpectedly continuing input into a later boot.

Absolute coordinates are 0..32767. To map a screenshot pixel, use
`round(pixel_x * 32767 / (width - 1))` and likewise for Y. Match the screenshot
to the OS desktop extent, scaling and monitor arrangement first. Linux/Windows
and VibeOS may map multiple monitors differently. Raw reports are fixed-size
input reports: keyboard 8 bytes, relative mouse 5 bytes (truncated to the boot
format when required), absolute pointer 5 bytes. No arbitrary USB requests or
descriptors can be sent through this escape hatch.

## Power and the early UEFI window

The Pico 2 W datasheet identifies **WL_GPIO2** as its VBUS monitor. GPIO24 on this
wireless board is not the non-wireless Pico's VBUS input. The documented dual
supply arrangement feeds an independent supply into VSYS through a Schottky
diode, with common ground; the onboard USB diode isolates the other path. Do not
short VSYS to VBUS when using both supplies. See sections 3.4–3.5 and figures 8–10
of the [official Pico 2 W datasheet](https://datasheets.raspberrypi.com/picow/pico-2-w-datasheet.pdf).

This arrangement could keep Wi-Fi and the control session alive while target
power is cycled, provided that the separate supply itself stays on. Before use,
verify the actual board and power arrangement on the bench, including no VBUS
back-feed. Firmware would need a separately validated VBUS-aware attach/detach
mode using WL_GPIO2 (after wireless initialization), clearing queued input on
detach and waiting for new configuration on reattach. The current default build
does not claim externally powered USB disconnect compliance. No unverified
wiring or automatic power switching is enabled by this change.

`first_network_ready_ms` and `first_configured_ms` measure milestones against
the Pico boot clock. Cold power-on and host-observed authenticated readiness
must be recorded separately from firmware reset/BOOTSEL reboot. Neither a
successful Windows check nor Wi-Fi association proves that the Pi UEFI F1
window is reachable. Physical Pi 4B/5 early-boot acceptance remains required.

## Linux / WSL flashing

```sh
bash tools/build.sh
bash tools/flash.sh                          # native Linux picotool
bash tools/flash.sh --mount /mnt/f           # explicitly selected WSL BOOTSEL mount
bash tools/flash.sh --part Config --mount /mnt/f
bash tools/flash.sh --part Initial --mount /mnt/f
```

The mount fallback checks the RP2350 BOOTSEL identity. It never formats a disk.
Firmware-only flashing preserves the final configuration sector. Config and
Initial consume local secrets opaquely using the existing provisioning tools;
generated artifacts must stay ignored and must never be uploaded.
