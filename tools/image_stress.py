"""Windows-only physical USB acceptance on explicitly identified disposable media.

Never formats disks. Requires Pico identity AND the PICOUSBTEST volume label.
Manifest lives on the development host. Eject and reattach between write/read
to prevent a successful comparison being satisfied by Windows' file cache.
"""

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path


def identify(letter: str) -> Path:
    if len(letter) != 1 or not letter.isascii() or not letter.isalpha():
        raise ValueError("single drive letter required")
    script = (
        f"$d = Get-Partition -DriveLetter {letter} | Get-Disk; "
        f"$v = Get-Volume -DriveLetter {letter}; "
        "@{name=$d.FriendlyName;serial=$d.SerialNumber;size=$d.Size;"
        "label=$v.FileSystemLabel;fs=$v.FileSystem} | ConvertTo-Json -Compress"
    )
    info = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", script]))
    if (
        info["name"] != "PICOUSB REMOTE DISK"
        or info["serial"] != "PDB0001"
        or info["label"] != "PICOUSBTEST"
        or info["size"] != 1024**3
        or info["fs"] != "NTFS"
    ):
        raise RuntimeError("disposable Pico test volume identity mismatch")
    print("Verified Pico 1 GiB NTFS test volume.", flush=True)
    return Path(f"{letter}:\\")


def eject(letter: str) -> None:
    identify(letter)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k.CreateFileW.restype = wintypes.HANDLE
    k.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.CreateFileW(f"\\\\.\\{letter}:", 0xC0000000, 3, None, 3, 0, None)
    if h == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not k.FlushFileBuffers(h):
            raise ctypes.WinError(ctypes.get_last_error())
        returned = wintypes.DWORD()
        for code in (0x90018, 0x90020, 0x2D4808):
            if not k.DeviceIoControl(h, code, None, 0, None, 0, ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())
        print("Volume flushed, locked, dismounted, and safely ejected.", flush=True)
    finally:
        k.CloseHandle(h)


def transfer(args: argparse.Namespace) -> None:
    root = identify(args.drive)
    started = time.monotonic()
    total = 0
    manifest = {} if args.action == "write" else json.loads(args.manifest.read_text())
    for index in range(4):
        name = f"PicoUSB transfer {index}.bin"
        digest = hashlib.sha256()
        if args.action == "write":
            with (root / name).open("xb", buffering=0) as f:
                for block in range(64):
                    data = hashlib.shake_256(f"picousb:{index}:{block}".encode()).digest(1024**2)
                    if f.write(data) != len(data):
                        raise RuntimeError("short test write")
                    digest.update(data)
                    total += len(data)
                    if block % 8 == 7:
                        print(
                            f"Written {total // 1024**2} MiB in {time.monotonic() - started:.1f}s",
                            flush=True,
                        )
                os.fsync(f.fileno())
            manifest[name] = digest.hexdigest()
            args.manifest.write_text(json.dumps(manifest, indent=2))
        else:
            with (root / name).open("rb", buffering=0) as f:
                while data := f.read(1024**2):
                    digest.update(data)
                    total += len(data)
                    if total % (8 * 1024**2) == 0:
                        print(
                            f"Read {total // 1024**2} MiB in {time.monotonic() - started:.1f}s",
                            flush=True,
                        )
            if digest.hexdigest() != manifest[name]:
                raise RuntimeError("USB file hash mismatch")
            print(f"File {index}: SHA-256 verified.", flush=True)
    if total != 256 * 1024**2:
        raise RuntimeError("unexpected total transfer size")
    elapsed = time.monotonic() - started
    result = {"action": args.action, "bytes": total, "seconds": elapsed, "passed": True}
    with args.manifest.with_suffix(".results.jsonl").open("a") as f:
        f.write(json.dumps(result) + "\n")
    print(f"PASS: {args.action} {total} bytes in {elapsed:.1f}s", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["write", "read", "eject"])
    p.add_argument("--drive", required=True)
    p.add_argument("--manifest", type=Path, default=Path("build/image-test/hashes.json"))
    args = p.parse_args()
    if args.action == "eject":
        eject(args.drive)
    else:
        transfer(args)


if __name__ == "__main__":
    main()
