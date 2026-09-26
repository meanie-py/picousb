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
