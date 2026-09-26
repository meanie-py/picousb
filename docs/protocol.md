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
The Python deadline includes waiting to send. A timed-out or cancelled control
request closes the connection because the device may already have applied it;
call `reconnect()` and explicitly reattach storage before continuing. This does
not roll back completed writes or input already delivered to the USB host.

## Authentication and status

| Op | Name | Request | Success response |
| --- | --- | --- | --- |
| 1 | authenticate | session=0, A=B=0, payload=UTF-8 DEVICE_TOKEN | new session ID |
| 2 | status | A=B=0, empty payload | A=media present, B=queued HID reports |
| 3 | metrics | A=B=0, empty payload | A=layout version 1, B=6 records, 288-byte payload |
| 4 | UDP diagnostic | arm: A=1..60000 ms, B=0, random 16-byte cookie; query: A=B=0, empty payload | A=layout version 1, B=8081, five u32 counters |

When no DEVICE_TOKEN was provisioned, authenticate with an empty payload.
Authenticated operations require the returned session. Neither token nor Wi-Fi
credentials are returned. A live status response establishes network connectivity;
after connection loss network state is known locally by the client. UART startup
logs report network state codes and assigned IP, never the SSID/password/token.

### Aggregate timing diagnostics

Authenticated operation 3 returns counters since boot without resetting them.
Older firmware rejects the operation with error 8. Each 48-byte record has
little-endian layout `<IQI8I`: count (u32), total microseconds (u64), maximum
microseconds (u32), and eight non-overlapping histogram counts (u32 each).
Bins are <=100 us, <=1 ms, <=5 ms, <=10 ms, <=25 ms, <=50 ms, <=100 ms, and
>100 ms. Sampling stops at count UINT32_MAX rather than wrapping. No payload
data, addresses, filenames, or configuration values are included.

Records, in order:

1. Successful read: USB callback starting the remote request to validated host reply.
2. Successful write: USB callback starting the remote request to validated host reply.
3. Successful USB read batch: host reply/async handoff to TinyUSB data completion.
4. Duration of `cyw43_arch_poll()`.
5. Duration of `tud_task()`.
6. Start-to-start main-loop interval, including sleep and other loop work.

USB batch timing includes deferred dispatch and main-loop scheduling; it is not
wire time alone. Aborted/failed operations do not produce successful batch
samples. Counter differences isolate a test interval; maxima remain since-boot
maxima. Unsigned timer subtraction handles a microsecond timer wrap; valid
samples must span less than its approximately 71-minute period. Metrics calls
are ordinary authenticated control traffic: query before/after a measurement,
not repeatedly during it. `tools/profile_storage.py --firmware-metrics` does so.

## Optional UDP echo diagnostic

Operation 4 requires the authenticated WebSocket session. Arming resets counters;
querying does not renew the lease. The cookie is supplied by the host and never
returned through the control API. Counters are valid datagrams received, echoes
submitted to lwIP, invalid datagrams, rate-limited datagrams, and send failures,
in that order (little-endian u32). It provides no file or USB operations.

UDP port 8081 accepts 28..1200-byte datagrams only from the active TCP peer's IP.
Header `<4sI16sI` is magic `PDU1`, session ID, cookie, and nonzero sequence number;
remaining bytes are synthetic test data. A valid datagram is echoed unchanged.
No replies are sent without authentication, with a mismatched cookie/session,
after lease expiry, or after disconnect. Cookie knowledge is not protection
against a hostile network observer; this remains a trusted-network diagnostic.

Replies are limited to one every two milliseconds, with no queued catch-up and
no amplification. Counters distinguish rate drops and send failures from observed
end-to-end loss. Echo submission is not proof of delivery. UDP callback memory is
bounded; storage continues to use WebSockets. Tests should avoid IP fragmentation
(the supplied probe uses 1200-byte packets) and must report loss as well as RTT.

