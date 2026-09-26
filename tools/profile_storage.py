"""Credential-safe timing probes; no payloads, paths, or exception details logged.

Local mode uses a temporary synthetic image. Device mode serves an explicitly
selected image read-only and measures host service time during real USB reads.
It never changes firmware, emits HID, or weakens persistence.
"""

import argparse
import asyncio
import json
import logging
import statistics
import struct
import tempfile
import time
from collections import Counter, deque
from pathlib import Path
from unittest.mock import patch

from config_block import parse_env
from pico_devbox import DevBox
from pico_devbox.client import Storage
from pico_devbox.image import ImageDisk
from pico_devbox.protocol import DevBoxError, Message, Op

METRIC_NAMES = ("network_read", "network_write", "usb_read", "wifi_poll", "usb_task", "loop")
METRIC_RECORD = struct.Struct("<IQI8I")


def decode_metrics(reply: Message) -> dict[str, dict[str, object]]:
    if reply.a != 1 or reply.b != len(METRIC_NAMES) or len(reply.payload) != 48 * reply.b:
        raise ValueError("unsupported metrics layout")
    result: dict[str, dict[str, object]] = {}
    for name, fields in zip(METRIC_NAMES, METRIC_RECORD.iter_unpack(reply.payload), strict=True):
        count, total, maximum, *bins = fields
        if sum(bins) != count:
            raise ValueError("invalid metrics counts")
        result[name] = {"count": count, "total_us": total, "max_us": maximum, "bins": bins}
    return result


def timing(values: list[float] | deque[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(ordered),
        "median_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000, 3),
        "total_s": round(sum(ordered), 4),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def emit(event: str, **values: object) -> None:
    print(json.dumps({"event": event, **values}), flush=True)


async def local(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="profile-", dir=folder) as temp:
        path = Path(temp) / "synthetic.img"
        path.write_bytes(b"\0" * (4 * 1024 * 1024))
        disk = ImageDisk(path, read_only=False)
        try:
            for count in (1, 8, 32):
                payload = b"P" * (512 * count)
                for operation in ("write_fsync", "read"):
                    samples = []
                    for index in range(128):
                        start = time.perf_counter()
                        if operation == "read":
                            await asyncio.to_thread(disk.read, index * count, count)
                        else:
                            await asyncio.to_thread(disk.write, index * count, payload)
                        samples.append(time.perf_counter() - start)
                    emit(
                        "local",
                        operation=operation,
                        bytes_per_request=len(payload),
                        **timing(samples),
                    )
        finally:
            disk.close()


async def device(args: argparse.Namespace) -> None:
    values = parse_env(args.config.read_text(encoding="utf-8-sig"))
    token = values.pop("DEVICE_TOKEN", "")
    values.clear()
    # Bounded rolling samples for a session that waits for user safe-eject.
    service: deque[float] = deque(maxlen=65536)
    reads: deque[float] = deque(maxlen=65536)
    gaps: deque[float] = deque(maxlen=65536)
    sizes: Counter[int] = Counter()
    previous_end: float | None = None
    original_read = ImageDisk.read
    original_handle = Storage.handle

    def read(disk: ImageDisk, lba: int, count: int = 1) -> bytes:
        start = time.perf_counter()
        result = original_read(disk, lba, count)
        reads.append(time.perf_counter() - start)
        sizes[count * 512] += 1
        return result

    async def handle(storage: Storage, message: Message) -> None:
        nonlocal previous_end
        start = time.perf_counter()
        if previous_end is not None:
            gaps.append(start - previous_end)
        await original_handle(storage, message)
        previous_end = time.perf_counter()
        service.append(previous_end - start)

    with patch.object(ImageDisk, "read", read), patch.object(Storage, "handle", handle):
        async with DevBox(args.url, token=token) as box:
            rtt = []
            for _ in range(50):
                start = time.perf_counter()
                await box.status()
                rtt.append(time.perf_counter() - start)
            emit("status_roundtrip", **timing(rtt))
            if getattr(args, "firmware_metrics", False):
                emit("firmware_before", counters=decode_metrics(await box.request(Op.METRICS)))
            if args.mode == "network":
                # STATUS with a payload is rejected after the complete frame is
                # received. This measures host-to-Pico bytes plus a tiny reply,
                # with no USB, disk access, HID, or new firmware operation.
                for size in (512, 4096, 16384):
                    samples = []
                    for _ in range(100):
                        start = time.perf_counter()
                        try:
                            await box.request(Op.STATUS, payload=b"P" * size)
                        except DevBoxError as error:
                            if error.code != 8:
                                raise
                        else:
                            raise RuntimeError("expected argument rejection")
                        samples.append(time.perf_counter() - start)
                    emit(
                        "network_upload_rejected_status",
                        bytes_per_request=size,
                        mib_per_s=round(size * len(samples) / sum(samples) / 1024**2, 3),
                        **timing(samples),
                    )
                if getattr(args, "firmware_metrics", False):
                    emit("firmware_after", counters=decode_metrics(await box.request(Op.METRICS)))
                return
            await box.storage.serve_image(args.image, read_only=True)
            emit("attached_read_only")
            try:
                while not box.storage.ejected.is_set():
                    try:
                        await asyncio.wait_for(box.storage.ejected.wait(), 10)
                    except TimeoutError:
                        if box._receiver is not None and box._receiver.done():
                            raise RuntimeError("connection ended")
                    emit(
                        "storage",
                        read_bytes=box.storage.read_bytes,
                        errors=box.storage.errors,
                        request_sizes=dict(sizes),
                        disk_read=timing(reads),
                        host_service=timing(service),
                        between_requests=timing(gaps),
                    )
                if getattr(args, "firmware_metrics", False):
                    emit("firmware_after", counters=decode_metrics(await box.request(Op.METRICS)))
            finally:
                emit(
                    "profile_finished", read_bytes=box.storage.read_bytes, errors=box.storage.errors
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("local", "device", "network"))
    parser.add_argument("--folder", type=Path, default=Path("build/profile"))
    parser.add_argument("--url", default="ws://picousb.local:8080/ws")
    parser.add_argument("--config", type=Path, default=Path(".env.flash"))
    parser.add_argument("--image", type=Path)
    parser.add_argument("--firmware-metrics", action="store_true")
    args = parser.parse_args()
    if args.mode == "device" and args.image is None:
        parser.error("device mode requires --image")
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(local(args.folder) if args.mode == "local" else device(args))
    except KeyboardInterrupt:
        emit("stopped")
    except Exception:
        emit("failed", detail="Check connectivity and inputs locally; details suppressed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
