# Raw image acceptance

Use disposable regular files. These tests do not touch the target Raspberry Pi,
and do not establish boot compatibility with Pi 4B or Pi 5. The Pico remains
connected to the development Windows PC over USB and Wi-Fi.

## Prepare an NTFS test image

In WSL, install `ntfs-3g` and create a **new** image under ignored build output:

```sh
mkdir -p build/image-test
test ! -e build/image-test/ntfs.img && \
  truncate -s 1G build/image-test/ntfs.img && \
  mkntfs -F -Q -L PICOUSBTEST -s 512 -c 4096 build/image-test/ntfs.img
```

Here `-F` permits formatting the regular file. Never substitute a physical disk
path. This is an NTFS superfloppy for data testing, not a bootable Windows image.
A partitioned boot image can be served unchanged by the same backend.

From Windows, with the host package installed:

```powershell
python tools/device_session.py --image build/image-test/ntfs.img --writable
```

This helper consumes local credentials opaquely. It prints only status and byte
counters, never configuration or frame payloads. Leave it running. Windows should
recognize a 1 GiB `PICOUSB REMOTE DISK`, serial `PDB0001`, labeled `PICOUSBTEST`.

## Physical endurance and integrity

In another Windows terminal, substitute the actual drive letter:

```powershell
python tools/image_stress.py write --drive F
python tools/image_stress.py eject --drive F
# Restart device_session.py with the same image, then:
python tools/image_stress.py read --drive F
python tools/image_stress.py eject --drive F
```

The test refuses to operate unless disk identity, serial, size, NTFS filesystem,
and disposable label all match. It creates four 64 MiB files with distinct
deterministic pseudorandom content, fsyncs each, and records SHA-256 hashes on the
development host. After eject/reattach it reads and verifies all 256 MiB again.
The names contain spaces and exceed 8.3, exercising NTFS rather than directory
mode's FAT implementation. The total is 512 MiB of file payload, plus metadata.
Reattachment prevents the verification from relying on the previous mount's
Windows file cache. Do not bypass a volume-lock/eject failure; close other handles
and retry. Existing test files are never silently overwritten.

## Recovery and write protection

On disposable media only, stop the backend during a separate test-file write.
The target should report an I/O failure and the Pico should remove the media.
Restart the backend and explicitly reattach the same image. Let Windows mount
and replay its journal, check filesystem consistency with Windows `chkdsk`, and
recheck hashes of previously completed files. Keep the interrupted file out of
the success criteria: its unacknowledged/cached writes may be absent.

`python tools/image_stress.py fault-write --drive F` provides the separate
interruption workload. Wait for its acknowledged-byte output before stopping
the backend. It creates a new `Interrupted transfer.bin`; an existing file is
an error. After reattachment and filesystem checks, the `sample` action verifies
known first/last bytes of the four large files. The `metadata` action tests a
new disposable directory, Unicode names, overwrite, rename, and deletion.

After a clean eject, serve without `--writable`. Windows writes must fail and
reads must still match. No network storage write should reach the backend.
Use the `protected-write` action: it calls the native Windows file API to require
ERROR_WRITE_PROTECT (19). Python's regular file API can reduce that error to
generic EACCES, which is not enough evidence by itself. The `eject` action accepts
this explicit write-protection result when flushing read-only media, but still
requires an exclusive volume lock and successful dismount/eject.

NTFS journaling is implemented by Windows, not PicoUSB. A successful sector write
means `fsync` succeeded on the backing image; it is not an atomic filesystem
transaction. This test can establish recovery from a lost backend process or
connection, not survival of host SSD failure or whole-system power loss.
Raw images have no additional PicoUSB write-ahead journal or copy-on-write layer.
Do not independently mount the same image while it is being served.

Automated tests cover random multi-sector writes/reads, size and boundary checks,
read-only enforcement, fsync failure propagation, exclusive image locks, and
process exit immediately after an acknowledged write. Native tests cover capacity,
bulk USB completion, write-protection sense, timeout/disconnect, and BOT abort.
Record physical results separately in [hardware acceptance](hardware-acceptance.md).
