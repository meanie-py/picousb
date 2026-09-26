# Opt-in UDP read experiment — 2026-09-26

The preceding [UDP/TCP diagnostic](udp-diagnostic-2026-09-26.md) showed UDP
remaining responsive during five 1.05–1.45 second TCP bulk stalls. That supports
testing UDP storage data, but does not identify the precise TCP recovery cause.

## Scope

`--udp-reads` / `DevBox(..., udp_reads=True)` selects reliable UDP for successful
READ replies only. WebSocket retains authentication, requests, errors, HID,
writes, durable acknowledgments and flush/eject. Default behavior is unchanged.

Each read uses at most sixteen 1024-byte chunks, a four-chunk sending window,
cumulative acknowledgment bitmap, selective retransmission with capped backoff,
and CRC32 over the whole batch. There is one 16 KiB reassembly buffer on the Pico
in addition to the existing read-ahead buffer. Three-second host retry and
five-second firmware request deadlines bound failures. Loss of the control
session revokes the UDP cookie and takes media offline. See
[protocol](protocol.md#experimental-udp-read-data) for exact framing.

This deliberately does not add UDP writes, early write acknowledgments, another
journal, or automatic transport fallback. A useful first measurement compares
the same verified NTFS file reads with and without the flag, after safe eject
and reattach to avoid a Windows file-cache-only result.

## Automated verification

- 210 Python tests passed; package coverage 92.32%; Ruff and strict mypy passed.
- Five native ASan/UBSan suites passed, including new UDP read reassembly tests.
- Pico 2 W firmware compiled and passed the reserved-config-sector boundary check.
- Native tests cover malformed sizes/indices, session/cookie/request identity,
  reversed/duplicate chunks, missing final ACK retries, generation changes,
  partial abort/disconnect/timeout, CRC failure and single USB completion.
- Python tests use real loopback UDP sockets and a fake WebSocket Pico to drop
  data and a final ACK, recover the exact payload, then verify a queued write is
  acknowledged over WebSocket only after the image changes. Disconnect/reconnect
  closes the UDP endpoint and negotiates a different cookie.

## Physical acceptance

Flashed the public firmware from `0c927ca`, preserving the configuration sector.
Windows mounted the existing disposable 1 GiB NTFS image, read-only. The target
Pi remained outside this experiment. The 18 UDP-specific Python tests also
passed on the Windows runtime (in addition to Linux's full test suite).

The four-packet UDP window read `PicoUSB transfer 0.bin` (64 MiB) in **246.740 s,
0.259 MiB/s**. Every 1 MiB block matched its deterministic generator; SHA256 was
`5043231cc01ca41e74d97918be1830bd3e3c93d292538487d74d09a3317e6283`.
Safe eject completed. Backend totals include mount metadata and Windows read-ahead:
68,678,144 bytes in 4,220 reads, zero storage errors, 123 retransmitted datagrams.
These retries are not a packet-loss count: a missing ACK can also cause a retry.

UDP host service median/p95/max were 25.874/35.481/321.563 ms. Disk access totaled
only 0.291 seconds. Firmware network-read wait totaled 228.870 seconds with a
4,407.030 ms maximum; USB data handoff totaled 108.128 seconds, maximum 34.960 ms.
These intervals overlap and must not be added. Wi-Fi poll maximum was 7.801 ms.
The long firmware wait alongside much shorter host service is consistent with
delay before the host receives a READ request, which still uses TCP. It does not
identify the precise TCP recovery mechanism. The four-chunk UDP window also adds
multiple ACK round trips per batch. This experiment has not established a speed
advantage; default transport remains WebSocket.

After safe eject and a fresh attachment, the same file over WebSocket read in
**179.666 s, 0.356 MiB/s**, again with every block and the full hash matching.
This is the comparison from the same firmware and test session, rather than the
earlier 0.577 MiB/s result under different network conditions.

| Transport | Verified file size | Elapsed | Throughput | Storage errors |
| --- | --- | --- | --- | --- |
| UDP read data, window 4 | 64 MiB | 246.740 s | 0.259 MiB/s | 0 |
| WebSocket | 64 MiB | 179.666 s | 0.356 MiB/s | 0 |

UDP was about 27% slower in this pair. WebSocket backend totals were 67,530,752
bytes in 4,149 reads. Firmware network-read time increased by 127.524 seconds;
USB handoff time increased by 97.475 seconds. Host service median was 0.309 ms,
but that measures a buffered WebSocket send, whereas UDP service waits for ACKs;
those host-service measurements are not equivalent network-completion timings.
The file-throughput comparison is the relevant end-to-end result. Backend byte
totals differ slightly due to filesystem metadata and speculative reads.

Writable acceptance then used a fresh UDP-enabled session on the same disposable
image. Windows created and flushed a deterministic 2 MiB file, overwrote one
512-byte sector, and safely ejected. A new read-only session mounted the image
and verified all 2,097,152 bytes, including the overwrite, through physical USB.
SHA256: `f13a55ffa05506abb040eb1f09594ffe1ab60f293d1a592316d3d3e6a5de4ceb`.
Writes remained on WebSocket; no storage errors were reported. Final media was
safely ejected and backend processes exited.

Keep the flag experimental. A next controlled experiment could move the small
READ requests onto the reliable UDP exchange too, then compare window sizes,
while retaining WebSocket authentication/control and durable writes. Current
measurements do not prove that this will be faster or identify a TCP stack bug.
This acceptance covers 128 MiB of matched large reads and a small persisted
write, not a multi-hour soak. No boot, power-loss, AP failure, or filesystem
crash-recovery acceptance is implied.
