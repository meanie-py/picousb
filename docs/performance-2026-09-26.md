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

The next baseline firmware adds these aggregate counters through authenticated
operation 3 (see protocol.md), with no power-management or buffering change.
Use `--firmware-metrics` with the network/device probes to collect before/after
snapshots. This instrumentation is software-tested; new hardware measurements
must be recorded separately after flashing. It adds six fixed-size accumulators
and a small TinyUSB per-read-batch completion hook, not packet logging.

### Instrumented physical baseline

Flashed the instrumented normal build with default Wi-Fi power management.
The same disposable read-only volume served a verified 4 MiB application read
in 21.624 seconds. Including mount traffic and Windows read-ahead, the backend
served 8,778,240 bytes over 564 read requests (528 were 16 KiB), with no errors.
Both network and USB firmware counters recorded exactly 564 completions:

| Firmware stage | Total | Mean per batch | Maximum |
| --- | ---: | ---: | ---: |
| Remote read request to validated reply | 9.009038 s | 15.974 ms | 1,316.273 ms |
| Reply/async handoff to USB data completion | 12.928364 s | 22.923 ms | 25.477 ms |

USB timing is 59% of these two accumulated stages, network wait 41%. This is
not a pure wire-time split: the USB stage includes deferred dispatch and loop
scheduling, and the network stage includes host service. Most USB batches were
10–25 ms; network timing had two samples above 100 ms. During the entire measured
interval, Wi-Fi polling consumed 4.088680 seconds and USB task calls 0.226331
seconds. Poll CPU time overlaps the network/USB stages; do not add it to them.
Maximum Wi-Fi poll call since boot was 7.714 ms. Counters reveal no multi-second
poll call corresponding to the 1.316-second network stall; packet/power/network
causes are still unresolved. The test ended with successful safe eject.

Build the next one-variable comparison with
`PICOUSB_WIFI_NO_POWERSAVE=ON bash tools/build.sh`. Normal builds explicitly
default this option to OFF, avoiding a sticky CMake experiment setting. The
comparison selects the pinned driver's `CYW43_NONE_PM` after enabling station
mode (which applies the driver's default); failure to apply it
leaves USB responsive but does not start networking, so it cannot masquerade
as a successful comparison.

### Power-saving-disabled physical comparison

Flashed the comparison build. Repeated the same verified 4 MiB application read:
25.096 seconds, 8,782,336 backend bytes, 565 requests (528 were 16 KiB), zero
backend errors, and successful safe eject. One extra 4 KiB metadata request
occurred compared with the baseline.

| Firmware stage | Total | Mean per batch | Maximum |
| --- | ---: | ---: | ---: |
| Remote read request to validated reply | 12.539894 s | 22.194 ms | 1,369.898 ms |
| Reply/async handoff to USB data completion | 12.923680 s | 22.874 ms | 26.987 ms |

The network histogram had five samples above 100 ms. USB time was effectively
unchanged; disabling power saving did not eliminate the large network stalls or
improve this run. This is one sequential A/B pair, not randomized evidence that
power saving is universally faster. Do not promote the experiment to the default.
The Pico was left on this functioning comparison build with no media attached;
normal build commands still select default power management.

The measured USB handoff is a substantial fixed part of each sequential batch.
The pinned TinyUSB RP2 driver already double-buffers bulk IN USB packets; adding
another USB packet buffer is not the same as overlapping network and USB work.
The next performance implementation candidate is a bounded read-prefetch buffer
to overlap those stages. It needs explicit ownership/cancellation tests for BOT
reset, disconnect, random reads, media replacement, and intervening writes.
Retain durable write acknowledgment and do not promise a throughput multiplier
before measuring. Firmware timestamps identify where time is spent, but have
not yet identified the cause of the intermittent network stalls.

Validation at this checkpoint: 185 Python tests (91.26% host coverage), all three
native sanitizer suites, Ruff and strict mypy passed. Default and experimental
Pico 2 W builds and reserved-configuration boundary checks passed. The TinyUSB
hook was also tested against fresh pinned upstream source, repeated application,
and upgrade from the existing async patch. These are local checks; no new CI
success is claimed here.

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
