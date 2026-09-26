"""Measure unchanged directory flushes using temporary synthetic data only.

Run with the Python environment where pico_devbox is installed. No device,
credentials, existing storage image, or physical USB media is accessed.
"""

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from pico_devbox.storage import DirectoryDisk


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="picousb-audit-") as name:
        root = Path(name)
        (root / "BENCH.BIN").write_bytes(b"x" * (16 * 1024 * 1024))
        disk = DirectoryDisk(root)
        try:
            with (
                patch("os.fsync", wraps=os.fsync) as sync,
                patch("os.replace", wraps=os.replace) as replace,
            ):
                start = time.perf_counter()
                for _ in range(5):
                    disk.export()
                print(
                    {
                        "unchanged_flushes": 5,
                        "seconds": round(time.perf_counter() - start, 4),
                        "fsync_calls": sync.call_count,
                        "replace_calls": replace.call_count,
                    }
                )
        finally:
            disk.close()


if __name__ == "__main__":
    main()
