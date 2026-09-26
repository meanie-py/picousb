# Initial performance isolation, 2026-09-26

Measured after host reliability commit `3ffccf7`, with the existing normal Pico
firmware unchanged. Windows host; real Pico 2 W; existing disposable NTFS image.
No target Pi access, HID input, firmware flash, or durability changes occurred.

## Measurements

| Probe | Result |
| --- | --- |
| Local ImageDisk 16 KiB write + fsync + asyncio worker dispatch | median 2.687 ms, p95 3.612 ms; 128 operations |
| Local ImageDisk 16 KiB read + worker dispatch | median 0.119 ms, p95 0.192 ms; 128 operations |
| Small authenticated device status, before mounting | median 4.201 ms, p95 11.390 ms; 50 operations |
| Host service during physical USB reads, including worker dispatch and WebSocket send | median 0.296 ms, p95 0.457 ms; 1,080 storage operations including eject |
| Image read alone during that session | median 0.042 ms, p95 0.072 ms; 1,079 reads |
| Gap from completed host response handling to next storage request | median 36.807 ms, p95 51.334 ms |

The application read 8 MiB in 66.258 seconds. The backend actually served
17,231,872 bytes including mounting and Windows read-ahead, mostly 16 KiB
requests (1,044). Do not divide the application bytes by this time and treat it
as device payload throughput. The host spent only 0.3342 seconds servicing the
session's requests, with zero backend errors. Gaps include USB, network, device
processing, and host idle time; their total includes deliberate pauses. They
are not a measurement of radio latency alone. WebSocket send completion means
local transport acceptance, not confirmation the Pico has received the bytes.

The image was attached read-only and safely ejected. The application's SHA-256
was `01c505c79ce1c51d727cf3cfafdb3d81a7c1bdf8db8139c903148357cba4a3a9`;
it matches the independently regenerated first eight 1 MiB synthetic blocks
from the acceptance test's deterministic data generator.

## Network-only isolation

The existing STATUS operation rejects a nonempty payload with error 8 after
receiving the complete frame. An authenticated probe sends synthetic payloads
and verifies that exact rejection. This avoids media/HID operations and firmware
changes. It measures host-to-Pico upload plus a tiny reply, not sustained TCP
capacity or the reverse bulk direction.

Each of three runs sent 100 requests per payload size, sequentially:

| Payload | Run 1 MiB/s | Run 2 MiB/s | Run 3 MiB/s | Median request time across runs |
| --- | ---: | ---: | ---: | --- |
| 512 bytes | 0.144 | 0.026 | 0.137 | 2.787–2.799 ms |
| 4 KiB | 0.609 | 0.652 | 0.715 | 4.327–5.318 ms |
| 16 KiB | 0.594 | 0.387 | 0.300 | 11.497–12.075 ms |

Median and p95 timings alone hide occasional large stalls. For example, the
third 16 KiB run took 5.2129 seconds for 100 requests despite a median of
11.497 ms and p95 of 24.748 ms. The probe now also reports the maximum latency
for subsequent runs; the three recorded runs predate that output field.

## Interpretation and next experiment

Host Python/disk service is not the dominant cost in the measured read path.
Local durable writes are also much faster than the historical end-to-end batch
rate, although a physical write profile is still needed to quantify that path.
Do not disable fsync based on these results.

Network/device delay is significant even without USB. The physical storage path
adds another serialized stage; the current single buffer cannot overlap the
next network fetch with the current USB transfer. These results do not isolate
Wi-Fi power saving, packet retransmission, firmware polling, or USB scheduling.
Small-request latency does not predict bulk throughput.

Next useful experiment: add bounded aggregate firmware timestamps around storage
request submission, reply arrival, and USB transfer completion. Separately record
main-loop/poll latency. Compare an otherwise identical power-saving-disabled
build only after collecting that baseline. Preserve one-variable comparisons,
write acknowledgments, cancellation behavior, and bounded buffers. Instrument
counts/times only: no authentication payloads, packet dumps, or configuration.

## Reproduction

Use the Windows environment with the editable host package installed:

```powershell
build/windows-venv/Scripts/python.exe tools/profile_storage.py local
build/windows-venv/Scripts/python.exe tools/profile_storage.py network --url ws://DEVICE_IP:8080/ws
build/windows-venv/Scripts/python.exe tools/profile_storage.py device --url ws://DEVICE_IP:8080/ws --image build/image-test/ntfs.img
```

Local mode creates and deletes only its own synthetic temporary image beneath
`build/profile`. Network/device modes consume `.env.flash` opaquely and suppress
exception details. Device mode attaches read-only and waits for safe eject;
perform guarded USB reads in a separate terminal. `tools/image_stress.py` checks
Pico identity, capacity, filesystem, and the disposable volume label before
performing its actions. Never substitute a physical disk for the test image.

Device timing distributions retain at most 65,536 samples each; cumulative byte
and request-size counters cover the whole session. Periodic summaries are
cumulative/rolling snapshots, not independent ten-second measurements.
