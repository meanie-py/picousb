"""Check the public firmware's extent; never inspect config or provisioned images."""

import sys
from pathlib import Path

if Path(sys.argv[1]).stat().st_size > 4 * 1024 * 1024 - 4096:
    raise SystemExit("Firmware overlaps reserved configuration sector")
print("Firmware ends before reserved configuration sector.")
