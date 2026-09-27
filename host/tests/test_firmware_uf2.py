import importlib.util
import struct
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "firmware_uf2", Path(__file__).parents[2] / "tools" / "firmware_uf2.py"
)
assert _spec and _spec.loader
firmware_uf2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(firmware_uf2)

ARM_S = 0xE48BFF59


def block(address: int, family: int = ARM_S, fill: int = 0) -> bytes:
    header = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, address, 256, 0, 1, family)
    return header + bytes([fill]) * 256 + bytes(220) + struct.pack("<I", 0x0AB16F30)


def test_strips_the_e10_block_that_wraps_onto_the_config_sector() -> None:
    e10 = block(0x10FFFF00, firmware_uf2.ABSOLUTE_FAMILY, 0xEF)
    code = block(0x10000000) + block(0x10000100)
    data, removed = firmware_uf2.strip(e10 + code)
    assert removed == 1 and data == code
    firmware_uf2.check(data)
    with pytest.raises(ValueError):
        firmware_uf2.check(e10 + code)


def test_refuses_firmware_data_in_the_config_sector() -> None:
    for address in (0x103FF000, 0x103FFF00, 0x107FF000):  # direct and wrapped
        with pytest.raises(ValueError, match="overlaps"):
            firmware_uf2.strip(block(address))


def test_leaves_addresses_outside_flash_alone() -> None:
    other = block(0x20000000)
    assert firmware_uf2.strip(other) == (other, 0)


def test_rejects_non_uf2() -> None:
    with pytest.raises(ValueError):
        firmware_uf2.blocks(b"x" * 100)
    with pytest.raises(ValueError):
        firmware_uf2.blocks(bytes(512))


def test_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "fw.uf2"
    path.write_bytes(block(0x10FFFF00, firmware_uf2.ABSOLUTE_FAMILY) + block(0x10000000))
    monkeypatch.setattr(sys, "argv", ["firmware_uf2.py", "check", str(path)])
    assert firmware_uf2.main() == 1
    monkeypatch.setattr(sys, "argv", ["firmware_uf2.py", "strip", str(path)])
    assert firmware_uf2.main() == 0
    monkeypatch.setattr(sys, "argv", ["firmware_uf2.py", "check", str(path)])
    assert firmware_uf2.main() == 0
    monkeypatch.setattr(sys, "argv", ["firmware_uf2.py"])
    assert firmware_uf2.main() == 2
