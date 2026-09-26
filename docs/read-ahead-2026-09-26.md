# Bounded read-ahead, 2026-09-26

## Implementation

`firmware/src/storage_pipeline.c` separates the USB foreground operation from
one optional network prefetch. TinyUSB retains its existing transfer buffer;
the pipeline adds one 16 KiB cache. It never pipelines or acknowledges writes
early. The host protocol and its durable-write contract are unchanged.

After a full-sized successful read, the main loop requests the next sequential
batch while USB sends the previous one. A ready cache hit completes the queued
USB read without another network request. A miss waits for the sole outstanding
prefetch, then requests the actual blocks. Partial cache subranges are bounded
and consumed once. Predictions stop at the disk boundary and do not follow
small metadata reads.

Writes and flush/eject clear cache/hints and discard outstanding prediction data
before their own network operation. Session/media generation changes prevent
reattached media from inheriting cache contents. BOT abort clears every retained
pointer and request owner; late replies remain rejected by the existing core.
Failure or timeout of speculative I/O conservatively makes media unavailable.
There is at most one network request and one queued foreground USB operation.
All completions and submissions are deferred to normal event-loop execution;
USB callbacks never wait for networking. A queued foreground operation can wait
behind the remaining prefetch deadline as documented in protocol.md.

The RP2350 linker reports the pipeline structure as 16,432 bytes, including its
16,384-byte cache. No disk-sized allocation, flash journal, or persistent Pico
storage is introduced. Normal Wi-Fi power management is restored for this build.

## Verification

Four native sanitizer suites pass, including the new pipeline suite. Cases cover
in-flight and ready hits, subrange reads, random misses, deferred writes and
durable acknowledgment, flush/eject ordering, speculative failure/timeout,
disconnect, media replacement within a single loop, BOT abort/late replies,
end-of-disk clamping, invalid sizes/LBAs, and read-only write rejection.
The 185 Python tests, Ruff, strict mypy, Pico 2 W build, and reserved flash
boundary check also pass.

Physical Windows tests use only the retained disposable 1 GiB NTFS image:

- Same deterministic 4 MiB application read: **17.844 s**, versus **21.624 s**
  for the earlier instrumented default-power baseline (17.5% less elapsed time).
  This is one short comparison with Windows read-ahead, not a universal rate.
- Median host-observed inter-request gap fell from about **35.2 ms to 24.0 ms**.
- Scattered first/last samples of all four original 64 MiB files matched.
- Created a new 2 MiB synthetic file, read it, overwrote one sector, and fsynced.
  After safe eject and read-only reattachment, its entire contents matched the
  independently regenerated expected data including the overwritten sector.
- NTFS directory creation, Unicode names, overwrite, rename, and deletion passed.
- No backend errors in those runs. Network stalls remain; one read wait reached
  about two seconds. Overlap improves ordinary operation but does not solve that
  underlying intermittent network delay.

The longer sequential test then verified all **64 MiB in 110.914 seconds**:
**0.577 MiB/s (4.84 Mbit/s)**, including deterministic expected-data generation
and comparison on the host. SHA-256:
`5043231cc01ca41e74d97918be1830bd3e3c93d292538487d74d09a3317e6283`.
The fresh read-only session served 69,681,152 backend bytes across 4,281 reads,
including mount traffic, the preceding 2 MiB overwrite verification, and prefetch.
There were zero backend errors. Median inter-request gap was 23.817 ms.

The earlier 256 MiB acceptance run measured 0.247 MiB/s reads; the new result
is encouraging but differs in transfer length and run conditions. The small
same-request comparison above is the closer controlled before/after check.
No write-speed improvement is claimed. All media was safely ejected, and the Pico
remains on the read-ahead firmware with normal Wi-Fi power management.

Physical cable/BOT-reset stress and AP failure with this cache remain open;
their cancellation paths have native coverage, not new physical acceptance.

With overlap, network timing and USB timing totals cover concurrent intervals:
do not add them to estimate wall time. Network counters include unused prefetch;
USB counters include cache hits, so their counts need not match.
