import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux/WSL shell workflow")


# One synthetic public firmware block at the start of flash.
FIRMWARE = (
    struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, 0x10000000, 256, 0, 1, 0xE48BFF59)
    + bytes(476)
    + struct.pack("<I", 0x0AB16F30)
)


def fixture_tree(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    tools = Path(__file__).resolve().parents[2] / "tools"
    for name in ("flash.sh", "firmware_uf2.py"):
        shutil.copyfile(tools / name, tmp_path / "tools" / name)
    (tmp_path / "build/firmware").mkdir(parents=True)
    (tmp_path / "build/firmware/picousb.uf2").write_bytes(FIRMWARE)
    return tmp_path / "tools/flash.sh"


@pytest.mark.parametrize("valid", [True, False])
def test_mount_identity(tmp_path: Path, valid: bool):
    script = fixture_tree(tmp_path)
    mount = tmp_path / "boot volume"
    mount.mkdir()
    (mount / "INFO_UF2.TXT").write_text("Board-ID: RP2350\n" if valid else "unrelated volume\n")
    result = subprocess.run(["bash", str(script), "--mount", str(mount)], capture_output=True)
    assert (result.returncode == 0) == valid
    assert (mount / "picousb.uf2").exists() == valid
    if valid:
        assert (mount / "picousb.uf2").read_bytes() == FIRMWARE


def test_picotool_stops_after_failed_load(tmp_path: Path):
    script = fixture_tree(tmp_path)
    commands = tmp_path / "bin"
    commands.mkdir()
    tool = commands / "picotool"
    tool.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$FLASH_TEST_LOG"\nexit 1\n')
    tool.chmod(0o755)
    log = tmp_path / "calls"
    env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}", FLASH_TEST_LOG=str(log))
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True)
    assert result.returncode != 0 and log.read_text() == "load\n"


def test_refuses_firmware_uf2_that_reaches_config(tmp_path: Path):
    script = fixture_tree(tmp_path)
    e10 = (
        struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0xA000, 0x10FFFF00, 256, 0, 2, 0xE48BFF57)
        + bytes(476)
        + struct.pack("<I", 0x0AB16F30)
    )
    (tmp_path / "build/firmware/picousb.uf2").write_bytes(e10 + FIRMWARE)
    mount = tmp_path / "boot volume"
    mount.mkdir()
    (mount / "INFO_UF2.TXT").write_text("Board-ID: RP2350\n")
    result = subprocess.run(["bash", str(script), "--mount", str(mount)], capture_output=True)
    assert result.returncode != 0 and not (mount / "picousb.uf2").exists()
