import argparse
import asyncio
import os
import random
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pico_devbox import DevBox, DevBoxError, cli
from pico_devbox.image import ImageDisk
from pico_devbox.protocol import RESPONSE, Message, Op
from pico_devbox.storage import StorageError
from websockets.asyncio.server import ServerConnection, serve


def make_image(path: Path, size: int = 1024 * 1024) -> Path:
    with path.open("wb") as f:
        f.truncate(size)
    return path


def test_capacity_last_sector_and_persistence(tmp_path: Path) -> None:
    path = make_image(tmp_path / "disk.img", 1024 * 1024 * 1024)
    disk = ImageDisk(path, read_only=False)
    assert disk.sectors == 2097152
    data = bytes(range(256)) * 2
    disk.write(disk.sectors - 1, data)
    assert disk.read(disk.sectors - 1) == data
    for lba in (-1, disk.sectors):
        with pytest.raises(StorageError):
            disk.read(lba)
        with pytest.raises(StorageError):
            disk.write(lba, data)
    with pytest.raises(StorageError):
        disk.read(disk.sectors - 1, 2)
    disk.export()
    disk.close()
    disk = ImageDisk(path)
    assert disk.read(disk.sectors - 1) == data
    with pytest.raises(StorageError):
        disk.write(0, data)
    disk.export()
    disk.close()
    with pytest.raises(StorageError):
        disk.read(0)
    with pytest.raises(StorageError):
        disk.export()


@pytest.mark.parametrize("size", [0, 1, 511, 513])
def test_bad_size(tmp_path: Path, size: int) -> None:
    with pytest.raises(StorageError):
        ImageDisk(make_image(tmp_path / "bad.img", size))


def test_lock_and_mutation(tmp_path: Path) -> None:
    path = make_image(tmp_path / "disk.img")
    disk = ImageDisk(path, read_only=False)
    with pytest.raises(OSError):
        ImageDisk(path, read_only=False)
    with path.open("r+b") as f:
        f.truncate(512)
    with pytest.raises(StorageError):
        disk.read(0)
    disk.close()
    ImageDisk(path).close()


def test_reject_directory_and_link(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        ImageDisk(tmp_path)
    path = make_image(tmp_path / "disk.img")
    link = tmp_path / "link.img"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(StorageError):
        ImageDisk(link)


def test_fsync_failure_is_not_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    disk = ImageDisk(make_image(tmp_path / "disk.img"), read_only=False)

    def fail(fd: int) -> None:
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        disk.write(0, b"a" * 512)
    disk.close()


def test_random_bulk_io_and_bounds(tmp_path: Path) -> None:
    disk = ImageDisk(make_image(tmp_path / "disk.img"), read_only=False)
    rng = random.Random(731)
    expected = bytearray(1024**2)
    for _ in range(100):
        count = rng.randint(1, 32)
        lba = rng.randint(0, disk.sectors - count)
        data = rng.randbytes(count * 512)
        disk.write(lba, data)
        expected[lba * 512 : (lba + count) * 512] = data
    for lba in range(0, disk.sectors, 32):
        assert disk.read(lba, 32) == expected[lba * 512 : (lba + 32) * 512]
    for count in (0, 33):
        with pytest.raises(StorageError):
            disk.read(0, count)
    for size in (0, 513, 16385, 16896):
        with pytest.raises(StorageError):
            disk.write(0, bytes(size))
    disk.close()


def test_process_exit_releases_lock_and_retains_acknowledged_write(tmp_path: Path) -> None:
    path = make_image(tmp_path / "disk.img")
    code = """
import os, sys
from pico_devbox.image import ImageDisk
disk = ImageDisk(sys.argv[1], read_only=False)
disk.write(2000, b'x' * 16384)
os._exit(19)
"""
    result = subprocess.run([sys.executable, "-c", code, str(path)], check=False)
    assert result.returncode == 19
    disk = ImageDisk(path)
    assert disk.read(2000, 32) == b"x" * 16384
    disk.close()


@pytest.mark.parametrize("read_only", [True, False])
async def test_image_over_websocket(tmp_path: Path, read_only: bool) -> None:
    path = make_image(tmp_path / "disk.img")
    done = asyncio.Event()
    replies: list[Message] = []

    async def server(ws: ServerConnection) -> None:
        auth = Message.decode(await ws.recv())
        await ws.send(Message(Op.AUTH | RESPONSE, auth.id, 42).encode())
        attach = Message.decode(await ws.recv())
        assert (attach.a, attach.b, attach.payload) == (2048, 512, bytes([read_only]))
        await ws.send(Message(Op.ATTACH | RESPONSE, attach.id, 42).encode())
        requests = [
            Message(Op.WRITE, 0x80000001, 42, 2016, 32, b"z" * 16384),
            Message(Op.READ, 0x80000002, 42, 2016, 32),
            Message(Op.READ, 0x80000003, 42, 2048, 1),
            Message(Op.FLUSH, 0x80000004, 42),
            Message(Op.EJECT, 0x80000005, 42),
        ]
        for request in requests:
            await ws.send(request.encode())
            replies.append(Message.decode(await ws.recv()))
        done.set()
        await ws.wait_closed()

    async with serve(server, "127.0.0.1", 0) as s:
        async with DevBox(f"ws://127.0.0.1:{s.sockets[0].getsockname()[1]}") as box:
            await box.storage.serve_image(path, read_only=read_only)
            await asyncio.wait_for(done.wait(), 10)
            assert box.storage.ejected.is_set()
    assert [r.status for r in replies] == [6 if read_only else 0, 0, 6, 0, 0]
    assert replies[1].payload == (b"\0" if read_only else b"z") * 16384


@pytest.mark.parametrize("writable", [True, False])
async def test_image_cli(writable: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    box = AsyncMock()
    box.__aenter__.return_value = box
    box.storage.ejected = asyncio.Event()
    box.storage.ejected.set()
    box.ws.close_code = None
    monkeypatch.setenv("PICO_DEVICE_TOKEN", "synthetic")
    monkeypatch.setattr(cli, "DevBox", lambda *a, **kw: box)
    await cli.run(
        argparse.Namespace(
            command="serve-image", url="ws://fake", image="test.img", writable=writable
        )
    )
    box.storage.serve_image.assert_awaited_once_with("test.img", read_only=not writable)


async def test_concurrent_attach_is_exclusive(tmp_path: Path) -> None:
    path = make_image(tmp_path / "one.img")
    other = make_image(tmp_path / "two.img")
    attached = []

    async def server(ws: ServerConnection) -> None:
        async for raw in ws:
            m = Message.decode(raw)
            if m.op == Op.ATTACH:
                attached.append(m)
            await ws.send(Message(m.op | RESPONSE, m.id, 42).encode())

    async with serve(server, "127.0.0.1", 0) as s:
        async with DevBox(f"ws://127.0.0.1:{s.sockets[0].getsockname()[1]}") as box:
            results = await asyncio.gather(
                box.storage.serve_image(path),
                box.storage.serve_image(other),
                return_exceptions=True,
            )
            assert sum(isinstance(r, DevBoxError) for r in results) == 1
            assert len(attached) == 1
    ImageDisk(path).close()
    ImageDisk(other).close()