## HID commands

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
Only one network block batch or synchronization operation is outstanding on the Pico.
After a successful full 16 KiB read, the Pico may fetch the next sequential batch
into one additional 16 KiB buffer while USB transmits the current batch. The
prediction is clamped at the end of the medium. Smaller reads do not initiate
prediction; a requested subrange may consume a ready cached batch.

One foreground USB operation may wait behind an in-flight prediction. A different
read waits for that response and then requests its own blocks if the cache does
not cover it. Writes and flush/eject invalidate cached data and discard an
in-flight prediction before starting their own operation. Prefetch replies never
complete USB operations directly. Cache consumption and all network submissions
run from the main loop, avoiding callback reentrancy or waits inside TinyUSB.
Session/media changes and BOT reset invalidate the cache and queued ownership.
The queued foreground operation can wait for the remainder of the speculative
request's deadline before its own network deadline starts; these are per-network
request limits, not a hard end-to-end USB completion deadline.
An error/timeout on a speculative request conservatively makes media unavailable;
it does not fabricate a USB completion when no foreground request is waiting.

Read timing counters include successful speculative network fetches. USB read
counters include cache hits, so their counts need not match. Failed or unused
predictions are not evidence of data delivered to USB. Network and USB timing
intervals now overlap and their totals must not be added as elapsed wall time.
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
If the validated names and content hashes match the host baseline, publication
is skipped, preserving ordinary-file identity and timestamps. The image is still
fsynced and host changes and malformed FAT metadata are still rejected.
It stages content and a recovery marker before changing the ordinary directory.
If publication is interrupted, `.picousb/export-pending.json` blocks subsequent
exports as well as reattachment;
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

## Experimental UDP read data

Opt in with `DevBox(..., udp_reads=True)` or the CLI global `--udp-reads` flag.
The default remains WebSocket-only. After authentication, before attaching media,
opcode 5 negotiates version `a=1`, `b=0`, and a fresh random 16-byte cookie.
The reply has `a=1`, `b=8081`, no payload. Negotiation while media is attached or
an operation is pending returns BUSY. Unsupported firmware fails explicitly.
Reconnect requires a new cookie and explicit media attachment.

READ requests still arrive over WebSocket. Successful READ data travels to UDP
port 8081 in chunks of 1024 bytes (a final 512-byte chunk is allowed). Error
responses, WRITE data and durable acknowledgments, FLUSH, EJECT, HID and control
remain on WebSocket. A successful WebSocket READ response is rejected in UDP mode.

UDP header is 36 bytes, little endian, Python `<4sII16sHHI`:

| Offset | Field |
| --- | --- |
| 0 | `PDR1` data / `PDA1` acknowledgment |
| 4 | authenticated session, uint32 |
| 8 | storage request ID, uint32 |
| 12 | 16-byte negotiated cookie |
| 28 | total read bytes, uint16, 512..16384, multiple of 512 |
| 30 | data: zero-based chunk index; ACK: cumulative 16-bit received bitmap |
| 32 | CRC32 of the entire read payload, uint32 |

Data follows the header; ACKs have no payload. Maximum datagram size is 1060.
The adapter admits only the authenticated WebSocket peer's IP; the core checks
session, cookie, request, size, bounds, media generation and deadline. The host
uses a connected UDP socket and checks every ACK field. This is a session binding,
not encryption or protection against a hostile peer observing this trusted LAN.

The host keeps at most four unacknowledged chunks in flight, retransmits missing
chunks with bounded backoff, and allows three seconds per read reply. The Pico
retains one 16 KiB reassembly buffer. Duplicate and reordered chunks are safe;
the final bitmap remains available for retries of a lost final ACK. USB completion
occurs once, only after full reassembly and CRC verification. CRC failure takes
media offline. Incomplete reads time out under the existing five-second firmware
deadline. Host retry exhaustion closes the session; there is no silent transport
fallback. Disconnect revokes the cookie and media; stale packets cannot complete
a later request. UDP does not change persistence guarantees or add write caching.
