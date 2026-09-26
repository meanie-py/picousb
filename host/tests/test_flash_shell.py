import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux/WSL shell workflow")


def fixture_tree(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    source = Path(__file__).resolve().parents[2] / "tools/flash.sh"
    shutil.copyfile(source, tmp_path / "tools/flash.sh")
    (tmp_path / "build/firmware").mkdir(parents=True)
    (tmp_path / "build/firmware/picousb.uf2").write_bytes(b"synthetic-public-firmware")
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
        assert (mount / "picousb.uf2").read_bytes() == b"synthetic-public-firmware"


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
