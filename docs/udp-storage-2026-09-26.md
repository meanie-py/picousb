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

Pending updated firmware installation and Windows-host comparison. Automated
results above do not establish radio throughput, USB stability or boot behavior.
The target Pi remains outside this experiment.
