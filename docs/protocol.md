# Pico DevBox protocol v1

Endpoint: `ws://<device>:8080/ws`. Discovery: DHCP hostname `picousb`, mDNS
`picousb.local` where multicast crosses the chosen network. One TCP/WebSocket
client at a time, including unauthenticated clients. Additional clients are
disconnected. Complete authentication within 10 seconds. No TLS or compression;
use a trusted network with host-to-device reachability.

## WebSocket transport

RFC6455 HTTP upgrade at `/ws`, version 13, masked client frames, unmasked server
frames. Binary messages only. Ping/pong supported. Fragmentation, extensions,
text frames, RSV bits, and messages over 16412 bytes are rejected by disconnecting.
The supplied Python client uses unfragmented frames and disables compression.
HTTP headers are limited to 2048 bytes; the bounded TCP accumulator is 17880
bytes (maximum envelope, frame overhead, and one TCP segment). Invalid envelopes
close the session rather than echoing their contents. Bulk buffers are static,
not allocated on the firmware stack. Upgrade host and firmware together: early
0.1 clients supported only 1024-byte payloads and single-sector requests.

## Binary envelope

All integers are little endian; Python layout is `<4sBBHIIIII` (28 bytes).

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | ASCII `PDB1` (magic and protocol version) |
| 4 | 1 | Operation; responses set bit 7 |
| 5 | 1 | Status; requests must use zero |
| 6 | 2 | Reserved, must be zero |
| 8 | 4 | Nonzero request ID |
| 12 | 4 | Connection session ID |
| 16 | 4 | Argument A |
| 20 | 4 | Argument B |
| 24 | 4 | Payload length, 0..16384 |
| 28 | length | Payload |

One WebSocket message contains exactly one envelope plus its payload. Control
request IDs increase strictly within `1..0x7fffffff`; no replay or reuse.
Firmware storage IDs occupy `0x80000001..0xfffffffe` and do not repeat during
one boot. Responses echo operation (plus bit 7), ID, and session. New connections
receive a fresh nonzero session ID. Reconnect never resumes old pending requests.

Errors: 0 success, 1 protocol, 2 authentication, 3 capacity, 4 busy, 5 offline,
6 I/O, 7 timeout (reserved), 8 invalid arguments. Errors contain no secret,
payload excerpt, local path, or exception text. Local Python failures use
`DevBoxError` with a numeric `code`; local request deadlines raise `TimeoutError`.

## Authentication and status

| Op | Name | Request | Success response |
| --- | --- | --- | --- |
| 1 | authenticate | session=0, A=B=0, payload=UTF-8 DEVICE_TOKEN | new session ID |
| 2 | status | A=B=0, empty payload | A=media present, B=queued HID reports |

When no DEVICE_TOKEN was provisioned, authenticate with an empty payload.
Authenticated operations require the returned session. Neither token nor Wi-Fi
credentials are returned. A live status response establishes network connectivity;
after connection loss network state is known locally by the client. UART startup
logs report network state codes and assigned IP, never the SSID/password/token.

## HID

| Op | Operation | A | B | Payload |
| --- | --- | --- | --- | --- |
| 10 | keyboard.key_down | HID usage | 0 | empty |
| 11 | keyboard.key_up | HID usage | 0 | empty |
| 12 | keyboard.press | HID usage | modifier mask | empty |
| 13 | keyboard.release_all | 0 | 0 | empty |
| 14 | keyboard.type_text | 0 | 0 | up to 64 ASCII bytes |
| 20 | mouse.move | signed X | signed Y | empty |
| 21 | mouse.button_down | button bit | 0 | empty |
| 22 | mouse.button_up | button bit | 0 | empty |
| 23 | mouse.scroll | signed wheel | 0 | empty |
| 24 | mouse.click | button bit | 0 | empty |
| 25 | mouse.release_all | 0 | 0 | empty |

Keyboard uses the standard boot keyboard report: modifier byte plus six key
usages. Modifier usages 224..231 map to mask bits 0..7 (left Ctrl/Shift/Alt/GUI,
right Ctrl/Shift/Alt/GUI). Ordinary usages 4..115 accepted. A seventh held key
fails without changing state. Press temporarily adds the modifier mask and key,
then restores the held state; pressing an already held ordinary key is busy.
Typing requires no held keys/modifiers; ASCII 32..126 plus backspace, tab, CR,
LF map to US-layout reports. Unsupported text rejects the whole command.

Mouse buttons: left=1, right=2, middle=4. Motion/wheel are signed 32-bit values
encoded in A/B, constrained to -127..127. The Python API chunks larger motion.
HID responses acknowledge bounded queue acceptance, not target application
consumption. `await box.drain()` waits until reports are submitted to USB; it
cannot prove that the target OS handled them. Queue capacity is 256 reports.
Disconnect clears queued actions and emits keyboard/mouse release reports.

