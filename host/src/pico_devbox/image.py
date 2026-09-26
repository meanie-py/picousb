"""Filesystem-agnostic raw image backend; no physical disks or image creation.

The target owns filesystem recovery. Every acknowledged write is fsynced;
flush/eject also fsync. This cannot make a multi-sector filesystem update atomic.
"""

import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO

from .protocol import MAX_PAYLOAD
from .storage import StorageError, no_link


class ImageDisk:
    def __init__(self, path: str | Path, *, read_only: bool = True) -> None:
        source = Path(path).absolute()
        for parent in (source, *source.parents):
            no_link(parent)
        if not source.is_file():
            raise StorageError("image must be an existing regular file")
        self.file: BinaryIO | None = None
        self.read_only = read_only
        try:
            self.file = source.open("rb" if read_only else "r+b", buffering=0)
            info = os.fstat(self.file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size % 512:
                raise StorageError("image must contain complete 512-byte sectors")
            self.sectors = info.st_size // 512
            if not 1 <= self.sectors <= 0x7FFFFFFF:
                raise StorageError("unsupported image capacity")
            # Kernel locks disappear on process exit: no stale lock-file removal.
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise

    def _seek(self, lba: int, length: int) -> BinaryIO:
        if (
            self.file is None
            or not 512 <= length <= MAX_PAYLOAD
            or length % 512
            or not 0 <= lba < self.sectors
            or length // 512 > self.sectors - lba
        ):
            raise StorageError("invalid image operation")
        if os.fstat(self.file.fileno()).st_size != self.sectors * 512:
            raise StorageError("image size changed while attached")
        self.file.seek(lba * 512)
        return self.file

    def read(self, lba: int, count: int = 1) -> bytes:
        size = count * 512
        result = self._seek(lba, size).read(size)
        if len(result) != size:
            raise StorageError("short image read")
        return result

    def write(self, lba: int, data: bytes) -> None:
        if self.read_only:
            raise StorageError("image is read-only")
        f = self._seek(lba, len(data))
        if f.write(data) != len(data):
            raise StorageError("short image write")
        os.fsync(f.fileno())

    def export(self) -> None:
        if self.file is None:
            raise StorageError("image closed")
        if not self.read_only:
            os.fsync(self.file.fileno())

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
