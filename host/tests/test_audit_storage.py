import os
from pathlib import Path

import pytest
from pico_devbox.storage import DATA_START, DirectoryDisk, StorageError


def test_repeated_close_preserves_new_owner_lock(tmp_path: Path) -> None:
    old = DirectoryDisk(tmp_path)
    old.close()
    current = DirectoryDisk(tmp_path)
    try:
        old.close()
        assert current.lock.exists()
        with pytest.raises(StorageError, match="locked"):
            DirectoryDisk(tmp_path)
    finally:
        current.close()


def test_export_retry_preserves_recovery_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "A.TXT").write_bytes(b"before")
    disk = DirectoryDisk(tmp_path)
    disk.write(DATA_START, b"after!".ljust(512, b"\0"))

    def fail_replace(src: object, dst: object) -> None:
        raise OSError("synthetic publication failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", fail_replace)
            with pytest.raises(OSError):
                disk.export()
        marker = disk.state / "export-pending.json"
        evidence = marker.read_bytes()
        with pytest.raises(StorageError, match="interrupted export"):
            disk.export()
        assert marker.read_bytes() == evidence
        assert (tmp_path / "A.TXT").read_bytes() == b"before"
    finally:
        disk.close()


def test_unchanged_flush_does_not_republish_files(tmp_path: Path) -> None:
    source = tmp_path / "A.BIN"
    source.write_bytes(b"a" * (1024 * 1024))
    disk = DirectoryDisk(tmp_path)
    try:
        before = source.stat()
        disk.export()
        disk.export()
        after = source.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert sorted(p.name for p in disk.state.iterdir()) == ["disk.img", "lock", "manifest.json"]
    finally:
        disk.close()
