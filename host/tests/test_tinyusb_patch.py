"""Exercise the extension against the pinned upstream source, not a mock copy."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import patch_tinyusb  # noqa: E402


def test_pinned_patch_fresh_upgrade_idempotence(tmp_path: Path):
    deps = Path(os.environ.get("PICOUSB_DEPS_ROOT", ROOT / "build/deps"))
    usb = deps / "pico-sdk/lib/tinyusb"
    if not (usb / "src/class/msc/msc_device.c").exists():
        pytest.skip("run tools/bootstrap.sh to obtain pinned TinyUSB")
    original = subprocess.check_output(
        [
            "git",
            "-C",
            str(usb),
            "show",
            "331c263400dd5ccf77264cab1fa6bbd9f374b44e:src/class/msc/msc_device.c",
        ],
        text=True,
    )
    path = tmp_path / "msc_device.c"
    path.write_text(original)
    patch_tinyusb.patch(path)
    first = path.read_text()
    patch_tinyusb.patch(path)
    assert path.read_text() == first
    assert first.count("tud_msc_read_batch_complete_cb(xferred_bytes);") == 1
    transfer = first[first.index("    case MSC_STAGE_DATA:") :]
    assert transfer.index("tud_msc_read_batch_complete_cb(xferred_bytes);") < transfer.index(
        "proc_read10_cmd(p_msc);"
    )
    # Upgrading a previous V1 installation takes the early-return branch.
    without_metrics = first.replace("        tud_msc_read_batch_complete_cb(xferred_bytes);\n", "")
    start = without_metrics.index("/* PICOUSB_READ_BATCH_TIMING_V1 */")
    end = without_metrics.index("bool mscd_xfer_cb", start)
    path.write_text(without_metrics[:start] + without_metrics[end:])
    patch_tinyusb.patch(path)
    assert path.read_text() == first


def test_metrics_patch_refuses_unrecognized_source(tmp_path: Path):
    path = tmp_path / "msc_device.c"
    path.write_text("/* PICOUSB_ASYNC_SCSI_V1 */\nunknown source\n")
    with pytest.raises(RuntimeError, match="timing patch refused"):
        patch_tinyusb.patch(path)
    assert path.read_text() == "/* PICOUSB_ASYNC_SCSI_V1 */\nunknown source\n"
