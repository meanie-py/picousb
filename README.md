# picousb

A planned Pico 2 W appliance providing Wi-Fi-controlled USB keyboard, mouse, and host-backed mass storage for development of a custom OS on a Raspberry Pi.

**Status:** repository and architecture handoff established; firmware and host client are not implemented yet.

```text
Development host / AI agent
    | Wi-Fi / WebSocket: control and sector operations
    v
Pico 2 W (RP2350)
    | USB composite: HID keyboard + HID mouse + MSC
    v
Target Raspberry Pi / custom OS
    | HDMI
    v
Capture card on development host (existing, outside this project)
```

The host will expose a directory-oriented Python API (`pico_devbox.DevBox` with `storage.serve_directory(...)`). A host-managed FAT volume translates directory contents to USB sectors. The Pico carries bounded sector requests without holding the disk in memory.

See [the agreed architecture and milestones](docs/architecture.md) and [agent secret-handling rules](AGENTS.md).

## Configuration privacy

Copy `.env.flash.example` to `.env.flash` and edit it locally, outside agent sessions. The real file and generated binary/UF2 outputs are ignored. Agents must not read their contents into chat or tool logs. Future provisioning tools will consume this file locally without echoing values.

Exactly 4096 bytes at the end of the selected board's flash will be reserved for validated, immutable-at-runtime configuration. No runtime API will reveal or change credentials.

## Planned layout

- `firmware/`: Pico SDK/TinyUSB firmware, portable logic, native unit tests.
- `host/`: async Python library, CLI, host FAT backend, pytest integration tests.
- `tools/`: build and opaque configuration provisioning/flash tools.
- `docs/`: architecture, protocol, and hardware acceptance procedure.

Build and usage instructions will be added with the working implementation; there are no runnable firmware or client commands yet.