## Storage attachment and sector transport

| Op | Direction | Meaning |
| --- | --- | --- |
| 30 | host to Pico | attach: A=1..2147483647 sectors, B=512 bytes; empty payload means writable, or one byte 0=writable / 1=read-only |
| 31 | host to Pico | detach: empty payload; rejected while I/O pending |
| 40 | Pico to host | read: A=LBA, B=1..32 sectors; response payload exactly B*512 bytes |
| 41 | Pico to host | write: A=LBA, B=1..32 sectors; request payload exactly B*512 bytes |
| 42 | Pico to host | flush/export: A=B=0, empty payload |
| 43 | Pico to host | eject/export: A=B=0, empty payload |

Writes respond only after the host image write and `fsync` succeed. Success
responses to writes, flush, eject, and control operations have empty payloads.
Only one block batch or synchronization operation is outstanding on the Pico.
Each batch has a five-second deadline; export/flush/eject has 30 seconds. Late,
duplicate, wrong-session, and unexpected responses disconnect the control peer.
Timeout, backend failure, or disconnect fails the USB operation and removes
media. No upload is cached awaiting a future client.

Target SCSI READ(10)/WRITE(10) use asynchronous callbacks. SYNCHRONIZE CACHE(10)
and START STOP UNIT (stop/eject) use a small pinned TinyUSB extension to defer
the command-status wrapper until the host replies. IMMED flush is rejected.
Media replacement signals unit attention. BOT reset cancels outstanding I/O;
an interrupted operation requires reattachment. No flash writes occur at runtime.

Capacity is reported from the active attachment. Read-only media reports SCSI
write protection and rejects WRITE(10) with DATA PROTECT / WRITE PROTECTED
(sense 07/27/00), before any network write. The host backend independently rejects
writes too. Batches cannot cross the end of the image. No write-back cache exists
in Pico RAM; a disconnect can leave earlier acknowledged batches intact and the
last unacknowledged batch in an indeterminate state.

## Raw images

`serve_image(path, read_only=True)` exposes the complete existing regular file,
including partition tables, boot sectors, and filesystem journals. Files must
contain whole 512-byte sectors, with capacity below 1 TiB. No parsing, conversion,
or sector-size translation is performed: use images prepared for 512-byte logical
sectors, not 4Kn media or CD-ROM layouts. There is no directory export or
filesystem repair. NTFS, FAT, and other layouts
are transported identically. Use `read_only=False` explicitly for writes to the
original image, and use a disposable copy for experiments. This is not an overlay.

An exclusive operating-system lock prevents another cooperating image server
from attaching the same file and is released automatically on process exit.
Do not edit, truncate, mount, or replace the image concurrently. Flush/eject
fsyncs writable images. Filesystem recovery belongs to the USB host OS; durable
sector acknowledgments do not make multi-sector transactions atomic or recover
writes still in the target's cache. Reattach is explicit after a disconnect.

## FAT and directory ownership

32 MiB FAT16 superfloppy, 512-byte sectors, 4 sectors/cluster, 1 reserved sector,
2 FATs of 128 sectors each, 512 root slots (32 sectors), data beginning at sector
289. No partition table. Nested directories supported to depth 16; at most 8192
entries. ASCII FAT 8.3 names in the host view; valid target-created long-name
records are checked but exported using their DOS aliases (long spelling is not
preserved in v1). Case-insensitive collisions, reserved Windows names, invalid
long-name metadata, unsupported geometry, malformed chains, crosslinks,
orphan allocations, symlinks, and Windows reparse points are rejected.

`serve_directory` imports once into `.picousb/disk.img`. The existing recovery
image takes precedence on later attaches. The target exclusively owns the
volume while mounted; host changes make export fail. Internal state is excluded
from the FAT view. Images live on the host, not in Pico RAM/flash.

A flush/eject validates the complete graph before exporting files and deletions.
It stages content and a recovery marker before changing the ordinary directory.
If publication is interrupted, `.picousb/export-pending.json` blocks reattachment;
the stage, backups, and image remain available for explicit recovery. Successful
exports remove staging and backup copies. A process crash may leave `.picousb/lock`;
remove only that lock after confirming no process owns the volume. Do not delete
the recovery image to resolve an error: it may contain acknowledged target writes.

Sector persistence and ordinary-file publication are different guarantees. An
idle timeout is not a flush. Safe eject is the intended completion boundary.
Closing/disconnecting the host does not implicitly export possibly inconsistent
metadata. Host filesystem durability ultimately depends on OS/filesystem/device
flush guarantees; directory-wide publication is not a single atomic filesystem
transaction. Concurrent local adversarial mutation of the backing root is outside
the exclusive-ownership contract; use a dedicated directory with restricted access.

UART streaming is deferred. GP0/GP1 currently carry diagnostic UART at 115200 baud.
