# Agreed architecture

Accepted 2026-09-26. This records decisions from the project handoff and its review, not completed functionality.

## Purpose and hardware

The appliance enables an AI-assisted development host to exercise real USB on a custom OS running on a target Raspberry Pi. Existing UART work has reached the point where USB development is needed. The host already receives target HDMI through a capture card; video transport is outside scope.

The ordered product is a Pico 2 W. Windows detected an RP2350 boot device, consistent with that family, but radio presence and operation still require firmware verification. Use Pico SDK board `pico2_w` and RP2350, superseding the original RP2040/Pico W request. Retain board-independent logic; original Pico W compatibility is optional until separately verified.

Use Pico SDK C/C++ and TinyUSB. Expose HID keyboard, HID mouse, and MSC over one target-facing USB connection. Initial text typing assumes a US keyboard layout. Reserve GP0 TX / GP1 RX for optional future UART; existing serial and capture workflows remain separate.

## Configuration and secrets

Reserve exactly the final 4096 bytes of the configured board flash using linker constraints and an independent image-boundary check. Derive addresses from the selected board's flash size; do not reuse the original Pico W address on Pico 2 W.

Provision locally from `.env.flash`: WIFI_SSID, WIFI_PASSWORD, optional DEVICE_TOKEN, and tolerated unknown keys. Document a versioned binary format containing magic, payload length, and CRC32. Validate before use, fail clearly with sanitized diagnostics, and provide no runtime config write or secret-read API.

The user's privacy requirement includes keeping credentials out of agent context, conversation logs, source control, and uploaded artifacts. A local tool may read the file internally, but agents must never inspect it or generated secret-bearing images. Tests use synthetic data only. Follow AGENTS.md.

## Network, control, and USB

Use a versioned WebSocket protocol with request IDs, explicit responses, fixed size limits, checked lengths, bounded queues, and authentication when DEVICE_TOKEN is configured. One controlling/storage session is sufficient. Reject additional sessions rather than implicitly replacing ownership.

This is a trusted-network appliance using ws:// initially. WPA protects the wireless link, not end-to-end traffic; do not present this as secure against hostile LAN peers.

Initialize USB independently of network availability. Report unavailable storage as not ready. Reconnect Wi-Fi with bounded backoff. Release all held keys/buttons on control-session loss. Cancel pending operations on loss, timeout, or media detach; reject stale replies after reconnect using session/media identity as well as request IDs. Announce media changes when replacing the backing volume.

Keep keyboard/mouse state, ASCII mapping, protocol validation, and storage request state machines independent of hardware adapters. Implement the full requested key/button down/up, press/click, release-all, typing, motion, and wheel operations. UART streaming may follow the core slice and must not delay it.

## Storage choice and synchronization contract

Use the host-side FAT implementation and block proxy alternative for version one. The public API remains directory-based: `await box.storage.serve_directory(path)`. The host may internally create a small FAT image and durable recovery journal; users do not manage disk images manually. Start with a fixed small volume (for example 32 MiB) and document filename rules before implementation. FAT 8.3 is acceptable initially; long names are desirable.

Import the directory on media attach. The target exclusively owns the mounted FAT volume; concurrent host edits are unsupported. Do not rebuild allocation or directory metadata while mounted. Optimize enumeration or data transfer only after measuring a working first version.

Arbitrary sector writes may leave temporary inconsistencies between FAT, directory entries, and file data. Acknowledging a sector write therefore means its bytes are durably stored on the host, not that it has already become a coherent ordinary file. Persist sectors before acknowledging; never acknowledge an offline or failed write.

Export validated filesystem state to ordinary files at explicitly implemented synchronization points. Support flush and safe eject; safe eject is the strongest normal completion boundary. Do not treat an idle timer alone as proof of filesystem consistency. Record the precise export/flush contract in protocol documentation and tests. Creation, overwrite, deletion, listing, and reads must work by final acceptance.

Preserve recoverable host image/journal state after interruption and do not propagate a corrupt partial snapshot or speculative deletions into the directory. Reconnecting must not silently create a fresh image over unsynchronized target writes. Failed requests cannot roll back earlier acknowledged writes; OS-cached writes never delivered to the device cannot be recovered.

Confine all imports/exports to the selected root. Account for path traversal, malicious FAT names/metadata, symlinks, junctions, and destination replacement races. Use bounded validation, checked cluster chains, and explicit rejection of unsupported entries.

## USB timing

Pin a Pico SDK/TinyUSB combination supporting asynchronous MSC completion and prove it with a small hardware experiment. USB callbacks enqueue bounded sector work; Wi-Fi/WebSocket processing continues independently. Apply deadlines and explicit SCSI failure/not-ready behavior. Never synchronously fetch network data inside a USB callback. The Pico buffers only a small bounded number of sectors, never a full image or large file.

## Delivery milestones

1. Board bring-up and radio verification; reserved flash config with opaque provisioning, native parser/CRC tests, linker boundary validation.
2. Authenticated WebSocket connection and HID keyboard/mouse on the actual target; portable state-machine tests and Python fake-server tests.
3. Read-only directory-backed FAT over asynchronous MSC; demonstrate target enumeration, directory listing, and correct reads.
4. Durable writes, validated directory export, creation/overwrite/deletion, flush/eject, disconnect/reconnect and recovery; exercise conventional desktop OS and custom target OS behavior.
5. Complete Python async API/CLI, reconnect/error handling, protocol documentation, build/flash workflows, and CI. Optional UART follows core reliability.

## Verification and acceptance

Use native firmware tests with fake flash/USB/network transports, pytest with temporary directories and a fake Pico WebSocket server, formatting/lint/type checks, schema/protocol conformance, and actual Pico 2 W cross-compilation in CI. Validate FAT output with independent Linux filesystem tools when practical. CI configuration must fit the Gitea runner available on Sunspot; do not assume a GitHub runner exists.

Cover corruption, absent/unknown config keys, input size boundaries, malformed envelopes, authentication, modifier/report ordering, mouse state, request IDs, timeouts, concurrent host requests, cancellation, stale responses, path containment, FAT chains, and failed persistence. Include tests proving validation errors never echo secret values.

Physical acceptance separately verifies Wi-Fi, composite enumeration, HID behavior, mounting/reads/writes, sustained transfers under network latency, unplug/reconnect, no-backend errors, and recovery. Automated success alone does not satisfy those checks. USB power loss on the target may also power down the appliance unless a suitable separate power arrangement is provided.
