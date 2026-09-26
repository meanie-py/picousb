# UDP/TCP comparison diagnostic, 2026-09-26

This adds a diagnostic, not a UDP storage transport. Existing WebSocket storage,
durable writes, read-ahead, and HID behavior are unchanged. Tests use synthetic
packets with no mounted media and do not access the target Pi.

## Method

An authenticated WebSocket request arms a maximum 60-second UDP echo lease with
a fresh 128-bit host-generated cookie. The Pico requires the active TCP peer's
source IP, session ID, cookie, valid header, and packet length <=1200 bytes.
Echoes are limited to one every 2 ms. No retransmission or receive queue is added.
No packet contents or cookies are printed. Disconnect revokes the lease.
The exact format and counters are in [protocol.md](protocol.md).

The Windows probe sends paced 1200-byte datagrams and measures unique replies,
missing replies after a two-second grace period, duplicates/reordering, RTT,
arrival gaps, sender scheduling lag, and concurrent TCP request latency.
Firmware reports received-valid, echo-submitted, invalid, rate-dropped, and
send-failed counters. These distinguish deliberate rate limiting from remaining
missing replies; successful lwIP submission does not prove radio delivery.

The original asyncio-based pacing reached only 64 packets/s because Windows
short timers were quantized. The corrected sender uses Python's high-resolution
`time.sleep` off the event loop, never bursts to catch up, and reports actual
rate rather than claiming the requested rate. TCP sample collection now stops
with UDP sending. The exploratory run's 1.485-second TCP sample occurred after
UDP sending stopped and is excluded from simultaneous-stall conclusions.

## Corrected physical measurements

| Run | Duration | Actual UDP packets/s | Sent / Pico valid received | Echo submitted / host received | Explicit rate drops |
| --- | ---: | ---: | --- | --- | ---: |
| Small TCP status, requested 100/s | 30 s | 93.96 | 2819 / 2819 | 2784 / 2760 | 35 |
| Small TCP status, requested 300/s | 30 s | 261.93 | 7858 / 7858 | 7204 / 7172 | 654 |
| 16 KiB TCP batches, 100 ms pause | 45 s | 94.65 | 4260 / 4260 | 4184 / 4167 | 76 |

All three runs had zero invalid packets, duplicates, reordering, socket errors,
or firmware send failures. Every sent probe reached firmware. After accounting
for intentional rate drops, 24, 32, and 17 submitted echoes respectively were
not observed by the host. That is approximately 0.86%, 0.44%, and 0.41% of
submitted echoes. It does not distinguish radio loss, driver queues, host receive
drops, or replies delayed beyond the observation window.

UDP median RTT was 4.276, 4.128, and 4.514 ms respectively; maximum was about
111 ms. TCP maxima were 100.900, 46.420, and 112.521 ms. No corrected run above
had a TCP sample above 250 ms. Median sender scheduling lag stayed below 0.7 ms.
The rate drops show that packets sometimes arrive bunched despite paced sending;
their cause is not established. Do not label all missing replies as radio loss.

### Back-to-back bulk TCP reproduces the long stalls

A final 45-second run removed the 100 ms inter-request pause, keeping UDP paced
at requested 100/s (actual 94.18/s). It completed 3,106 TCP requests carrying
16 KiB each. TCP median RTT was 11.155 ms and p95 18.466 ms, but five requests
paused for **1,052.456, 1,291.351, 1,447.034, 1,219.699, and 1,386.881 ms**.
Their start offsets were 8.827, 13.127, 17.530, 19.262, and 29.715 seconds.

UDP continued through the same interval: 4,239 sent/received by the firmware,
4,035 echo submissions, and 4,013 unique host replies. Of 226 missing replies,
204 were deliberate rate drops and 22 were submitted echoes not observed by
the host (about 0.55% of submissions). No send failures, invalid packets,
duplicates, reordering, or socket errors were recorded. UDP median RTT was
7.621 ms, maximum 106.856 ms, and maximum gap between unique replies 113.712 ms.
Maximum sender scheduling lag was 7.160 ms.

This reproduces second-scale TCP stalls while an independent UDP flow remains
responsive. It strongly argues against a global firmware/event-loop/radio pause
as the explanation for those five stalls. TCP loss recovery or flow control is
now the leading hypothesis; these measurements do not distinguish retransmission
timeouts, a closed receive window, or another TCP-specific path issue. They also
do not identify where a missing TCP packet would have been lost. A selective UDP
read protocol is a more justified experiment now, but must recover missing
datagrams; raw UDP echo latency is not reliable disk-transfer latency.

The diagnostic implementation passed 192 Python tests, four native sanitizer
suites, Ruff and strict mypy, and Pico 2 W compilation/flash-boundary checks.
Firmware was physically flashed and these four corrected comparisons exercised
UDP and TCP concurrently. No storage media was attached during these runs.

## Reproduction

```powershell
build/windows-venv/Scripts/python.exe tools/probe_udp.py --url ws://DEVICE_IP:8080/ws --seconds 30 --pps 100
build/windows-venv/Scripts/python.exe tools/probe_udp.py --url ws://DEVICE_IP:8080/ws --seconds 30 --pps 300
build/windows-venv/Scripts/python.exe tools/probe_udp.py --url ws://DEVICE_IP:8080/ws --seconds 45 --pps 100 --tcp-bytes 16384
```

`--tcp-bytes` sends synthetic payloads to STATUS and requires argument error 8
after the full frame arrives. It never sends HID or storage commands. Add
`--tcp-interval 0` for sequential batches without the default 100 ms pause.
Limits are 1..45 seconds and 1..400 requested packets/s. Missing replies are
counted, not retried; this measures delivered latency and loss together.

Counter limitations: wrong-source and oversized datagrams are dropped before
the portable validator and are not included in its invalid count. RTT is an
end-to-end echo measurement, not one-way latency or isolated radio time.
TCP and UDP packets also differ in size, direction, and reliability semantics.
