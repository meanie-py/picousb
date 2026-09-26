import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from pico_devbox.storage import DATA_START, FAT_SECTORS, DirectoryDisk, StorageError, entry


def test_roundtrip_and_recovery(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_bytes(b"hello\0world" * 300)
    (tmp_path / "SUB").mkdir()
    (tmp_path / "SUB" / "inner.bin").write_bytes(bytes(range(256)))
    disk = DirectoryDisk(tmp_path)
    assert disk.read(0)[510:] == b"\x55\xaa"
    disk.export()
    assert (tmp_path / "hello.txt").read_bytes() == b"hello\0world" * 300
    assert (tmp_path / "SUB" / "inner.bin").read_bytes() == bytes(range(256))
    disk.close()


@pytest.mark.skipif(not shutil.which("mcopy"), reason="FAT tools required")
def test_target_long_names_export_dos_alias(tmp_path: Path) -> None:
    root = tmp_path / "drive"
    root.mkdir()
    disk = DirectoryDisk(root)
    incoming = tmp_path / "a longer name.txt"
    incoming.write_text("target-created long name")
    subprocess.run(["mcopy", "-i", str(disk.image), str(incoming), "::"], check=True)
    disk.export()
    exported = [p for p in root.iterdir() if p.is_file()]
    assert len(exported) == 1
    assert "~" in exported[0].name
    assert exported[0].read_text() == incoming.read_text()
    disk.close()
    reopened = DirectoryDisk(root)
    reopened.export()
    reopened.close()


def test_arbitrary_target_writes_create_overwrite_delete(tmp_path: Path) -> None:
    disk = DirectoryDisk(tmp_path)
    root_lba = 1 + 2 * FAT_SECTORS
    # Target creates a file: data, directory, and FAT arrive separately.
    disk.write(DATA_START, b"target bytes".ljust(512, b"\0"))
    disk.write(root_lba, entry("NEW.TXT", 2, 12).ljust(512, b"\0"))
    with pytest.raises(StorageError):
        disk.export()
    assert not (tmp_path / "NEW.TXT").exists()
    for lba in (1, 1 + FAT_SECTORS):
        fat = bytearray(disk.read(lba))
        struct.pack_into("<H", fat, 4, 0xFFFF)
        disk.write(lba, bytes(fat))
    disk.export()
    assert (tmp_path / "NEW.TXT").read_bytes() == b"target bytes"
    disk.write(DATA_START, b"overwritten!".ljust(512, b"\0"))
    disk.export()
    assert (tmp_path / "NEW.TXT").read_bytes() == b"overwritten!"
    root = bytearray(disk.read(root_lba))
    root[0] = 0xE5
    disk.write(root_lba, bytes(root))
    for lba in (1, 1 + FAT_SECTORS):
        fat = bytearray(disk.read(lba))
        struct.pack_into("<H", fat, 4, 0)
        disk.write(lba, bytes(fat))
    disk.export()
    assert not (tmp_path / "NEW.TXT").exists()
    disk.close()


def test_reconnect_keeps_unexported_writes(tmp_path: Path) -> None:
    (tmp_path / "A.TXT").write_bytes(b"before")
    disk = DirectoryDisk(tmp_path)
    disk.write(DATA_START, b"after!".ljust(512, b"\0"))
    disk.close()
    disk = DirectoryDisk(tmp_path)
    assert (tmp_path / "A.TXT").read_bytes() == b"before"
    disk.export()
    assert (tmp_path / "A.TXT").read_bytes() == b"after!"
    disk.close()


@pytest.mark.parametrize(
    "name", ["../ESCAPE", "/ABS", "CON", "TOOLONGNAME.TXT", "foo/../bar", "..", "x:y"]
)
def test_path_rejection(tmp_path: Path, name: str) -> None:
    disk = DirectoryDisk(tmp_path)
    with pytest.raises(StorageError):
        disk._path(name)
    disk.close()


def test_symlink_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "drive"
    root.mkdir()
    (root / "LINK").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        DirectoryDisk(root)


def test_symlink_replacement_rejected(tmp_path: Path) -> None:
    (tmp_path / "A.TXT").write_text("before")
    disk = DirectoryDisk(tmp_path)
    (tmp_path / "A.TXT").unlink()
    (tmp_path / "A.TXT").symlink_to("/etc/passwd")
    with pytest.raises(StorageError):
        disk.export()
    disk.close()


def test_lock_and_host_edits(tmp_path: Path) -> None:
    disk = DirectoryDisk(tmp_path)
    with pytest.raises(StorageError):
        DirectoryDisk(tmp_path)
    (tmp_path / "NEW.TXT").write_text("host edit")
    with pytest.raises(StorageError):
        disk.export()
    disk.close()


@pytest.mark.parametrize("lba,count", [(-1, 1), (65536, 1), (65535, 2), (0, 0), (0, 3)])
def test_sector_bounds(tmp_path: Path, lba: int, count: int) -> None:
    disk = DirectoryDisk(tmp_path)
    with pytest.raises(StorageError):
        disk.read(lba, count)
    disk.close()


def test_bad_chain_and_fat_mirror(tmp_path: Path) -> None:
    (tmp_path / "A.TXT").write_text("hello")
    disk = DirectoryDisk(tmp_path)
    fat = bytearray(disk.read(1))
    struct.pack_into("<H", fat, 4, 2)
    disk.write(1, bytes(fat))
    with pytest.raises(StorageError, match="copies disagree"):
        disk.export()
    disk.write(1 + FAT_SECTORS, bytes(fat))
    with pytest.raises(StorageError, match="looping"):
        disk.export()
    assert (tmp_path / "A.TXT").read_text() == "hello"
    disk.close()


@pytest.mark.skipif(
    not shutil.which("fsck.fat") or not shutil.which("mcopy"), reason="FAT tools required"
)
def test_independent_fat_tools(tmp_path: Path) -> None:
    root = tmp_path / "drive"
    root.mkdir()
    (root / "HELLO.TXT").write_text("from directory")
    disk = DirectoryDisk(root)
    result = subprocess.run(["fsck.fat", "-n", str(disk.image)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    incoming = tmp_path / "TARGET.TXT"
    incoming.write_text("from target filesystem tools")
    subprocess.run(["mcopy", "-i", str(disk.image), str(incoming), "::TARGET.TXT"], check=True)
    disk.export()
    assert (root / "TARGET.TXT").read_text() == incoming.read_text()
    subprocess.run(["mdel", "-i", str(disk.image), "::HELLO.TXT"], check=True)
    disk.export()
    assert not (root / "HELLO.TXT").exists()
    disk.close()
